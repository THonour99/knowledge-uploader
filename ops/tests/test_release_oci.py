from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

TEST_SHA = "a" * 40
TEST_REPOSITORY = "example/knowledge-uploader"
NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def _load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/release_oci.py"
    spec = importlib.util.spec_from_file_location("release_oci", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release_oci")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(content: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(content).hexdigest()


def _canonical_digest(value: object) -> str:
    return _digest(_json_bytes(value)).removeprefix("sha256:")


def _descriptor(content: bytes, media_type: str) -> dict[str, object]:
    return {"mediaType": media_type, "digest": _digest(content), "size": len(content)}


def _statement(
    *, manifest_digest: str, predicate_type: str, architecture: str
) -> dict[str, object]:
    predicate: dict[str, object]
    if "provenance" in predicate_type:
        predicate = {
            "materials": [
                {
                    "uri": f"pkg:docker/library/base@stable?platform=linux%2F{architecture}",
                    "digest": {"sha256": "b" * 64},
                }
            ]
        }
    else:
        predicate = {"name": "synthetic SPDX fixture"}
    return {
        "_type": "https://in-toto.io/Statement/v0.1",
        "subject": [
            {
                "name": "fixture",
                "digest": {"sha256": manifest_digest.removeprefix("sha256:")},
            }
        ],
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def _write_oci_archive(
    path: Path,
    *,
    revision: str = TEST_SHA,
    include_sbom: bool = True,
    include_base_material: bool = True,
) -> None:
    blobs: dict[str, bytes] = {}

    def add_blob(content: bytes) -> dict[str, object]:
        descriptor = _descriptor(content, "application/octet-stream")
        blobs[str(descriptor["digest"])] = content
        return descriptor

    manifest_descriptors: list[dict[str, object]] = []
    platform_manifests: list[tuple[str, str]] = []
    for architecture in ("amd64", "arm64"):
        config = _json_bytes(
            {
                "architecture": architecture,
                "os": "linux",
                "config": {"Labels": {"org.opencontainers.image.revision": revision}},
                "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "c" * 64]},
            }
        )
        config_descriptor = add_blob(config)
        config_descriptor["mediaType"] = "application/vnd.oci.image.config.v1+json"
        layer = f"fixture-layer-{architecture}".encode()
        layer_descriptor = add_blob(layer)
        layer_descriptor["mediaType"] = "application/vnd.oci.image.layer.v1.tar"
        manifest = _json_bytes(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": config_descriptor,
                "layers": [layer_descriptor],
            }
        )
        manifest_descriptor = add_blob(manifest)
        manifest_descriptor.update(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {"os": "linux", "architecture": architecture},
            }
        )
        manifest_descriptors.append(manifest_descriptor)
        platform_manifests.append((architecture, str(manifest_descriptor["digest"])))

    empty_config = _json_bytes({})
    empty_config_descriptor = add_blob(empty_config)
    empty_config_descriptor["mediaType"] = "application/vnd.unknown.config.v1+json"
    for architecture, manifest_digest in platform_manifests:
        predicate_types = ["https://slsa.dev/provenance/v0.2"]
        if include_sbom:
            predicate_types.append("https://spdx.dev/Document")
        layers: list[dict[str, object]] = []
        for predicate_type in predicate_types:
            statement = _statement(
                manifest_digest=manifest_digest,
                predicate_type=predicate_type,
                architecture=architecture,
            )
            if "provenance" in predicate_type and not include_base_material:
                predicate = statement["predicate"]
                assert isinstance(predicate, dict)
                predicate["materials"] = [
                    {
                        "uri": "pkg:docker/docker/dockerfile@1.10",
                        "digest": {"sha256": "e" * 64},
                    }
                ]
            content = _json_bytes(statement) + b"\n"
            descriptor = add_blob(content)
            descriptor.update(
                {
                    "mediaType": "application/vnd.in-toto+json",
                    "annotations": {"in-toto.io/predicate-type": predicate_type},
                }
            )
            layers.append(descriptor)
        attestation_manifest = _json_bytes(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": empty_config_descriptor,
                "layers": layers,
            }
        )
        attestation_descriptor = add_blob(attestation_manifest)
        attestation_descriptor.update(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": manifest_digest,
                },
            }
        )
        manifest_descriptors.append(attestation_descriptor)

    index = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": manifest_descriptors,
        }
    )
    members: dict[str, bytes] = {
        "oci-layout": _json_bytes({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
    }
    for digest, content in blobs.items():
        members[f"blobs/sha256/{digest.removeprefix('sha256:')}"] = content
    with tarfile.open(path, mode="w") as archive:
        for name, content in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))


def _source_inputs(root: Path) -> list[Path]:
    paths = [
        root / "backend/Dockerfile",
        root / "backend/requirements.txt",
        root / "frontend/Dockerfile",
        root / "frontend/package-lock.json",
    ]
    for index, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture-{index}\n", encoding="utf-8", newline="\n")
    policy_path = root / "ops/policies/dr-release-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_bytes(
        (Path(__file__).parents[2] / "ops/policies/dr-release-policy.json").read_bytes()
    )
    return [*paths, policy_path]


def _create_bundle(tmp_path: Path) -> tuple[ModuleType, Path, dict[str, object]]:
    module = _load_module()
    backend = tmp_path / "backend.oci.tar"
    frontend = tmp_path / "frontend.oci.tar"
    _write_oci_archive(backend)
    _write_oci_archive(frontend)
    output = tmp_path / "bundle"
    output.mkdir()
    metadata = module.create_provenance(
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        git_ref="refs/heads/main",
        workflow_run_id=101,
        workflow_run_attempt=2,
        backend_archive=backend,
        frontend_archive=frontend,
        inputs=_source_inputs(tmp_path / "source"),
        repository_root=tmp_path / "source",
        output_dir=output,
        now=NOW,
    )
    (output / "backend.oci.tar").write_bytes(backend.read_bytes())
    (output / "frontend.oci.tar").write_bytes(frontend.read_bytes())
    return module, output, dict(metadata)


def test_create_and_verify_bundle_binds_multiarch_attestations_and_inputs(tmp_path: Path) -> None:
    module, bundle, metadata = _create_bundle(tmp_path)

    verified = module.verify_bundle(
        bundle_dir=bundle,
        expected_repository=TEST_REPOSITORY,
        expected_git_sha=TEST_SHA,
        expected_run_id=101,
        expected_run_attempt=2,
        require_archives=True,
        now=NOW + timedelta(minutes=1),
    )

    assert verified == metadata
    assert metadata["artifact"] == {
        "bundle_name": f"release-oci-bundle-{TEST_SHA}-101-2",
        "provenance_name": f"release-oci-provenance-{TEST_SHA}-101-2",
        "generated_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=8)).isoformat(),
    }
    images = metadata["images"]
    assert isinstance(images, dict)
    backend = images["backend"]
    assert isinstance(backend, dict)
    platforms = backend["platforms"]
    assert isinstance(platforms, list)
    assert {(row["os"], row["architecture"]) for row in platforms} == {
        ("linux", "amd64"),
        ("linux", "arm64"),
    }
    assert all(row["provenance_digest"].startswith("sha256:") for row in platforms)
    assert all(row["sbom_digest"].startswith("sha256:") for row in platforms)
    assert all(row["base_materials"] for row in platforms)


