from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import collect_ragflow_live_evidence as collector
from scripts import ragflow_live_evidence_contract as contract
from scripts import run_ragflow_live_evidence as live
from scripts import verify_application_deployment_attestation as deployment_verifier
from scripts import verify_endpoint_owner_attestation as owner_verifier

REPOSITORY = "example/knowledge-uploader"
GIT_SHA = "a" * 40
NONCE = "N" * 32
DEPLOYMENT_IDENTITY = "d" * 64
APP_ENDPOINT_HASH = "1" * 64
APP_SPKI_HASH = "2" * 64
RAGFLOW_ENDPOINT_HASH = "3" * 64
RAGFLOW_SPKI_HASH = "4" * 64
DATASET_HASH = "5" * 64
MAPPING_HASH = "6" * 64
CATEGORY_HASH = "7" * 64
BUNDLE_DIGEST = "sha256:" + "8" * 64


def _probe(
    *,
    owner_attestation_sha256: str = "9" * 64,
    owner_policy_sha256: str = "a" * 64,
    deployment_attestation_sha256: str = "b" * 64,
    deployment_policy_sha256: str = "c" * 64,
    trust_sha256: str = "d" * 64,
) -> dict[str, object]:
    return {
        "schema": contract.PROBE_SCHEMA,
        "version": 1,
        "requirement_id": contract.REQUIREMENT_ID,
        "verdict": "ready",
        "evidence_kind": "real_external_service",
        "probe_mode": "preseeded_remote_reconciliation",
        "network_timeout_simulation": False,
        "fault_injection": False,
        "environment": "staging",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "workflow": {
            "path": contract.WORKFLOW_PATH,
            "run_id": 2001,
            "run_attempt": 1,
        },
        "main_ci": {
            "run_id": 1001,
            "run_attempt": 2,
            "bundle_artifact_id": 99,
            "bundle_artifact_digest": BUNDLE_DIGEST,
        },
        "trust": {"workflow_trust_sha256": trust_sha256},
        "owner_attestation": {
            "attestation_sha256": owner_attestation_sha256,
            "policy_sha256": owner_policy_sha256,
            "nonce_sha256": hashlib.sha256(NONCE.encode()).hexdigest(),
        },
        "deployment_attestation": {
            "attestation_sha256": deployment_attestation_sha256,
            "policy_sha256": deployment_policy_sha256,
            "deployment_identity_sha256": DEPLOYMENT_IDENTITY,
        },
        "identities": {
            "endpoint_identity_sha256": RAGFLOW_ENDPOINT_HASH,
            "tls_spki_sha256": RAGFLOW_SPKI_HASH,
            "dataset_identity_sha256": DATASET_HASH,
            "dataset_mapping_id_sha256": MAPPING_HASH,
            "category_id_sha256": CATEGORY_HASH,
            "app_endpoint_identity_sha256": APP_ENDPOINT_HASH,
            "app_tls_spki_sha256": APP_SPKI_HASH,
            "app_file_id_sha256": "e" * 64,
            "remote_name_sha256": "f" * 64,
            "remote_document_id_sha256": "0" * 64,
            "first_task_id_sha256": "1" * 64,
            "repeat_task_id_sha256": "2" * 64,
            "delete_task_id_sha256": "3" * 64,
        },
        "stages": {
            "initial_dataset": {"dataset_total": 0, "exact_name_count": 0},
            "preseed": {
                "dataset_total": 1,
                "exact_name_count": 1,
                "remote_id_match": True,
                "commit_observed": True,
            },
            "first_sync": {
                "task_type": "ragflow_upload",
                "task_status": "succeeded",
                "app_file_status": "parsed",
                "app_parse_status": "DONE",
                "dataset_total": 1,
                "exact_name_count": 1,
                "remote_id_match": True,
                "reconciliation_log_observed": True,
                "remote_upload_log_observed": False,
                "parse_start_log_observed": True,
            },
            "repeat_sync": {
                "request_mode": "new_task",
                "task_type": "ragflow_status_check",
                "task_status": "succeeded",
                "app_file_status": "parsed",
                "app_parse_status": "DONE",
                "dataset_total": 1,
                "exact_name_count": 1,
                "remote_id_match": True,
                "reconciliation_log_observed": False,
                "remote_upload_log_observed": False,
                "parse_start_log_observed": False,
            },
            "parse": {
                "app_terminal": True,
                "remote_terminal": True,
                "remote_run": "DONE",
                "task_terminal": True,
            },
            "application_delete": {
                "requested": True,
                "delete_task_status": "succeeded",
                "dataset_total": 0,
                "exact_name_count": 0,
                "confirmed": True,
            },
        },
        "cleanup": {
            "application_cleanup_confirmed": True,
            "emergency_direct_cleanup_used": False,
            "dataset_total": 0,
            "exact_name_count": 0,
            "confirmed": True,
        },
        "started_at": "2026-07-18T08:00:00Z",
        "finished_at": "2026-07-18T08:01:00Z",
    }


