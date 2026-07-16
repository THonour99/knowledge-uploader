from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_tool() -> ModuleType:
    tool_path = Path(__file__).parents[1] / "backup_restore.py"
    spec = importlib.util.spec_from_file_location("backup_restore", tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load backup/restore tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


def _manifest() -> dict[str, object]:
    return {
        "format_version": 1,
        "backup_id": "20260716T000000Z-aabbccdd",
        "created_at": "2026-07-16T00:00:00+00:00",
        "source": {"database": "knowledge_uploader", "bucket": "knowledge-files"},
        "alembic_revision": "20260716o001",
        "database": {
            "dump_file": "database.dump",
            "sha256": "0" * 64,
            "tables": {},
        },
        "object_store": {"directory": "minio", "objects": []},
        "runtime_configs": [
            {"key": "ragflow.api_key", "is_secret": True, "has_value": True}
        ],
        "validation": {"database_restore": "passed", "object_mirror": "passed"},
    }


def test_manifest_allows_secret_key_names_but_never_values(tool: Any) -> None:
    manifest = _manifest()
    tool._assert_manifest_has_no_secrets(manifest)

    manifest["api_key"] = "plaintext"
    with pytest.raises(RuntimeError, match="forbidden secret field"):
        tool._assert_manifest_has_no_secrets(manifest)


def test_manifest_rejects_environment_secret_leak(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    manifest["source"] = {"database": "secret-value", "bucket": "knowledge-files"}
    monkeypatch.setenv("PGPASSWORD", "secret-value")

    with pytest.raises(RuntimeError, match="environment secret"):
        tool._assert_manifest_has_no_secrets(manifest)


def test_manifest_checksum_detects_tampering(tool: Any, tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    checksum = tool._sha256_file(manifest_path)
    (tmp_path / "manifest.sha256").write_text(
        f"{checksum}  manifest.json\n",
        encoding="utf-8",
    )
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        tool._load_and_verify_manifest(tmp_path)


def test_restore_refuses_production(tool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(RuntimeError, match="forbidden in production"):
        tool._require_non_production("production")

    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(RuntimeError, match="forbidden in production"):
        tool._require_non_production("staging")


def test_restore_cleanup_failure_does_not_refresh_success_metric(
    tool: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "database.dump").write_bytes(b"test-dump")
    (backup_dir / "minio").mkdir()
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "MC_HOST_source"):
        monkeypatch.setenv(name, "test-value")
    manifest = _manifest()
    monkeypatch.setattr(tool, "_load_and_verify_manifest", lambda _path: manifest)
    monkeypatch.setattr(tool, "_assert_database_absent", lambda _name: None)
    monkeypatch.setattr(tool, "_assert_bucket_absent", lambda _name: None)
    monkeypatch.setattr(tool, "_verify_backup_files", lambda _path, _manifest: None)
    monkeypatch.setattr(tool, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool, "_database_snapshot", lambda _name: {})
    monkeypatch.setattr(tool, "_alembic_revision", lambda _name: "20260716o001")
    monkeypatch.setattr(tool, "_config_metadata", lambda _name: manifest["runtime_configs"])
    monkeypatch.setattr(tool, "_object_manifest", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        tool,
        "_cleanup_restore_targets",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )
    metric_writes: list[str] = []
    monkeypatch.setattr(
        tool,
        "_write_success_metric",
        lambda _path, metric_name: metric_writes.append(metric_name),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        tool.restore(
            backup_dir=backup_dir,
            target_environment="staging",
            target_database="restore_validation",
            target_bucket="restore-validation",
            metrics_file=tmp_path / "restore.prom",
            evidence_dir=tmp_path / "evidence",
            cleanup_after_validation=True,
            health_url=None,
        )

    assert metric_writes == []


def test_backup_member_must_not_escape_root(tool: Any, tmp_path: Path) -> None:
    backup_root = tmp_path / "backup"
    backup_root.mkdir()
    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"secret")

    with pytest.raises(RuntimeError, match="escapes the backup directory"):
        tool._resolve_backup_member(backup_root, "../outside.dump", label="database dump")


def test_restore_evidence_must_be_outside_immutable_backup(
    tool: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "MC_HOST_source"):
        monkeypatch.setenv(name, "test-value")

    with pytest.raises(RuntimeError, match="outside the immutable backup"):
        tool.restore(
            backup_dir=backup_dir,
            target_environment="staging",
            target_database="restore_validation",
            target_bucket="restore-validation",
            metrics_file=tmp_path / "restore.prom",
            evidence_dir=backup_dir / "evidence",
            cleanup_after_validation=True,
            health_url=None,
        )


def test_database_snapshot_labels_md5_digest_honestly(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(("documents", "2|abc123"))
    monkeypatch.setattr(tool, "_psql", lambda *_args: next(responses))

    assert tool._database_snapshot("restore_validation") == {
        "documents": {"rows": 2, "row_digest_md5": "abc123"}
    }


def test_failed_backup_removes_only_its_exact_partial_directory(
    tool: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "MC_HOST_source"):
        monkeypatch.setenv(name, "test-value")
    monkeypatch.setattr(tool, "_backup_id", lambda: "20260716T010101Z-aabbccdd")
    monkeypatch.setattr(
        tool,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dump failed")),
    )
    unrelated = tmp_path / ".partial-unrelated"
    unrelated.mkdir()

    with pytest.raises(RuntimeError, match="dump failed"):
        tool.backup(
            output_dir=tmp_path,
            database_name="knowledge_uploader",
            bucket="knowledge-files",
            metrics_file=tmp_path / "backup.prom",
        )

    assert unrelated.is_dir()
    assert not (tmp_path / ".partial-20260716T010101Z-aabbccdd").exists()


def test_logical_restore_metric_never_claims_full_dr_drill(tool: Any, tmp_path: Path) -> None:
    metrics_file = tmp_path / "restore.prom"
    tool._write_attempt_metric(
        metrics_file,
        "knowledge_uploader_logical_restore_validation",
        success=True,
    )
    metrics = (tmp_path / "restore-attempt.prom").read_text(encoding="utf-8")

    assert "logical_restore_validation_last_attempt_success 1" in metrics
    assert "restore_drill" not in metrics