def test_provenance_strict_schema_rejects_extra_fields_and_stale_metadata(tmp_path: Path) -> None:
    module, _bundle, metadata = _create_bundle(tmp_path)
    forged = copy.deepcopy(metadata)
    forged["mutable_tag"] = "latest"

    with pytest.raises(module.ContractError, match="schema mismatch"):
        module.validate_provenance(forged, now=NOW)
    with pytest.raises(module.ContractError, match="stale"):
        module.validate_provenance(metadata, now=NOW + timedelta(hours=9))


def test_verify_rejects_archive_replacement_even_with_same_source_sha(tmp_path: Path) -> None:
    module, bundle, _metadata = _create_bundle(tmp_path)
    with (bundle / "backend.oci.tar").open("ab") as stream:
        stream.write(b"rebuild-with-the-same-git-sha")

    with pytest.raises(module.ContractError, match="archive checksum mismatch"):
        module.verify_bundle(
            bundle_dir=bundle,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_run_id=101,
            expected_run_attempt=2,
            require_archives=True,
            now=NOW,
        )


def test_verify_parses_the_checksum_verified_provenance_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, bundle, metadata = _create_bundle(tmp_path)
    provenance_path = bundle / module.PROVENANCE_FILENAME
    checksum_path = bundle / module.CHECKSUM_FILENAME
    original_verify_checksum = module._verify_checksum
    forged_payload = b'{"attacker_override":true}\n'

    def replace_after_checksum_verification(path: Path, sidecar_path: Path) -> object:
        snapshot = original_verify_checksum(path, sidecar_path)
        provenance_path.write_bytes(forged_payload)
        checksum_path.write_text(
            f"{_digest(forged_payload).removeprefix('sha256:')}  {provenance_path.name}\n",
            encoding="utf-8",
            newline="\n",
        )
        return snapshot

    monkeypatch.setattr(module, "_verify_checksum", replace_after_checksum_verification)

    verified = module.verify_bundle(
        bundle_dir=bundle,
        expected_repository=TEST_REPOSITORY,
        expected_git_sha=TEST_SHA,
        expected_run_id=101,
        expected_run_attempt=2,
        require_archives=False,
        now=NOW,
    )

    assert verified == metadata
    assert provenance_path.read_bytes() == forged_payload


@pytest.mark.parametrize(
    ("include_sbom", "include_base_material", "message"),
    [
        (False, True, "lacks complete provenance/SBOM"),
        (True, False, "no digest-bound base image material"),
    ],
)
def test_create_rejects_incomplete_buildkit_attestations(
    tmp_path: Path,
    include_sbom: bool,
    include_base_material: bool,
    message: str,
) -> None:
    module = _load_module()
    archive = tmp_path / "incomplete.oci.tar"
    _write_oci_archive(
        archive,
        include_sbom=include_sbom,
        include_base_material=include_base_material,
    )

    with pytest.raises(module.ContractError, match=message):
        module._parse_image_archive(archive, git_sha=TEST_SHA)


def test_create_rejects_source_change_while_archives_are_snapshotted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    backend_archive = tmp_path / "backend.oci.tar"
    frontend_archive = tmp_path / "frontend.oci.tar"
    _write_oci_archive(backend_archive)
    _write_oci_archive(frontend_archive)
    source_root = tmp_path / "source"
    source_inputs = _source_inputs(source_root)
    original_parser = module._parse_image_archive
    parsed_archives = 0

    def parse_then_change_source(path: Path, **kwargs: object) -> dict[str, object]:
        nonlocal parsed_archives
        result = original_parser(path, **kwargs)
        parsed_archives += 1
        if parsed_archives == 1:
            source_inputs[0].write_text("changed generation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(module, "_parse_image_archive", parse_then_change_source)

    with pytest.raises(module.ContractError, match="source inputs changed"):
        module.create_provenance(
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            git_ref="refs/heads/main",
            workflow_run_id=101,
            workflow_run_attempt=2,
            backend_archive=backend_archive,
            frontend_archive=frontend_archive,
            inputs=source_inputs,
            repository_root=source_root,
            output_dir=tmp_path / "bundle",
            now=NOW,
        )


def test_dr_policy_provenance_binding_rejects_mismatch() -> None:
    module = _load_module()
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()
    metadata: dict[str, object] = {
        "inputs": [
            {
                "path": module.DR_RELEASE_POLICY_INPUT_PATH,
                "sha256": module._sha256_bytes(policy_payload),
            }
        ]
    }

    module._verify_dr_policy_provenance_binding(
        metadata,
        policy_payload=policy_payload,
    )
    metadata["inputs"] = [
        {
            "path": module.DR_RELEASE_POLICY_INPUT_PATH,
            "sha256": "sha256:" + "f" * 64,
        }
    ]

    with pytest.raises(module.ContractError, match="DR policy checksum"):
        module._verify_dr_policy_provenance_binding(
            metadata,
            policy_payload=policy_payload,
        )


def test_load_arm64_tags_only_the_config_digest_from_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, bundle, metadata = _create_bundle(tmp_path)
    images = metadata["images"]
    assert isinstance(images, dict)
    calls: list[list[str]] = []
    loaded_archives: list[bytes] = []
    hash_fds: list[int] = []
    tar_fds: list[int] = []
    load_fds: list[int] = []
    hash_streams: list[object] = []
    tar_streams: list[object] = []
    load_streams: list[object] = []
    backend_path = bundle / "backend.oci.tar"
    original_archives = [
        backend_path.read_bytes(),
        (bundle / "frontend.oci.tar").read_bytes(),
    ]
    original_sha256_stream = module._sha256_stream
    original_tar_open = module.tarfile.open

    def hash_private_snapshot(stream: object) -> str:
        hash_streams.append(stream)
        hash_fds.append(stream.fileno())
        digest = original_sha256_stream(stream)
        if len(hash_fds) == 1:
            backend_path.write_bytes(b"source inode changed after private snapshot")
        return digest

    def observe_tar_stream(*args: object, **kwargs: object) -> object:
        stream = kwargs["fileobj"]
        tar_streams.append(stream)
        tar_fds.append(stream.fileno())
        return original_tar_open(*args, **kwargs)

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        if command == ["docker", "image", "load"]:
            stream = _kwargs.get("stdin")
            assert hasattr(stream, "read")
            load_streams.append(stream)
            load_fds.append(stream.fileno())
            loaded_archives.append(stream.read())
            return SimpleNamespace(stdout="loaded")
        if command[:3] == ["docker", "image", "inspect"]:
            image_name = "backend" if command[-1].startswith("backend") else "frontend"
            image = images[image_name]
            assert isinstance(image, dict)
            platforms = image["platforms"]
            assert isinstance(platforms, list)
            platform = next(row for row in platforms if row["architecture"] == "arm64")
            return SimpleNamespace(
                stdout=json.dumps(
                    [
                        {
                            "Id": platform["config_digest"],
                            "Architecture": "arm64",
                            "Os": "linux",
                            "Config": {"Labels": {"org.opencontainers.image.revision": TEST_SHA}},
                        }
                    ]
                )
            )
        return SimpleNamespace(stdout="loaded")

    monkeypatch.setattr(module, "_sha256_stream", hash_private_snapshot)
    monkeypatch.setattr(module.tarfile, "open", observe_tar_stream)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.load_arm64_images(
        bundle_dir=bundle,
        backend_tag="backend:dgx",
        frontend_tag="frontend:dgx",
        expected_repository=TEST_REPOSITORY,
        expected_git_sha=TEST_SHA,
        expected_run_id=101,
        expected_run_attempt=2,
        now=NOW,
    )

    tag_calls = [call for call in calls if call[:3] == ["docker", "image", "tag"]]
    assert len(tag_calls) == 2
    assert all(call[3].startswith("sha256:") for call in tag_calls)
    assert {call[4] for call in tag_calls} == {"backend:dgx", "frontend:dgx"}
    assert loaded_archives == original_archives
    assert backend_path.read_bytes() == b"source inode changed after private snapshot"
    assert hash_fds == tar_fds == load_fds
    assert len(hash_streams) == len(tar_streams) == len(load_streams) == 2
    assert all(
        hashed is parsed is loaded
        for hashed, parsed, loaded in zip(
            hash_streams,
            tar_streams,
            load_streams,
            strict=True,
        )
    )
    assert [call for call in calls if call[:3] == ["docker", "image", "load"]] == [
        ["docker", "image", "load"],
        ["docker", "image", "load"],
    ]


def test_load_rejects_archive_path_replaced_after_bundle_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, bundle, _metadata = _create_bundle(tmp_path)
    backend_path = bundle / "backend.oci.tar"
    forged_path = tmp_path / "forged-backend.oci.tar"
    _write_oci_archive(forged_path, revision="b" * 40)
    original_verify = module.verify_bundle

    def verify_then_replace(**kwargs: object) -> dict[str, object]:
        metadata = dict(original_verify(**kwargs))
        forged_path.replace(backend_path)
        return metadata

    docker_calls: list[list[str]] = []
    monkeypatch.setattr(module, "verify_bundle", verify_then_replace)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **_kwargs: docker_calls.append(command),
    )

    with pytest.raises(module.ContractError, match="archive checksum mismatch"):
        module.load_arm64_images(
            bundle_dir=bundle,
            backend_tag="backend:dgx",
            frontend_tag="frontend:dgx",
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_run_id=101,
            expected_run_attempt=2,
            now=NOW,
        )

    assert docker_calls == []


