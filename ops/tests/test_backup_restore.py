from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml


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
        "format_version": 2,
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
        "runtime_configs": [{"key": "ragflow.api_key", "is_secret": True, "has_value": True}],
        "validation": {"database_restore": "passed", "object_mirror": "passed"},
        "reference_integrity": {
            "status": "passed",
            "active_reference_count": 0,
            "deleted_reference_count": 0,
            "object_count": 0,
            "missing_referenced": [],
            "retained_deleted": [],
            "unexplained_orphaned": [],
        },
    }


def _set_backup_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_env: str = "development",
    scheme: str = "http",
    dr_access_key: str = "dr-backup-operator",
    dr_secret_key: str = "dr-backup-secret",
    application_access_key: str = "application-data-plane",
    root_user: str = "minio-root-identity",
    declared_access_key: str | None = None,
    declared_secret_key: str | None = None,
) -> None:
    alias_access_key = declared_access_key or dr_access_key
    alias_secret_key = declared_secret_key or dr_secret_key
    values = {
        "APP_ENV": app_env,
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGUSER": "knowledge",
        "PGPASSWORD": "database-secret",
        "DR_MINIO_ACCESS_KEY": dr_access_key,
        "DR_MINIO_SECRET_KEY": dr_secret_key,
        "MINIO_ACCESS_KEY": application_access_key,
        "MINIO_ROOT_USER": root_user,
        "MC_HOST_source": f"{scheme}://{alias_access_key}:{alias_secret_key}@minio:9000",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _set_protected_ca_environment(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, list[str | None]]:
    ca_file = tmp_path / "private-ca.crt"
    ca_file.write_text("temporary CA fixture", encoding="utf-8")
    monkeypatch.setenv("MINIO_CA_CERT_FILE", str(ca_file))
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.setattr(tool, "_is_read_only_file", lambda _path: True)
    loaded_ca_files: list[str | None] = []

    def load_context(*, cafile: str | None = None) -> object:
        loaded_ca_files.append(cafile)
        return object()

    monkeypatch.setattr(tool.ssl, "create_default_context", load_context)
    return ca_file, loaded_ca_files


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
    _set_backup_environment(monkeypatch)
    manifest = _manifest()
    monkeypatch.setattr(tool, "_load_and_verify_manifest", lambda _path: manifest)
    monkeypatch.setattr(tool, "_assert_database_absent", lambda _name: None)
    monkeypatch.setattr(tool, "_assert_bucket_absent", lambda _name: None)
    monkeypatch.setattr(tool, "_verify_backup_files", lambda _path, _manifest: None)
    monkeypatch.setattr(tool, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool, "_database_snapshot", lambda _name: {})
    monkeypatch.setattr(tool, "_alembic_revision", lambda _name: "20260716o001")
    monkeypatch.setattr(tool, "_config_metadata", lambda _name: manifest["runtime_configs"])
    monkeypatch.setattr(tool, "_file_object_references", lambda _name: [])
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
    _set_backup_environment(monkeypatch)

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


def test_database_snapshot_uses_bounded_row_counts_without_row_digest(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(("documents", "2"))
    monkeypatch.setattr(tool, "_psql", lambda *_args: next(responses))

    assert tool._database_snapshot("restore_validation") == {"documents": {"rows": 2}}


def test_reference_integrity_requires_all_non_deleted_objects(tool: Any) -> None:
    report = tool._reference_integrity_report(
        file_references=[
            {"bucket": "knowledge-files", "object_key": "active.pdf", "status": "parsed"},
            {"bucket": "knowledge-files", "object_key": "gone.pdf", "status": "deleted"},
        ],
        objects=[{"key": "gone.pdf", "size": 1, "sha256": "0" * 64}],
        source_bucket="knowledge-files",
    )

    assert report["missing_referenced"] == ["active.pdf"]
    assert report["retained_deleted"] == ["gone.pdf"]
    assert report["unexplained_orphaned"] == []
    with pytest.raises(RuntimeError, match="missing=1"):
        tool._require_reference_integrity(report)


def test_reference_integrity_classifies_deleted_retention_without_false_orphan(
    tool: Any,
) -> None:
    report = tool._reference_integrity_report(
        file_references=[
            {"bucket": "knowledge-files", "object_key": "active.pdf", "status": "parsed"},
            {"bucket": "knowledge-files", "object_key": "retained.pdf", "status": "deleted"},
        ],
        objects=[
            {"key": "active.pdf", "size": 1, "sha256": "0" * 64},
            {"key": "retained.pdf", "size": 1, "sha256": "1" * 64},
        ],
        source_bucket="knowledge-files",
    )

    assert report == {
        "status": "passed",
        "active_reference_count": 1,
        "deleted_reference_count": 1,
        "object_count": 2,
        "missing_referenced": [],
        "retained_deleted": ["retained.pdf"],
        "unexplained_orphaned": [],
    }
    tool._require_reference_integrity(report)


def test_reference_integrity_rejects_unknown_orphan_and_foreign_active_reference(
    tool: Any,
) -> None:
    report = tool._reference_integrity_report(
        file_references=[
            {"bucket": "other-bucket", "object_key": "foreign.pdf", "status": "approved"}
        ],
        objects=[{"key": "orphan.pdf", "size": 1, "sha256": "0" * 64}],
        source_bucket="knowledge-files",
    )

    assert report["missing_referenced"] == ["other-bucket/foreign.pdf"]
    assert report["unexplained_orphaned"] == ["orphan.pdf"]
    with pytest.raises(RuntimeError, match="unexplained_orphaned=1"):
        tool._require_reference_integrity(report)


def test_failed_backup_removes_only_its_exact_partial_directory(
    tool: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_backup_environment(monkeypatch)
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


def test_development_allows_http_for_a_dedicated_minio_dr_operator(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_backup_environment(monkeypatch)

    tool._validate_minio_dr_configuration()


@pytest.mark.parametrize("app_env", ["staging", "prod", "production"])
def test_protected_environment_requires_https_without_leaking_credentials(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: str,
) -> None:
    _set_backup_environment(monkeypatch, app_env=app_env, scheme="http")
    ca_file, loaded_ca_files = _set_protected_ca_environment(tool, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="must use HTTPS") as error:
        tool._validate_minio_dr_configuration()

    message = str(error.value)
    assert "dr-backup-operator" not in message
    assert "dr-backup-secret" not in message

    _set_backup_environment(monkeypatch, app_env=app_env, scheme="https")
    tool._validate_minio_dr_configuration()
    assert loaded_ca_files == [str(ca_file.resolve())]


@pytest.mark.parametrize("operation", ["backup", "restore"])
@pytest.mark.parametrize(
    "ca_problem",
    ["missing_minio", "missing_ssl", "mismatch", "writable", "invalid_pem"],
)
def test_protected_ca_failure_precedes_commands_and_metrics_without_leaking_values(
    tool: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    ca_problem: str,
) -> None:
    _set_backup_environment(monkeypatch, app_env="staging", scheme="https")
    first_ca = tmp_path / "sensitive-first-ca.crt"
    second_ca = tmp_path / "sensitive-second-ca.crt"
    first_ca.write_text("not a PEM certificate", encoding="utf-8")
    second_ca.write_text("another invalid certificate", encoding="utf-8")
    monkeypatch.setenv("MINIO_CA_CERT_FILE", str(first_ca))
    monkeypatch.setenv("SSL_CERT_FILE", str(first_ca))
    monkeypatch.setattr(tool, "_is_read_only_file", lambda _path: True)

    if ca_problem == "missing_minio":
        monkeypatch.delenv("MINIO_CA_CERT_FILE")
    elif ca_problem == "missing_ssl":
        monkeypatch.delenv("SSL_CERT_FILE")
    elif ca_problem == "mismatch":
        monkeypatch.setenv("SSL_CERT_FILE", str(second_ca))
    elif ca_problem == "writable":
        monkeypatch.setattr(tool, "_is_read_only_file", lambda _path: False)

    commands: list[object] = []
    monkeypatch.setattr(tool, "run", lambda *args, **kwargs: commands.append((args, kwargs)))
    metrics_file = tmp_path / "attempt.prom"

    with pytest.raises(RuntimeError, match="MinIO DR TLS") as error:
        if operation == "backup":
            tool.backup(
                output_dir=tmp_path / "backups",
                database_name="knowledge_uploader",
                bucket="knowledge-files",
                metrics_file=metrics_file,
            )
        else:
            tool.restore(
                backup_dir=tmp_path / "missing-backup",
                target_environment="staging",
                target_database="restore_validation",
                target_bucket="restore-validation",
                metrics_file=metrics_file,
                evidence_dir=tmp_path / "evidence",
                cleanup_after_validation=True,
                health_url=None,
            )

    message = str(error.value)
    assert commands == []
    assert list(tmp_path.glob("*.prom")) == []
    for sensitive_value in (
        str(first_ca),
        str(second_ca),
        "dr-backup-operator",
        "dr-backup-secret",
    ):
        assert sensitive_value not in message


@pytest.mark.parametrize("operation", ["backup", "restore"])
@pytest.mark.parametrize("collision_name", ["MINIO_ACCESS_KEY", "MINIO_ROOT_USER"])
def test_identity_reuse_fails_before_backup_or_restore_executes(
    tool: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    collision_name: str,
) -> None:
    _set_backup_environment(monkeypatch)
    monkeypatch.setenv(collision_name, "dr-backup-operator")
    commands: list[object] = []
    monkeypatch.setattr(tool, "run", lambda *args, **kwargs: commands.append((args, kwargs)))
    metrics_file = tmp_path / "attempt.prom"

    with pytest.raises(RuntimeError, match="dedicated access-key identity") as error:
        if operation == "backup":
            tool.backup(
                output_dir=tmp_path / "backups",
                database_name="knowledge_uploader",
                bucket="knowledge-files",
                metrics_file=metrics_file,
            )
        else:
            tool.restore(
                backup_dir=tmp_path / "missing-backup",
                target_environment="staging",
                target_database="restore_validation",
                target_bucket="restore-validation",
                metrics_file=metrics_file,
                evidence_dir=tmp_path / "evidence",
                cleanup_after_validation=True,
                health_url=None,
            )

    assert commands == []
    assert not metrics_file.exists()
    assert not metrics_file.with_name("attempt-attempt.prom").exists()
    assert "dr-backup-operator" not in str(error.value)


@pytest.mark.parametrize(
    ("declared_access_key", "declared_secret_key"),
    [("different-operator", None), (None, "different-secret")],
)
def test_mc_alias_must_declare_the_configured_dr_operator_without_leaking_values(
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
    declared_access_key: str | None,
    declared_secret_key: str | None,
) -> None:
    _set_backup_environment(
        monkeypatch,
        declared_access_key=declared_access_key,
        declared_secret_key=declared_secret_key,
    )

    with pytest.raises(RuntimeError, match="declare exactly") as error:
        tool._validate_minio_dr_configuration()

    message = str(error.value)
    for credential in (
        "dr-backup-operator",
        "dr-backup-secret",
        "different-operator",
        "different-secret",
    ):
        assert credential not in message


def _protected_compose_values(root: Path, tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "DR_MINIO_ACCESS_KEY": "dr-protected-access-fixture",
        "DR_MINIO_SECRET_KEY": "dr-protected-secret-fixture",
        "MINIO_ACCESS_KEY": "application-protected-access-fixture",
        "MINIO_SECRET_KEY": "application-protected-secret-fixture",
        "MINIO_ROOT_USER": "root-protected-access-fixture",
        "MINIO_ROOT_PASSWORD": "root-protected-secret-fixture",
        "MINIO_TLS_DIR": (tmp_path / "minio-tls").as_posix(),
        "PROMETHEUS_CONFIG_FILE": (root / "ops/observability/prometheus.yml").as_posix(),
    }


def _run_compose_config(
    *,
    root: Path,
    tmp_path: Path,
    values: dict[str, str],
    protected: bool,
    json_output: bool,
) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable")

    environment = os.environ.copy()
    controlled_names = {
        "APP_ENV",
        "COMPOSE_ENV_FILES",
        "DR_MINIO_ACCESS_KEY",
        "DR_MINIO_SECRET_KEY",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_TLS_DIR",
        "PROMETHEUS_CONFIG_FILE",
    }
    for name in controlled_names:
        environment.pop(name, None)

    version = subprocess.run(
        [docker, "compose", "version"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if version.returncode != 0:
        pytest.skip("Docker Compose plugin is unavailable")

    env_file = tmp_path / ("protected-compose.env" if protected else "development-compose.env")
    env_file.write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
        encoding="utf-8",
    )
    compose_files = [root / "docker-compose.yml"]
    if protected:
        compose_files.append(root / "docker-compose.observability.yml")
    compose_files.append(root / "docker-compose.ops.yml")
    if protected:
        compose_files.append(root / "docker-compose.observability.protected.yml")

    command = [docker, "compose", "--env-file", str(env_file)]
    for compose_file in compose_files:
        command.extend(("-f", str(compose_file)))
    command.extend(("--profile", "ops", "config"))
    command.extend(("--format", "json") if json_output else ("--quiet",))
    return subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_compose_wires_dedicated_dr_identity_and_protected_tls() -> None:
    compose_path = Path(__file__).parents[2] / "docker-compose.ops.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    environment = compose["services"]["backup-restore"]["environment"]

    assert environment["DR_MINIO_ACCESS_KEY"] == (
        "${DR_MINIO_ACCESS_KEY:?DR_MINIO_ACCESS_KEY is required}"
    )
    assert environment["DR_MINIO_SECRET_KEY"] == (
        "${DR_MINIO_SECRET_KEY:?DR_MINIO_SECRET_KEY is required}"
    )
    assert environment["MINIO_ACCESS_KEY"] == "${MINIO_ACCESS_KEY:-knowledge}"
    assert environment["MINIO_ROOT_USER"] == "${MINIO_ROOT_USER:-knowledge-root}"
    assert environment["MC_HOST_source"] == (
        "http://${DR_MINIO_ACCESS_KEY:?DR_MINIO_ACCESS_KEY is required}:"
        "${DR_MINIO_SECRET_KEY:?DR_MINIO_SECRET_KEY is required}@minio:9000"
    )

    root = Path(__file__).parents[2]
    protected = yaml.safe_load(
        (root / "docker-compose.observability.protected.yml").read_text(encoding="utf-8")
    )
    protected_service = protected["services"]["backup-restore"]
    assert protected_service["environment"] == {
        "MINIO_ACCESS_KEY": "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required}",
        "MINIO_ROOT_USER": "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}",
        "MC_HOST_source": (
            "https://${DR_MINIO_ACCESS_KEY:?DR_MINIO_ACCESS_KEY is required}:"
            "${DR_MINIO_SECRET_KEY:?DR_MINIO_SECRET_KEY is required}@minio:9000"
        ),
        "MINIO_CA_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
        "SSL_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
    }
    assert protected_service["volumes"] == [
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:/run/secrets/minio-ca/ca.crt:ro"
    ]


@pytest.mark.parametrize("variable", ["MINIO_ACCESS_KEY", "MINIO_ROOT_USER"])
@pytest.mark.parametrize("state", ["missing", "empty"])
def test_protected_compose_rejects_missing_or_empty_primary_minio_identity(
    tmp_path: Path,
    variable: str,
    state: str,
) -> None:
    root = Path(__file__).parents[2]
    values = _protected_compose_values(root, tmp_path)
    credential_values = tuple(
        values[name]
        for name in (
            "DR_MINIO_ACCESS_KEY",
            "DR_MINIO_SECRET_KEY",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
            "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD",
        )
    )
    if state == "missing":
        values.pop(variable)
    else:
        values[variable] = ""

    result = _run_compose_config(
        root=root,
        tmp_path=tmp_path,
        values=values,
        protected=True,
        json_output=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert variable in output
    assert all(value not in output for value in credential_values)


def test_protected_compose_resolves_explicit_primary_minio_identities(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    values = _protected_compose_values(root, tmp_path)

    result = _run_compose_config(
        root=root,
        tmp_path=tmp_path,
        values=values,
        protected=True,
        json_output=True,
    )

    assert result.returncode == 0
    resolved = json.loads(result.stdout)
    environment = resolved["services"]["backup-restore"]["environment"]
    assert environment["MINIO_ACCESS_KEY"] == values["MINIO_ACCESS_KEY"]
    assert environment["MINIO_ROOT_USER"] == values["MINIO_ROOT_USER"]


def test_development_compose_keeps_primary_minio_identity_defaults(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    values = {
        "APP_ENV": "development",
        "DR_MINIO_ACCESS_KEY": "development-dr-access-fixture",
        "DR_MINIO_SECRET_KEY": "development-dr-secret-fixture",
    }

    result = _run_compose_config(
        root=root,
        tmp_path=tmp_path,
        values=values,
        protected=False,
        json_output=True,
    )

    assert result.returncode == 0
    resolved = json.loads(result.stdout)
    environment = resolved["services"]["backup-restore"]["environment"]
    assert environment["MINIO_ACCESS_KEY"] == "knowledge"
    assert environment["MINIO_ROOT_USER"] == "knowledge-root"


def test_protected_runbook_commands_include_the_tls_overlay() -> None:
    root = Path(__file__).parents[2]
    runbook = (root / "ops" / "runbooks" / "backup-restore.md").read_text(encoding="utf-8")
    protected_section = runbook.split("## Protected staging backup and restore", 1)[1]
    protected_section = protected_section.split("## Backup staleness alert", 1)[0]
    commands = [
        line.strip()
        for line in protected_section.splitlines()
        if line.strip().startswith("docker compose ")
    ]

    assert len(commands) == 2
    required_files = (
        "-f docker-compose.yml",
        "-f docker-compose.observability.yml",
        "-f docker-compose.ops.yml",
        "-f docker-compose.observability.protected.yml",
    )
    for command in commands:
        assert all(fragment in command for fragment in required_files)

    required_minio_variables = (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "DR_MINIO_ACCESS_KEY",
        "DR_MINIO_SECRET_KEY",
        "MINIO_TLS_DIR",
        "MINIO_ENDPOINT",
        "MINIO_BUCKET",
        "MINIO_SECURE",
        "MINIO_CA_CERT_FILE",
        "SSL_CERT_FILE",
    )
    assert all(f"`{name}`" in protected_section for name in required_minio_variables)
    assert "deployment secret owner" in protected_section
    assert "never accept the development defaults" in protected_section
