from __future__ import annotations

import base64
import copy
import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
REPOSITORY = "example/knowledge-uploader"
GIT_SHA = "a" * 40
WORKFLOW_RUN_ID = 702
WORKFLOW_RUN_ATTEMPT = 4
NONCE = "N" * 32
APP_ENDPOINT_HASH = "b" * 64
APP_SPKI_HASH = "c" * 64
BUNDLE_DIGEST = "sha256:" + "d" * 64
DEPLOYMENT_HASH = "e" * 64


def _load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/verify_application_deployment_attestation.py"
    spec = importlib.util.spec_from_file_location(
        "verify_application_deployment_attestation",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load application deployment attestation verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verifier() -> ModuleType:
    return _load_module()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _public_key(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return _b64url(raw)


def _policy(
    private_key: Ed25519PrivateKey,
    *,
    key_id: str = "test-only-application-deployment-owner",
    max_age: int = 900,
) -> dict[str, object]:
    return {
        "schema": "knowledge-uploader.application-deployment-owner-trust-policy.v1",
        "version": 1,
        "policy_id": "test-only-application-deployment-owner-policy-v1",
        "max_attestation_lifetime_seconds": 900,
        "max_attestation_age_seconds": max_age,
        "keys": [
            {
                "key_id": key_id,
                "algorithm": "Ed25519",
                "public_key_base64url": _public_key(private_key),
                "owner_role": "application_deployment_owner",
                "environment": "test",
                "repository": REPOSITORY,
                "permissions": ["confirm.application-deployment"],
                "not_before": "2020-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ],
    }


def _text(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "owner_role": "application_deployment_owner",
        "permission": "confirm.application-deployment",
        "environment": "test",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "workflow_run_attempt": WORKFLOW_RUN_ATTEMPT,
        "nonce": NONCE,
        "app_endpoint_identity_sha256": APP_ENDPOINT_HASH,
        "app_tls_spki_sha256": APP_SPKI_HASH,
        "main_ci_run_id": 101,
        "main_ci_run_attempt": 2,
        "main_bundle_artifact_id": 901,
        "main_bundle_artifact_digest": BUNDLE_DIGEST,
        "deployment_identity_sha256": DEPLOYMENT_HASH,
        "artifact_deployed": True,
        "issued_at": "2026-07-18T07:55:00Z",
        "not_before": "2026-07-18T07:55:00Z",
        "expires_at": "2026-07-18T08:10:00Z",
    }
    payload.update(overrides)
    return payload


def _attestation(
    verifier: ModuleType,
    private_key: Ed25519PrivateKey,
    *,
    payload: dict[str, object] | None = None,
    key_id: str = "test-only-application-deployment-owner",
) -> dict[str, object]:
    signed: dict[str, object] = {
        "schema": "knowledge-uploader.application-deployment-owner-attestation.v1",
        "version": 1,
        "algorithm": "Ed25519",
        "key_id": key_id,
        "payload": _payload() if payload is None else payload,
    }
    signature = private_key.sign(verifier.canonical_signed_bytes(signed))
    return {**signed, "signature": _b64url(signature)}


def _expected(verifier: ModuleType) -> object:
    return verifier.ExpectedDeploymentContext(
        environment="test",
        repository=REPOSITORY,
        git_sha=GIT_SHA,
        workflow_run_id=WORKFLOW_RUN_ID,
        workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
        nonce=NONCE,
        app_endpoint_identity_sha256=APP_ENDPOINT_HASH,
        app_tls_spki_sha256=APP_SPKI_HASH,
        main_ci_run_id=101,
        main_ci_run_attempt=2,
        main_bundle_artifact_id=901,
        main_bundle_artifact_digest=BUNDLE_DIGEST,
        deployment_identity_sha256=DEPLOYMENT_HASH,
    )


def _assert_error(
    verifier: ModuleType,
    code: str,
    callback: Callable[[], object],
) -> None:
    with pytest.raises(verifier.DeploymentAttestationVerificationError) as caught:
        callback()
    assert caught.value.code == code
    assert str(caught.value) == code


def test_valid_owner_confirmation_binds_exact_deployment_and_main_bundle(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()

    verifier.verify_application_deployment_attestation(
        _attestation(verifier, private_key),
        _policy(private_key),
        expected=_expected(verifier),
        now=NOW,
    )


def test_canonical_form_is_order_independent_and_signature_covers_key_id(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key)
    signed = {key: attestation[key] for key in verifier.SIGNED_FIELDS}
    reordered = dict(reversed(list(signed.items())))

    assert verifier.canonical_signed_bytes(signed) == verifier.canonical_signed_bytes(reordered)

    forged = copy.deepcopy(attestation)
    forged["key_id"] = "test-only-replacement-deployment-owner"
    _assert_error(
        verifier,
        "signature_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            forged,
            _policy(private_key, key_id="test-only-replacement-deployment-owner"),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    ("field", "different"),
    [
        ("environment", "staging"),
        ("repository", "other/knowledge-uploader"),
        ("git_sha", "1" * 40),
        ("workflow_run_id", WORKFLOW_RUN_ID + 1),
        ("workflow_run_attempt", WORKFLOW_RUN_ATTEMPT + 1),
        ("nonce", "R" * 32),
        ("app_endpoint_identity_sha256", "1" * 64),
        ("app_tls_spki_sha256", "2" * 64),
        ("main_ci_run_id", 102),
        ("main_ci_run_attempt", 3),
        ("main_bundle_artifact_id", 902),
        ("main_bundle_artifact_digest", "sha256:" + "3" * 64),
        ("deployment_identity_sha256", "4" * 64),
    ],
)
def test_rejects_cross_context_and_exact_artifact_replay(
    verifier: ModuleType, field: str, different: object
) -> None:
    private_key = Ed25519PrivateKey.generate()
    expected = replace(_expected(verifier), **{field: different})

    _assert_error(
        verifier,
        "context_mismatch",
        lambda: verifier.verify_application_deployment_attestation(
            _attestation(verifier, private_key),
            _policy(private_key),
            expected=expected,
            now=NOW,
        ),
    )


def test_endpoint_owner_attestation_and_policy_cannot_be_substituted(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key)

    wrong_schema = copy.deepcopy(attestation)
    wrong_schema["schema"] = "knowledge-uploader.endpoint-owner-attestation.v1"
    _assert_error(
        verifier,
        "attestation_schema_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            wrong_schema,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )

    endpoint_policy = json.loads(
        (
            Path(__file__).parents[1] / "policies/endpoint-owner-attestation-policy.v1.example.json"
        ).read_text(encoding="utf-8")
    )
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            attestation,
            endpoint_policy,
            expected=_expected(verifier),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("owner_role", "ragflow_endpoint_owner", "deployment_authority_invalid"),
        ("permission", "confirm.endpoint", "deployment_authority_invalid"),
        ("artifact_deployed", False, "deployment_binding_invalid"),
        ("deployment_identity_sha256", "https://deployment.example", "deployment_binding_invalid"),
        ("main_bundle_artifact_digest", "sha256:UPPER", "artifact_binding_invalid"),
    ],
)
def test_rejects_wrong_authority_or_unconfirmed_deployment(
    verifier: ModuleType, field: str, value: object, code: str
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key)
    forged = copy.deepcopy(valid)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    payload[field] = value

    _assert_error(
        verifier,
        code,
        lambda: verifier.verify_application_deployment_attestation(
            forged,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


@pytest.mark.parametrize("field", ["workflow_run_id", "workflow_run_attempt"])
@pytest.mark.parametrize("value", [0, -1, 1.0, True, "702"])
def test_live_workflow_binding_requires_positive_builtin_integers(
    verifier: ModuleType,
    field: str,
    value: object,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key)
    invalid_payload = copy.deepcopy(attestation)
    payload = invalid_payload["payload"]
    assert isinstance(payload, dict)
    payload[field] = value

    _assert_error(
        verifier,
        "workflow_binding_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            invalid_payload,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )

    invalid_expected = replace(_expected(verifier), **{field: value})
    _assert_error(
        verifier,
        "expected_context_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            attestation,
            _policy(private_key),
            expected=invalid_expected,
            now=NOW,
        ),
    )


@pytest.mark.parametrize("value", [0, -1, 1.0, True, "101"])
def test_artifact_run_identifiers_require_positive_builtin_integers(
    verifier: ModuleType, value: object
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key)
    forged = copy.deepcopy(valid)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    payload["main_ci_run_id"] = value

    _assert_error(
        verifier,
        "artifact_binding_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            forged,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


def test_rejects_float_versions_and_unhashable_payload_types(verifier: ModuleType) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key)

    float_version = copy.deepcopy(valid)
    float_version["version"] = 1.0
    _assert_error(
        verifier,
        "attestation_schema_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            float_version,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )

    float_policy = _policy(private_key)
    float_policy["version"] = 1.0
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            valid,
            float_policy,
            expected=_expected(verifier),
            now=NOW,
        ),
    )

    unhashable = copy.deepcopy(valid)
    payload = unhashable["payload"]
    assert isinstance(payload, dict)
    payload["repository"] = []
    _assert_error(
        verifier,
        "payload_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            unhashable,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


def test_rejects_algorithm_confusion_untrusted_key_and_noncanonical_signature(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key)
    policy = _policy(private_key)

    confused = copy.deepcopy(valid)
    confused["algorithm"] = "HS256"
    _assert_error(
        verifier,
        "algorithm_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            confused, policy, expected=_expected(verifier), now=NOW
        ),
    )

    unknown = copy.deepcopy(valid)
    unknown["key_id"] = "unknown-deployment-owner"
    _assert_error(
        verifier,
        "untrusted_key",
        lambda: verifier.verify_application_deployment_attestation(
            unknown, policy, expected=_expected(verifier), now=NOW
        ),
    )

    padded = copy.deepcopy(valid)
    padded["signature"] = str(padded["signature"]) + "="
    _assert_error(
        verifier,
        "signature_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            padded, policy, expected=_expected(verifier), now=NOW
        ),
    )


def test_rejects_key_substitution_duplicate_keys_and_permission_broadening(
    verifier: ModuleType,
) -> None:
    signer = Ed25519PrivateKey.generate()
    replacement = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, signer)

    _assert_error(
        verifier,
        "signature_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            attestation,
            _policy(replacement),
            expected=_expected(verifier),
            now=NOW,
        ),
    )

    duplicate = _policy(signer)
    keys = duplicate["keys"]
    assert isinstance(keys, list)
    duplicate_key = copy.deepcopy(keys[0])
    assert isinstance(duplicate_key, dict)
    duplicate_key["key_id"] = "test-only-duplicate-deployment-owner"
    keys.append(duplicate_key)
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            attestation,
            duplicate,
            expected=_expected(verifier),
            now=NOW,
        ),
    )

    broadened = _policy(signer)
    broadened_keys = broadened["keys"]
    assert isinstance(broadened_keys, list)
    key = broadened_keys[0]
    assert isinstance(key, dict)
    key["permissions"] = ["confirm.application-deployment", "confirm.endpoint"]
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            attestation,
            broadened,
            expected=_expected(verifier),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    "times",
    [
        {
            "issued_at": "2026-07-18T07:45:00Z",
            "not_before": "2026-07-18T07:45:00Z",
            "expires_at": "2026-07-18T08:00:00Z",
        },
        {
            "issued_at": "2026-07-18T08:01:00Z",
            "not_before": "2026-07-18T08:01:00Z",
            "expires_at": "2026-07-18T08:10:00Z",
        },
        {
            "issued_at": "2026-07-18T08:00:00Z",
            "not_before": "2026-07-18T08:01:00Z",
            "expires_at": "2026-07-18T08:10:00Z",
        },
        {
            "issued_at": "2026-07-18T07:40:00Z",
            "not_before": "2026-07-18T07:40:00Z",
            "expires_at": "2026-07-18T08:10:00Z",
        },
    ],
)
def test_rejects_expired_future_and_overlong_confirmations(
    verifier: ModuleType, times: dict[str, object]
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, payload=_payload(**times))

    _assert_error(
        verifier,
        "time_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            attestation,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


def test_rejects_stale_confirmation_even_before_expiry(verifier: ModuleType) -> None:
    private_key = Ed25519PrivateKey.generate()

    _assert_error(
        verifier,
        "time_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            _attestation(verifier, private_key),
            _policy(private_key, max_age=60),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


@pytest.mark.parametrize("field", ["endpoint_url", "api_key", "raw_response", "image_tag"])
def test_unknown_url_secret_or_raw_response_fields_are_rejected(
    verifier: ModuleType, field: str
) -> None:
    private_key = Ed25519PrivateKey.generate()
    forged = _attestation(verifier, private_key)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    payload[field] = "https://private.example/?token=super-secret"

    _assert_error(
        verifier,
        "attestation_schema_invalid",
        lambda: verifier.verify_application_deployment_attestation(
            forged,
            _policy(private_key),
            expected=_expected(verifier),
            now=NOW,
        ),
    )


def test_file_loader_rejects_duplicate_keys_deep_json_and_oversize(
    verifier: ModuleType, tmp_path: Path
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * verifier.MAX_ATTESTATION_BYTES + b"}")

    for path in (duplicate, deep, oversized):
        _assert_error(
            verifier,
            "input_invalid",
            lambda path=path: verifier._load_json(
                path,
                max_bytes=verifier.MAX_ATTESTATION_BYTES,
            ),
        )


def test_file_loader_rejects_symlinks(verifier: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable to this test user")

    _assert_error(
        verifier,
        "input_invalid",
        lambda: verifier._load_json(link, max_bytes=verifier.MAX_ATTESTATION_BYTES),
    )


def test_stable_reader_rejects_descriptor_metadata_mutation(
    verifier: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mutable.json"
    path.write_text("{}\n", encoding="utf-8")
    real_fstat = verifier.os.fstat
    calls = 0

    def mutated_fstat(descriptor: int) -> object:
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

    monkeypatch.setattr(verifier.os, "fstat", mutated_fstat)
    _assert_error(
        verifier,
        "input_invalid",
        lambda: verifier._load_json(path, max_bytes=verifier.MAX_ATTESTATION_BYTES),
    )


def _cli_args(attestation_path: Path, policy_path: Path) -> list[str]:
    return [
        "--attestation",
        str(attestation_path),
        "--policy",
        str(policy_path),
        "--environment",
        "test",
        "--repository",
        REPOSITORY,
        "--git-sha",
        GIT_SHA,
        "--workflow-run-id",
        str(WORKFLOW_RUN_ID),
        "--workflow-run-attempt",
        str(WORKFLOW_RUN_ATTEMPT),
        "--nonce",
        NONCE,
        "--app-endpoint-identity-sha256",
        APP_ENDPOINT_HASH,
        "--app-tls-spki-sha256",
        APP_SPKI_HASH,
        "--main-ci-run-id",
        "101",
        "--main-ci-run-attempt",
        "2",
        "--main-bundle-artifact-id",
        "901",
        "--main-bundle-artifact-digest",
        BUNDLE_DIGEST,
        "--deployment-identity-sha256",
        DEPLOYMENT_HASH,
    ]


def test_cli_accepts_valid_files_and_outputs_only_generic_success(
    verifier: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_key = Ed25519PrivateKey.generate()
    instant = datetime.now(UTC).replace(microsecond=0)
    payload = _payload(
        issued_at=_text(instant - timedelta(seconds=5)),
        not_before=_text(instant - timedelta(seconds=5)),
        expires_at=_text(instant + timedelta(minutes=5)),
    )
    attestation_path = tmp_path / "attestation.json"
    policy_path = tmp_path / "policy.json"
    attestation_path.write_text(
        json.dumps(_attestation(verifier, private_key, payload=payload)),
        encoding="utf-8",
    )
    policy_path.write_text(json.dumps(_policy(private_key)), encoding="utf-8")

    assert verifier.main(_cli_args(attestation_path, policy_path)) == 0
    captured = capsys.readouterr()
    assert captured.out == "application deployment attestation verified\n"
    assert captured.err == ""


def test_cli_failure_never_echoes_arguments_or_sensitive_payload(
    verifier: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "https://private.example/?token=super-secret"
    assert verifier.main(["--repository", secret]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "application deployment attestation verification failed: arguments_invalid\n"
    )
    assert secret not in captured.err

    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key)
    payload = attestation["payload"]
    assert isinstance(payload, dict)
    payload["raw_response"] = secret
    attestation_path = tmp_path / "bad-attestation.json"
    policy_path = tmp_path / "policy.json"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    policy_path.write_text(json.dumps(_policy(private_key)), encoding="utf-8")

    assert verifier.main(_cli_args(attestation_path, policy_path)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "application deployment attestation verification failed: attestation_schema_invalid\n"
    )
    assert secret not in captured.err
    assert "super-secret" not in captured.err


def test_example_policy_and_guidance_are_test_only_and_exclude_private_keys(
    verifier: ModuleType,
) -> None:
    root = Path(__file__).parents[1] / "policies"
    policy_path = root / "application-deployment-owner-policy.v1.example.json"
    guide_path = root / "application-deployment-owner-attestation.v1.example.md"
    policy_raw = policy_path.read_text(encoding="utf-8")
    guide = guide_path.read_text(encoding="utf-8")
    policy = json.loads(policy_raw)

    verifier._parse_policy(policy)
    assert policy["policy_id"].startswith("test-only-")
    key = policy["keys"][0]
    assert key["key_id"].startswith("test-only-")
    assert key["owner_role"] == "application_deployment_owner"
    assert key["environment"] == "test"
    assert key["repository"] == REPOSITORY
    assert "private_key" not in policy_raw.lower()
    assert "secret" not in policy_raw.lower()
    assert "independently observe the deployment platform" in guide
    assert "runner-self-reported" in guide
    assert "does not prove" in guide
    assert "must retain the signed attestation JSON" in guide
    assert "public-key policy" in guide
    assert "read-only public-key policy supplied by the" in guide
    assert "APPLICATION_DEPLOYMENT_OWNER_POLICY_PATH" in guide
    assert "APPLICATION_DEPLOYMENT_OWNER_POLICY_SHA256" in guide
    assert "independently pins the exact" in guide
    assert '"workflow_run_id"' in guide
    assert '"workflow_run_attempt"' in guide
    assert "No production trust key or policy is stored" in guide
    assert "ops/policies/application-deployment-owner-policy.v1.json" not in guide
    assert "must never contain the signing private key" in guide
    assert "deployment control-plane/OCI responses" in guide