def test_oci_archive_parses_private_snapshot_after_source_content_changes(tmp_path: Path) -> None:
    module = _load_module()
    archive_path = tmp_path / "backend.oci.tar"
    _write_oci_archive(archive_path)
    trusted_payload = archive_path.read_bytes()

    with module.OciArchive(archive_path) as archive:
        archive_path.write_bytes(b"forged path contents")
        assert archive.sha256 == _digest(trusted_payload)
        assert archive.read("oci-layout") == _json_bytes({"imageLayoutVersion": "1.0.0"})

    assert archive_path.read_bytes() == b"forged path contents"


def test_oci_archive_constructor_closes_all_resources_on_unexpected_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    archive_path = tmp_path / "broken.oci.tar"
    archive_path.write_bytes(b"fixture")

    class BrokenArchive:
        def __init__(self) -> None:
            self.closed = False

        def getmembers(self) -> list[tarfile.TarInfo]:
            raise ValueError("unexpected parser failure")

        def close(self) -> None:
            self.closed = True

    broken_archive = BrokenArchive()
    snapshots: list[object] = []
    original_snapshot_file = module._snapshot_file

    def capture_snapshot(*args: object, **kwargs: object) -> object:
        snapshot = original_snapshot_file(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(module, "_snapshot_file", capture_snapshot)
    monkeypatch.setattr(module.tarfile, "open", lambda *_args, **_kwargs: broken_archive)

    with pytest.raises(ValueError, match="unexpected parser failure"):
        module.OciArchive(archive_path)

    assert broken_archive.closed is True
    assert len(snapshots) == 1
    assert snapshots[0].closed is True


def test_dgx_binding_rejects_locally_rebuilt_image_id(tmp_path: Path) -> None:
    module, bundle, metadata = _create_bundle(tmp_path)
    images = metadata["images"]
    assert isinstance(images, dict)
    image_ids: dict[str, str] = {}
    for name in ("backend", "frontend"):
        image = images[name]
        assert isinstance(image, dict)
        platforms = image["platforms"]
        assert isinstance(platforms, list)
        image_ids[name] = next(
            row["config_digest"] for row in platforms if row["architecture"] == "arm64"
        )
    infrastructure_path = tmp_path / "infrastructure-e2e.json"
    infrastructure_path.write_text(
        json.dumps(
            {
                "status": "development_passed",
                "git_sha": TEST_SHA,
                "environment": "staging",
                "backend_image_id": "sha256:" + "d" * 64,
                "frontend_image_id": image_ids["frontend"],
            }
        ),
        encoding="utf-8",
    )
    dgx_path = tmp_path / "dgx-spark-evidence.json"
    dgx_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "git_sha": TEST_SHA,
                "environment": "staging",
                "architecture": "arm64",
                "backend_image_id": "sha256:" + "d" * 64,
                "frontend_image_id": image_ids["frontend"],
                "compose_e2e_evidence_sha256": _digest(
                    infrastructure_path.read_bytes()
                ).removeprefix("sha256:"),
            }
        ),
        encoding="utf-8",
    )
    trust = tmp_path / "trust.json"
    trust.write_text("{}\n", encoding="utf-8")

    with pytest.raises(module.ContractError, match="runtime image ID"):
        module.bind_dgx_evidence(
            bundle_dir=bundle,
            infrastructure_path=infrastructure_path,
            dgx_path=dgx_path,
            trust_summary_path=trust,
            output_path=tmp_path / "binding.json",
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            main_run_id=101,
            main_run_attempt=2,
            dgx_run_id=202,
            dgx_run_attempt=1,
            now=NOW,
        )


