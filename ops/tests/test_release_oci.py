from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import tarfile
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
    return paths


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


def test_load_arm64_tags_only_the_config_digest_from_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, bundle, metadata = _create_bundle(tmp_path)
    images = metadata["images"]
    assert isinstance(images, dict)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
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

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.load_arm64_images(
        bundle_dir=bundle,
        backend_tag="backend:dgx",
        frontend_tag="frontend:dgx",
        expected_repository=TEST_REPOSITORY,
        expected_git_sha=TEST_SHA,
        expected_run_id=101,
        expected_run_attempt=2,
    )

    tag_calls = [call for call in calls if call[:3] == ["docker", "image", "tag"]]
    assert len(tag_calls) == 2
    assert all(call[3].startswith("sha256:") for call in tag_calls)
    assert {call[4] for call in tag_calls} == {"backend:dgx", "frontend:dgx"}


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
                "status": "passed",
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
                "compose_e2e_evidence_sha256": module._sha256_file(
                    infrastructure_path
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


def test_authorization_and_deployment_handoff_keep_the_exact_main_artifact(
    tmp_path: Path,
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
            "provenance_sha256": module._sha256_file(bundle / module.PROVENANCE_FILENAME),
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
        f"{module._sha256_file(trust_path).removeprefix('sha256:')}  {trust_path.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in module.REQUIRED_RELEASE_EVIDENCE:
        path = bundle / name
        if not path.exists():
            path.write_text(f"fixture:{name}\n", encoding="utf-8", newline="\n")

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

    assert authorization["source_artifact"]["artifact_id"] == 901
    assert authorization["source_artifact"]["artifact_name"] == artifact["bundle_name"]
    assert authorization["workflow_run_attempts"] == {
        "main_ci": 2,
        "dgx": 3,
        "external": 1,
        "protected_release": 1,
    }
    assert authorization["evidence_artifacts"]["dgx"]["artifact_id"] == 903
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
