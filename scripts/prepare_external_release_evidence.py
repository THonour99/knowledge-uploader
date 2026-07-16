"""Collect protected-environment evidence without manufacturing operational results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY_DIR = ROOT / "ops" / "observability"
SOURCE_EVIDENCE_FILES = (
    "alertmanager-notification.json",
    "dr-release.json",
    "email-delivery.json",
)
OUTPUT_FILES = (*SOURCE_EVIDENCE_FILES, "alertmanager.yml", "promtool.json")
GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
MANIFEST_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
EVIDENCE_MAX_AGE = timedelta(hours=2)
PROMETHEUS_IMAGE = (
    "prom/prometheus:v3.12.0"
    "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
)
ALERTMANAGER_IMAGE = (
    "prom/alertmanager:v0.28.1"
    "@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
)
SUPPORTED_VALIDATOR_ARCHITECTURES = frozenset({"amd64", "arm64"})
MAX_SOURCE_EVIDENCE_BYTES = 4 * 1024 * 1024
INLINE_ALERTMANAGER_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "api_secret",
        "api_url",
        "auth_password",
        "auth_secret",
        "bearer_token",
        "bot_token",
        "client_secret",
        "credentials",
        "password",
        "routing_key",
        "secret_key",
        "service_key",
        "slack_api_url",
        "token",
        "token_id",
        "token_secret",
        "url",
        "webhook_url",
    }
)
FORBIDDEN_EVIDENCE_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "broker_payload",
        "broker_url",
        "ciphertext",
        "connection_string",
        "cookie",
        "database_url",
        "email_body",
        "password",
        "raw_token",
        "secret",
        "smtp_password",
        "token",
        "webhook_url",
    }
)


class EvidencePreparationError(RuntimeError):
    """A bounded preparation failure that does not expose command output or evidence data."""

    def __init__(self, step: str) -> None:
        super().__init__(f"external evidence preparation failed at step: {step}")
        self.step = step


class ValidatorImageEvidence(TypedDict):
    """Content-addressed validator identity captured on the protected runner."""

    reference: str
    manifest_list_digest: str
    image_id: str
    operating_system: str
    architecture: str
    docker_architecture: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, step: str, timeout_seconds: float = 180.0) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EvidencePreparationError(step) from error
    if completed.returncode != 0:
        raise EvidencePreparationError(step)
    return completed.stdout.strip()


def _normalize_architecture(value: object, *, step: str) -> str:
    if not isinstance(value, str):
        raise EvidencePreparationError(step)
    normalized = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "amd64",
        "x86_64": "amd64",
    }.get(value.strip().lower())
    if normalized not in SUPPORTED_VALIDATOR_ARCHITECTURES:
        raise EvidencePreparationError(step)
    return normalized


def _validator_manifest_digest(reference: str, *, expected: str, step: str) -> str:
    if reference != expected or "@" not in reference:
        raise EvidencePreparationError(step)
    digest = reference.rsplit("@", maxsplit=1)[1]
    if MANIFEST_DIGEST_PATTERN.fullmatch(digest) is None:
        raise EvidencePreparationError(step)
    return digest


def _docker_architecture() -> str:
    return _normalize_architecture(
        _run(
            ["docker", "info", "--format", "{{.Architecture}}"],
            step="docker_architecture",
        ),
        step="docker_architecture",
    )


def _image_metadata(
    reference: str,
    *,
    manifest_list_digest: str,
    docker_architecture: str,
    step: str,
) -> ValidatorImageEvidence:
    raw = _run(
        ["docker", "image", "inspect", "--format", "{{json .}}", reference],
        step=step,
    )
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise EvidencePreparationError(step) from error
    if not isinstance(loaded, dict):
        raise EvidencePreparationError(step)
    image_id = loaded.get("Id")
    operating_system = loaded.get("Os")
    architecture = _normalize_architecture(loaded.get("Architecture"), step=step)
    if (
        not isinstance(image_id, str)
        or IMAGE_ID_PATTERN.fullmatch(image_id) is None
        or operating_system != "linux"
        or architecture != docker_architecture
    ):
        raise EvidencePreparationError(step)
    return {
        "reference": reference,
        "manifest_list_digest": manifest_list_digest,
        "image_id": image_id,
        "operating_system": operating_system,
        "architecture": architecture,
        "docker_architecture": docker_architecture,
    }


def _pull_validator_image(
    reference: str,
    *,
    expected: str,
    docker_architecture: str,
    step: str,
) -> ValidatorImageEvidence:
    manifest_list_digest = _validator_manifest_digest(
        reference,
        expected=expected,
        step=f"{step}_reference",
    )
    _run(
        [
            "docker",
            "pull",
            "--platform",
            f"linux/{docker_architecture}",
            reference,
        ],
        step=f"{step}_pull",
    )
    return _image_metadata(
        reference,
        manifest_list_digest=manifest_list_digest,
        docker_architecture=docker_architecture,
        step=f"{step}_inspect",
    )


def _source_file(source_dir: Path, filename: str) -> Path:
    candidate = source_dir / filename
    if candidate.is_symlink() or not candidate.is_file():
        raise EvidencePreparationError(f"source_{filename}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(source_dir)
    except ValueError as error:
        raise EvidencePreparationError(f"source_{filename}") from error
    return resolved


def _read_stable_regular_file(path: Path, *, step: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise EvidencePreparationError(step)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > MAX_SOURCE_EVIDENCE_BYTES
        ):
            raise EvidencePreparationError(step)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_SOURCE_EVIDENCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except (OSError, ValueError) as error:
        raise EvidencePreparationError(step) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > MAX_SOURCE_EVIDENCE_BYTES
        or len(payload) != opened.st_size
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise EvidencePreparationError(step)
    return payload


def _load_json_object(payload: bytes, *, step: str) -> dict[str, Any]:
    try:
        loaded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidencePreparationError(step) from error
    if not isinstance(loaded, dict):
        raise EvidencePreparationError(step)
    return loaded


def _validate_identity(
    evidence: dict[str, Any],
    *,
    filename: str,
    git_sha: str,
    environment: str,
    now: datetime,
) -> None:
    if (
        evidence.get("status") != "passed"
        or evidence.get("git_sha") != git_sha
        or evidence.get("environment") != environment
    ):
        raise EvidencePreparationError(f"identity_{filename}")
    generated_at = evidence.get("generated_at")
    if not isinstance(generated_at, str):
        raise EvidencePreparationError(f"identity_{filename}")
    try:
        timestamp = datetime.fromisoformat(generated_at)
    except ValueError as error:
        raise EvidencePreparationError(f"identity_{filename}") from error
    if timestamp.tzinfo is None:
        raise EvidencePreparationError(f"identity_{filename}")
    normalized = timestamp.astimezone(UTC)
    if not now - EVIDENCE_MAX_AGE <= normalized <= now + timedelta(minutes=5):
        raise EvidencePreparationError(f"identity_{filename}")


def _reject_sensitive_evidence_fields(evidence: dict[str, Any], *, filename: str) -> None:
    def walk(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                if str(raw_key).strip().lower() in FORBIDDEN_EVIDENCE_FIELDS:
                    raise EvidencePreparationError(f"sensitive_field_{filename}")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(evidence)


def _reject_inline_alertmanager_secrets(config_payload: bytes) -> None:
    try:
        loaded = yaml.safe_load(config_payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise EvidencePreparationError("alertmanager_secret_scan") from error

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower()
                if (
                    key in INLINE_ALERTMANAGER_SECRET_FIELDS
                    and not key.endswith("_file")
                    and child not in (None, "")
                ):
                    raise EvidencePreparationError("alertmanager_inline_secret")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(loaded)


def _run_observability_checks(
    *,
    alertmanager_config: Path,
    prometheus_image: str,
    alertmanager_image: str,
) -> tuple[ValidatorImageEvidence, ValidatorImageEvidence]:
    docker_architecture = _docker_architecture()
    prometheus_before = _pull_validator_image(
        prometheus_image,
        expected=PROMETHEUS_IMAGE,
        docker_architecture=docker_architecture,
        step="prometheus_image",
    )
    alertmanager_before = _pull_validator_image(
        alertmanager_image,
        expected=ALERTMANAGER_IMAGE,
        docker_architecture=docker_architecture,
        step="alertmanager_image",
    )
    observability_mount = f"{OBSERVABILITY_DIR.resolve()}:/work:ro"
    alertmanager_mount = f"{alertmanager_config.parent}:/alertmanager:ro"
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/promtool",
            "--volume",
            observability_mount,
            prometheus_image,
            "check",
            "config",
            "/work/prometheus.yml",
        ],
        step="prometheus_config",
    )
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/promtool",
            "--volume",
            observability_mount,
            prometheus_image,
            "test",
            "rules",
            "/work/alerts.test.yml",
        ],
        step="prometheus_rules",
    )
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/amtool",
            "--volume",
            alertmanager_mount,
            alertmanager_image,
            "check-config",
            f"/alertmanager/{alertmanager_config.name}",
        ],
        step="alertmanager_config",
    )
    prometheus_after = _image_metadata(
        prometheus_image,
        manifest_list_digest=prometheus_before["manifest_list_digest"],
        docker_architecture=docker_architecture,
        step="prometheus_image_postcheck",
    )
    alertmanager_after = _image_metadata(
        alertmanager_image,
        manifest_list_digest=alertmanager_before["manifest_list_digest"],
        docker_architecture=docker_architecture,
        step="alertmanager_image_postcheck",
    )
    if prometheus_after != prometheus_before:
        raise EvidencePreparationError("prometheus_image_identity_changed")
    if alertmanager_after != alertmanager_before:
        raise EvidencePreparationError("alertmanager_image_identity_changed")
    return prometheus_after, alertmanager_after


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def prepare(
    *,
    source_dir: Path,
    output_dir: Path,
    git_sha: str,
    environment: str,
    prometheus_image: str,
    alertmanager_image: str,
) -> tuple[Path, ...]:
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise EvidencePreparationError("git_identity")
    if environment not in {"staging", "production"}:
        raise EvidencePreparationError("environment")
    _validator_manifest_digest(
        prometheus_image,
        expected=PROMETHEUS_IMAGE,
        step="prometheus_image_reference",
    )
    _validator_manifest_digest(
        alertmanager_image,
        expected=ALERTMANAGER_IMAGE,
        step="alertmanager_image_reference",
    )
    source = source_dir.resolve()
    if not source.is_dir():
        raise EvidencePreparationError("source_directory")
    output = output_dir.resolve()
    if output == source or source in output.parents:
        raise EvidencePreparationError("output_directory")
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise EvidencePreparationError("output_directory")
    output.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    source_paths = {filename: _source_file(source, filename) for filename in SOURCE_EVIDENCE_FILES}
    alertmanager_config = _source_file(source, "alertmanager.yml")
    source_payloads = {
        filename: _read_stable_regular_file(path, step=f"snapshot_{filename}")
        for filename, path in source_paths.items()
    }
    alertmanager_payload = _read_stable_regular_file(
        alertmanager_config,
        step="snapshot_alertmanager.yml",
    )
    _reject_inline_alertmanager_secrets(alertmanager_payload)
    for filename, payload in source_payloads.items():
        evidence = _load_json_object(payload, step=f"parse_{filename}")
        _reject_sensitive_evidence_fields(evidence, filename=filename)
        _validate_identity(
            evidence,
            filename=filename,
            git_sha=git_sha,
            environment=environment,
            now=now,
        )

    for filename, payload in source_payloads.items():
        _atomic_write(output / filename, payload)
    copied_alertmanager = output / "alertmanager.yml"
    _atomic_write(copied_alertmanager, alertmanager_payload)

    prometheus_validator, alertmanager_validator = _run_observability_checks(
        alertmanager_config=copied_alertmanager,
        prometheus_image=prometheus_image,
        alertmanager_image=alertmanager_image,
    )

    promtool = {
        "status": "passed",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha,
        "environment": environment,
        "prometheus_config": "passed",
        "prometheus_rules": "passed",
        "alertmanager_config": "passed",
        "prometheus_config_sha256": _sha256(OBSERVABILITY_DIR / "prometheus.yml"),
        "prometheus_rules_sha256": _sha256(OBSERVABILITY_DIR / "alerts.yml"),
        "alertmanager_config_sha256": _sha256(copied_alertmanager),
        "prometheus_image": prometheus_validator["reference"],
        "prometheus_manifest_list_digest": prometheus_validator["manifest_list_digest"],
        "prometheus_image_id": prometheus_validator["image_id"],
        "prometheus_image_os": prometheus_validator["operating_system"],
        "prometheus_image_architecture": prometheus_validator["architecture"],
        "prometheus_docker_architecture": prometheus_validator["docker_architecture"],
        "alertmanager_image": alertmanager_validator["reference"],
        "alertmanager_manifest_list_digest": alertmanager_validator["manifest_list_digest"],
        "alertmanager_image_id": alertmanager_validator["image_id"],
        "alertmanager_image_os": alertmanager_validator["operating_system"],
        "alertmanager_image_architecture": alertmanager_validator["architecture"],
        "alertmanager_docker_architecture": alertmanager_validator["docker_architecture"],
        "source_evidence_sha256": {
            filename: _sha256(output / filename) for filename in SOURCE_EVIDENCE_FILES
        },
    }
    _atomic_write(
        output / "promtool.json",
        (json.dumps(promtool, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return tuple(output / filename for filename in OUTPUT_FILES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument(
        "--environment",
        choices=("staging", "production"),
        required=True,
    )
    parser.add_argument("--prometheus-image", default=PROMETHEUS_IMAGE)
    parser.add_argument("--alertmanager-image", default=ALERTMANAGER_IMAGE)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        files = prepare(
            source_dir=arguments.source_dir,
            output_dir=arguments.output_dir,
            git_sha=arguments.git_sha,
            environment=arguments.environment,
            prometheus_image=arguments.prometheus_image,
            alertmanager_image=arguments.alertmanager_image,
        )
    except EvidencePreparationError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "status": "prepared",
                "files": [path.name for path in files],
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