def _write_valid_external_evidence(
    module: ModuleType,
    bundle: Path,
    *,
    now: datetime,
) -> dict[str, bytes]:
    root = Path(__file__).parents[2]
    policy_payload = (root / "ops/policies/dr-release-policy.json").read_bytes()
    (bundle / "dr-release-policy.json").write_bytes(policy_payload)
    source_generated_at = (now - timedelta(minutes=12)).isoformat()
    projected_at = (now - timedelta(minutes=10)).isoformat()
    alertmanager_payload = (
        b"route:\n  receiver: protected-webhook\nreceivers:\n"
        b"  - name: protected-webhook\n    webhook_configs:\n"
        b"      - url_file: /run/secrets/protected-webhook-url\n"
    )
    (bundle / "alertmanager.yml").write_bytes(alertmanager_payload)
    receipts: dict[str, dict[str, object]] = {
        "alertmanager-notification.json": {
            "alert_name": "KnowledgeUploaderProtectedReleaseProbe",
            "alert_fingerprint": "1" * 64,
            "receiver_name": "protected-webhook",
            "receiver_type": "webhook",
            "webhook_delivery_id_sha256": "2" * 64,
            "webhook_receipt_sha256": "3" * 64,
            "webhook_status_code": 202,
            "firing_at": source_generated_at,
            "delivered_at": source_generated_at,
            "resolved_at": source_generated_at,
        },
        "dr-release.json": {
            "backup_id": "20260716T000000Z-aabbccdd",
            "backup_manifest_sha256": "4" * 64,
            "restore_evidence_sha256": "5" * 64,
            "restore_started_at": (now - timedelta(minutes=20)).isoformat(),
            "restore_completed_at": (now - timedelta(minutes=18)).isoformat(),
            "rpo_seconds": 60,
            "rpo_target_seconds": 300,
            "rto_seconds": 120,
            "rto_target_seconds": 600,
            "policy_sha256": module.protected_release_gate._sha256_bytes(policy_payload),
            "alembic_revision": "20260716o001",
            "database_tables_sha256": "6" * 64,
            "minio_missing_objects": 0,
            "minio_orphan_objects": 0,
            "minio_mismatched_objects": 0,
            "recovery_pair_id": "recovery-pair-001",
            "postgres_restore_point_sha256": "d" * 64,
            "minio_restore_point_sha256": "e" * 64,
            "postgres_pitr_enabled": True,
            "last_archived_at": (now - timedelta(minutes=15)).isoformat(),
            "full_backup_encrypted": True,
            "full_backup_immutable": True,
            "offsite_location_sha256": "7" * 64,
            "retention_until": (now + timedelta(days=31)).isoformat(),
            "minio_versioning_enabled": True,
            "minio_replication_enabled": True,
            "coordinated_snapshot": False,
            "key_version_sha256": "8" * 64,
            "decrypt_validation": "passed",
            "plaintext_emitted": False,
            "main_chain_smoke": "passed",
            "cleanup_validation": "passed",
        },
        "email-delivery.json": {
            "registration_delivery": "passed",
            "password_reset_delivery": "passed",
            "registration_message_id_sha256": "9" * 64,
            "password_reset_message_id_sha256": "a" * 64,
            "registration_smtp_receipt_sha256": "b" * 64,
            "password_reset_smtp_receipt_sha256": "c" * 64,
            "registration_smtp_result": "accepted",
            "password_reset_smtp_result": "accepted",
            "registration_delivered_at": source_generated_at,
            "password_reset_delivered_at": source_generated_at,
            "persistent_message": True,
            "broker_expiry_at_or_before_token_expiry": True,
            "publisher_confirm": "passed",
            "encrypted_envelope_observed": True,
            "plaintext_token_observed": False,
            "dlq_plaintext_token_observed": False,
            "publish_failure_public_response_indistinguishable": True,
            "publish_failure_public_statuses": {
                "register": 201,
                "resend_verification": 200,
                "forgot_password": 200,
            },
            "publish_failure_metric_recorded": True,
            "retry_issued_fresh_token": True,
            "smtp_delivery_semantics": "at_most_once_attempt",
        },
        "promtool.json": {
            "prometheus_config": "passed",
            "prometheus_rules": "passed",
            "alertmanager_config": "passed",
            "prometheus_config_sha256": module.protected_release_gate._sha256_path(
                root / "ops/observability/prometheus.protected.yml"
            ),
            "prometheus_rules_sha256": module.protected_release_gate._sha256_path(
                root / "ops/observability/alerts.yml"
            ),
            "alertmanager_config_sha256": module.protected_release_gate._sha256_bytes(
                alertmanager_payload
            ),
            "prometheus_image": module.protected_release_gate.PROMETHEUS_VALIDATOR_IMAGE,
            "prometheus_manifest_list_digest": (
                module.protected_release_gate.PROMETHEUS_VALIDATOR_IMAGE.rsplit("@", maxsplit=1)[1]
            ),
            "prometheus_image_id": "sha256:" + "b" * 64,
            "prometheus_image_os": "linux",
            "prometheus_image_architecture": "amd64",
            "prometheus_docker_architecture": "amd64",
            "alertmanager_image": module.protected_release_gate.ALERTMANAGER_VALIDATOR_IMAGE,
            "alertmanager_manifest_list_digest": (
                module.protected_release_gate.ALERTMANAGER_VALIDATOR_IMAGE.rsplit("@", maxsplit=1)[
                    1
                ]
            ),
            "alertmanager_image_id": "sha256:" + "c" * 64,
            "alertmanager_image_os": "linux",
            "alertmanager_image_architecture": "amd64",
            "alertmanager_docker_architecture": "amd64",
        },
    }
    payloads: dict[str, bytes] = {
        "alertmanager.yml": alertmanager_payload,
        "dr-release-policy.json": policy_payload,
    }
    for index, (name, contract) in enumerate(
        sorted(module.EXTERNAL_EVIDENCE_CONTRACTS.items()),
        1,
    ):
        output_schema, source_schema, source_tool = contract
        source_run_id = f"00000000-0000-4000-8000-{index:012d}"
        receipt = receipts[name]
        source_evidence = {
            "schema": source_schema,
            "generated_at": source_generated_at,
            "git_sha": TEST_SHA,
            "environment": "staging",
            "source_run_id": source_run_id,
            "source_run_attempt": 1,
            "source_tool": source_tool,
            "status": "passed",
            "receipt": receipt,
        }
        projection = {
            "schema": output_schema,
            "generated_at": projected_at,
            "git_sha": TEST_SHA,
            "environment": "staging",
            "collector_run_id": 505,
            "collector_run_attempt": 1,
            "status": "passed",
            "source": {
                "schema": source_schema,
                "generated_at": source_generated_at,
                "run_id": source_run_id,
                "run_attempt": 1,
                "tool": source_tool,
                "file_sha256": f"{index + 10:064x}",
                "canonical_sha256": _canonical_digest(source_evidence),
            },
            "receipt": receipt,
        }
        payload = json.dumps(projection, sort_keys=True).encode("utf-8")
        (bundle / name).write_bytes(payload)
        payloads[name] = payload
    return payloads


