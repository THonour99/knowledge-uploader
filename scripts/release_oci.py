"""Build and verify an immutable OCI release-artifact contract.

The contract deliberately binds deployable bytes, BuildKit provenance/SBOM
attestations, source inputs and the originating workflow run.  It never treats
a Docker tag, image ID from an unrelated rebuild, or a Git SHA by itself as a
release identity.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Final, cast

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from scripts import check_protected_release as protected_release_gate
else:
    try:
        from scripts import check_protected_release as protected_release_gate
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        protected_release_gate = importlib.import_module("check_protected_release")

SCHEMA: Final = "knowledge-uploader.release-oci.v1"
DGX_BINDING_SCHEMA: Final = "knowledge-uploader.dgx-oci-binding.v1"
AUTHORIZATION_SCHEMA: Final = "knowledge-uploader.release-authorization.v1"
PROVENANCE_FILENAME: Final = "release-oci-provenance.json"
CHECKSUM_FILENAME: Final = "release-oci-provenance.sha256"
MAIN_WORKFLOW: Final = ".github/workflows/knowledge-uploader.yml"
DGX_WORKFLOW: Final = ".github/workflows/dgx-spark-device.yml"
PROTECTED_WORKFLOW: Final = ".github/workflows/protected-release.yml"
EXTERNAL_WORKFLOW: Final = ".github/workflows/protected-external-evidence.yml"
DR_RELEASE_POLICY_INPUT_PATH: Final = "ops/policies/dr-release-policy.json"
REQUIRED_INPUT_PATHS: Final = frozenset(
    {
        "backend/Dockerfile",
        "backend/requirements.txt",
        "frontend/Dockerfile",
        "frontend/package-lock.json",
        DR_RELEASE_POLICY_INPUT_PATH,
    }
)
REQUIRED_PLATFORMS: Final = frozenset({("linux", "amd64", None), ("linux", "arm64", None)})
SHA256_PATTERN: Final = re.compile(r"sha256:[0-9a-f]{64}")
HEX_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_JSON_BYTES: Final = 64 * 1024 * 1024
MAX_CLOCK_SKEW: Final = timedelta(minutes=5)
MAX_PROVENANCE_AGE: Final = timedelta(hours=8)
AUTHORIZATION_TTL: Final = timedelta(minutes=30)
TRUST_SUMMARY_TTL: Final = timedelta(hours=2)
REQUIRED_RELEASE_EVIDENCE: Final = frozenset(
    {
        "alertmanager-notification.json",
        "alertmanager.yml",
        "dr-release-policy.json",
        "dr-release.json",
        "email-delivery.json",
        "promtool.json",
        "rabbitmq-dlq-replay.json",
        "infrastructure-e2e.json",
        "dgx-spark-evidence.json",
        "dgx-oci-consumption.json",
        PROVENANCE_FILENAME,
        CHECKSUM_FILENAME,
        "release-workflow-trust.json",
        "release-workflow-trust.json.sha256",
    }
)
EXTERNAL_EVIDENCE_CONTRACTS: Final = {
    "alertmanager-notification.json": (
        "knowledge-uploader.alertmanager-webhook-evidence.v1",
        "knowledge-uploader.alertmanager-webhook-source.v1",
        "alertmanager-webhook-receiver",
    ),
    "dr-release.json": (
        "knowledge-uploader.dr-release-evidence.v1",
        "knowledge-uploader.dr-release-source.v1",
        "backup-restore-drill",
    ),
    "email-delivery.json": (
        "knowledge-uploader.smtp-delivery-evidence.v1",
        "knowledge-uploader.smtp-delivery-source.v1",
        "smtp-delivery-probe",
    ),
    "promtool.json": (
        "knowledge-uploader.observability-validator-evidence.v1",
        "knowledge-uploader.observability-validator-source.v1",
        "observability-validator",
    ),
}


class ContractError(RuntimeError):
    """Raised when release material does not satisfy the fail-closed contract."""


@dataclass(frozen=True)
class Descriptor:
    digest: str
    size: int
    media_type: str


@dataclass(frozen=True)
class PlatformImage:
    os: str
    architecture: str
    variant: str | None
    manifest_digest: str
    config_digest: str
    revision: str


@dataclass(frozen=True)
class StableBytesSnapshot:
    payload: bytes
    sha256: str


@dataclass
class StableFileSnapshot:
    """Process-private copy of an untrusted regular file."""

    _stream: BinaryIO
    sha256: str
    size: int
    source_name: str

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def rewind(self) -> BinaryIO:
        if self._stream.closed:
            raise ContractError(f"file snapshot is closed: {self.source_name}")
        self._stream.seek(0)
        return self._stream

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> StableFileSnapshot:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True)
class StableJsonSnapshot:
    payload: bytes
    sha256: str
    parsed: Mapping[str, object]


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json_bytes(value: object) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (serialized + "\n").encode("utf-8")


def _write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _json_bytes(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return content


def _write_checksum(path: Path, *, filename: str, payload: bytes) -> None:
    content = (f"{hashlib.sha256(payload).hexdigest()}  {filename}\n").encode()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{context} must be a JSON object")
    return value


def _sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ContractError(f"{context} must be a JSON array")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ContractError(f"{context} schema mismatch: missing={missing}, extra={extra}")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{context} must be a non-empty string")
    return value


def _positive_integer(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"{context} must be a positive integer")
    return value


def _digest(value: object, context: str) -> str:
    text = _text(value, context)
    if SHA256_PATTERN.fullmatch(text) is None:
        raise ContractError(f"{context} must be a sha256 digest")
    return text


def _git_sha(value: object, context: str) -> str:
    text = _text(value, context).lower()
    if GIT_SHA_PATTERN.fullmatch(text) is None:
        raise ContractError(f"{context} must be a full hexadecimal Git SHA")
    return text


def _timestamp(value: object, context: str) -> datetime:
    text = _text(value, context)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{context} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{context} must include a timezone")
    return parsed.astimezone(UTC)


def _safe_relative_path(value: object, context: str) -> str:
    text = _text(value, context)
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts or "\\" in text or pure.as_posix() != text:
        raise ContractError(f"{context} must be a normalized relative POSIX path")
    return text


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_stable_regular_file(path: Path, context: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ContractError(f"cannot read unsafe {context}: {path}")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > MAX_JSON_BYTES
        ):
            raise ContractError(f"{context} changed before it could be read")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_JSON_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError as error:
        raise ContractError(f"cannot read {context}: {path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > MAX_JSON_BYTES
        or len(payload) != opened.st_size
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise ContractError(f"{context} changed while it was read")
    return payload


def _load_json_bytes(payload: bytes, context: str) -> Mapping[str, object]:
    if len(payload) < 2 or len(payload) > MAX_JSON_BYTES:
        raise ContractError(f"{context} has an unsafe size")
    try:
        raw: object = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ContractError(f"cannot read {context}") from error
    return _mapping(raw, context)


def _load_json(path: Path, context: str) -> Mapping[str, object]:
    return _load_json_bytes(_read_stable_regular_file(path, context), context)


def _snapshot_bytes(path: Path, context: str) -> StableBytesSnapshot:
    payload = _read_stable_regular_file(path, context)
    return StableBytesSnapshot(payload=payload, sha256=_sha256_bytes(payload))


def _snapshot_json(path: Path, context: str) -> StableJsonSnapshot:
    return _parse_json_snapshot(_snapshot_bytes(path, context), context)


def _parse_json_snapshot(
    snapshot: StableBytesSnapshot,
    context: str,
) -> StableJsonSnapshot:
    return StableJsonSnapshot(
        payload=snapshot.payload,
        sha256=snapshot.sha256,
        parsed=_load_json_bytes(snapshot.payload, context),
    )


def _sha256_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    stream.seek(0)
    return "sha256:" + digest.hexdigest()


def _snapshot_file(
    path: Path,
    context: str,
    *,
    expected_sha256: str | None = None,
) -> StableFileSnapshot:
    """Copy one source generation into a process-private temporary file."""

    descriptor = -1
    temporary: BinaryIO | None = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ContractError(f"cannot read unsafe {context}: {path}")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ContractError(f"{context} changed before it could be copied")

        temporary = cast(BinaryIO, tempfile.TemporaryFile(mode="w+b"))
        snapshot_stream = temporary
        copied_digest = hashlib.sha256()
        copied_size = 0
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                snapshot_stream.write(chunk)
                copied_digest.update(chunk)
                copied_size += len(chunk)
            after = os.fstat(source.fileno())
        current = path.lstat()
        if (
            copied_size != opened.st_size
            or (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(current.st_mode)
        ):
            raise ContractError(f"{context} changed while it was copied")

        snapshot_stream.flush()
        copied_sha256 = "sha256:" + copied_digest.hexdigest()
        snapshot_sha256 = _sha256_stream(snapshot_stream)
        if snapshot_sha256 != copied_sha256:
            raise ContractError(f"{context} private snapshot checksum mismatch")
        if expected_sha256 is not None and snapshot_sha256 != expected_sha256:
            raise ContractError(f"{context} checksum mismatch")
        snapshot = StableFileSnapshot(
            _stream=snapshot_stream,
            sha256=snapshot_sha256,
            size=copied_size,
            source_name=path.name,
        )
        temporary = None
        return snapshot
    except OSError as error:
        raise ContractError(f"cannot copy {context}: {path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.close()


def _descriptor(value: object, context: str) -> Descriptor:
    raw = _mapping(value, context)
    digest = _digest(raw.get("digest"), f"{context}.digest")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ContractError(f"{context}.size must be a non-negative integer")
    media_type = _text(raw.get("mediaType"), f"{context}.mediaType")
    return Descriptor(digest=digest, size=size, media_type=media_type)


class OciArchive:
    """Read an OCI archive only from a process-private file snapshot."""

    def __init__(
        self,
        path: Path,
        *,
        expected_sha256: str | None = None,
        context: str = "OCI archive",
    ) -> None:
        self.path = path
        self._snapshot: StableFileSnapshot | None = None
        self._tar: tarfile.TarFile | None = None
        self._members: dict[str, tarfile.TarInfo] = {}
        try:
            self._snapshot = _snapshot_file(
                path,
                context,
                expected_sha256=expected_sha256,
            )
            self.sha256 = self._snapshot.sha256
            self._tar = tarfile.open(
                None,
                mode="r:*",
                fileobj=self._snapshot.rewind(),
            )
            members = self._tar.getmembers()
            for member in members:
                normalized = PurePosixPath(member.name)
                if (
                    normalized.is_absolute()
                    or ".." in normalized.parts
                    or "\\" in member.name
                    or normalized.as_posix().lstrip("./") != member.name.lstrip("./")
                ):
                    raise ContractError(f"unsafe OCI archive member: {member.name}")
                name = normalized.as_posix().lstrip("./")
                if not name:
                    continue
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ContractError(f"non-regular OCI archive member: {name}")
                if name in self._members:
                    raise ContractError(f"duplicate OCI archive member: {name}")
                self._members[name] = member
        except ContractError:
            self.close()
            raise
        except (OSError, tarfile.TarError) as error:
            self.close()
            raise ContractError(f"cannot inspect OCI archive: {path}") from error
        except Exception:
            self.close()
            raise

    def payload_stream(self) -> BinaryIO:
        """Close the tar reader and rewind the same private FD for a consumer."""

        archive = self._tar
        self._tar = None
        if archive is None:
            raise ContractError("OCI archive payload is no longer available")
        archive.close()
        self._members.clear()
        snapshot = self._snapshot
        if snapshot is None:
            raise ContractError("OCI archive snapshot is closed")
        return snapshot.rewind()

    def close(self) -> None:
        archive = self._tar
        self._tar = None
        snapshot = self._snapshot
        self._snapshot = None
        try:
            if archive is not None:
                archive.close()
        finally:
            if snapshot is not None:
                snapshot.close()

    def __enter__(self) -> OciArchive:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def read(self, name: str, *, maximum: int = MAX_JSON_BYTES) -> bytes:
        archive = self._tar
        if archive is None:
            raise ContractError("OCI archive is closed")
        member = self._members.get(name)
        if member is None:
            raise ContractError(f"missing OCI archive member: {name}")
        if member.size > maximum:
            raise ContractError(f"OCI metadata member is too large: {name}")
        stream = archive.extractfile(member)
        if stream is None:
            raise ContractError(f"cannot read OCI archive member: {name}")
        content = stream.read(maximum + 1)
        if len(content) != member.size or len(content) > maximum:
            raise ContractError(f"OCI archive member size mismatch: {name}")
        return content

    def verify_blob(self, descriptor: Descriptor) -> bytes | None:
        archive = self._tar
        if archive is None:
            raise ContractError("OCI archive is closed")
        name = f"blobs/sha256/{descriptor.digest.removeprefix('sha256:')}"
        member = self._members.get(name)
        if member is None or member.size != descriptor.size:
            raise ContractError(f"OCI blob size or member mismatch: {descriptor.digest}")
        stream = archive.extractfile(member)
        if stream is None:
            raise ContractError(f"cannot read OCI blob: {descriptor.digest}")
        hasher = hashlib.sha256()
        capture = bytearray() if member.size <= MAX_JSON_BYTES else None
        total = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            hasher.update(chunk)
            if capture is not None:
                capture.extend(chunk)
        if total != descriptor.size or "sha256:" + hasher.hexdigest() != descriptor.digest:
            raise ContractError(f"OCI blob digest mismatch: {descriptor.digest}")
        return bytes(capture) if capture is not None else None


def _json_object(content: bytes | None, context: str) -> Mapping[str, object]:
    if content is None:
        raise ContractError(f"{context} exceeds the metadata size limit")
    try:
        parsed: object = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{context} is not valid UTF-8 JSON") from error
    return _mapping(parsed, context)


def _statement_objects(content: bytes, context: str) -> list[Mapping[str, object]]:
    statements: list[Mapping[str, object]] = []
    for index, line in enumerate(content.splitlines()):
        if not line.strip():
            continue
        try:
            parsed: object = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ContractError(f"{context}[{index}] is not valid JSON") from error
        statements.append(_mapping(parsed, f"{context}[{index}]"))
    if not statements:
        raise ContractError(f"{context} contains no in-toto statement")
    return statements


def _subject_matches(statement: Mapping[str, object], manifest_digest: str, context: str) -> None:
    subjects = _sequence(statement.get("subject"), f"{context}.subject")
    expected = manifest_digest.removeprefix("sha256:")
    for raw_subject in subjects:
        subject = _mapping(raw_subject, f"{context}.subject[]")
        digests = _mapping(subject.get("digest"), f"{context}.subject[].digest")
        if digests.get("sha256") == expected:
            return
    raise ContractError(f"{context} is not bound to manifest {manifest_digest}")


def _base_materials(statement: Mapping[str, object], context: str) -> list[dict[str, str]]:
    predicate = _mapping(statement.get("predicate"), f"{context}.predicate")
    materials = _sequence(predicate.get("materials"), f"{context}.predicate.materials")
    result: dict[tuple[str, str], dict[str, str]] = {}
    for index, raw_material in enumerate(materials):
        material = _mapping(raw_material, f"{context}.materials[{index}]")
        uri = _text(material.get("uri"), f"{context}.materials[{index}].uri")
        if not (uri.startswith("pkg:docker/") or uri.startswith("docker-image://")):
            continue
        package = uri.removeprefix("pkg:docker/").split("@", 1)[0].split("?", 1)[0]
        if uri.startswith("pkg:docker/") and (
            package.startswith("docker/dockerfile")
            or package.startswith("moby/buildkit")
            or package.startswith("tonistiigi/binfmt")
        ):
            continue
        digests = _mapping(material.get("digest"), f"{context}.materials[{index}].digest")
        digest_hex = digests.get("sha256")
        if not isinstance(digest_hex, str) or HEX_SHA256_PATTERN.fullmatch(digest_hex) is None:
            raise ContractError(f"{context} base image material lacks a sha256 digest")
        digest = "sha256:" + digest_hex
        result[(uri, digest)] = {"uri": uri, "digest": digest}
    if not result:
        raise ContractError(f"{context} has no digest-bound base image material")
    return [result[key] for key in sorted(result)]


def _platform_key(raw: Mapping[str, object], context: str) -> tuple[str, str, str | None]:
    os_name = _text(raw.get("os"), f"{context}.os").lower()
    architecture = _text(raw.get("architecture"), f"{context}.architecture").lower()
    variant_raw = raw.get("variant")
    variant = None if variant_raw is None else _text(variant_raw, f"{context}.variant").lower()
    return os_name, architecture, variant


def _parse_image_archive(
    path: Path,
    *,
    git_sha: str,
    expected_image: Mapping[str, object] | None = None,
    image_name: str | None = None,
    consumer: Callable[[BinaryIO], None] | None = None,
) -> dict[str, object]:
    expected_sha256 = (
        None
        if expected_image is None
        else _digest(expected_image.get("archive_sha256"), "expected archive_sha256")
    )
    context = "OCI archive" if image_name is None else f"{image_name} OCI archive"
    with OciArchive(
        path,
        expected_sha256=expected_sha256,
        context=context,
    ) as archive:
        archive_sha256 = archive.sha256
        layout = _json_object(archive.read("oci-layout"), "oci-layout")
        if layout.get("imageLayoutVersion") != "1.0.0":
            raise ContractError("unsupported OCI image layout version")
        index_content = archive.read("index.json")
        index = _json_object(index_content, "OCI index")
        if index.get("schemaVersion") != 2:
            raise ContractError("OCI index schemaVersion must be 2")
        manifests = _sequence(index.get("manifests"), "OCI index.manifests")

        platforms: dict[str, PlatformImage] = {}
        attestation_descriptors: list[tuple[Descriptor, str]] = []
        for index_number, raw_descriptor in enumerate(manifests):
            descriptor_raw = _mapping(raw_descriptor, f"OCI index.manifests[{index_number}]")
            descriptor = _descriptor(descriptor_raw, f"OCI index.manifests[{index_number}]")
            annotations_raw = descriptor_raw.get("annotations", {})
            annotations = _mapping(
                annotations_raw,
                f"OCI index.manifests[{index_number}].annotations",
            )
            if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
                subject = _digest(
                    annotations.get("vnd.docker.reference.digest"),
                    f"OCI index.manifests[{index_number}].attestationSubject",
                )
                attestation_descriptors.append((descriptor, subject))
                continue
            platform_raw = _mapping(
                descriptor_raw.get("platform"), f"OCI index.manifests[{index_number}].platform"
            )
            platform_key = _platform_key(
                platform_raw, f"OCI index.manifests[{index_number}].platform"
            )
            if platform_key not in REQUIRED_PLATFORMS:
                raise ContractError(f"unexpected deployable OCI platform: {platform_key}")
            key_text = "/".join(part for part in platform_key if part is not None)
            if key_text in platforms:
                raise ContractError(f"duplicate deployable OCI platform: {key_text}")
            manifest = _json_object(
                archive.verify_blob(descriptor), f"OCI manifest {descriptor.digest}"
            )
            if manifest.get("schemaVersion") != 2:
                raise ContractError(f"OCI manifest {descriptor.digest} schemaVersion must be 2")
            config_descriptor = _descriptor(
                manifest.get("config"), f"OCI manifest {descriptor.digest}.config"
            )
            config = _json_object(
                archive.verify_blob(config_descriptor), f"OCI config {config_descriptor.digest}"
            )
            layers = _sequence(manifest.get("layers"), f"OCI manifest {descriptor.digest}.layers")
            if not layers:
                raise ContractError(f"OCI manifest {descriptor.digest} has no layers")
            for layer_index, raw_layer in enumerate(layers):
                layer = _descriptor(
                    raw_layer, f"OCI manifest {descriptor.digest}.layers[{layer_index}]"
                )
                archive.verify_blob(layer)
            labels = _mapping(
                _mapping(config.get("config"), f"OCI config {config_descriptor.digest}.config").get(
                    "Labels"
                ),
                f"OCI config {config_descriptor.digest}.config.Labels",
            )
            revision = _git_sha(
                labels.get("org.opencontainers.image.revision"),
                f"OCI config {config_descriptor.digest} revision label",
            )
            if revision != git_sha:
                raise ContractError("OCI image revision label does not match the release Git SHA")
            platforms[key_text] = PlatformImage(
                os=platform_key[0],
                architecture=platform_key[1],
                variant=platform_key[2],
                manifest_digest=descriptor.digest,
                config_digest=config_descriptor.digest,
                revision=revision,
            )

        actual_platforms = {
            (item.os, item.architecture, item.variant) for item in platforms.values()
        }
        if actual_platforms != REQUIRED_PLATFORMS:
            raise ContractError(
                f"OCI archive platform set mismatch: expected={sorted(REQUIRED_PLATFORMS)}, "
                f"actual={sorted(actual_platforms)}"
            )

        attestations: dict[str, dict[str, object]] = {
            image.manifest_digest: {} for image in platforms.values()
        }
        for descriptor, subject_digest in attestation_descriptors:
            if subject_digest not in attestations:
                raise ContractError("attestation references a non-deployable OCI manifest")
            manifest = _json_object(
                archive.verify_blob(descriptor), f"attestation manifest {descriptor.digest}"
            )
            config_descriptor = _descriptor(
                manifest.get("config"),
                f"attestation manifest {descriptor.digest}.config",
            )
            archive.verify_blob(config_descriptor)
            layers = _sequence(
                manifest.get("layers"), f"attestation manifest {descriptor.digest}.layers"
            )
            for layer_index, raw_layer in enumerate(layers):
                layer_raw = _mapping(
                    raw_layer, f"attestation manifest {descriptor.digest}.layers[{layer_index}]"
                )
                layer = _descriptor(
                    layer_raw, f"attestation manifest {descriptor.digest}.layers[{layer_index}]"
                )
                content = archive.verify_blob(layer)
                if content is None:
                    raise ContractError("attestation statement exceeds the metadata size limit")
                layer_annotations = _mapping(
                    layer_raw.get("annotations", {}),
                    f"attestation manifest {descriptor.digest}.layers[{layer_index}].annotations",
                )
                predicate_hint = str(layer_annotations.get("in-toto.io/predicate-type", ""))
                for statement_index, statement in enumerate(
                    _statement_objects(content, f"attestation {layer.digest}")
                ):
                    predicate_type = _text(
                        statement.get("predicateType"),
                        f"attestation {layer.digest}[{statement_index}].predicateType",
                    )
                    if predicate_hint and predicate_hint != predicate_type:
                        raise ContractError(
                            "attestation predicate annotation does not match statement"
                        )
                    _subject_matches(
                        statement,
                        subject_digest,
                        f"attestation {layer.digest}[{statement_index}]",
                    )
                    target = attestations[subject_digest]
                    if "slsa.dev/provenance" in predicate_type:
                        if "provenance_digest" in target:
                            raise ContractError(
                                "duplicate provenance attestation for an OCI platform"
                            )
                        target["provenance_digest"] = layer.digest
                        target["base_materials"] = _base_materials(
                            statement, f"attestation {layer.digest}[{statement_index}]"
                        )
                    elif predicate_type == "https://spdx.dev/Document":
                        if "sbom_digest" in target:
                            raise ContractError("duplicate SBOM attestation for an OCI platform")
                        target["sbom_digest"] = layer.digest

        result_platforms: list[dict[str, object]] = []
        for key_text in sorted(platforms):
            image = platforms[key_text]
            attestation = attestations[image.manifest_digest]
            required = {"provenance_digest", "sbom_digest", "base_materials"}
            if set(attestation) != required:
                raise ContractError(
                    f"platform {key_text} lacks complete provenance/SBOM attestations"
                )
            result_platforms.append(
                {
                    "os": image.os,
                    "architecture": image.architecture,
                    "variant": image.variant,
                    "manifest_digest": image.manifest_digest,
                    "config_digest": image.config_digest,
                    "revision": image.revision,
                    "provenance_digest": attestation["provenance_digest"],
                    "sbom_digest": attestation["sbom_digest"],
                    "base_materials": attestation["base_materials"],
                }
            )

        result: dict[str, object] = {
            "archive": path.name,
            "archive_sha256": archive_sha256,
            "index_digest": _sha256_bytes(index_content),
            "platforms": result_platforms,
        }
        if expected_image is not None and result != expected_image:
            raise ContractError(
                f"{image_name or path.name} OCI archive no longer matches provenance metadata"
            )
        if consumer is not None:
            consumer(archive.payload_stream())
        return result


def _input_record(path: Path, root: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ContractError(f"source input is outside the repository: {path}") from error
    if relative not in REQUIRED_INPUT_PATHS:
        raise ContractError(f"unexpected release source input: {relative}")
    if not resolved.is_file() or resolved.is_symlink():
        raise ContractError(f"release source input must be a regular file: {relative}")
    snapshot = _snapshot_bytes(resolved, f"release source input {relative}")
    return {"path": relative, "sha256": snapshot.sha256}


def create_provenance(
    *,
    repository: str,
    git_sha: str,
    git_ref: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    backend_archive: Path,
    frontend_archive: Path,
    inputs: Sequence[Path],
    repository_root: Path,
    output_dir: Path,
    now: datetime | None = None,
) -> Mapping[str, object]:
    sha = _git_sha(git_sha, "git_sha")
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ContractError("repository must use the owner/name form")
    if git_ref != "refs/heads/main":
        raise ContractError("release OCI artifacts may only originate from refs/heads/main")
    run_id = _positive_integer(workflow_run_id, "workflow_run_id")
    attempt = _positive_integer(workflow_run_attempt, "workflow_run_attempt")
    records = [_input_record(path, repository_root) for path in inputs]
    if {record["path"] for record in records} != REQUIRED_INPUT_PATHS:
        raise ContractError("release source-input inventory is incomplete")
    image_records = {
        "backend": _parse_image_archive(backend_archive, git_sha=sha),
        "frontend": _parse_image_archive(frontend_archive, git_sha=sha),
    }
    confirmed_records = [_input_record(path, repository_root) for path in inputs]
    if confirmed_records != records:
        raise ContractError("release source inputs changed while OCI archives were snapshotted")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    expires_at = timestamp + MAX_PROVENANCE_AGE
    bundle_name = f"release-oci-bundle-{sha}-{run_id}-{attempt}"
    provenance_name = f"release-oci-provenance-{sha}-{run_id}-{attempt}"
    metadata: Mapping[str, object] = {
        "schema": SCHEMA,
        "repository": repository,
        "source": {
            "git_sha": sha,
            "git_ref": git_ref,
            "workflow_path": MAIN_WORKFLOW,
            "workflow_run_id": run_id,
            "workflow_run_attempt": attempt,
            "workflow_event": "push",
        },
        "artifact": {
            "bundle_name": bundle_name,
            "provenance_name": provenance_name,
            "generated_at": timestamp.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        "inputs": sorted(records, key=lambda item: item["path"]),
        "images": image_records,
    }
    validate_provenance(metadata, now=timestamp, require_fresh=True)
    metadata_path = output_dir / PROVENANCE_FILENAME
    content = _write_json(metadata_path, metadata)
    _write_checksum(
        output_dir / CHECKSUM_FILENAME,
        filename=PROVENANCE_FILENAME,
        payload=content,
    )
    return metadata


def _validate_materials(value: object, context: str) -> None:
    materials = _sequence(value, context)
    if not materials:
        raise ContractError(f"{context} must not be empty")
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(materials):
        material = _mapping(raw, f"{context}[{index}]")
        _exact_keys(material, {"uri", "digest"}, f"{context}[{index}]")
        uri = _text(material.get("uri"), f"{context}[{index}].uri")
        if not (uri.startswith("pkg:docker/") or uri.startswith("docker-image://")):
            raise ContractError(f"{context}[{index}].uri is not a base-image material")
        digest = _digest(material.get("digest"), f"{context}[{index}].digest")
        if (uri, digest) in seen:
            raise ContractError(f"{context} contains duplicate material records")
        seen.add((uri, digest))


def _validate_image(value: object, context: str, *, git_sha: str) -> None:
    image = _mapping(value, context)
    _exact_keys(
        image,
        {"archive", "archive_sha256", "index_digest", "platforms"},
        context,
    )
    archive = _safe_relative_path(image.get("archive"), f"{context}.archive")
    if PurePosixPath(archive).parent != PurePosixPath(".") or not archive.endswith(".oci.tar"):
        raise ContractError(f"{context}.archive must name an OCI tar in the bundle root")
    _digest(image.get("archive_sha256"), f"{context}.archive_sha256")
    _digest(image.get("index_digest"), f"{context}.index_digest")
    platforms = _sequence(image.get("platforms"), f"{context}.platforms")
    seen: set[tuple[str, str, str | None]] = set()
    for index, raw in enumerate(platforms):
        platform = _mapping(raw, f"{context}.platforms[{index}]")
        _exact_keys(
            platform,
            {
                "os",
                "architecture",
                "variant",
                "manifest_digest",
                "config_digest",
                "revision",
                "provenance_digest",
                "sbom_digest",
                "base_materials",
            },
            f"{context}.platforms[{index}]",
        )
        key = _platform_key(platform, f"{context}.platforms[{index}]")
        if key in seen:
            raise ContractError(f"{context}.platforms contains duplicates")
        seen.add(key)
        for name in (
            "manifest_digest",
            "config_digest",
            "provenance_digest",
            "sbom_digest",
        ):
            _digest(platform.get(name), f"{context}.platforms[{index}].{name}")
        if _git_sha(platform.get("revision"), f"{context}.platforms[{index}].revision") != git_sha:
            raise ContractError(f"{context}.platforms[{index}] revision mismatch")
        _validate_materials(
            platform.get("base_materials"), f"{context}.platforms[{index}].base_materials"
        )
    if seen != REQUIRED_PLATFORMS:
        raise ContractError(f"{context}.platforms must contain exactly linux/amd64 and linux/arm64")


def validate_provenance(
    value: object,
    *,
    now: datetime | None = None,
    require_fresh: bool = True,
    expected_repository: str | None = None,
    expected_git_sha: str | None = None,
    expected_run_id: int | None = None,
    expected_run_attempt: int | None = None,
) -> Mapping[str, object]:
    metadata = _mapping(value, "release OCI provenance")
    _exact_keys(
        metadata,
        {"schema", "repository", "source", "artifact", "inputs", "images"},
        "release OCI provenance",
    )
    if metadata.get("schema") != SCHEMA:
        raise ContractError("unsupported release OCI provenance schema")
    repository = _text(metadata.get("repository"), "release OCI provenance.repository")
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ContractError("release OCI provenance.repository is invalid")
    if expected_repository is not None and repository != expected_repository:
        raise ContractError("release OCI provenance repository mismatch")
    source = _mapping(metadata.get("source"), "release OCI provenance.source")
    _exact_keys(
        source,
        {
            "git_sha",
            "git_ref",
            "workflow_path",
            "workflow_run_id",
            "workflow_run_attempt",
            "workflow_event",
        },
        "release OCI provenance.source",
    )
    git_sha = _git_sha(source.get("git_sha"), "release OCI provenance.source.git_sha")
    if expected_git_sha is not None and git_sha != _git_sha(expected_git_sha, "expected_git_sha"):
        raise ContractError("release OCI provenance Git SHA mismatch")
    if source.get("git_ref") != "refs/heads/main":
        raise ContractError("release OCI provenance did not originate from refs/heads/main")
    if source.get("workflow_path") != MAIN_WORKFLOW or source.get("workflow_event") != "push":
        raise ContractError("release OCI provenance has an untrusted workflow identity")
    run_id = _positive_integer(source.get("workflow_run_id"), "source.workflow_run_id")
    attempt = _positive_integer(source.get("workflow_run_attempt"), "source.workflow_run_attempt")
    if expected_run_id is not None and run_id != expected_run_id:
        raise ContractError("release OCI provenance workflow run mismatch")
    if expected_run_attempt is not None and attempt != expected_run_attempt:
        raise ContractError("release OCI provenance workflow attempt mismatch")
    artifact = _mapping(metadata.get("artifact"), "release OCI provenance.artifact")
    _exact_keys(
        artifact,
        {"bundle_name", "provenance_name", "generated_at", "expires_at"},
        "release OCI provenance.artifact",
    )
    expected_suffix = f"{git_sha}-{run_id}-{attempt}"
    if artifact.get("bundle_name") != f"release-oci-bundle-{expected_suffix}":
        raise ContractError("release OCI bundle name is not bound to source run identity")
    if artifact.get("provenance_name") != f"release-oci-provenance-{expected_suffix}":
        raise ContractError(
            "release OCI provenance artifact name is not bound to source run identity"
        )
    generated_at = _timestamp(artifact.get("generated_at"), "artifact.generated_at")
    expires_at = _timestamp(artifact.get("expires_at"), "artifact.expires_at")
    if expires_at <= generated_at or expires_at - generated_at > MAX_PROVENANCE_AGE:
        raise ContractError("release OCI provenance has an invalid validity window")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if generated_at > current + MAX_CLOCK_SKEW:
        raise ContractError("release OCI provenance was generated in the future")
    if require_fresh and not (generated_at - MAX_CLOCK_SKEW <= current <= expires_at):
        raise ContractError("release OCI provenance is stale")
    inputs = _sequence(metadata.get("inputs"), "release OCI provenance.inputs")
    paths: set[str] = set()
    for index, raw in enumerate(inputs):
        record = _mapping(raw, f"release OCI provenance.inputs[{index}]")
        _exact_keys(record, {"path", "sha256"}, f"release OCI provenance.inputs[{index}]")
        path = _safe_relative_path(record.get("path"), f"inputs[{index}].path")
        if path in paths:
            raise ContractError("release OCI provenance contains duplicate source inputs")
        paths.add(path)
        _digest(record.get("sha256"), f"inputs[{index}].sha256")
    if paths != REQUIRED_INPUT_PATHS:
        raise ContractError("release OCI provenance source-input inventory is incomplete")
    images = _mapping(metadata.get("images"), "release OCI provenance.images")
    _exact_keys(images, {"backend", "frontend"}, "release OCI provenance.images")
    _validate_image(images.get("backend"), "images.backend", git_sha=git_sha)
    _validate_image(images.get("frontend"), "images.frontend", git_sha=git_sha)
    return metadata


def _verify_snapshot_checksum(
    snapshot: StableBytesSnapshot | StableJsonSnapshot,
    checksum: StableBytesSnapshot,
    *,
    filename: str,
    context: str,
) -> str:
    try:
        line = checksum.payload.decode("utf-8")
    except UnicodeError as error:
        raise ContractError(f"{context} checksum file is malformed") from error
    match = re.fullmatch(rf"([0-9a-f]{{64}})  {re.escape(filename)}\n", line)
    if match is None:
        raise ContractError(f"{context} checksum file is malformed")
    actual = snapshot.sha256.removeprefix("sha256:")
    if actual != match.group(1):
        raise ContractError(f"{context} checksum mismatch")
    return snapshot.sha256


def _verify_checksum(
    metadata_path: Path,
    checksum_path: Path,
) -> StableJsonSnapshot:
    metadata = _snapshot_json(metadata_path, "release OCI provenance")
    checksum = _snapshot_bytes(checksum_path, "release OCI provenance checksum")
    _verify_snapshot_checksum(
        metadata,
        checksum,
        filename=PROVENANCE_FILENAME,
        context="release OCI provenance",
    )
    return metadata


def verify_bundle(
    *,
    bundle_dir: Path,
    expected_repository: str | None = None,
    expected_git_sha: str | None = None,
    expected_run_id: int | None = None,
    expected_run_attempt: int | None = None,
    require_archives: bool,
    now: datetime | None = None,
    provenance_snapshot: StableJsonSnapshot | None = None,
    checksum_snapshot: StableBytesSnapshot | None = None,
) -> Mapping[str, object]:
    metadata_path = bundle_dir / PROVENANCE_FILENAME
    checksum_path = bundle_dir / CHECKSUM_FILENAME
    if (provenance_snapshot is None) != (checksum_snapshot is None):
        raise ContractError("provenance and checksum snapshots must be supplied together")
    if provenance_snapshot is None or checksum_snapshot is None:
        provenance_snapshot = _verify_checksum(metadata_path, checksum_path)
    else:
        _verify_snapshot_checksum(
            provenance_snapshot,
            checksum_snapshot,
            filename=PROVENANCE_FILENAME,
            context="release OCI provenance",
        )
    metadata = validate_provenance(
        provenance_snapshot.parsed,
        now=now,
        expected_repository=expected_repository,
        expected_git_sha=expected_git_sha,
        expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt,
    )
    if require_archives:
        images = _mapping(metadata["images"], "release OCI provenance.images")
        source = _mapping(metadata["source"], "release OCI provenance.source")
        git_sha = _git_sha(source["git_sha"], "source.git_sha")
        for name in ("backend", "frontend"):
            image = _mapping(images[name], f"images.{name}")
            archive_path = bundle_dir / _safe_relative_path(
                image["archive"], f"images.{name}.archive"
            )
            if not archive_path.is_file() or archive_path.is_symlink():
                raise ContractError(f"missing regular OCI archive for {name}")
            _parse_image_archive(
                archive_path,
                git_sha=git_sha,
                expected_image=image,
                image_name=name,
            )
    return metadata


def _arm64_platform(metadata: Mapping[str, object], image_name: str) -> Mapping[str, object]:
    images = _mapping(metadata.get("images"), "provenance.images")
    image = _mapping(images.get(image_name), f"provenance.images.{image_name}")
    for raw in _sequence(image.get("platforms"), f"provenance.images.{image_name}.platforms"):
        platform = _mapping(raw, f"provenance.images.{image_name}.platform")
        if platform.get("os") == "linux" and platform.get("architecture") == "arm64":
            return platform
    raise ContractError(f"provenance lacks linux/arm64 for {image_name}")


def load_arm64_images(
    *,
    bundle_dir: Path,
    backend_tag: str,
    frontend_tag: str,
    expected_repository: str,
    expected_git_sha: str,
    expected_run_id: int,
    expected_run_attempt: int,
    now: datetime | None = None,
) -> None:
    metadata = verify_bundle(
        bundle_dir=bundle_dir,
        expected_repository=expected_repository,
        expected_git_sha=expected_git_sha,
        expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt,
        require_archives=False,
        now=now,
    )
    images = _mapping(metadata.get("images"), "provenance.images")
    for image_name, tag in (("backend", backend_tag), ("frontend", frontend_tag)):
        image = _mapping(images.get(image_name), f"provenance.images.{image_name}")
        archive = bundle_dir / _safe_relative_path(image.get("archive"), f"{image_name}.archive")

        def load_snapshot(archive_stream: BinaryIO) -> None:
            subprocess.run(
                ["docker", "image", "load"],
                stdin=archive_stream,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        source = _mapping(metadata.get("source"), "provenance.source")
        _parse_image_archive(
            archive,
            git_sha=_git_sha(source.get("git_sha"), "provenance.source.git_sha"),
            expected_image=image,
            image_name=image_name,
            consumer=load_snapshot,
        )
        platform = _arm64_platform(metadata, image_name)
        config_digest = _digest(platform.get("config_digest"), f"{image_name}.config_digest")
        subprocess.run(
            ["docker", "image", "tag", config_digest, tag],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        inspect = subprocess.run(
            ["docker", "image", "inspect", tag],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        try:
            rows: object = json.loads(inspect.stdout)
        except json.JSONDecodeError as error:
            raise ContractError(f"docker inspect returned invalid JSON for {image_name}") from error
        sequence = _sequence(rows, f"docker inspect {image_name}")
        if len(sequence) != 1:
            raise ContractError(f"docker inspect returned multiple images for {image_name}")
        row = _mapping(sequence[0], f"docker inspect {image_name}[0]")
        labels = _mapping(
            _mapping(row.get("Config"), f"docker inspect {image_name}.Config").get("Labels"),
            f"docker inspect {image_name}.Config.Labels",
        )
        if (
            row.get("Id") != config_digest
            or str(row.get("Architecture", "")).lower() not in {"arm64", "aarch64"}
            or str(row.get("Os", "")).lower() != "linux"
            or labels.get("org.opencontainers.image.revision") != expected_git_sha
        ):
            raise ContractError(f"loaded {image_name} does not match the OCI arm64 manifest")


def bind_dgx_evidence(
    *,
    bundle_dir: Path,
    infrastructure_path: Path,
    dgx_path: Path,
    trust_summary_path: Path,
    output_path: Path,
    repository: str,
    git_sha: str,
    environment: str,
    main_run_id: int,
    main_run_attempt: int,
    dgx_run_id: int,
    dgx_run_attempt: int,
    now: datetime | None = None,
) -> Mapping[str, object]:
    provenance_file = _snapshot_bytes(
        bundle_dir / PROVENANCE_FILENAME,
        "release OCI provenance",
    )
    provenance_snapshot = _parse_json_snapshot(
        provenance_file,
        "release OCI provenance",
    )
    checksum_snapshot = _snapshot_bytes(
        bundle_dir / CHECKSUM_FILENAME,
        "release OCI provenance checksum",
    )
    metadata = verify_bundle(
        bundle_dir=bundle_dir,
        expected_repository=repository,
        expected_git_sha=git_sha,
        expected_run_id=main_run_id,
        expected_run_attempt=main_run_attempt,
        require_archives=True,
        now=now,
        provenance_snapshot=provenance_snapshot,
        checksum_snapshot=checksum_snapshot,
    )
    infrastructure_snapshot = _snapshot_json(
        infrastructure_path,
        "infrastructure E2E evidence",
    )
    dgx_snapshot = _snapshot_json(dgx_path, "DGX evidence")
    trust_snapshot = _snapshot_bytes(trust_summary_path, "release workflow trust")
    infrastructure = infrastructure_snapshot.parsed
    dgx = dgx_snapshot.parsed
    sha = _git_sha(git_sha, "git_sha")
    expected_statuses = {
        "infrastructure": "development_passed",
        "DGX": "passed",
    }
    for name, evidence in (("infrastructure", infrastructure), ("DGX", dgx)):
        if evidence.get("status") != expected_statuses[name]:
            raise ContractError(f"{name} evidence status is invalid")
        if evidence.get("git_sha") != sha or evidence.get("environment") != environment:
            raise ContractError(f"{name} evidence identity mismatch")
    if str(dgx.get("architecture", "")).lower() not in {"arm64", "aarch64"}:
        raise ContractError("DGX evidence is not ARM64")
    if (
        dgx.get("compose_e2e_evidence_sha256")
        != infrastructure_snapshot.sha256.removeprefix("sha256:")
    ):
        raise ContractError("DGX evidence is not bound to infrastructure E2E evidence")
    image_records: dict[str, object] = {}
    image_metadata = _mapping(metadata["images"], "provenance.images")
    for name in ("backend", "frontend"):
        platform = _arm64_platform(metadata, name)
        image_id = infrastructure.get(f"{name}_image_id")
        if image_id != dgx.get(f"{name}_image_id") or image_id != platform.get("config_digest"):
            raise ContractError(f"{name} runtime image ID is not the OCI arm64 config digest")
        image_record = _mapping(image_metadata[name], f"provenance.images.{name}")
        image_records[name] = {
            "index_digest": image_record["index_digest"],
            "manifest_digest": platform["manifest_digest"],
            "config_digest": platform["config_digest"],
            "archive_sha256": image_record["archive_sha256"],
        }
    source = _mapping(metadata["source"], "provenance.source")
    artifact = _mapping(metadata["artifact"], "provenance.artifact")
    payload: Mapping[str, object] = {
        "schema": DGX_BINDING_SCHEMA,
        "status": "passed",
        "generated_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "environment": environment,
        "repository": repository,
        "git_sha": sha,
        "source": {
            "main_workflow_run_id": source["workflow_run_id"],
            "main_workflow_run_attempt": source["workflow_run_attempt"],
            "bundle_name": artifact["bundle_name"],
            "provenance_sha256": provenance_snapshot.sha256,
        },
        "dgx": {
            "workflow_run_id": _positive_integer(dgx_run_id, "dgx_run_id"),
            "workflow_run_attempt": _positive_integer(dgx_run_attempt, "dgx_run_attempt"),
            "infrastructure_evidence_sha256": infrastructure_snapshot.sha256,
            "device_evidence_sha256": dgx_snapshot.sha256,
            "workflow_trust_sha256": trust_snapshot.sha256,
        },
        "images": image_records,
    }
    _write_json(output_path, payload)
    return payload


def _validate_dgx_binding(
    value: object,
    *,
    repository: str,
    git_sha: str,
    environment: str,
) -> Mapping[str, object]:
    binding = _mapping(value, "DGX OCI binding")
    _exact_keys(
        binding,
        {
            "schema",
            "status",
            "generated_at",
            "environment",
            "repository",
            "git_sha",
            "source",
            "dgx",
            "images",
        },
        "DGX OCI binding",
    )
    if binding.get("schema") != DGX_BINDING_SCHEMA or binding.get("status") != "passed":
        raise ContractError("DGX OCI binding did not pass or uses an unknown schema")
    _timestamp(binding.get("generated_at"), "DGX OCI binding.generated_at")
    if binding.get("repository") != repository or binding.get("environment") != environment:
        raise ContractError("DGX OCI binding release identity mismatch")
    if _git_sha(binding.get("git_sha"), "DGX OCI binding.git_sha") != git_sha:
        raise ContractError("DGX OCI binding Git SHA mismatch")
    source = _mapping(binding.get("source"), "DGX OCI binding.source")
    _exact_keys(
        source,
        {
            "main_workflow_run_id",
            "main_workflow_run_attempt",
            "bundle_name",
            "provenance_sha256",
        },
        "DGX OCI binding.source",
    )
    _positive_integer(source.get("main_workflow_run_id"), "DGX binding main run ID")
    _positive_integer(source.get("main_workflow_run_attempt"), "DGX binding main run attempt")
    _text(source.get("bundle_name"), "DGX binding bundle_name")
    _digest(source.get("provenance_sha256"), "DGX binding provenance_sha256")
    dgx = _mapping(binding.get("dgx"), "DGX OCI binding.dgx")
    _exact_keys(
        dgx,
        {
            "workflow_run_id",
            "workflow_run_attempt",
            "infrastructure_evidence_sha256",
            "device_evidence_sha256",
            "workflow_trust_sha256",
        },
        "DGX OCI binding.dgx",
    )
    _positive_integer(dgx.get("workflow_run_id"), "DGX binding run ID")
    _positive_integer(dgx.get("workflow_run_attempt"), "DGX binding run attempt")
    for field in (
        "infrastructure_evidence_sha256",
        "device_evidence_sha256",
        "workflow_trust_sha256",
    ):
        _digest(dgx.get(field), f"DGX OCI binding.dgx.{field}")
    images = _mapping(binding.get("images"), "DGX OCI binding.images")
    _exact_keys(images, {"backend", "frontend"}, "DGX OCI binding.images")
    for name in ("backend", "frontend"):
        image = _mapping(images.get(name), f"DGX OCI binding.images.{name}")
        _exact_keys(
            image,
            {"index_digest", "manifest_digest", "config_digest", "archive_sha256"},
            f"DGX OCI binding.images.{name}",
        )
        for field in ("index_digest", "manifest_digest", "config_digest", "archive_sha256"):
            _digest(image.get(field), f"DGX OCI binding.images.{name}.{field}")
    return binding


def _verified_json_snapshot(path: Path, context: str) -> StableJsonSnapshot:
    snapshot = _snapshot_json(path, context)
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum = _snapshot_bytes(checksum_path, f"{context} checksum")
    _verify_snapshot_checksum(
        snapshot,
        checksum,
        filename=path.name,
        context=context,
    )
    return snapshot


def _validate_trust_run(
    value: object,
    *,
    context: str,
    git_sha: str,
) -> Mapping[str, object]:
    run = _mapping(value, context)
    _exact_keys(
        run,
        {
            "role",
            "run_id",
            "run_attempt",
            "workflow_path",
            "event",
            "head_sha",
            "head_branch",
            "status",
            "conclusion",
            "created_at",
            "updated_at",
        },
        context,
    )
    _positive_integer(run.get("run_id"), f"{context}.run_id")
    _positive_integer(run.get("run_attempt"), f"{context}.run_attempt")
    if _git_sha(run.get("head_sha"), f"{context}.head_sha") != git_sha:
        raise ContractError(f"{context} Git SHA mismatch")
    created_at = _timestamp(run.get("created_at"), f"{context}.created_at")
    updated_at = _timestamp(run.get("updated_at"), f"{context}.updated_at")
    if updated_at < created_at:
        raise ContractError(f"{context} timestamps are inconsistent")
    return run


def _validate_trust_artifact(
    value: object,
    *,
    context: str,
    expected_name: str,
    expected_run_id: int,
) -> Mapping[str, object]:
    artifact = _mapping(value, context)
    _exact_keys(
        artifact,
        {
            "id",
            "name",
            "digest",
            "size_in_bytes",
            "workflow_run_id",
            "created_at",
            "expires_at",
        },
        context,
    )
    _positive_integer(artifact.get("id"), f"{context}.id")
    _positive_integer(artifact.get("size_in_bytes"), f"{context}.size_in_bytes")
    if artifact.get("name") != expected_name or artifact.get("workflow_run_id") != expected_run_id:
        raise ContractError(f"{context} name or workflow run mismatch")
    _digest(artifact.get("digest"), f"{context}.digest")
    _timestamp(artifact.get("created_at"), f"{context}.created_at")
    _timestamp(artifact.get("expires_at"), f"{context}.expires_at")
    return artifact


def _trust_release_roles(
    trust: Mapping[str, object],
    *,
    repository: str,
    git_sha: str,
    now: datetime,
) -> tuple[Mapping[str, object], Mapping[str, object], dict[str, Mapping[str, object]]]:
    _exact_keys(
        trust,
        {
            "schema",
            "generated_at",
            "expires_at",
            "repository",
            "release_ref",
            "current",
            "main_ci",
            "evidence_runs",
        },
        "release workflow trust",
    )
    if trust.get("schema") != "knowledge-uploader.release-workflow-trust.v1":
        raise ContractError("unsupported release workflow trust schema")
    generated_at = _timestamp(trust.get("generated_at"), "release workflow trust.generated_at")
    expires_at = _timestamp(trust.get("expires_at"), "release workflow trust.expires_at")
    if (
        generated_at > now + MAX_CLOCK_SKEW
        or expires_at <= generated_at
        or expires_at - generated_at > TRUST_SUMMARY_TTL
        or now > expires_at
    ):
        raise ContractError("release workflow trust is stale or has an invalid validity window")
    repository_record = _mapping(trust.get("repository"), "release workflow trust.repository")
    _exact_keys(
        repository_record,
        {"id", "full_name", "default_branch"},
        "release workflow trust.repository",
    )
    _positive_integer(repository_record.get("id"), "release workflow trust.repository.id")
    if repository_record.get("full_name") != repository:
        raise ContractError("release workflow trust repository mismatch")
    _text(repository_record.get("default_branch"), "release workflow trust.default_branch")
    release_ref = _mapping(trust.get("release_ref"), "release workflow trust.release_ref")
    _exact_keys(release_ref, {"ref", "kind", "git_sha"}, "release workflow trust.release_ref")
    if _git_sha(release_ref.get("git_sha"), "release workflow trust ref SHA") != git_sha:
        raise ContractError("release workflow trust ref SHA mismatch")
    if release_ref.get("kind") not in {"protected_default_branch", "protected_signed_tag"}:
        raise ContractError("release workflow trust ref is not protected")
    current = _validate_trust_run(
        trust.get("current"),
        context="release workflow trust.current",
        git_sha=git_sha,
    )
    if (
        current.get("role") != "protected_release"
        or current.get("workflow_path") != PROTECTED_WORKFLOW
        or current.get("event") != "workflow_dispatch"
        or current.get("status") not in {"queued", "in_progress", "completed"}
    ):
        raise ContractError("release authorization was not issued by the protected workflow")
    main_record = _mapping(trust.get("main_ci"), "release workflow trust.main_ci")
    main_artifacts = main_record.get("artifacts")
    main_without_artifacts = {key: item for key, item in main_record.items() if key != "artifacts"}
    main = dict(
        _validate_trust_run(
            main_without_artifacts,
            context="release workflow trust.main_ci",
            git_sha=git_sha,
        )
    )
    _exact_keys(
        main_record,
        set(main_without_artifacts) | {"artifacts"},
        "release workflow trust.main_ci",
    )
    if (
        main.get("role") != "main_ci"
        or main.get("workflow_path") != MAIN_WORKFLOW
        or main.get("event") != "push"
        or main.get("status") != "completed"
        or main.get("conclusion") != "success"
    ):
        raise ContractError("release workflow trust main CI identity is invalid")
    main_run_id = _positive_integer(main.get("run_id"), "release workflow trust.main_ci.run_id")
    main_run_attempt = _positive_integer(
        main.get("run_attempt"),
        "release workflow trust.main_ci.run_attempt",
    )
    artifacts = _mapping(main_artifacts, "release workflow trust.main_ci.artifacts")
    _exact_keys(artifacts, {"bundle", "provenance"}, "release workflow trust.main_ci.artifacts")
    main["artifacts"] = {
        "bundle": _validate_trust_artifact(
            artifacts.get("bundle"),
            context="release workflow trust.main_ci.bundle",
            expected_name=f"release-oci-bundle-{git_sha}-{main_run_id}-{main_run_attempt}",
            expected_run_id=main_run_id,
        ),
        "provenance": _validate_trust_artifact(
            artifacts.get("provenance"),
            context="release workflow trust.main_ci.provenance",
            expected_name=f"release-oci-provenance-{git_sha}-{main_run_id}-{main_run_attempt}",
            expected_run_id=main_run_id,
        ),
    }
    evidence: dict[str, Mapping[str, object]] = {}
    for raw in _sequence(trust.get("evidence_runs"), "release workflow trust.evidence_runs"):
        raw_record = _mapping(raw, "release workflow trust.evidence_runs[]")
        artifact_value = raw_record.get("artifact")
        record_without_artifact = {
            key: item for key, item in raw_record.items() if key != "artifact"
        }
        record = dict(
            _validate_trust_run(
                record_without_artifact,
                context="release workflow trust.evidence_runs[]",
                git_sha=git_sha,
            )
        )
        _exact_keys(
            raw_record,
            set(record_without_artifact) | {"artifact"},
            "release workflow trust.evidence_runs[]",
        )
        role = _text(record.get("role"), "release workflow trust evidence role")
        if role in evidence:
            raise ContractError("release workflow trust contains duplicate evidence roles")
        expected_workflow = DGX_WORKFLOW if role == "dgx" else EXTERNAL_WORKFLOW
        if (
            role not in {"dgx", "external"}
            or record.get("workflow_path") != expected_workflow
            or record.get("event") != "workflow_dispatch"
            or record.get("status") != "completed"
            or record.get("conclusion") != "success"
        ):
            raise ContractError(f"release workflow trust {role} identity is invalid")
        run_id = _positive_integer(record.get("run_id"), f"release workflow trust {role}.run_id")
        run_attempt = _positive_integer(
            record.get("run_attempt"),
            f"release workflow trust {role}.run_attempt",
        )
        prefix = "dgx-spark-evidence" if role == "dgx" else "protected-release-external-evidence"
        record["artifact"] = _validate_trust_artifact(
            artifact_value,
            context=f"release workflow trust {role}.artifact",
            expected_name=f"{prefix}-{git_sha}-{run_id}-{run_attempt}",
            expected_run_id=run_id,
        )
        evidence[role] = record
    if set(evidence) != {"dgx", "external"}:
        raise ContractError("release workflow trust evidence inventory is incomplete")
    run_ids = {
        _positive_integer(current.get("run_id"), "release workflow trust.current.run_id"),
        main_run_id,
        *(
            _positive_integer(record.get("run_id"), f"release workflow trust {role}.run_id")
            for role, record in evidence.items()
        ),
    }
    if len(run_ids) != 4:
        raise ContractError("release workflow trust reuses a workflow run across roles")
    return current, main, evidence


def _snapshot_release_authorization_evidence(
    evidence_dir: Path,
) -> dict[str, StableBytesSnapshot]:
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise ContractError("release evidence directory is missing or unsafe")
    root = evidence_dir.resolve()
    actual_names = {path.name for path in root.iterdir() if path.is_file()}
    missing = REQUIRED_RELEASE_EVIDENCE - actual_names
    if missing:
        raise ContractError(
            f"release authorization evidence inventory is incomplete: {sorted(missing)}"
        )
    return {
        filename: _snapshot_bytes(
            root / filename,
            f"release authorization evidence {filename}",
        )
        for filename in sorted(REQUIRED_RELEASE_EVIDENCE)
    }


def _verify_dr_policy_provenance_binding(
    metadata: Mapping[str, object],
    *,
    policy_payload: bytes,
) -> None:
    policy_input_sha256: object | None = None
    for index, raw in enumerate(
        _sequence(metadata.get("inputs"), "release provenance.inputs")
    ):
        record = _mapping(raw, f"release provenance.inputs[{index}]")
        if record.get("path") == DR_RELEASE_POLICY_INPUT_PATH:
            policy_input_sha256 = record.get("sha256")
            break
    if policy_input_sha256 != _sha256_bytes(policy_payload):
        raise ContractError(
            "release provenance DR policy checksum does not match the authorized contract"
        )


def authorize_release(
    *,
    evidence_dir: Path,
    dgx_binding_path: Path,
    trust_summary_path: Path,
    output_path: Path,
    repository: str,
    git_sha: str,
    environment: str,
    now: datetime | None = None,
) -> Mapping[str, object]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    sha = _git_sha(git_sha, "git_sha")
    evidence_root = evidence_dir.resolve()
    expected_binding_path = evidence_root / "dgx-oci-consumption.json"
    expected_trust_path = evidence_root / "release-workflow-trust.json"
    if (
        dgx_binding_path.is_symlink()
        or dgx_binding_path.resolve() != expected_binding_path
        or trust_summary_path.is_symlink()
        or trust_summary_path.resolve() != expected_trust_path
    ):
        raise ContractError("release authorization evidence path is outside the stable bundle")
    evidence_snapshots = _snapshot_release_authorization_evidence(evidence_dir)
    evidence_payloads = {
        name: snapshot.payload for name, snapshot in evidence_snapshots.items()
    }
    try:
        contract_payloads = protected_release_gate.snapshot_contract_payloads()
        evidence_errors = protected_release_gate.validate_evidence_payloads(
            evidence_payloads,
            git_sha=sha,
            environment=environment,
            contract_payloads=contract_payloads,
            now=timestamp,
        )
    except (RuntimeError, UnicodeError, yaml.YAMLError):
        raise ContractError("release evidence authorization validation failed") from None
    if evidence_errors:
        raise ContractError("release evidence authorization validation failed")
    provenance_snapshot = _parse_json_snapshot(
        evidence_snapshots[PROVENANCE_FILENAME],
        "release OCI provenance",
    )
    provenance_checksum = evidence_snapshots[CHECKSUM_FILENAME]
    metadata = verify_bundle(
        bundle_dir=evidence_dir,
        expected_repository=repository,
        expected_git_sha=sha,
        require_archives=False,
        now=timestamp,
        provenance_snapshot=provenance_snapshot,
        checksum_snapshot=provenance_checksum,
    )
    _verify_dr_policy_provenance_binding(
        metadata,
        policy_payload=contract_payloads[DR_RELEASE_POLICY_INPUT_PATH],
    )
    provenance_sha256 = provenance_snapshot.sha256
    binding_snapshot = _parse_json_snapshot(
        evidence_snapshots["dgx-oci-consumption.json"],
        "DGX OCI binding",
    )
    binding = _validate_dgx_binding(
        binding_snapshot.parsed,
        repository=repository,
        git_sha=sha,
        environment=environment,
    )
    trust_snapshot = _parse_json_snapshot(
        evidence_snapshots["release-workflow-trust.json"],
        "release workflow trust",
    )
    _verify_snapshot_checksum(
        trust_snapshot,
        evidence_snapshots["release-workflow-trust.json.sha256"],
        filename="release-workflow-trust.json",
        context="release workflow trust",
    )
    trust_sha256 = trust_snapshot.sha256
    trust = trust_snapshot.parsed
    current, main, evidence_runs = _trust_release_roles(
        trust,
        repository=repository,
        git_sha=sha,
        now=timestamp,
    )
    source = _mapping(metadata.get("source"), "release provenance.source")
    artifact = _mapping(metadata.get("artifact"), "release provenance.artifact")
    binding_source = _mapping(binding.get("source"), "DGX binding.source")
    binding_dgx = _mapping(binding.get("dgx"), "DGX binding.dgx")
    if (
        main.get("run_id") != source.get("workflow_run_id")
        or main.get("run_attempt") != source.get("workflow_run_attempt")
        or binding_source.get("main_workflow_run_id") != source.get("workflow_run_id")
        or binding_source.get("main_workflow_run_attempt") != source.get("workflow_run_attempt")
        or binding_source.get("bundle_name") != artifact.get("bundle_name")
        or binding_source.get("provenance_sha256") != provenance_sha256
    ):
        raise ContractError("main CI, provenance and DGX source identities do not match")
    if binding_dgx.get("workflow_run_id") != evidence_runs["dgx"].get("run_id") or binding_dgx.get(
        "workflow_run_attempt"
    ) != evidence_runs["dgx"].get("run_attempt"):
        raise ContractError("DGX binding workflow run does not match protected trust metadata")
    if (
        binding_dgx.get("infrastructure_evidence_sha256")
        != evidence_snapshots["infrastructure-e2e.json"].sha256
        or binding_dgx.get("device_evidence_sha256")
        != evidence_snapshots["dgx-spark-evidence.json"].sha256
        or binding_dgx.get("workflow_trust_sha256") != trust_snapshot.sha256
    ):
        raise ContractError("DGX binding evidence digests do not match authorization snapshots")
    main_artifacts = _mapping(main.get("artifacts"), "release workflow trust.main_ci.artifacts")
    bundle_artifact = _mapping(main_artifacts.get("bundle"), "main CI bundle artifact")
    provenance_artifact = _mapping(main_artifacts.get("provenance"), "main CI provenance artifact")
    if bundle_artifact.get("name") != artifact.get("bundle_name") or provenance_artifact.get(
        "name"
    ) != artifact.get("provenance_name"):
        raise ContractError("trusted GitHub artifact names do not match OCI provenance")
    evidence_digests = {
        name: evidence_snapshots[name].sha256
        for name in sorted(REQUIRED_RELEASE_EVIDENCE)
    }
    image_authorizations: dict[str, object] = {}
    images = _mapping(metadata.get("images"), "release provenance.images")
    binding_images = _mapping(binding.get("images"), "DGX binding.images")
    for name in ("backend", "frontend"):
        image = _mapping(images.get(name), f"release provenance.images.{name}")
        platform = _arm64_platform(metadata, name)
        bound = _mapping(binding_images.get(name), f"DGX binding.images.{name}")
        expected = {
            "index_digest": image.get("index_digest"),
            "manifest_digest": platform.get("manifest_digest"),
            "config_digest": platform.get("config_digest"),
            "archive_sha256": image.get("archive_sha256"),
        }
        if dict(bound) != expected:
            raise ContractError(f"DGX {name} digests do not match release provenance")
        image_authorizations[name] = {"archive": image.get("archive"), **expected}
    payload: Mapping[str, object] = {
        "schema": AUTHORIZATION_SCHEMA,
        "status": "authorized",
        "generated_at": timestamp.isoformat(),
        "expires_at": (timestamp + AUTHORIZATION_TTL).isoformat(),
        "environment": environment,
        "repository": repository,
        "git_sha": sha,
        "release_ref": _mapping(trust.get("release_ref"), "release workflow trust.release_ref"),
        "workflow_runs": {
            "main_ci": main.get("run_id"),
            "dgx": evidence_runs["dgx"].get("run_id"),
            "external": evidence_runs["external"].get("run_id"),
            "protected_release": current.get("run_id"),
        },
        "workflow_run_attempts": {
            "main_ci": main.get("run_attempt"),
            "dgx": evidence_runs["dgx"].get("run_attempt"),
            "external": evidence_runs["external"].get("run_attempt"),
            "protected_release": current.get("run_attempt"),
        },
        "evidence_artifacts": {
            role: {
                "workflow_run_id": record.get("run_id"),
                "workflow_run_attempt": record.get("run_attempt"),
                "artifact_id": _mapping(
                    record.get("artifact"),
                    f"release workflow trust {role}.artifact",
                ).get("id"),
                "artifact_name": _mapping(
                    record.get("artifact"),
                    f"release workflow trust {role}.artifact",
                ).get("name"),
                "artifact_digest": _mapping(
                    record.get("artifact"),
                    f"release workflow trust {role}.artifact",
                ).get("digest"),
            }
            for role, record in sorted(evidence_runs.items())
        },
        "source_artifact": {
            "workflow_run_id": source.get("workflow_run_id"),
            "workflow_run_attempt": source.get("workflow_run_attempt"),
            "artifact_id": bundle_artifact.get("id"),
            "artifact_name": bundle_artifact.get("name"),
            "artifact_digest": bundle_artifact.get("digest"),
            "provenance_artifact_id": provenance_artifact.get("id"),
            "provenance_artifact_digest": provenance_artifact.get("digest"),
            "provenance_sha256": provenance_sha256,
        },
        "images": image_authorizations,
        "evidence_sha256": evidence_digests,
        "workflow_trust_sha256": trust_sha256,
        "deployment_policy": "download_exact_artifact_id_then_verify_oci_archives",
    }
    output_payload = _write_json(output_path, payload)
    _write_checksum(
        output_path.with_suffix(output_path.suffix + ".sha256"),
        filename=output_path.name,
        payload=output_payload,
    )
    return payload


def validate_deployment_handoff(
    *,
    authorization_path: Path,
    bundle_dir: Path,
    repository: str,
    git_sha: str,
    environment: str,
    now: datetime | None = None,
) -> Mapping[str, object]:
    authorization_snapshot = _verified_json_snapshot(
        authorization_path,
        "release authorization",
    )
    authorization = authorization_snapshot.parsed
    _exact_keys(
        authorization,
        {
            "schema",
            "status",
            "generated_at",
            "expires_at",
            "environment",
            "repository",
            "git_sha",
            "release_ref",
            "workflow_runs",
            "workflow_run_attempts",
            "evidence_artifacts",
            "source_artifact",
            "images",
            "evidence_sha256",
            "workflow_trust_sha256",
            "deployment_policy",
        },
        "release authorization",
    )
    if (
        authorization.get("schema") != AUTHORIZATION_SCHEMA
        or authorization.get("status") != "authorized"
        or authorization.get("deployment_policy")
        != "download_exact_artifact_id_then_verify_oci_archives"
    ):
        raise ContractError("release authorization schema, status or deployment policy is invalid")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    generated_at = _timestamp(authorization.get("generated_at"), "authorization.generated_at")
    expires_at = _timestamp(authorization.get("expires_at"), "authorization.expires_at")
    if (
        generated_at > timestamp + MAX_CLOCK_SKEW
        or expires_at <= generated_at
        or expires_at - generated_at > AUTHORIZATION_TTL
        or timestamp > expires_at
    ):
        raise ContractError("release authorization is stale or has an invalid validity window")
    sha = _git_sha(git_sha, "git_sha")
    if (
        authorization.get("repository") != repository
        or authorization.get("git_sha") != sha
        or authorization.get("environment") != environment
    ):
        raise ContractError("release authorization deployment identity mismatch")
    release_ref = _mapping(authorization.get("release_ref"), "authorization.release_ref")
    _exact_keys(release_ref, {"ref", "kind", "git_sha"}, "authorization.release_ref")
    if (
        release_ref.get("kind") not in {"protected_default_branch", "protected_signed_tag"}
        or release_ref.get("git_sha") != sha
    ):
        raise ContractError("release authorization protected ref is invalid")
    workflow_runs = _mapping(authorization.get("workflow_runs"), "authorization.workflow_runs")
    _exact_keys(
        workflow_runs,
        {"main_ci", "dgx", "external", "protected_release"},
        "authorization.workflow_runs",
    )
    run_ids = {
        _positive_integer(value, f"authorization.workflow_runs.{name}")
        for name, value in workflow_runs.items()
    }
    if len(run_ids) != 4:
        raise ContractError("release authorization reuses a workflow run across trust roles")
    workflow_attempts = _mapping(
        authorization.get("workflow_run_attempts"),
        "authorization.workflow_run_attempts",
    )
    _exact_keys(
        workflow_attempts,
        {"main_ci", "dgx", "external", "protected_release"},
        "authorization.workflow_run_attempts",
    )
    for role, attempt in workflow_attempts.items():
        _positive_integer(attempt, f"authorization.workflow_run_attempts.{role}")
    evidence_artifacts = _mapping(
        authorization.get("evidence_artifacts"),
        "authorization.evidence_artifacts",
    )
    _exact_keys(evidence_artifacts, {"dgx", "external"}, "authorization.evidence_artifacts")
    for role, value in evidence_artifacts.items():
        artifact = _mapping(value, f"authorization.evidence_artifacts.{role}")
        _exact_keys(
            artifact,
            {
                "workflow_run_id",
                "workflow_run_attempt",
                "artifact_id",
                "artifact_name",
                "artifact_digest",
            },
            f"authorization.evidence_artifacts.{role}",
        )
        if artifact.get("workflow_run_id") != workflow_runs.get(role) or artifact.get(
            "workflow_run_attempt"
        ) != workflow_attempts.get(role):
            raise ContractError(f"authorization {role} artifact run identity mismatch")
        _positive_integer(
            artifact.get("artifact_id"),
            f"authorization.evidence_artifacts.{role}.artifact_id",
        )
        _text(
            artifact.get("artifact_name"),
            f"authorization.evidence_artifacts.{role}.artifact_name",
        )
        _digest(
            artifact.get("artifact_digest"),
            f"authorization.evidence_artifacts.{role}.artifact_digest",
        )
    source_artifact = _mapping(authorization.get("source_artifact"), "source_artifact")
    _exact_keys(
        source_artifact,
        {
            "workflow_run_id",
            "workflow_run_attempt",
            "artifact_id",
            "artifact_name",
            "artifact_digest",
            "provenance_artifact_id",
            "provenance_artifact_digest",
            "provenance_sha256",
        },
        "source_artifact",
    )
    _positive_integer(source_artifact.get("artifact_id"), "source_artifact.artifact_id")
    _positive_integer(
        source_artifact.get("provenance_artifact_id"),
        "source_artifact.provenance_artifact_id",
    )
    _text(source_artifact.get("artifact_name"), "source_artifact.artifact_name")
    for field in ("artifact_digest", "provenance_artifact_digest", "provenance_sha256"):
        _digest(source_artifact.get(field), f"source_artifact.{field}")
    authorized_images = _mapping(authorization.get("images"), "authorization.images")
    _exact_keys(authorized_images, {"backend", "frontend"}, "authorization.images")
    evidence_digests = _mapping(
        authorization.get("evidence_sha256"), "authorization.evidence_sha256"
    )
    _exact_keys(
        evidence_digests,
        set(REQUIRED_RELEASE_EVIDENCE),
        "authorization.evidence_sha256",
    )
    deployment_snapshots = {
        name: _snapshot_bytes(
            bundle_dir / name,
            f"deployment evidence {name}",
        )
        for name in sorted(REQUIRED_RELEASE_EVIDENCE)
    }
    for name, digest_value in evidence_digests.items():
        expected_digest = _digest(digest_value, f"authorization.evidence_sha256.{name}")
        actual_digest = deployment_snapshots[name].sha256
        if actual_digest != expected_digest:
            raise ContractError(f"deployment evidence checksum mismatch: {name}")
    workflow_trust_sha256 = _digest(
        authorization.get("workflow_trust_sha256"),
        "authorization.workflow_trust_sha256",
    )
    if (
        workflow_trust_sha256
        != deployment_snapshots["release-workflow-trust.json"].sha256
    ):
        raise ContractError("deployment workflow trust checksum mismatch")
    provenance_snapshot = _parse_json_snapshot(
        deployment_snapshots[PROVENANCE_FILENAME],
        "release OCI provenance",
    )
    metadata = verify_bundle(
        bundle_dir=bundle_dir,
        expected_repository=repository,
        expected_git_sha=sha,
        expected_run_id=_positive_integer(
            source_artifact.get("workflow_run_id"), "source_artifact.workflow_run_id"
        ),
        expected_run_attempt=_positive_integer(
            source_artifact.get("workflow_run_attempt"),
            "source_artifact.workflow_run_attempt",
        ),
        require_archives=True,
        now=timestamp,
        provenance_snapshot=provenance_snapshot,
        checksum_snapshot=deployment_snapshots[CHECKSUM_FILENAME],
    )
    if source_artifact.get("provenance_sha256") != provenance_snapshot.sha256:
        raise ContractError("deployment bundle provenance checksum mismatch")
    metadata_images = _mapping(metadata.get("images"), "provenance.images")
    for name in ("backend", "frontend"):
        authorized = _mapping(authorized_images.get(name), f"authorization.images.{name}")
        image = _mapping(metadata_images.get(name), f"provenance.images.{name}")
        arm64 = _arm64_platform(metadata, name)
        if authorized != {
            "archive": image.get("archive"),
            "index_digest": image.get("index_digest"),
            "manifest_digest": arm64.get("manifest_digest"),
            "config_digest": arm64.get("config_digest"),
            "archive_sha256": image.get("archive_sha256"),
        }:
            raise ContractError(f"deployment {name} OCI digests differ from authorization")
    return authorization


def _parse_input_paths(values: Sequence[str]) -> list[Path]:
    return [Path(value) for value in values]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create strict provenance from two OCI archives")
    create.add_argument("--repository", required=True)
    create.add_argument("--git-sha", required=True)
    create.add_argument("--git-ref", required=True)
    create.add_argument("--workflow-run-id", required=True, type=int)
    create.add_argument("--workflow-run-attempt", required=True, type=int)
    create.add_argument("--backend-archive", required=True, type=Path)
    create.add_argument("--frontend-archive", required=True, type=Path)
    create.add_argument("--source-input", action="append", required=True)
    create.add_argument("--repository-root", type=Path, default=Path.cwd())
    create.add_argument("--output-dir", required=True, type=Path)

    verify = subparsers.add_parser("verify", help="Verify provenance and optionally OCI archives")
    verify.add_argument("--bundle-dir", required=True, type=Path)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--git-sha", required=True)
    verify.add_argument("--workflow-run-id", required=True, type=int)
    verify.add_argument("--workflow-run-attempt", required=True, type=int)
    verify.add_argument("--require-archives", action="store_true")

    load = subparsers.add_parser("load-arm64", help="Load exact arm64 OCI configs into Docker")
    load.add_argument("--bundle-dir", required=True, type=Path)
    load.add_argument("--repository", required=True)
    load.add_argument("--git-sha", required=True)
    load.add_argument("--workflow-run-id", required=True, type=int)
    load.add_argument("--workflow-run-attempt", required=True, type=int)
    load.add_argument("--backend-tag", required=True)
    load.add_argument("--frontend-tag", required=True)

    bind = subparsers.add_parser(
        "bind-dgx",
        help="Bind physical evidence to OCI manifest/config IDs",
    )
    bind.add_argument("--bundle-dir", required=True, type=Path)
    bind.add_argument("--infrastructure-evidence", required=True, type=Path)
    bind.add_argument("--dgx-evidence", required=True, type=Path)
    bind.add_argument("--workflow-trust", required=True, type=Path)
    bind.add_argument("--output", required=True, type=Path)
    bind.add_argument("--repository", required=True)
    bind.add_argument("--git-sha", required=True)
    bind.add_argument("--environment", choices=("staging", "production"), required=True)
    bind.add_argument("--main-run-id", required=True, type=int)
    bind.add_argument("--main-run-attempt", required=True, type=int)
    bind.add_argument("--dgx-run-id", required=True, type=int)
    bind.add_argument("--dgx-run-attempt", required=True, type=int)

    authorize = subparsers.add_parser(
        "authorize",
        help="Issue a short-lived digest-bound deployment authorization",
    )
    authorize.add_argument("--evidence-dir", required=True, type=Path)
    authorize.add_argument("--dgx-binding", required=True, type=Path)
    authorize.add_argument("--workflow-trust", required=True, type=Path)
    authorize.add_argument("--output", required=True, type=Path)
    authorize.add_argument("--repository", required=True)
    authorize.add_argument("--git-sha", required=True)
    authorize.add_argument("--environment", choices=("staging", "production"), required=True)

    handoff = subparsers.add_parser(
        "verify-deployment",
        help="Verify that deployment consumes the authorized OCI artifact bytes",
    )
    handoff.add_argument("--authorization", required=True, type=Path)
    handoff.add_argument("--bundle-dir", required=True, type=Path)
    handoff.add_argument("--repository", required=True)
    handoff.add_argument("--git-sha", required=True)
    handoff.add_argument("--environment", choices=("staging", "production"), required=True)
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    try:
        if arguments.command == "create":
            metadata = create_provenance(
                repository=arguments.repository,
                git_sha=arguments.git_sha,
                git_ref=arguments.git_ref,
                workflow_run_id=arguments.workflow_run_id,
                workflow_run_attempt=arguments.workflow_run_attempt,
                backend_archive=arguments.backend_archive,
                frontend_archive=arguments.frontend_archive,
                inputs=_parse_input_paths(arguments.source_input),
                repository_root=arguments.repository_root,
                output_dir=arguments.output_dir,
            )
            artifact = _mapping(metadata["artifact"], "artifact")
            sys.stdout.write(f"created {artifact['bundle_name']}\n")
        elif arguments.command == "verify":
            verify_bundle(
                bundle_dir=arguments.bundle_dir,
                expected_repository=arguments.repository,
                expected_git_sha=arguments.git_sha,
                expected_run_id=arguments.workflow_run_id,
                expected_run_attempt=arguments.workflow_run_attempt,
                require_archives=arguments.require_archives,
            )
            sys.stdout.write("release OCI provenance verified\n")
        elif arguments.command == "load-arm64":
            load_arm64_images(
                bundle_dir=arguments.bundle_dir,
                backend_tag=arguments.backend_tag,
                frontend_tag=arguments.frontend_tag,
                expected_repository=arguments.repository,
                expected_git_sha=arguments.git_sha,
                expected_run_id=arguments.workflow_run_id,
                expected_run_attempt=arguments.workflow_run_attempt,
            )
            sys.stdout.write("release OCI arm64 images loaded and verified\n")
        elif arguments.command == "bind-dgx":
            bind_dgx_evidence(
                bundle_dir=arguments.bundle_dir,
                infrastructure_path=arguments.infrastructure_evidence,
                dgx_path=arguments.dgx_evidence,
                trust_summary_path=arguments.workflow_trust,
                output_path=arguments.output,
                repository=arguments.repository,
                git_sha=arguments.git_sha,
                environment=arguments.environment,
                main_run_id=arguments.main_run_id,
                main_run_attempt=arguments.main_run_attempt,
                dgx_run_id=arguments.dgx_run_id,
                dgx_run_attempt=arguments.dgx_run_attempt,
            )
            sys.stdout.write("DGX runtime evidence bound to release OCI digests\n")
        elif arguments.command == "authorize":
            authorize_release(
                evidence_dir=arguments.evidence_dir,
                dgx_binding_path=arguments.dgx_binding,
                trust_summary_path=arguments.workflow_trust,
                output_path=arguments.output,
                repository=arguments.repository,
                git_sha=arguments.git_sha,
                environment=arguments.environment,
            )
            sys.stdout.write("digest-bound deployment authorization issued\n")
        elif arguments.command == "verify-deployment":
            validate_deployment_handoff(
                authorization_path=arguments.authorization,
                bundle_dir=arguments.bundle_dir,
                repository=arguments.repository,
                git_sha=arguments.git_sha,
                environment=arguments.environment,
            )
            sys.stdout.write("deployment handoff matches authorized OCI bytes\n")
        else:  # pragma: no cover - argparse enforces the command choices
            raise ContractError("unsupported command")
    except (ContractError, OSError, subprocess.CalledProcessError) as error:
        sys.stderr.write(f"release OCI gate failed: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