def _janitor(probe: dict[str, object]) -> dict[str, object]:
    identities = probe["identities"]
    assert isinstance(identities, dict)
    return {
        "schema": contract.JANITOR_SCHEMA,
        "version": 1,
        **{
            field: copy.deepcopy(probe[field])
            for field in (
                "environment",
                "repository",
                "git_sha",
                "workflow",
                "main_ci",
                "trust",
                "owner_attestation",
                "deployment_attestation",
            )
        },
        "identities": {
            field: identities[field]
            for field in (
                "endpoint_identity_sha256",
                "tls_spki_sha256",
                "dataset_identity_sha256",
                "app_endpoint_identity_sha256",
                "app_tls_spki_sha256",
            )
        }
        | {"canary_filename_sha256": "4" * 64},
        "cleanup": {
            "app_candidates_seen": 0,
            "app_delete_requests": 0,
            "remote_candidates_seen": 0,
            "remote_delete_requests": 0,
            "dataset_total": 0,
            "canary_remote_count": 0,
            "confirmed": True,
        },
        "started_at": "2026-07-18T08:02:00Z",
        "finished_at": "2026-07-18T08:03:00Z",
    }


def test_contract_accepts_only_full_real_idempotent_cleanup_chain() -> None:
    probe = _probe()
    janitor = _janitor(probe)

    evidence = contract.collect_evidence(
        probe,
        janitor,
        probe_sha256="5" * 64,
        janitor_sha256="6" * 64,
        expected_repository=REPOSITORY,
        expected_git_sha=GIT_SHA,
        expected_environment="staging",
        expected_run_id=2001,
        expected_run_attempt=1,
        expected_main_run_id=1001,
        expected_main_run_attempt=2,
    )

    assert evidence["verdict"] == "ready"
    assert evidence["requirement_id"] == "EXT-RAGFLOW-001"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("stages", "initial_dataset", "dataset_total"), 1),
        (("stages", "preseed", "exact_name_count"), 2),
        (("stages", "repeat_sync", "request_mode"), "reused_task"),
        (("stages", "repeat_sync", "task_type"), "ragflow_upload"),
        (("stages", "repeat_sync", "remote_upload_log_observed"), True),
        (("stages", "repeat_sync", "parse_start_log_observed"), True),
        (("stages", "parse", "remote_run"), "RUNNING"),
        (("cleanup", "emergency_direct_cleanup_used"), True),
        (("cleanup", "dataset_total"), 1),
    ],
)
def test_contract_rejects_pollution_reuse_fake_parse_and_unconfirmed_cleanup(
    path: tuple[str, ...], value: object
) -> None:
    probe = _probe()
    cursor: dict[str, object] = probe
    for key in path[:-1]:
        nested = cursor[key]
        assert isinstance(nested, dict)
        cursor = nested
    cursor[path[-1]] = value

    with pytest.raises(contract.EvidenceContractError):
        contract.validate_probe(probe)