def _write_valid_internal_evidence(
    module: ModuleType,
    bundle: Path,
    metadata: dict[str, object],
    *,
    now: datetime,
) -> dict[str, bytes]:
    gate = module.protected_release_gate
    images = metadata["images"]
    assert isinstance(images, dict)
    image_ids: dict[str, str] = {}
    for name in ("backend", "frontend"):
        image = images[name]
        assert isinstance(image, dict)
        platforms = image["platforms"]
        assert isinstance(platforms, list)
        image_ids[name] = next(
            row["config_digest"] for row in platforms if row["architecture"] == "arm64"
        )

    run_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    success_task_id = uuid.UUID("20000000-0000-4000-8000-000000000001")
    retry_task_id = uuid.UUID("20000000-0000-4000-8000-000000000002")
    exhausted_task_id = uuid.UUID("20000000-0000-4000-8000-000000000003")
    replay_task_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"rabbitmq-replay:ragflow_queue:{exhausted_task_id}",
    )
    audit_log_id = uuid.UUID("30000000-0000-4000-8000-000000000001")
    generated_at = (now - timedelta(minutes=5)).isoformat()
    task_name = "ragflow.create_upload_task"
    rabbitmq = {
        "status": "passed",
        "generated_at": generated_at,
        "git_sha": TEST_SHA,
        "environment": "staging",
        "probe_run_id": str(run_id),
        "success": {
            "task_id": str(success_task_id),
            "correlation_id": str(success_task_id),
            "probe_run_id": str(run_id),
            "task_name": task_name,
            "queue_name": "ragflow_queue",
            "result": "passed",
            "dlq_count_after": 0,
        },
        "intermediate_retry": {
            "task_id": str(retry_task_id),
            "correlation_id": str(retry_task_id),
            "probe_run_id": str(run_id),
            "task_name": task_name,
            "queue_name": "ragflow_queue",
            "result": "passed",
            "retries_observed": 1,
            "dlq_count_during_retry": 0,
        },
        "exhausted": {
            "task_id": str(exhausted_task_id),
            "correlation_id": str(exhausted_task_id),
            "probe_run_id": str(run_id),
            "task_name": task_name,
            "queue_name": "ragflow_queue",
            "result": "dead_lettered",
            "attempts": 4,
            "retry_count": 3,
            "dead_letter_reason": "rejected",
            "delivery_path": "celery_worker_retry_exhaustion",
            "dlq_count_after": 1,
        },
        "replay": {
            "queue_name": "ragflow_queue",
            "task_name": task_name,
            "probe_run_id": str(run_id),
            "original_task_id": str(exhausted_task_id),
            "original_correlation_id": str(exhausted_task_id),
            "replay_task_id": str(replay_task_id),
            "replay_correlation_id": str(replay_task_id),
            "raw_payload_copied": False,
            "persistent_message": True,
            "replay_policy": "clean_room_allowlist_only",
            "audit_log_id": str(audit_log_id),
            "result": "queued",
        },
        "resolved": {
            "queue_name": "ragflow_queue",
            "task_name": task_name,
            "probe_run_id": str(run_id),
            "original_task_id": str(exhausted_task_id),
            "replay_task_id": str(replay_task_id),
            "replay_correlation_id": str(replay_task_id),
            "audit_log_id": str(audit_log_id),
            "result": "passed",
            "dlq_count_after": 0,
            "domain_state": "passed",
            "ragflow_terminal_state": "parsed",
        },
    }
    rabbitmq_payload = json.dumps(rabbitmq, sort_keys=True).encode("utf-8")
    (bundle / "rabbitmq-dlq-replay.json").write_bytes(rabbitmq_payload)

    def fault_receipt(
        dependency: str,
        *,
        service: str,
        outage: str,
        observation: str,
        anchor: str,
    ) -> dict[str, object]:
        target_id = uuid.uuid5(uuid.NAMESPACE_URL, f"fault:{dependency}")
        receipt: dict[str, object] = {
            "status": "passed",
            "run_id": str(run_id),
            "service": service,
            "target_file_id": str(target_id),
            "outage_observed": outage,
            "failure_observation": observation,
            "durability_anchor": anchor,
            "queue_messages_before": 1,
            "queue_messages_after_restore": 1,
            "remote_upload_delta": 1,
            "remote_document_count": 1,
            "terminal_state": "parsed",
            "event_loss_detected": False,
            "duplicate_remote_document": False,
        }
        if dependency == "rabbitmq":
            receipt["broker_message_persisted"] = True
        elif dependency == "redis":
            receipt.update(
                {
                    "retry_task_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "fault:redis:retry")),
                    "retry_task_name": task_name,
                    "retry_queue": "ragflow_queue",
                    "retry_count_observed": 1,
                    "retry_status_before_restore": "requeued",
                }
            )
        else:
            receipt.update(
                {
                    "failed_task_id": str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"fault:{dependency}:failed")
                    ),
                    "retry_status_before": "failed",
                    "retry_status_after": "queued",
                }
            )
        return receipt

    fault_recovery = {
        "rabbitmq": fault_receipt(
            "rabbitmq",
            service="rabbitmq",
            outage="ready_503",
            observation="persistent_message_held_while_broker_unavailable",
            anchor="rabbitmq_durable_queue",
        ),
        "redis": fault_receipt(
            "redis",
            service="redis",
            outage="ready_503",
            observation="celery_retry_requeued_while_cache_unavailable",
            anchor="celery_retry_message",
        ),
        "minio": fault_receipt(
            "minio",
            service="minio",
            outage="ready_503",
            observation="postgres_failed_sync_task_before_remote_upload",
            anchor="postgres_sync_task",
        ),
        "ragflow": fault_receipt(
            "ragflow",
            service="mock-ragflow",
            outage="tls_endpoint_unreachable",
            observation="postgres_failed_sync_task_before_remote_upload",
            anchor="postgres_sync_task",
        ),
    }
    infrastructure = {
        "evidence_contract_version": 3,
        "status": "development_passed",
        "generated_at": generated_at,
        "git_sha": TEST_SHA,
        "environment": "staging",
        "full_compose_e2e": "development_passed",
        "architecture": "arm64",
        "docker_architecture": "arm64",
        "run_id": str(run_id),
        "compose_project": "knowledge-uploader-dgx-test",
        "source_worktree_clean": True,
        "cleanup_status": "passed",
        "resolved_compose_sha256": "b" * 64,
        "tls_certificate_sha256": "e" * 64,
        "tls": {
            "status": "passed",
            "ca_sha256": "1" * 64,
            "certificate_bundle_sha256": "e" * 64,
            "certificates": {
                "minio": "2" * 64,
                "ragflow": "3" * 64,
                "smtp": "4" * 64,
                "gateway": "5" * 64,
            },
            "verified_channels": [
                "gateway_https",
                "minio_https",
                "ragflow_https",
                "smtp_starttls",
            ],
        },
        "prometheus_minio_tls": {
            "status": "passed",
            "job": "minio",
            "health": "up",
            "scrape_url": "https://minio:9000/minio/v2/metrics/cluster",
            "config_sha256": gate._sha256_path(
                Path(__file__).parents[2]
                / "ops/observability/prometheus.protected.yml"
            ),
            "ca_file": "/etc/prometheus/tls/ca.crt",
            "server_name": "minio",
            "certificate_verification": "required",
        },
        "rabbitmq_probe_run_id": str(run_id),
        "rabbitmq_evidence_sha256": gate._sha256_bytes(rabbitmq_payload),
        "backend_image_revision": TEST_SHA,
        "backend_image_id": image_ids["backend"],
        "frontend_image_revision": TEST_SHA,
        "frontend_image_id": image_ids["frontend"],
        "results": {name: "passed" for name in gate.REQUIRED_INFRASTRUCTURE_RESULTS},
        "service_container_ids": {
            service: f"{index:064x}"
            for index, service in enumerate(sorted(gate.REQUIRED_SERVICE_CONTAINERS), 1)
        },
        "service_image_ids": {
            service: "sha256:" + f"{index + 100:064x}"
            for index, service in enumerate(sorted(gate.REQUIRED_SERVICE_CONTAINERS), 1)
        },
        "worker_queue_consumers": {
            queue: 1 for queue in gate.REQUIRED_WORKER_QUEUES
        },
        "business_probe": {
            "status": "passed",
            "email_verification_floor": "passed",
            "mock_smtp_delivery": "passed",
        },
        "fault_recovery": fault_recovery,
    }
    infrastructure_payload = json.dumps(infrastructure, sort_keys=True).encode("utf-8")
    (bundle / "infrastructure-e2e.json").write_bytes(infrastructure_payload)
    dgx = {
        "status": "passed",
        "generated_at": generated_at,
        "git_sha": TEST_SHA,
        "environment": "staging",
        "architecture": "arm64",
        "docker_architecture": "arm64",
        "backend_image_revision": TEST_SHA,
        "backend_image_id": image_ids["backend"],
        "frontend_image_revision": TEST_SHA,
        "frontend_image_id": image_ids["frontend"],
        "full_compose_e2e": "passed",
        "run_id": str(run_id),
        "compose_project": infrastructure["compose_project"],
        "resolved_compose_sha256": infrastructure["resolved_compose_sha256"],
        "compose_e2e_evidence_sha256": gate._sha256_bytes(infrastructure_payload),
    }
    dgx_payload = json.dumps(dgx, sort_keys=True).encode("utf-8")
    (bundle / "dgx-spark-evidence.json").write_bytes(dgx_payload)
    return {
        "rabbitmq-dlq-replay.json": rabbitmq_payload,
        "infrastructure-e2e.json": infrastructure_payload,
        "dgx-spark-evidence.json": dgx_payload,
    }


