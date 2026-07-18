from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import stat
import sys
import urllib.error
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TEST_SHA = "a" * 40
TEST_REPOSITORY = "example/knowledge-uploader"
TEST_REPOSITORY_ID = 77
TEST_RUN_ID = 303
TEST_RUN_ATTEMPT = 2
TEST_ARTIFACT_ID = 990
NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def _load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/release_workflow_trust.py"
    spec = importlib.util.spec_from_file_location("deployment_source_trust", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release_workflow_trust")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _authorization(*, run_id: int = TEST_RUN_ID, attempt: int = TEST_RUN_ATTEMPT) -> bytes:
    return (
        json.dumps(
            {
                "schema": "knowledge-uploader.release-authorization.v1",
                "status": "authorized",
                "repository": TEST_REPOSITORY,
                "git_sha": TEST_SHA,
                "environment": "staging",
                "release_ref": {
                    "ref": "refs/heads/main",
                    "kind": "protected_default_branch",
                    "git_sha": TEST_SHA,
                },
                "workflow_runs": {"protected_release": run_id},
                "workflow_run_attempts": {"protected_release": attempt},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _archive(
    *,
    authorization: bytes | None = None,
    extra_members: list[tuple[zipfile.ZipInfo | str, bytes]] | None = None,
) -> bytes:
    stream = io.BytesIO()
    payload = authorization or _authorization()
    checksum = hashlib.sha256(payload).hexdigest().encode()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("release-authorization.json", payload)
        bundle.writestr(
            "release-authorization.json.sha256",
            checksum + b"  release-authorization.json\n",
        )
        for name, value in extra_members or []:
            bundle.writestr(name, value)
    return stream.getvalue()


def _artifact(module: ModuleType, archive: bytes) -> dict[str, object]:
    name = f"protected-release-validated-{TEST_SHA}-staging-{TEST_RUN_ID}-{TEST_RUN_ATTEMPT}"
    return {
        "id": TEST_ARTIFACT_ID,
        "name": name,
        "size_in_bytes": len(archive),
        "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
        "expired": False,
        "created_at": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "workflow_run": {"id": TEST_RUN_ID},
    }


def _responses(module: ModuleType, archive: bytes) -> dict[str, dict[str, object]]:
    artifact = _artifact(module, archive)
    return {
        f"/repos/{TEST_REPOSITORY}": {
            "id": TEST_REPOSITORY_ID,
            "full_name": TEST_REPOSITORY,
            "default_branch": "main",
        },
        f"/repos/{TEST_REPOSITORY}/branches/main": {
            "name": "main",
            "protected": True,
        },
        f"/repos/{TEST_REPOSITORY}/actions/runs/{TEST_RUN_ID}": {
            "id": TEST_RUN_ID,
            "run_attempt": TEST_RUN_ATTEMPT,
            "path": module.PROTECTED_WORKFLOW,
            "head_sha": TEST_SHA,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "created_at": (NOW - timedelta(minutes=15)).isoformat(),
            "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
            "repository": {
                "id": TEST_REPOSITORY_ID,
                "full_name": TEST_REPOSITORY,
            },
        },
        f"/repos/{TEST_REPOSITORY}/actions/runs/{TEST_RUN_ID}/artifacts?per_page=100": {
            "total_count": 1,
            "artifacts": [artifact],
        },
        f"/repos/{TEST_REPOSITORY}/actions/artifacts/{TEST_ARTIFACT_ID}": copy.deepcopy(artifact),
    }


class FakeClient:
    def __init__(
        self,
        module: ModuleType,
        responses: dict[str, dict[str, object]],
        archive: bytes,
    ) -> None:
        self.module = module
        self.responses = responses
        self.archive = archive
        self.download_paths: list[str] = []

    def get(self, path: str) -> dict[str, object]:
        if path not in self.responses:
            raise AssertionError(f"unexpected GitHub API request: {path}")
        return copy.deepcopy(self.responses[path])

    def download(
        self,
        path: str,
        destination: object,
        *,
        maximum_bytes: int,
    ) -> object:
        assert path == (f"/repos/{TEST_REPOSITORY}/actions/artifacts/{TEST_ARTIFACT_ID}/zip")
        assert len(self.archive) <= maximum_bytes
        self.download_paths.append(path)
        destination.write(self.archive)
        return self.module.DownloadSnapshot(
            size_in_bytes=len(self.archive),
            sha256="sha256:" + hashlib.sha256(self.archive).hexdigest(),
        )


def _download(
    module: ModuleType,
    client: FakeClient,
    output: Path,
    **overrides: object,
) -> object:
    artifact = client.responses[f"/repos/{TEST_REPOSITORY}/actions/artifacts/{TEST_ARTIFACT_ID}"]
    arguments: dict[str, object] = {
        "repository": TEST_REPOSITORY,
        "repository_id": TEST_REPOSITORY_ID,
        "git_sha": TEST_SHA,
        "git_ref": "refs/heads/main",
        "environment": "staging",
        "workflow_run_id": TEST_RUN_ID,
        "workflow_run_attempt": TEST_RUN_ATTEMPT,
        "artifact_id": TEST_ARTIFACT_ID,
        "artifact_digest": artifact["digest"],
        "output_dir": output,
        "now": NOW,
    }
    arguments.update(overrides)
    return module.download_verified_deployment_source(client, **arguments)


def test_download_source_authenticates_run_artifact_bytes_and_authorization(
    tmp_path: Path,
) -> None:
    module = _load_module()
    archive = _archive(extra_members=[("nested/evidence.json", b"{}\n")])
    client = FakeClient(module, _responses(module, archive), archive)

    source = _download(module, client, tmp_path / "bundle")

    assert source.workflow_run_id == TEST_RUN_ID
    assert source.workflow_run_attempt == TEST_RUN_ATTEMPT
    assert source.artifact_id == TEST_ARTIFACT_ID
    assert source.authorization_path.read_bytes() == _authorization()
    assert (source.bundle_dir / "nested/evidence.json").read_bytes() == b"{}\n"
    assert len(client.download_paths) == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_attempt", 9, "attempt mismatch"),
        ("path", ".github/workflows/attacker.yml", "unexpected workflow"),
        ("head_sha", "b" * 40, "Git SHA mismatch"),
        ("head_branch", "feature/attacker", "branch mismatch"),
        ("event", "push", "event mismatch"),
        ("status", "in_progress", "did not complete successfully"),
        ("conclusion", "failure", "did not complete successfully"),
        (
            "repository",
            {"id": 88, "full_name": "attacker/fork"},
            "different repository",
        ),
    ],
)
def test_download_source_rejects_forged_protected_run(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    module = _load_module()
    archive = _archive()
    responses = _responses(module, archive)
    responses[f"/repos/{TEST_REPOSITORY}/actions/runs/{TEST_RUN_ID}"][field] = value
    client = FakeClient(module, responses, archive)

    with pytest.raises(module.TrustError, match=message):
        _download(module, client, tmp_path / "bundle")
    assert not client.download_paths


def test_download_source_rejects_unprotected_branch(tmp_path: Path) -> None:
    module = _load_module()
    archive = _archive()
    responses = _responses(module, archive)
    responses[f"/repos/{TEST_REPOSITORY}/branches/main"]["protected"] = False

    with pytest.raises(module.TrustError, match="not protected"):
        _download(module, FakeClient(module, responses, archive), tmp_path / "bundle")


def test_download_source_rejects_duplicate_or_incomplete_artifact_listing(
    tmp_path: Path,
) -> None:
    module = _load_module()
    archive = _archive()
    responses = _responses(module, archive)
    listing = responses[
        f"/repos/{TEST_REPOSITORY}/actions/runs/{TEST_RUN_ID}/artifacts?per_page=100"
    ]
    artifacts = listing["artifacts"]
    assert isinstance(artifacts, list)
    artifacts.append(copy.deepcopy(artifacts[0]))
    listing["total_count"] = 2
    with pytest.raises(module.TrustError, match="exactly one"):
        _download(
            module,
            FakeClient(module, responses, archive),
            tmp_path / "duplicate",
        )

    listing["total_count"] = 3
    with pytest.raises(module.TrustError, match="listing is incomplete"):
        _download(
            module,
            FakeClient(module, responses, archive),
            tmp_path / "incomplete",
        )


def test_download_source_rejects_artifact_id_metadata_and_raw_digest_forgery(
    tmp_path: Path,
) -> None:
    module = _load_module()
    archive = _archive()
    responses = _responses(module, archive)
    client = FakeClient(module, responses, archive)
    with pytest.raises(module.TrustError, match="identity mismatch"):
        _download(module, client, tmp_path / "wrong-id", artifact_id=991)
    assert not client.download_paths

    responses = _responses(module, archive)
    exact = responses[f"/repos/{TEST_REPOSITORY}/actions/artifacts/{TEST_ARTIFACT_ID}"]
    exact["expires_at"] = (NOW + timedelta(days=2)).isoformat()
    with pytest.raises(module.TrustError, match="metadata mismatch"):
        _download(
            module,
            FakeClient(module, responses, archive),
            tmp_path / "metadata",
        )

    forged_bytes = bytearray(archive)
    forged_bytes[-10] ^= 1
    forged_archive = bytes(forged_bytes)
    responses = _responses(module, archive)
    with pytest.raises(module.TrustError, match="differs from GitHub and deployment anchors"):
        _download(
            module,
            FakeClient(module, responses, forged_archive),
            tmp_path / "raw-digest",
        )
    assert not (tmp_path / "raw-digest").exists()


def test_download_source_rejects_cross_run_authorization(tmp_path: Path) -> None:
    module = _load_module()
    archive = _archive(authorization=_authorization(run_id=999))
    client = FakeClient(module, _responses(module, archive), archive)

    with pytest.raises(module.TrustError, match="protected workflow run mismatch"):
        _download(module, client, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("member", ["../escape", "nested/../../escape", "C:/escape"])
def test_download_source_rejects_zip_path_traversal(
    tmp_path: Path,
    member: str,
) -> None:
    module = _load_module()
    archive = _archive(extra_members=[(member, b"escape")])
    client = FakeClient(module, _responses(module, archive), archive)

    with pytest.raises(module.TrustError, match=r"ZIP|non-portable"):
        _download(module, client, tmp_path / "bundle")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    "member",
    ["CONIN$", "CONOUT$.txt", "COM¹", "COM².log", "LPT³"],
)
def test_download_source_rejects_all_windows_device_names(
    tmp_path: Path,
    member: str,
) -> None:
    module = _load_module()
    archive = _archive(extra_members=[(member, b"device")])
    client = FakeClient(module, _responses(module, archive), archive)

    with pytest.raises(module.TrustError, match="reserved ZIP member name"):
        _download(module, client, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_download_source_rejects_zip_symlink_and_expansion_limit(tmp_path: Path) -> None:
    module = _load_module()
    symlink = zipfile.ZipInfo("linked-authorization")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = _archive(extra_members=[(symlink, b"release-authorization.json")])
    with pytest.raises(module.TrustError, match="link or special"):
        _download(
            module,
            FakeClient(module, _responses(module, archive), archive),
            tmp_path / "symlink",
        )

    archive = _archive(extra_members=[("A/x", b"x"), ("a/y", b"y")])
    with pytest.raises(module.TrustError, match="case-colliding"):
        _download(
            module,
            FakeClient(module, _responses(module, archive), archive),
            tmp_path / "case-collision",
        )

    archive = _archive(extra_members=[("large.bin", b"x" * 128)])
    with pytest.raises(module.TrustError, match="extraction safety limit"):
        _download(
            module,
            FakeClient(module, _responses(module, archive), archive),
            tmp_path / "large",
            maximum_extracted_bytes=64,
        )


def test_download_source_requires_fresh_output_and_safe_https_api(tmp_path: Path) -> None:
    module = _load_module()
    archive = _archive()
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(module.TrustError, match="must not already exist"):
        _download(
            module,
            FakeClient(module, _responses(module, archive), archive),
            output,
        )
    with pytest.raises(module.TrustError, match="HTTPS origin"):
        module.GitHubClient(token="token", api_url="http://api.github.test")


def test_protected_release_workflow_exposes_exact_artifact_coordinates() -> None:
    workflow = (Path(__file__).parents[2] / ".github/workflows/protected-release.yml").read_text(
        encoding="utf-8"
    )

    assert "id: validated-artifact" in workflow
    assert "steps.validated-artifact.outputs.artifact-id" in workflow
    assert "steps.validated-artifact.outputs.artifact-digest" in workflow
    assert "VALIDATED_REPOSITORY_ID: ${{ github.repository_id }}" in workflow
    assert "artifact digest: `sha256:{artifact_digest}`" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "steps.release-trust.outputs.main_bundle_artifact_id" in workflow
    assert "--bundle-dir evidence/main-bundle" in workflow
    assert "--require-archives" in workflow
    assert '"backend.oci.tar", "frontend.oci.tar"' in workflow
    assert "Attach and verify exact OCI archives for deployment" in workflow
    assert 'source_root = Path("evidence/main-bundle")' in workflow
    assert 'destination_root = Path("artifacts")' in workflow
    assert "unsafe final OCI archive handoff" in workflow
    assert "for name in sorted(provenance_names):" in workflow
    authorize_index = workflow.index("Issue short-lived digest-bound deployment authorization")
    attach_index = workflow.index("Attach and verify exact OCI archives for deployment")
    upload_index = workflow.index("Preserve the validated evidence and authorization bundle")
    assert authorize_index < attach_index < upload_index


def test_github_client_download_uses_https_redirect_without_forwarding_token() -> None:
    module = _load_module()
    payload = b"verified archive bytes"

    class Response:
        status = 200

        def __init__(self) -> None:
            self.headers = {"Content-Length": str(len(payload))}
            self.stream = io.BytesIO(payload)

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return self.stream.read(size)

        def geturl(self) -> str:
            return "https://objects.example.test/artifact.zip"

    class Opener:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, request: object, *, timeout: int) -> object:
            del timeout
            self.calls += 1
            if self.calls == 1:
                assert request.get_header("Authorization") == "Bearer token"
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "Found",
                    {"Location": "https://objects.example.test/artifact.zip"},
                    None,
                )
            assert request.get_header("Authorization") is None
            return Response()

    client = module.GitHubClient(token="token")
    opener = Opener()
    client._opener = opener
    destination = io.BytesIO()

    snapshot = client.download(
        f"/repos/{TEST_REPOSITORY}/actions/artifacts/{TEST_ARTIFACT_ID}/zip",
        destination,
        maximum_bytes=1024,
    )

    assert destination.getvalue() == payload
    assert snapshot.sha256 == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert opener.calls == 2
    with pytest.raises(module.TrustError, match="API path is invalid"):
        client.download(
            "https://attacker.invalid/artifact.zip",
            io.BytesIO(),
            maximum_bytes=1024,
        )