def test_contract_rejects_task_identity_reuse_and_janitor_replay() -> None:
    probe = _probe()
    identities = probe["identities"]
    assert isinstance(identities, dict)
    identities["repeat_task_id_sha256"] = identities["first_task_id_sha256"]
    with pytest.raises(contract.EvidenceContractError, match="identities_invalid"):
        contract.validate_probe(probe)

    valid = _probe()
    janitor = _janitor(valid)
    janitor["deployment_attestation"] = {
        "attestation_sha256": "0" * 64,
        "policy_sha256": "c" * 64,
        "deployment_identity_sha256": DEPLOYMENT_IDENTITY,
    }
    with pytest.raises(contract.EvidenceContractError, match="collector_binding_mismatch"):
        contract.collect_evidence(
            valid,
            janitor,
            probe_sha256="5" * 64,
            janitor_sha256="6" * 64,
            expected_repository=REPOSITORY,
            expected_git_sha=GIT_SHA,
            expected_environment="staging",
            expected_run_id=2001,
            expected_run_attempt=1,
            expected_main_run_id=1001,
            expected_main_run_attempt=2,
        )


def test_stable_reader_rejects_symlink_and_metadata_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8", newline="\n")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(contract.EvidenceContractError, match="input_invalid"):
        contract.read_json(link)

    real_fstat = contract.os.fstat
    calls = 0

    def changed_fstat(descriptor: int) -> object:
        nonlocal calls
        calls += 1
        result = real_fstat(descriptor)
        if calls != 2:
            return result
        return SimpleNamespace(
            st_mode=result.st_mode,
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_size=result.st_size,
            st_mtime_ns=result.st_mtime_ns + 1,
        )

    monkeypatch.setattr(contract.os, "fstat", changed_fstat)
    with pytest.raises(contract.EvidenceContractError, match="input_invalid"):
        contract.read_json(target)


def test_workflow_trust_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    trust = tmp_path / "release-workflow-trust.json"
    trust.write_text("{}\n", encoding="utf-8", newline="\n")
    trust.with_suffix(".json.sha256").write_text(
        f"{'0' * 64}  {trust.name}\n", encoding="utf-8", newline="\n"
    )
    context = live.RunContext(
        environment="staging",
        repository=REPOSITORY,
        git_sha=GIT_SHA,
        run_id=2001,
        run_attempt=1,
        main_run_id=1001,
        main_run_attempt=2,
        nonce=NONCE,
        workflow_trust_path=trust,
        owner_attestation_path=tmp_path / "owner.json",
        owner_policy_path=tmp_path / "owner-policy.json",
        owner_policy_sha256="1" * 64,
        deployment_attestation_path=tmp_path / "deployment.json",
        deployment_policy_path=tmp_path / "deployment-policy.json",
        deployment_policy_sha256="2" * 64,
        deployment_identity_sha256=DEPLOYMENT_IDENTITY,
        timeout_seconds=480,
    )
    with pytest.raises(live.LiveProbeError, match="workflow_trust_checksum_invalid"):
        live._load_trust_binding(context)