def _rebind_external_projection(module: ModuleType, projection: dict[str, object]) -> None:
    source = projection["source"]
    receipt = projection["receipt"]
    assert isinstance(source, dict)
    assert isinstance(receipt, dict)
    reconstructed = {
        "schema": source["schema"],
        "generated_at": source["generated_at"],
        "git_sha": projection["git_sha"],
        "environment": projection["environment"],
        "source_run_id": source["run_id"],
        "source_run_attempt": source["run_attempt"],
        "source_tool": source["tool"],
        "status": projection["status"],
        "receipt": receipt,
    }
    source["canonical_sha256"] = _canonical_digest(reconstructed)


def test_authorization_and_deployment_handoff_keep_the_exact_main_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, bundle, metadata = _create_bundle(tmp_path)
    images = metadata["images"]
    source = metadata["source"]
    artifact = metadata["artifact"]
    assert isinstance(images, dict)
    assert isinstance(source, dict)
    assert isinstance(artifact, dict)
    binding_images: dict[str, object] = {}
    for name in ("backend", "frontend"):
        image = images[name]
        assert isinstance(image, dict)
        platforms = image["platforms"]
        assert isinstance(platforms, list)
        arm64 = next(row for row in platforms if row["architecture"] == "arm64")
        binding_images[name] = {
            "index_digest": image["index_digest"],
            "manifest_digest": arm64["manifest_digest"],
            "config_digest": arm64["config_digest"],
            "archive_sha256": image["archive_sha256"],
        }
    binding = {
        "schema": module.DGX_BINDING_SCHEMA,
        "status": "passed",
        "generated_at": NOW.isoformat(),
        "environment": "staging",
        "repository": TEST_REPOSITORY,
        "git_sha": TEST_SHA,
        "source": {
            "main_workflow_run_id": 101,
            "main_workflow_run_attempt": 2,
            "bundle_name": artifact["bundle_name"],
            "provenance_sha256": _digest((bundle / module.PROVENANCE_FILENAME).read_bytes()),
        },
        "dgx": {
            "workflow_run_id": 404,
            "workflow_run_attempt": 3,
            "infrastructure_evidence_sha256": "sha256:" + "1" * 64,
            "device_evidence_sha256": "sha256:" + "2" * 64,
            "workflow_trust_sha256": "sha256:" + "3" * 64,
        },
        "images": binding_images,
    }
    (bundle / "dgx-oci-consumption.json").write_text(json.dumps(binding), encoding="utf-8")
    trust = {
        "schema": "knowledge-uploader.release-workflow-trust.v1",
        "generated_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=2)).isoformat(),
        "repository": {"id": 77, "full_name": TEST_REPOSITORY, "default_branch": "main"},
        "release_ref": {
            "ref": "refs/heads/main",
            "kind": "protected_default_branch",
            "git_sha": TEST_SHA,
        },
        "current": {
            "role": "protected_release",
            "run_id": 303,
            "run_attempt": 1,
            "workflow_path": ".github/workflows/protected-release.yml",
            "event": "workflow_dispatch",
            "head_sha": TEST_SHA,
            "head_branch": "main",
            "status": "in_progress",
            "conclusion": None,
            "created_at": (NOW - timedelta(minutes=10)).isoformat(),
            "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        },
        "main_ci": {
            "role": "main_ci",
            "run_id": 101,
            "run_attempt": 2,
            "workflow_path": module.MAIN_WORKFLOW,
            "event": "push",
            "head_sha": TEST_SHA,
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "created_at": (NOW - timedelta(minutes=30)).isoformat(),
            "updated_at": (NOW - timedelta(minutes=20)).isoformat(),
            "artifacts": {
                "bundle": {
                    "id": 901,
                    "name": artifact["bundle_name"],
                    "digest": "sha256:" + "4" * 64,
                    "size_in_bytes": 4096,
                    "workflow_run_id": 101,
                    "created_at": (NOW - timedelta(minutes=20)).isoformat(),
                    "expires_at": (NOW + timedelta(days=1)).isoformat(),
                },
                "provenance": {
                    "id": 902,
                    "name": artifact["provenance_name"],
                    "digest": "sha256:" + "5" * 64,
                    "size_in_bytes": 1024,
                    "workflow_run_id": 101,
                    "created_at": (NOW - timedelta(minutes=20)).isoformat(),
                    "expires_at": (NOW + timedelta(days=1)).isoformat(),
                },
            },
        },
        "evidence_runs": [
            {
                "role": "dgx",
                "run_id": 404,
                "run_attempt": 3,
                "workflow_path": module.DGX_WORKFLOW,
                "event": "workflow_dispatch",
                "head_sha": TEST_SHA,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "created_at": (NOW - timedelta(minutes=18)).isoformat(),
                "updated_at": (NOW - timedelta(minutes=12)).isoformat(),
                "artifact": {
                    "id": 903,
                    "name": f"dgx-spark-evidence-{TEST_SHA}-404-3",
                    "digest": "sha256:" + "6" * 64,
                    "size_in_bytes": 4096,
                    "workflow_run_id": 404,
                    "created_at": (NOW - timedelta(minutes=12)).isoformat(),
                    "expires_at": (NOW + timedelta(days=1)).isoformat(),
                },
            },
            {
                "role": "external",
                "run_id": 505,
                "run_attempt": 1,
                "workflow_path": module.EXTERNAL_WORKFLOW,
                "event": "workflow_dispatch",
                "head_sha": TEST_SHA,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "created_at": (NOW - timedelta(minutes=16)).isoformat(),
                "updated_at": (NOW - timedelta(minutes=11)).isoformat(),
                "artifact": {
                    "id": 904,
                    "name": (f"protected-release-external-evidence-{TEST_SHA}-505-1"),
                    "digest": "sha256:" + "7" * 64,
                    "size_in_bytes": 4096,
                    "workflow_run_id": 505,
                    "created_at": (NOW - timedelta(minutes=11)).isoformat(),
                    "expires_at": (NOW + timedelta(days=1)).isoformat(),
                },
            },
        ],
    }
    trust_path = bundle / "release-workflow-trust.json"
    trust_path.write_text(
        json.dumps(trust, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    trust_path.with_suffix(".json.sha256").write_text(
        f"{_digest(trust_path.read_bytes()).removeprefix('sha256:')}  {trust_path.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in module.REQUIRED_RELEASE_EVIDENCE:
        path = bundle / name
        if not path.exists():
            path.write_text(f"fixture:{name}\n", encoding="utf-8", newline="\n")
    external_payloads = _write_valid_external_evidence(module, bundle, now=NOW)
    internal_payloads = _write_valid_internal_evidence(
        module,
        bundle,
        metadata,
        now=NOW,
    )
    binding_dgx = binding["dgx"]
    assert isinstance(binding_dgx, dict)
    binding_dgx["infrastructure_evidence_sha256"] = module._sha256_bytes(
        internal_payloads["infrastructure-e2e.json"]
    )
    binding_dgx["device_evidence_sha256"] = module._sha256_bytes(
        internal_payloads["dgx-spark-evidence.json"]
    )
    binding_dgx["workflow_trust_sha256"] = _digest(trust_path.read_bytes())
    (bundle / "dgx-oci-consumption.json").write_text(
        json.dumps(binding),
        encoding="utf-8",
    )
    external_path = bundle / "email-delivery.json"
    validated_payload = external_payloads["email-delivery.json"]
    replacement_payload = validated_payload + b"\n"
    original_email_validator = module.protected_release_gate._email_delivery_evidence_errors
    replaced_after_validation = False

    def replace_after_validation(
        evidence: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        nonlocal replaced_after_validation
        errors = original_email_validator(evidence, now=now)
        if not replaced_after_validation:
            external_path.write_bytes(replacement_payload)
            replaced_after_validation = True
        return errors

    monkeypatch.setattr(
        module.protected_release_gate,
        "_email_delivery_evidence_errors",
        replace_after_validation,
    )

    authorization_path = bundle / "release-authorization.json"
    authorization = module.authorize_release(
        evidence_dir=bundle,
        dgx_binding_path=bundle / "dgx-oci-consumption.json",
        trust_summary_path=trust_path,
        output_path=authorization_path,
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        environment="staging",
        now=NOW,
    )

    authorization_payload = authorization_path.read_bytes()
    assert authorization_path.with_suffix(".json.sha256").read_text(encoding="utf-8") == (
        f"{_digest(authorization_payload).removeprefix('sha256:')}  "
        f"{authorization_path.name}\n"
    )
    assert authorization["source_artifact"]["artifact_id"] == 901
    assert authorization["source_artifact"]["artifact_name"] == artifact["bundle_name"]
    assert authorization["workflow_run_attempts"] == {
        "main_ci": 2,
        "dgx": 3,
        "external": 1,
        "protected_release": 1,
    }
    assert authorization["evidence_artifacts"]["dgx"]["artifact_id"] == 903
    evidence_sha256 = authorization["evidence_sha256"]
    assert isinstance(evidence_sha256, dict)
    for name, payload in external_payloads.items():
        assert evidence_sha256[name] == module._sha256_bytes(payload)
    assert external_path.read_bytes() == replacement_payload
    with pytest.raises(module.ContractError, match="deployment evidence checksum mismatch"):
        module.validate_deployment_handoff(
            authorization_path=authorization_path,
            bundle_dir=bundle,
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW + timedelta(minutes=1),
        )
    external_path.write_bytes(validated_payload)
    monkeypatch.setattr(
        module.protected_release_gate,
        "_email_delivery_evidence_errors",
        original_email_validator,
    )
    original_evidence_validator = module.protected_release_gate.validate_evidence_payloads
    for index, evidence_name in enumerate(
        (
            "dr-release-policy.json",
            "rabbitmq-dlq-replay.json",
            "infrastructure-e2e.json",
            "dgx-spark-evidence.json",
            "dgx-oci-consumption.json",
        ),
        1,
    ):
        evidence_path = bundle / evidence_name
        original_payload = evidence_path.read_bytes()
        replacement = original_payload + b"\n"

        def replace_internal_after_validation(
            payloads: dict[str, bytes],
            *,
            git_sha: str,
            environment: str,
            contract_payloads: dict[str, bytes] | None = None,
            now: datetime | None = None,
            target_path: Path = evidence_path,
            target_payload: bytes = replacement,
        ) -> list[str]:
            assert contract_payloads is not None
            errors = original_evidence_validator(
                payloads,
                git_sha=git_sha,
                environment=environment,
                contract_payloads=contract_payloads,
                now=now,
            )
            target_path.write_bytes(target_payload)
            return errors

        monkeypatch.setattr(
            module.protected_release_gate,
            "validate_evidence_payloads",
            replace_internal_after_validation,
        )
        internal_authorization_path = bundle / f"internal-snapshot-{index}.json"
        internal_authorization = module.authorize_release(
            evidence_dir=bundle,
            dgx_binding_path=bundle / "dgx-oci-consumption.json",
            trust_summary_path=trust_path,
            output_path=internal_authorization_path,
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW,
        )
        internal_digests = internal_authorization["evidence_sha256"]
        assert isinstance(internal_digests, dict)
        assert internal_digests[evidence_name] == _digest(original_payload)
        assert evidence_path.read_bytes() == replacement
        with pytest.raises(module.ContractError, match="deployment evidence checksum mismatch"):
            module.validate_deployment_handoff(
                authorization_path=internal_authorization_path,
                bundle_dir=bundle,
                repository=TEST_REPOSITORY,
                git_sha=TEST_SHA,
                environment="staging",
                now=NOW + timedelta(minutes=1),
            )
        evidence_path.write_bytes(original_payload)
    monkeypatch.setattr(
        module.protected_release_gate,
        "validate_evidence_payloads",
        original_evidence_validator,
    )
    secret_marker = "LEAK-ME-EXTERNAL-EVIDENCE"
    alertmanager_path = bundle / "alertmanager.yml"
    valid_alertmanager = alertmanager_path.read_bytes()
    alertmanager_path.write_bytes(f"route: [{secret_marker}\n".encode())
    with pytest.raises(module.ContractError) as captured:
        module.authorize_release(
            evidence_dir=bundle,
            dgx_binding_path=bundle / "dgx-oci-consumption.json",
            trust_summary_path=trust_path,
            output_path=bundle / "invalid-authorization.json",
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW,
        )
    alertmanager_path.write_bytes(valid_alertmanager)
    assert str(captured.value) == "release evidence authorization validation failed"
    assert secret_marker not in repr(captured.value)
    assert captured.value.__cause__ is None
    original_authorize_release = module.authorize_release
    cli_arguments = SimpleNamespace(
        command="authorize",
        evidence_dir=bundle,
        dgx_binding=bundle / "dgx-oci-consumption.json",
        workflow_trust=trust_path,
        output=bundle / "leak-check-authorization.json",
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        environment="staging",
    )

    def reject_authorization(**_kwargs: object) -> None:
        raise captured.value

    monkeypatch.setattr(
        module,
        "_build_parser",
        lambda: SimpleNamespace(parse_args=lambda: cli_arguments),
    )
    monkeypatch.setattr(module, "authorize_release", reject_authorization)
    assert module.main() == 1
    cli_output = capsys.readouterr()
    assert secret_marker not in cli_output.out
    assert secret_marker not in cli_output.err
    assert "release evidence authorization validation failed" in cli_output.err
    monkeypatch.setattr(module, "authorize_release", original_authorize_release)

    external_path = bundle / "email-delivery.json"
    external_payload = external_path.read_bytes()
    forged = json.loads(external_payload)
    forged["collector_run_attempt"] = 2
    external_path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(module.ContractError, match="authorization validation failed"):
        module.authorize_release(
            evidence_dir=bundle,
            dgx_binding_path=bundle / "dgx-oci-consumption.json",
            trust_summary_path=trust_path,
            output_path=bundle / "forged-authorization.json",
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW,
        )
    external_path.write_bytes(external_payload)
    checked_projection = json.loads(external_payload)
    _, _, checked_receipt = module.protected_release_gate._validate_external_projection(
        checked_projection,
        filename="email-delivery.json",
        git_sha=TEST_SHA,
        environment="staging",
        collector_run_id=505,
        collector_run_attempt=1,
        now=NOW,
    )
    assert (
        module.protected_release_gate._email_delivery_evidence_errors(
            checked_receipt,
            now=NOW,
        )
        == []
    )
    replaced_projection = json.loads(external_payload)
    replaced_receipt = replaced_projection["receipt"]
    assert isinstance(replaced_receipt, dict)
    replaced_receipt["persistent_message"] = False
    _rebind_external_projection(module, replaced_projection)
    external_path.write_text(json.dumps(replaced_projection), encoding="utf-8")
    with pytest.raises(module.ContractError, match="authorization validation failed"):
        module.authorize_release(
            evidence_dir=bundle,
            dgx_binding_path=bundle / "dgx-oci-consumption.json",
            trust_summary_path=trust_path,
            output_path=bundle / "semantically-forged-authorization.json",
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW,
        )
    external_path.write_bytes(external_payload)
    wrong_keys_projection = json.loads(external_payload)
    wrong_keys_projection["receipt"] = {"unexpected_digest": "0" * 64}
    _rebind_external_projection(module, wrong_keys_projection)
    external_path.write_text(json.dumps(wrong_keys_projection), encoding="utf-8")
    with pytest.raises(module.ContractError, match="authorization validation failed"):
        module.authorize_release(
            evidence_dir=bundle,
            dgx_binding_path=bundle / "dgx-oci-consumption.json",
            trust_summary_path=trust_path,
            output_path=bundle / "wrong-keys-authorization.json",
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW,
        )
    external_path.write_bytes(external_payload)

    assert (
        module.validate_deployment_handoff(
            authorization_path=authorization_path,
            bundle_dir=bundle,
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW + timedelta(minutes=1),
        )
        == authorization
    )

    with (bundle / "frontend.oci.tar").open("ab") as stream:
        stream.write(b"same-tag-rebuild")
    with pytest.raises(module.ContractError, match="archive checksum mismatch"):
        module.validate_deployment_handoff(
            authorization_path=authorization_path,
            bundle_dir=bundle,
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            environment="staging",
            now=NOW + timedelta(minutes=1),
        )


def test_load_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    module = _load_module()
    evidence = tmp_path / "duplicate.json"
    evidence.write_text('{"collector_run_id":505,"collector_run_id":506}', encoding="utf-8")

    with pytest.raises(module.ContractError, match="cannot read duplicate evidence"):
        module._load_json(evidence, "duplicate evidence")


def test_stable_reader_rejects_symlink(tmp_path: Path) -> None:
    module = _load_module()
    target = tmp_path / "target.json"
    target.write_text('{"status":"passed"}', encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation is unavailable on this runner")

    with pytest.raises(module.ContractError, match="unsafe"):
        module._read_stable_regular_file(link, "linked evidence")


def test_stable_reader_rejects_path_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    victim = tmp_path / "victim.json"
    replacement = tmp_path / "replacement.json"
    victim.write_text('{"status":"passed"}', encoding="utf-8")
    replacement.write_text('{"status":"forged"}', encoding="utf-8")
    real_open = module.os.open
    exchanged = False

    def exchange_before_open(path: object, flags: int, *args: object) -> int:
        nonlocal exchanged
        if not exchanged and Path(path) == victim:
            replacement.replace(victim)
            exchanged = True
        return real_open(path, flags, *args)

    monkeypatch.setattr(module.os, "open", exchange_before_open)
    with pytest.raises(module.ContractError, match="changed before"):
        module._read_stable_regular_file(victim, "exchanged evidence")
