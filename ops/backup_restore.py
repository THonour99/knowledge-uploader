"""Validated PostgreSQL and MinIO backup/restore drill tooling."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

BACKUP_FORMAT_VERSION = 2
DATABASE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
MINIO_TLS_REQUIRED_ENVIRONMENTS = frozenset({"staging", "prod", "production"})
MINIO_DR_ENVIRONMENT = (
    "MC_HOST_source",
    "DR_MINIO_ACCESS_KEY",
    "DR_MINIO_SECRET_KEY",
    "MINIO_ACCESS_KEY",
    "MINIO_ROOT_USER",
)
RESTORE_DATABASE_PREFIX = "restore_"
RESTORE_BUCKET_PREFIX = "restore-"
MC_ALIAS = "source"


def run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(arguments[0])
    if executable is None:
        raise RuntimeError(f"required executable not found: {arguments[0]}")
    command = [executable, *arguments[1:]]
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(environment) if environment is not None else None,
    )


def backup(
    *,
    output_dir: Path,
    database_name: str,
    bucket: str,
    metrics_file: Path,
) -> Path:
    _validate_database_name(database_name)
    _validate_bucket_name(bucket)
    _require_environment(("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", *MINIO_DR_ENVIRONMENT))
    _validate_minio_dr_configuration()
    _write_attempt_metric(metrics_file, "knowledge_uploader_backup", success=False)
    backup_id = _backup_id()
    output_root = output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    partial_dir = output_root / f".partial-{backup_id}"
    final_dir = output_root / backup_id
    partial_dir.mkdir()
    completed = False
    try:
        dump_path = partial_dir / "database.dump"
        object_dir = partial_dir / "minio"
        verification_database = _verification_database_name(backup_id)

        run(
            (
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(dump_path),
                "--dbname",
                database_name,
            )
        )
        (
            database_snapshot,
            alembic_revision,
            config_metadata,
            file_references,
        ) = _snapshot_restored_dump(
            dump_path=dump_path,
            verification_database=verification_database,
        )
        object_dir.mkdir()
        run(("mc", "mirror", "--overwrite", f"{MC_ALIAS}/{bucket}", str(object_dir)))
        object_manifest = _object_manifest(
            object_dir,
            source_metadata=_minio_metadata(bucket),
        )
        reference_report = _reference_integrity_report(
            file_references=file_references,
            objects=object_manifest,
            source_bucket=bucket,
        )
        _require_reference_integrity(reference_report)
        manifest: dict[str, object] = {
            "format_version": BACKUP_FORMAT_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "source": {
                "database": database_name,
                "bucket": bucket,
            },
            "alembic_revision": alembic_revision,
            "database": {
                "dump_file": dump_path.name,
                "sha256": _sha256_file(dump_path),
                "tables": database_snapshot,
            },
            "object_store": {
                "directory": object_dir.name,
                "objects": object_manifest,
            },
            "runtime_configs": config_metadata,
            "validation": {
                "database_restore": "passed",
                "object_mirror": "passed",
                "database_object_references": "passed",
            },
            "reference_integrity": reference_report,
            "consistency_boundary": "uncoordinated_full_dump_then_object_mirror",
        }
        _assert_manifest_has_no_secrets(manifest)
        manifest_path = partial_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        (partial_dir / "manifest.sha256").write_text(
            f"{_sha256_file(manifest_path)}  manifest.json\n",
            encoding="utf-8",
        )
        partial_dir.replace(final_dir)
        completed = True
    finally:
        if not completed:
            _remove_current_partial_backup(
                output_root=output_root,
                partial_dir=partial_dir,
                backup_id=backup_id,
            )
    _write_success_metric(
        metrics_file,
        "knowledge_uploader_backup_last_success_timestamp_seconds",
    )
    _write_attempt_metric(metrics_file, "knowledge_uploader_backup", success=True)
    return final_dir


def restore(
    *,
    backup_dir: Path,
    target_environment: str,
    target_database: str,
    target_bucket: str,
    metrics_file: Path,
    evidence_dir: Path,
    cleanup_after_validation: bool,
    health_url: str | None,
) -> dict[str, object]:
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    _require_non_production(target_environment)
    _validate_restore_target_names(target_database, target_bucket)
    _require_environment(("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", *MINIO_DR_ENVIRONMENT))
    _validate_minio_dr_configuration()
    _write_attempt_metric(
        metrics_file,
        "knowledge_uploader_logical_restore_validation",
        success=False,
    )
    source_dir = backup_dir.resolve()
    evidence_root = evidence_dir.resolve()
    if evidence_root == source_dir or source_dir in evidence_root.parents:
        raise RuntimeError("restore evidence directory must be outside the immutable backup")
    manifest = _load_and_verify_manifest(source_dir)
    source = _mapping(manifest["source"], "source")
    if target_database == source.get("database") or target_bucket == source.get("bucket"):
        raise RuntimeError("restore target must not equal a backup source")
    _assert_database_absent(target_database)
    _assert_bucket_absent(target_bucket)

    dump_path = _resolve_backup_member(
        source_dir,
        str(_mapping(manifest["database"], "database")["dump_file"]),
        label="database dump",
    )
    object_dir = _resolve_backup_member(
        source_dir,
        str(_mapping(manifest["object_store"], "object_store")["directory"]),
        label="object directory",
    )
    _verify_backup_files(source_dir, manifest)
    run(("createdb", "--maintenance-db", "postgres", target_database))
    run(
        (
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--dbname",
            target_database,
            str(dump_path),
        )
    )
    run(("mc", "mb", f"{MC_ALIAS}/{target_bucket}"))
    run(("mc", "mirror", "--overwrite", str(object_dir), f"{MC_ALIAS}/{target_bucket}"))

    restored_tables = _database_snapshot(target_database)
    expected_tables = _mapping(_mapping(manifest["database"], "database")["tables"], "tables")
    if restored_tables != expected_tables:
        raise RuntimeError("restored database row counts do not match manifest")
    if _alembic_revision(target_database) != manifest["alembic_revision"]:
        raise RuntimeError("restored Alembic revision does not match manifest")
    if _config_metadata(target_database) != manifest["runtime_configs"]:
        raise RuntimeError("restored runtime config metadata does not match manifest")

    with tempfile.TemporaryDirectory(prefix="knowledge-restore-verify-") as temporary:
        mirrored = Path(temporary) / "minio"
        mirrored.mkdir()
        run(("mc", "mirror", f"{MC_ALIAS}/{target_bucket}", str(mirrored)))
        restored_objects = _object_manifest(mirrored, source_metadata={})
    expected_objects = _mapping(manifest["object_store"], "object_store")["objects"]
    object_report = _object_difference(restored_objects, expected_objects)
    if any(object_report.values()):
        raise RuntimeError("restored MinIO objects do not match manifest")
    source_bucket = source.get("bucket")
    if not isinstance(source_bucket, str) or not source_bucket:
        raise RuntimeError("backup source bucket is invalid")
    reference_report = _reference_integrity_report(
        file_references=_file_object_references(target_database),
        objects=restored_objects,
        source_bucket=source_bucket,
    )
    _require_reference_integrity(reference_report)
    expected_reference_report = _mapping(
        manifest.get("reference_integrity"),
        "reference integrity",
    )
    if reference_report != expected_reference_report:
        raise RuntimeError("restored database/object reference report does not match manifest")
    if health_url is not None:
        _verify_health_url(health_url)

    completed_at = datetime.now(UTC)
    backup_created_at = _parse_utc_timestamp(manifest["created_at"], "backup created_at")
    evidence: dict[str, object] = {
        "backup_id": manifest["backup_id"],
        "restore_started_at": started_at.isoformat(),
        "restore_completed_at": completed_at.isoformat(),
        "rpo_seconds": max((started_at - backup_created_at).total_seconds(), 0.0),
        "rto_seconds": max(time.monotonic() - started_monotonic, 0.0),
        "target_environment": target_environment,
        "target_database": target_database,
        "target_bucket": target_bucket,
        "alembic_revision": manifest["alembic_revision"],
        "database_tables": expected_tables,
        "database_validation": "passed",
        "object_validation": "passed",
        "object_report": object_report,
        "reference_integrity": reference_report,
        "service_health_validation": "passed" if health_url is not None else "not_requested",
        "main_chain_smoke": "not_provided",
        "consistency_boundary": manifest.get("consistency_boundary", "unknown"),
        "cleanup_after_validation": cleanup_after_validation,
    }
    if cleanup_after_validation:
        _cleanup_restore_targets(target_database, target_bucket)
        evidence["cleanup_validation"] = "passed"
    else:
        evidence["cleanup_validation"] = "not_requested"
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence_name = _safe_evidence_name(str(manifest["backup_id"]))
    _write_json(evidence_root / f"{evidence_name}.json", evidence)
    _write_success_metric(
        metrics_file,
        "knowledge_uploader_logical_restore_validation_last_success_timestamp_seconds",
    )
    _write_attempt_metric(
        metrics_file,
        "knowledge_uploader_logical_restore_validation",
        success=True,
    )
    return evidence


def _snapshot_restored_dump(
    *,
    dump_path: Path,
    verification_database: str,
) -> tuple[
    dict[str, object],
    str,
    list[dict[str, object]],
    list[dict[str, str]],
]:
    _assert_database_absent(verification_database)
    run(("createdb", "--maintenance-db", "postgres", verification_database))
    try:
        run(
            (
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--dbname",
                verification_database,
                str(dump_path),
            )
        )
        return (
            _database_snapshot(verification_database),
            _alembic_revision(verification_database),
            _config_metadata(verification_database),
            _file_object_references(verification_database),
        )
    finally:
        run(
            (
                "dropdb",
                "--if-exists",
                "--force",
                "--maintenance-db",
                "postgres",
                verification_database,
            )
        )


def _database_snapshot(database_name: str) -> dict[str, object]:
    table_output = _psql(
        database_name,
        (
            "SELECT tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname = 'public' ORDER BY tablename"
        ),
    )
    snapshot: dict[str, object] = {}
    for table_name in (line for line in table_output.splitlines() if line):
        quoted_table = _quote_identifier(table_name)
        count_text = _psql(
            database_name,
            f"SELECT count(*)::text FROM public.{quoted_table}",
        )
        snapshot[table_name] = {"rows": int(count_text)}
    return snapshot


def _file_object_references(database_name: str) -> list[dict[str, str]]:
    output = _psql(
        database_name,
        (
            "SELECT json_build_object("
            "'bucket', bucket, 'object_key', object_key, 'status', status)::text "
            "FROM files WHERE storage_type = 'minio' "
            "ORDER BY bucket, object_key, status"
        ),
    )
    references: list[dict[str, str]] = []
    for raw_line in (line for line in output.splitlines() if line):
        raw_reference = json.loads(raw_line)
        reference = _mapping(raw_reference, "file object reference")
        if set(reference) != {"bucket", "object_key", "status"} or not all(
            isinstance(reference[field], str) and reference[field]
            for field in ("bucket", "object_key", "status")
        ):
            raise RuntimeError("file object reference is invalid")
        references.append(
            {
                "bucket": reference["bucket"],
                "object_key": reference["object_key"],
                "status": reference["status"],
            }
        )
    return references


def _alembic_revision(database_name: str) -> str:
    revision = _psql(
        database_name,
        "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1",
    ).strip()
    if not revision:
        raise RuntimeError("backup database has no Alembic revision")
    return revision


def _config_metadata(database_name: str) -> list[dict[str, object]]:
    output = _psql(
        database_name,
        (
            "SELECT key || '|' || is_secret::text || '|' || (value IS NOT NULL)::text "
            "FROM system_configs ORDER BY key"
        ),
    )
    configs: list[dict[str, object]] = []
    for row in (line for line in output.splitlines() if line):
        key, is_secret, has_value = row.split("|", 2)
        configs.append(
            {
                "key": key,
                "is_secret": is_secret == "true",
                "has_value": has_value == "true",
            }
        )
    return configs


def _psql(database_name: str, statement: str) -> str:
    return run(
        (
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--dbname",
            database_name,
            "--command",
            statement,
        )
    ).stdout.strip()


def _minio_metadata(bucket: str) -> dict[str, dict[str, object]]:
    result = run(("mc", "ls", "--recursive", "--json", f"{MC_ALIAS}/{bucket}"))
    metadata: dict[str, dict[str, object]] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        key = str(item.get("key", "")).removeprefix(f"{bucket}/").lstrip("/")
        if not key:
            continue
        metadata[key] = {
            "etag": item.get("etag"),
            "size": int(item.get("size", 0)),
        }
    return metadata


def _object_manifest(
    root: Path,
    *,
    source_metadata: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
        relative_key = file_path.relative_to(root).as_posix()
        metadata = source_metadata.get(relative_key, {})
        objects.append(
            {
                "key": relative_key,
                "size": file_path.stat().st_size,
                "etag": metadata.get("etag"),
                "sha256": _sha256_file(file_path),
            }
        )
    return objects


def _comparable_objects(raw_objects: object) -> list[dict[str, object]]:
    if not isinstance(raw_objects, list):
        raise RuntimeError("manifest object list is invalid")
    comparable: list[dict[str, object]] = []
    for raw_item in raw_objects:
        item = _mapping(raw_item, "object")
        comparable.append(
            {
                "key": item["key"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
        )
    return sorted(comparable, key=lambda item: str(item["key"]))


def _object_difference(actual: object, expected: object) -> dict[str, list[str]]:
    actual_by_key = {str(item["key"]): item for item in _comparable_objects(actual)}
    expected_by_key = {str(item["key"]): item for item in _comparable_objects(expected)}
    return {
        "missing": sorted(expected_by_key.keys() - actual_by_key.keys()),
        "orphaned": sorted(actual_by_key.keys() - expected_by_key.keys()),
        "mismatched": sorted(
            key
            for key in actual_by_key.keys() & expected_by_key.keys()
            if actual_by_key[key] != expected_by_key[key]
        ),
    }


def _reference_integrity_report(
    *,
    file_references: object,
    objects: object,
    source_bucket: str,
) -> dict[str, object]:
    if not isinstance(file_references, list):
        raise RuntimeError("file object reference list is invalid")
    object_keys = {str(item["key"]) for item in _comparable_objects(objects)}
    active_keys: set[str] = set()
    deleted_keys: set[str] = set()
    foreign_active_references: set[str] = set()
    for raw_reference in file_references:
        reference = _mapping(raw_reference, "file object reference")
        bucket = reference.get("bucket")
        object_key = reference.get("object_key")
        status = reference.get("status")
        if (
            not isinstance(bucket, str)
            or not bucket
            or not isinstance(object_key, str)
            or not object_key
            or not isinstance(status, str)
            or not status
        ):
            raise RuntimeError("file object reference is invalid")
        if bucket != source_bucket:
            if status != "deleted":
                foreign_active_references.add(f"{bucket}/{object_key}")
            continue
        if status == "deleted":
            deleted_keys.add(object_key)
        else:
            active_keys.add(object_key)

    missing_referenced = sorted((active_keys - object_keys) | foreign_active_references)
    retained_deleted = sorted((deleted_keys & object_keys) - active_keys)
    unexplained_orphaned = sorted(object_keys - active_keys - deleted_keys)
    passed = not missing_referenced and not unexplained_orphaned
    return {
        "status": "passed" if passed else "failed",
        "active_reference_count": len(active_keys) + len(foreign_active_references),
        "deleted_reference_count": len(deleted_keys),
        "object_count": len(object_keys),
        "missing_referenced": missing_referenced,
        "retained_deleted": retained_deleted,
        "unexplained_orphaned": unexplained_orphaned,
    }


def _require_reference_integrity(report: Mapping[str, object]) -> None:
    if report.get("status") != "passed":
        raise RuntimeError(
            "database/object reference integrity failed: "
            f"missing={len(_string_list(report.get('missing_referenced')))}, "
            f"unexplained_orphaned={len(_string_list(report.get('unexplained_orphaned')))}"
        )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError("reference integrity list is invalid")
    return value


def _load_and_verify_manifest(backup_dir: Path) -> dict[str, object]:
    backup_root = backup_dir.resolve()
    manifest_path = _resolve_backup_member(backup_root, "manifest.json", label="manifest")
    checksum_path = _resolve_backup_member(
        backup_root,
        "manifest.sha256",
        label="manifest checksum",
    )
    expected_checksum = checksum_path.read_text(encoding="utf-8").split()[0]
    if not secrets.compare_digest(expected_checksum, _sha256_file(manifest_path)):
        raise RuntimeError("backup manifest checksum mismatch")
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = _mapping(raw_manifest, "manifest")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise RuntimeError("unsupported backup format version")
    _assert_manifest_has_no_secrets(manifest)
    return manifest


def _verify_backup_files(backup_dir: Path, manifest: Mapping[str, object]) -> None:
    backup_root = backup_dir.resolve()
    database = _mapping(manifest["database"], "database")
    dump_path = _resolve_backup_member(
        backup_root,
        str(database["dump_file"]),
        label="database dump",
    )
    if _sha256_file(dump_path) != database["sha256"]:
        raise RuntimeError("database dump checksum mismatch")
    object_store = _mapping(manifest["object_store"], "object_store")
    object_dir = _resolve_backup_member(
        backup_root,
        str(object_store["directory"]),
        label="object directory",
    )
    actual_objects = _object_manifest(object_dir, source_metadata={})
    if _comparable_objects(actual_objects) != _comparable_objects(object_store["objects"]):
        raise RuntimeError("local backup objects do not match manifest")


def _assert_database_absent(database_name: str) -> None:
    escaped = database_name.replace("'", "''")
    exists = _psql(
        "postgres",
        f"SELECT 1 FROM pg_database WHERE datname = '{escaped}'",
    )
    if exists:
        raise RuntimeError(f"target database already exists: {database_name}")


def _assert_bucket_absent(bucket: str) -> None:
    result = run(("mc", "ls", "--json", MC_ALIAS))
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        key = str(item.get("key", "")).strip("/")
        if key == bucket:
            raise RuntimeError(f"target bucket already exists: {bucket}")


def _cleanup_restore_targets(database_name: str, bucket: str) -> None:
    _validate_restore_target_names(database_name, bucket)
    run(
        (
            "dropdb",
            "--if-exists",
            "--force",
            "--maintenance-db",
            "postgres",
            database_name,
        )
    )
    run(("mc", "rb", "--force", f"{MC_ALIAS}/{bucket}"))


def _remove_current_partial_backup(
    *,
    output_root: Path,
    partial_dir: Path,
    backup_id: str,
) -> None:
    root = output_root.resolve()
    expected_name = f".partial-{backup_id}"
    if partial_dir.name != expected_name:
        raise RuntimeError("refusing to clean an unexpected partial backup path")
    resolved = partial_dir.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise RuntimeError("refusing to clean a partial backup outside output root") from error
    if len(relative.parts) != 1 or relative.name != expected_name:
        raise RuntimeError("refusing to clean a nested or renamed partial backup")
    if resolved.exists():
        shutil.rmtree(resolved)


def _verify_health_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("health URL must be an absolute HTTP(S) URL")
    with urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("restored service health check failed")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError("restored service health payload is not ready")


def _write_success_metric(path: Path, metric_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        f"# TYPE {metric_name} gauge\n{metric_name} {time.time():.6f}\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_attempt_metric(path: Path, prefix: str, *, success: bool) -> None:
    attempt_path = path.with_name(f"{path.stem}-attempt{path.suffix}")
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = attempt_path.with_suffix(attempt_path.suffix + ".tmp")
    timestamp_name = f"{prefix}_last_attempt_timestamp_seconds"
    result_name = f"{prefix}_last_attempt_success"
    temporary.write_text(
        "\n".join(
            (
                f"# TYPE {timestamp_name} gauge",
                f"{timestamp_name} {time.time():.6f}",
                f"# TYPE {result_name} gauge",
                f"{result_name} {1 if success else 0}",
                "",
            )
        ),
        encoding="utf-8",
    )
    temporary.replace(attempt_path)


def _assert_manifest_has_no_secrets(manifest: Mapping[str, object]) -> None:
    forbidden_fields = frozenset(
        {
            "password",
            "api_key",
            "secret_key",
            "access_key",
            "encryption_key",
            "jwt_secret",
            "token",
            "value",
        }
    )

    def assert_safe_fields(value: object) -> None:
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key).strip().lower()
                if key in forbidden_fields:
                    raise RuntimeError("backup manifest contains a forbidden secret field")
                assert_safe_fields(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_safe_fields(nested)

    assert_safe_fields(manifest)
    raw_configs = manifest.get("runtime_configs", [])
    if not isinstance(raw_configs, list):
        raise RuntimeError("backup manifest runtime config metadata is invalid")
    for raw_config in raw_configs:
        config = _mapping(raw_config, "runtime config metadata")
        if set(config) != {"key", "is_secret", "has_value"}:
            raise RuntimeError("backup manifest contains runtime config values")
        if not isinstance(config["key"], str):
            raise RuntimeError("backup manifest runtime config key is invalid")
        if not isinstance(config["is_secret"], bool) or not isinstance(config["has_value"], bool):
            raise RuntimeError("backup manifest runtime config flags are invalid")

    serialized = json.dumps(manifest, ensure_ascii=False)
    serialized_lower = serialized.lower()
    if any(token in serialized_lower for token in ("postgresql://", "postgres://", "mc_host_")):
        raise RuntimeError("backup manifest contains a forbidden connection string")
    for environment_name in (
        "PGPASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "ENCRYPTION_KEY",
        "JWT_SECRET",
        "RAGFLOW_API_KEY",
        "AI_API_KEY",
    ):
        secret = os.environ.get(environment_name, "")
        if len(secret) >= 4 and secret in serialized:
            raise RuntimeError("backup manifest contains an environment secret")


def _require_non_production(target_environment: str) -> None:
    requested = target_environment.strip().lower()
    current = os.environ.get("APP_ENV", "").strip().lower()
    if requested in PRODUCTION_ENVIRONMENTS or current in PRODUCTION_ENVIRONMENTS:
        raise RuntimeError("restore is forbidden in production")


def _validate_restore_target_names(database_name: str, bucket: str) -> None:
    _validate_database_name(database_name)
    _validate_bucket_name(bucket)
    if not database_name.startswith(RESTORE_DATABASE_PREFIX):
        raise RuntimeError(f"restore database must start with {RESTORE_DATABASE_PREFIX}")
    if not bucket.startswith(RESTORE_BUCKET_PREFIX):
        raise RuntimeError(f"restore bucket must start with {RESTORE_BUCKET_PREFIX}")


def _validate_database_name(database_name: str) -> None:
    if DATABASE_NAME_RE.fullmatch(database_name) is None:
        raise RuntimeError("database name contains unsupported characters")


def _validate_bucket_name(bucket: str) -> None:
    if not 3 <= len(bucket) <= 63 or re.fullmatch(r"[a-z0-9][a-z0-9.-]+[a-z0-9]", bucket) is None:
        raise RuntimeError("bucket name is not valid")


def _require_environment(names: Sequence[str]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"required environment variables are missing: {', '.join(missing)}")


def _validate_minio_dr_configuration() -> None:
    dr_access_key = os.environ["DR_MINIO_ACCESS_KEY"]
    dr_secret_key = os.environ["DR_MINIO_SECRET_KEY"]
    application_access_key = os.environ["MINIO_ACCESS_KEY"]
    root_user = os.environ["MINIO_ROOT_USER"]

    if secrets.compare_digest(dr_access_key, application_access_key) or secrets.compare_digest(
        dr_access_key,
        root_user,
    ):
        raise RuntimeError("MinIO DR operator must use a dedicated access-key identity")

    try:
        source = urlsplit(os.environ["MC_HOST_source"])
        declared_access_key = source.username
        declared_secret_key = source.password
        hostname = source.hostname
        _ = source.port
    except ValueError:
        raise RuntimeError("MC_HOST_source must be an absolute HTTP(S) alias URL") from None

    if (
        source.scheme not in {"http", "https"}
        or not hostname
        or declared_access_key is None
        or declared_secret_key is None
        or source.path not in {"", "/"}
        or source.query
        or source.fragment
    ):
        raise RuntimeError("MC_HOST_source must be an absolute HTTP(S) alias URL")
    if not secrets.compare_digest(
        declared_access_key,
        dr_access_key,
    ) or not secrets.compare_digest(declared_secret_key, dr_secret_key):
        raise RuntimeError("MC_HOST_source must declare exactly the configured MinIO DR operator")

    app_environment = os.environ.get("APP_ENV", "").strip().lower()
    if app_environment in MINIO_TLS_REQUIRED_ENVIRONMENTS:
        if source.scheme != "https":
            raise RuntimeError(
                "MinIO DR endpoint must use HTTPS when APP_ENV is staging or production"
            )
        _validate_minio_ca_configuration()


def _validate_minio_ca_configuration() -> None:
    minio_ca_value = os.environ.get("MINIO_CA_CERT_FILE", "").strip()
    ssl_ca_value = os.environ.get("SSL_CERT_FILE", "").strip()
    if not minio_ca_value or not ssl_ca_value:
        raise RuntimeError("MinIO DR TLS requires a private CA bundle")

    try:
        minio_ca_file = Path(minio_ca_value).resolve(strict=True)
        ssl_ca_file = Path(ssl_ca_value).resolve(strict=True)
        same_file = minio_ca_file.samefile(ssl_ca_file)
        metadata = minio_ca_file.stat()
    except OSError:
        raise RuntimeError("MinIO DR TLS requires a valid private CA bundle") from None

    if not same_file or not stat.S_ISREG(metadata.st_mode) or not _is_read_only_file(minio_ca_file):
        raise RuntimeError("MinIO DR TLS requires one read-only regular CA bundle")

    try:
        ssl.create_default_context(cafile=str(minio_ca_file))
    except (OSError, ValueError):
        raise RuntimeError("MinIO DR TLS requires a loadable PEM CA bundle") from None


def _is_read_only_file(path: Path) -> bool:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        return error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    os.close(descriptor)
    return False


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _backup_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def _verification_database_name(backup_id: str) -> str:
    compact = backup_id.replace("-", "_").lower()
    return f"restore_verify_{compact}"[:63]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_backup_member(root: Path, relative_name: str, *, label: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute():
        raise RuntimeError(f"{label} path must be relative to the backup directory")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"{label} path escapes the backup directory") from error
    if not candidate.exists():
        raise RuntimeError(f"{label} does not exist in the backup directory")
    return candidate


def _parse_utc_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _safe_evidence_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("._")
    if not safe:
        raise RuntimeError("backup id cannot be used as an evidence filename")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"restore-{safe}-{timestamp}"


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--output-dir", type=Path, default=Path("/backups"))
    backup_parser.add_argument("--database", default=os.environ.get("POSTGRES_DB", ""))
    backup_parser.add_argument("--bucket", default=os.environ.get("MINIO_BUCKET", ""))
    backup_parser.add_argument(
        "--metrics-file",
        type=Path,
        default=Path("/metrics/backup.prom"),
    )

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser.add_argument("--target-environment", required=True)
    restore_parser.add_argument("--target-database", required=True)
    restore_parser.add_argument("--target-bucket", required=True)
    restore_parser.add_argument(
        "--metrics-file",
        type=Path,
        default=Path("/metrics/restore.prom"),
    )
    restore_parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("/evidence"),
    )
    restore_parser.add_argument("--cleanup-after-validation", action="store_true")
    restore_parser.add_argument("--health-url")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "backup":
        result = backup(
            output_dir=args.output_dir,
            database_name=args.database,
            bucket=args.bucket,
            metrics_file=args.metrics_file,
        )
        sys.stdout.write(f"{result}\n")
        return 0
    evidence = restore(
        backup_dir=args.backup_dir,
        target_environment=args.target_environment,
        target_database=args.target_database,
        target_bucket=args.target_bucket,
        metrics_file=args.metrics_file,
        evidence_dir=args.evidence_dir,
        cleanup_after_validation=args.cleanup_after_validation,
        health_url=args.health_url,
    )
    sys.stdout.write(json.dumps(evidence, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