@pytest.mark.asyncio
async def test_protected_app_client_executes_with_explicit_system_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    async def authorize(**kwargs: object) -> object:
        seen.append(kwargs["resolver"])
        return object()

    monkeypatch.setattr(live, "resolve_and_authorize_ragflow_endpoint", authorize)
    monkeypatch.setattr(
        live,
        "build_pinned_ragflow_transport",
        lambda _endpoint: httpx.MockTransport(lambda _request: httpx.Response(200)),
    )
    async with live.ProtectedAppClient(
        base_url="https://app.internal.example",
        tls_pin=bytes(range(32)),
    ):
        pass

    assert len(seen) == 1
    assert isinstance(seen[0], live.SystemHostResolver)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _time(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public_key(key: Ed25519PrivateKey) -> str:
    return _b64url(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )


def _signed_documents() -> tuple[dict[str, object], ...]:
    now = datetime.now(UTC)
    issued = _time(now - timedelta(seconds=30))
    expires = _time(now + timedelta(minutes=10))
    key_start = _time(now - timedelta(days=1))
    key_end = _time(now + timedelta(days=1))

    owner_key = Ed25519PrivateKey.generate()
    owner_payload: dict[str, object] = {
        "service_kind": "ragflow",
        "environment": "staging",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "endpoint_identity_sha256": RAGFLOW_ENDPOINT_HASH,
        "tls_spki_sha256": RAGFLOW_SPKI_HASH,
        "nonce": NONCE,
        "workflow_run_id": 2001,
        "workflow_run_attempt": 1,
        "issued_at": issued,
        "not_before": issued,
        "expires_at": expires,
        "allowed_operations": list(owner_verifier.RAGFLOW_OPERATIONS),
        "dataset_identity_sha256": DATASET_HASH,
        "dataset_isolated": True,
        "dataset_initially_empty": True,
    }
    owner_signed = {
        "schema": owner_verifier.ATTESTATION_SCHEMA,
        "version": 1,
        "algorithm": "Ed25519",
        "key_id": "test-ragflow-owner",
        "payload": owner_payload,
    }
    owner_attestation = {
        **owner_signed,
        "signature": _b64url(owner_key.sign(owner_verifier.canonical_signed_bytes(owner_signed))),
    }
    owner_policy = {
        "schema": owner_verifier.POLICY_SCHEMA,
        "version": 1,
        "policy_id": "test-ragflow-owner-policy",
        "max_attestation_lifetime_seconds": 900,
        "max_attestation_age_seconds": 900,
        "keys": [
            {
                "key_id": "test-ragflow-owner",
                "algorithm": "Ed25519",
                "public_key_base64url": _public_key(owner_key),
                "service_kind": "ragflow",
                "environment": "staging",
                "repository": REPOSITORY,
                "allowed_operations": list(owner_verifier.RAGFLOW_OPERATIONS),
                "not_before": key_start,
                "expires_at": key_end,
            }
        ],
    }

    deployment_key = Ed25519PrivateKey.generate()
    deployment_payload: dict[str, object] = {
        "owner_role": deployment_verifier.OWNER_ROLE,
        "permission": deployment_verifier.DEPLOYMENT_PERMISSION,
        "environment": "staging",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "nonce": NONCE,
        "workflow_run_id": 2001,
        "workflow_run_attempt": 1,
        "app_endpoint_identity_sha256": APP_ENDPOINT_HASH,
        "app_tls_spki_sha256": APP_SPKI_HASH,
        "main_ci_run_id": 1001,
        "main_ci_run_attempt": 2,
        "main_bundle_artifact_id": 99,
        "main_bundle_artifact_digest": BUNDLE_DIGEST,
        "deployment_identity_sha256": DEPLOYMENT_IDENTITY,
        "artifact_deployed": True,
        "issued_at": issued,
        "not_before": issued,
        "expires_at": expires,
    }
    deployment_signed = {
        "schema": deployment_verifier.ATTESTATION_SCHEMA,
        "version": 1,
        "algorithm": "Ed25519",
        "key_id": "test-deployment-owner",
        "payload": deployment_payload,
    }
    deployment_attestation = {
        **deployment_signed,
        "signature": _b64url(
            deployment_key.sign(deployment_verifier.canonical_signed_bytes(deployment_signed))
        ),
    }
    deployment_policy = {
        "schema": deployment_verifier.POLICY_SCHEMA,
        "version": 1,
        "policy_id": "test-deployment-owner-policy",
        "max_attestation_lifetime_seconds": 900,
        "max_attestation_age_seconds": 900,
        "keys": [
            {
                "key_id": "test-deployment-owner",
                "algorithm": "Ed25519",
                "public_key_base64url": _public_key(deployment_key),
                "owner_role": deployment_verifier.OWNER_ROLE,
                "environment": "staging",
                "repository": REPOSITORY,
                "permissions": [deployment_verifier.DEPLOYMENT_PERMISSION],
                "not_before": key_start,
                "expires_at": key_end,
            }
        ],
    }
    return owner_attestation, owner_policy, deployment_attestation, deployment_policy


def _write_json(path: Path, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_collector_independently_reverifies_and_archives_all_public_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_attestation, owner_policy, deployment_attestation, deployment_policy = _signed_documents()
    paths = {
        "owner_attestation": tmp_path / "owner-attestation.json",
        "owner_policy": tmp_path / "owner-policy.json",
        "deployment_attestation": tmp_path / "deployment-attestation.json",
        "deployment_policy": tmp_path / "deployment-policy.json",
        "trust": tmp_path / collector.TRUST_FILENAME,
    }
    owner_attestation_digest = _write_json(paths["owner_attestation"], owner_attestation)
    owner_policy_digest = _write_json(paths["owner_policy"], owner_policy)
    deployment_attestation_digest = _write_json(
        paths["deployment_attestation"], deployment_attestation
    )
    deployment_policy_digest = _write_json(paths["deployment_policy"], deployment_policy)
    trust_digest = _write_json(paths["trust"], {"source": "unit-test"})
    trust_checksum = paths["trust"].with_suffix(".json.sha256")
    trust_checksum.write_text(
        f"{trust_digest}  {paths['trust'].name}\n", encoding="utf-8", newline="\n"
    )
    probe = _probe(
        owner_attestation_sha256=owner_attestation_digest,
        owner_policy_sha256=owner_policy_digest,
        deployment_attestation_sha256=deployment_attestation_digest,
        deployment_policy_sha256=deployment_policy_digest,
        trust_sha256=trust_digest,
    )
    janitor = _janitor(probe)
    probe_path = tmp_path / "probe.json"
    janitor_path = tmp_path / "janitor.json"
    _write_json(probe_path, probe)
    _write_json(janitor_path, janitor)
    monkeypatch.setattr(
        collector,
        "validate_trust_summary",
        lambda *_args, **_kwargs: {
            "current": {
                "workflow_path": contract.WORKFLOW_PATH,
                "run_id": 2001,
                "run_attempt": 1,
            },
            "main_ci": {
                "run_id": 1001,
                "run_attempt": 2,
                "artifacts": {
                    "bundle": {"id": 99, "digest": BUNDLE_DIGEST},
                },
            },
        },
    )
    output = tmp_path / "public"
    result = collector.main(
        [
            "--probe",
            str(probe_path),
            "--janitor",
            str(janitor_path),
            "--repository",
            REPOSITORY,
            "--git-sha",
            GIT_SHA,
            "--environment",
            "staging",
            "--run-id",
            "2001",
            "--run-attempt",
            "1",
            "--main-run-id",
            "1001",
            "--main-run-attempt",
            "2",
            "--nonce",
            NONCE,
            "--deployment-identity-sha256",
            DEPLOYMENT_IDENTITY,
            "--owner-attestation",
            str(paths["owner_attestation"]),
            "--owner-policy",
            str(paths["owner_policy"]),
            "--owner-policy-sha256",
            owner_policy_digest,
            "--deployment-attestation",
            str(paths["deployment_attestation"]),
            "--deployment-policy",
            str(paths["deployment_policy"]),
            "--deployment-policy-sha256",
            deployment_policy_digest,
            "--workflow-trust",
            str(paths["trust"]),
            "--workflow-trust-checksum",
            str(trust_checksum),
            "--output-dir",
            str(output),
        ]
    )

    assert result == 0
    evidence = json.loads((output / collector.EVIDENCE_FILENAME).read_text(encoding="utf-8"))
    proof = evidence["proof"]
    assert isinstance(proof, dict)
    assert proof["owner_attestation"]["policy_sha256"] == owner_policy_digest
    assert proof["deployment_attestation"]["policy_sha256"] == deployment_policy_digest
    assert {item.name for item in output.iterdir()} == {
        collector.EVIDENCE_FILENAME,
        collector.EVIDENCE_FILENAME + ".sha256",
        collector.OWNER_ATTESTATION_FILENAME,
        collector.OWNER_POLICY_FILENAME,
        collector.DEPLOYMENT_ATTESTATION_FILENAME,
        collector.DEPLOYMENT_POLICY_FILENAME,
        collector.TRUST_FILENAME,
        collector.TRUST_CHECKSUM_FILENAME,
    }
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    for forbidden in (
        "https://private.internal.example",
        "Bearer-super-secret",
        "employee@example.com",
        "raw-control-plane-response",
        "private_key",
    ):
        assert forbidden not in serialized

    replay_output = tmp_path / "replay"
    replay_args = [
        "--probe",
        str(probe_path),
        "--janitor",
        str(janitor_path),
        "--repository",
        REPOSITORY,
        "--git-sha",
        GIT_SHA,
        "--environment",
        "staging",
        "--run-id",
        "2001",
        "--run-attempt",
        "1",
        "--main-run-id",
        "1001",
        "--main-run-attempt",
        "2",
        "--nonce",
        "R" * 32,
        "--deployment-identity-sha256",
        DEPLOYMENT_IDENTITY,
        "--owner-attestation",
        str(paths["owner_attestation"]),
        "--owner-policy",
        str(paths["owner_policy"]),
        "--owner-policy-sha256",
        owner_policy_digest,
        "--deployment-attestation",
        str(paths["deployment_attestation"]),
        "--deployment-policy",
        str(paths["deployment_policy"]),
        "--deployment-policy-sha256",
        deployment_policy_digest,
        "--workflow-trust",
        str(paths["trust"]),
        "--workflow-trust-checksum",
        str(trust_checksum),
        "--output-dir",
        str(replay_output),
    ]
    assert collector.main(replay_args) == 1
    assert not replay_output.exists()

    for flag in ("--owner-policy-sha256", "--deployment-policy-sha256"):
        forged_args = replay_args.copy()
        forged_args[forged_args.index("--nonce") + 1] = NONCE
        forged_args[forged_args.index(flag) + 1] = "0" * 64
        forged_output = tmp_path / f"forged-{flag.removeprefix('--')}"
        forged_args[forged_args.index("--output-dir") + 1] = str(forged_output)
        assert collector.main(forged_args) == 1
        assert not forged_output.exists()

    forged_documents = _signed_documents()
    forged_owner_attestation_path = tmp_path / "forged-owner-attestation.json"
    forged_owner_policy_path = tmp_path / "forged-owner-policy.json"
    forged_owner_attestation_digest = _write_json(
        forged_owner_attestation_path,
        forged_documents[0],
    )
    forged_owner_policy_digest = _write_json(forged_owner_policy_path, forged_documents[1])
    forged_probe = _probe(
        owner_attestation_sha256=forged_owner_attestation_digest,
        owner_policy_sha256=forged_owner_policy_digest,
        deployment_attestation_sha256=deployment_attestation_digest,
        deployment_policy_sha256=deployment_policy_digest,
        trust_sha256=trust_digest,
    )
    forged_probe_path = tmp_path / "forged-probe.json"
    forged_janitor_path = tmp_path / "forged-janitor.json"
    _write_json(forged_probe_path, forged_probe)
    _write_json(forged_janitor_path, _janitor(forged_probe))
    self_signed_args = replay_args.copy()
    for flag, value in (
        ("--probe", str(forged_probe_path)),
        ("--janitor", str(forged_janitor_path)),
        ("--nonce", NONCE),
        ("--owner-attestation", str(forged_owner_attestation_path)),
        ("--owner-policy", str(forged_owner_policy_path)),
        ("--output-dir", str(tmp_path / "forged-self-signed")),
    ):
        self_signed_args[self_signed_args.index(flag) + 1] = value
    assert collector.main(self_signed_args) == 1
    assert not (tmp_path / "forged-self-signed").exists()
