from __future__ import annotations

import base64
import copy
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
REPOSITORY = "example/knowledge-uploader"
GIT_SHA = "a" * 40
WORKFLOW_RUN_ID = 701
WORKFLOW_RUN_ATTEMPT = 3
ENDPOINT_HASH = "b" * 64
SPKI_HASH = "c" * 64
PROVIDER_HASH = "d" * 64
MODEL_HASH = "e" * 64
DATASET_HASH = "f" * 64
NONCE = "N" * 32


def _load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/verify_endpoint_owner_attestation.py"
    spec = importlib.util.spec_from_file_location("verify_endpoint_owner_attestation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load endpoint owner attestation verifier")
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


def _operations(service_kind: str) -> list[str]:
    if service_kind == "llm":
        return ["chat.completions.create"]
    return [
        "documents.list",
        "documents.upload",
        "documents.update",
        "documents.parse",
        "documents.delete",
    ]


def _policy(
    private_key: Ed25519PrivateKey,
    *,
    service_kind: str,
    key_id: str | None = None,
    max_age: int = 900,
) -> dict[str, object]:
    selected_key_id = key_id or f"test-only-{service_kind}-owner"
    return {
        "schema": "knowledge-uploader.endpoint-owner-trust-policy.v1",
        "version": 1,
        "policy_id": "test-only-endpoint-owner-policy-v1",
        "max_attestation_lifetime_seconds": 900,
        "max_attestation_age_seconds": max_age,
        "keys": [
            {
                "key_id": selected_key_id,
                "algorithm": "Ed25519",
                "public_key_base64url": _public_key(private_key),
                "service_kind": service_kind,
                "environment": "test",
                "repository": REPOSITORY,
                "allowed_operations": _operations(service_kind),
                "not_before": "2026-01-01T00:00:00Z",
                "expires_at": "2027-01-01T00:00:00Z",
            }
        ],
    }


def _payload(service_kind: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service_kind": service_kind,
        "environment": "test",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "workflow_run_attempt": WORKFLOW_RUN_ATTEMPT,
        "endpoint_identity_sha256": ENDPOINT_HASH,
        "tls_spki_sha256": SPKI_HASH,
        "nonce": NONCE,
        "issued_at": "2026-07-18T07:55:00Z",
        "not_before": "2026-07-18T07:55:00Z",
        "expires_at": "2026-07-18T08:10:00Z",
        "allowed_operations": _operations(service_kind),
    }
    if service_kind == "llm":
        payload.update(
            {
                "internal": True,
                "private": True,
                "nonbillable": True,
                "provider_identity_sha256": PROVIDER_HASH,
                "model_identity_sha256": MODEL_HASH,
                "zero_pricing_policy": {
                    "currency": "USD",
                    "unit": "million_tokens",
                    "input_price": 0,
                    "output_price": 0,
                },
            }
        )
    else:
        payload.update(
            {
                "dataset_identity_sha256": DATASET_HASH,
                "dataset_isolated": True,
                "dataset_initially_empty": True,
            }
        )
    payload.update(overrides)
    return payload


def _attestation(
    verifier: ModuleType,
    private_key: Ed25519PrivateKey,
    *,
    service_kind: str,
    payload: dict[str, object] | None = None,
    key_id: str | None = None,
) -> dict[str, object]:
    signed: dict[str, object] = {
        "schema": "knowledge-uploader.endpoint-owner-attestation.v1",
        "version": 1,
        "algorithm": "Ed25519",
        "key_id": key_id or f"test-only-{service_kind}-owner",
        "payload": _payload(service_kind) if payload is None else payload,
    }
    signature = private_key.sign(verifier.canonical_signed_bytes(signed))
    return {**signed, "signature": _b64url(signature)}


def _expected(verifier: ModuleType, service_kind: str) -> object:
    common = {
        "service_kind": service_kind,
        "environment": "test",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "workflow_run_attempt": WORKFLOW_RUN_ATTEMPT,
        "endpoint_identity_sha256": ENDPOINT_HASH,
        "tls_spki_sha256": SPKI_HASH,
        "nonce": NONCE,
    }
    if service_kind == "llm":
        return verifier.ExpectedContext(
            **common,
            provider_identity_sha256=PROVIDER_HASH,
            model_identity_sha256=MODEL_HASH,
        )
    return verifier.ExpectedContext(**common, dataset_identity_sha256=DATASET_HASH)


def _assert_error(verifier: ModuleType, code: str, callback: object) -> None:
    with pytest.raises(verifier.AttestationVerificationError) as caught:
        callback()
    assert caught.value.code == code
    assert str(caught.value) == code


@pytest.mark.parametrize("service_kind", ["llm", "ragflow"])
def test_valid_owner_attestation_binds_service_contract(
    verifier: ModuleType, service_kind: str
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind=service_kind)

    verifier.verify_attestation(
        attestation,
        _policy(private_key, service_kind=service_kind),
        expected=_expected(verifier, service_kind),
        now=NOW,
    )


def test_canonical_form_is_order_independent_and_signature_covers_key_id(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind="llm")
    signed = {key: attestation[key] for key in verifier.SIGNED_FIELDS}
    reordered = dict(reversed(list(signed.items())))

    assert verifier.canonical_signed_bytes(signed) == verifier.canonical_signed_bytes(reordered)

    forged = copy.deepcopy(attestation)
    forged["key_id"] = "test-only-replacement-key"
    policy = _policy(private_key, service_kind="llm", key_id="test-only-replacement-key")
    _assert_error(
        verifier,
        "signature_invalid",
        lambda: verifier.verify_attestation(
            forged, policy, expected=_expected(verifier, "llm"), now=NOW
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
        ("endpoint_identity_sha256", "1" * 64),
        ("tls_spki_sha256", "2" * 64),
        ("nonce", "R" * 32),
        ("provider_identity_sha256", "3" * 64),
        ("model_identity_sha256", "4" * 64),
    ],
)
def test_llm_attestation_rejects_cross_context_replay(
    verifier: ModuleType, field: str, different: object
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind="llm")
    expected = replace(_expected(verifier, "llm"), **{field: different})

    _assert_error(
        verifier,
        "context_mismatch",
        lambda: verifier.verify_attestation(
            attestation,
            _policy(private_key, service_kind="llm"),
            expected=expected,
            now=NOW,
        ),
    )


@pytest.mark.parametrize("field", ["workflow_run_id", "workflow_run_attempt"])
@pytest.mark.parametrize("value", [0, -1, 1.0, True, "701"])
def test_workflow_binding_requires_positive_builtin_integers(
    verifier: ModuleType,
    field: str,
    value: object,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind="llm")
    invalid_payload = copy.deepcopy(attestation)
    payload = invalid_payload["payload"]
    assert isinstance(payload, dict)
    payload[field] = value

    _assert_error(
        verifier,
        "workflow_binding_invalid",
        lambda: verifier.verify_attestation(
            invalid_payload,
            _policy(private_key, service_kind="llm"),
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )

    invalid_expected = replace(_expected(verifier, "llm"), **{field: value})
    _assert_error(
        verifier,
        "expected_context_invalid",
        lambda: verifier.verify_attestation(
            attestation,
            _policy(private_key, service_kind="llm"),
            expected=invalid_expected,
            now=NOW,
        ),
    )


def test_ragflow_attestation_rejects_dataset_and_service_replay(verifier: ModuleType) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind="ragflow")
    wrong_dataset = replace(_expected(verifier, "ragflow"), dataset_identity_sha256="1" * 64)
    _assert_error(
        verifier,
        "context_mismatch",
        lambda: verifier.verify_attestation(
            attestation,
            _policy(private_key, service_kind="ragflow"),
            expected=wrong_dataset,
            now=NOW,
        ),
    )

    llm_expected = _expected(verifier, "llm")
    _assert_error(
        verifier,
        "context_mismatch",
        lambda: verifier.verify_attestation(
            attestation,
            _policy(private_key, service_kind="ragflow"),
            expected=llm_expected,
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
def test_rejects_expired_future_and_overlong_attestations(
    verifier: ModuleType, times: dict[str, object]
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(
        verifier,
        private_key,
        service_kind="llm",
        payload=_payload("llm", **times),
    )

    _assert_error(
        verifier,
        "time_invalid",
        lambda: verifier.verify_attestation(
            attestation,
            _policy(private_key, service_kind="llm"),
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


def test_rejects_stale_attestation_even_while_not_expired(verifier: ModuleType) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind="llm")

    _assert_error(
        verifier,
        "time_invalid",
        lambda: verifier.verify_attestation(
            attestation,
            _policy(private_key, service_kind="llm", max_age=60),
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("internal", False, "llm_constraints_invalid"),
        ("private", False, "llm_constraints_invalid"),
        ("nonbillable", False, "llm_constraints_invalid"),
        ("provider_identity_sha256", "https://provider.example", "llm_constraints_invalid"),
        ("allowed_operations", ["chat.completions.create", "models.delete"], "operations_invalid"),
    ],
)
def test_llm_rejects_billable_public_or_broadened_contracts(
    verifier: ModuleType, field: str, value: object, code: str
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key, service_kind="llm")
    forged = copy.deepcopy(valid)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    payload[field] = value

    _assert_error(
        verifier,
        code,
        lambda: verifier.verify_attestation(
            forged,
            _policy(private_key, service_kind="llm"),
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


@pytest.mark.parametrize("price", [1, 0.0, False])
def test_llm_requires_exact_zero_pricing_policy(verifier: ModuleType, price: object) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key, service_kind="llm")
    forged = copy.deepcopy(valid)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    pricing = payload["zero_pricing_policy"]
    assert isinstance(pricing, dict)
    pricing["input_price"] = price

    _assert_error(
        verifier,
        "llm_constraints_invalid",
        lambda: verifier.verify_attestation(
            forged,
            _policy(private_key, service_kind="llm"),
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("dataset_isolated", False, "ragflow_constraints_invalid"),
        ("dataset_initially_empty", False, "ragflow_constraints_invalid"),
        ("dataset_identity_sha256", "http://ragflow/dataset", "ragflow_constraints_invalid"),
        ("allowed_operations", ["documents.list", "datasets.delete"], "operations_invalid"),
    ],
)
def test_ragflow_requires_isolated_empty_dataset_and_minimal_operations(
    verifier: ModuleType, field: str, value: object, code: str
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key, service_kind="ragflow")
    forged = copy.deepcopy(valid)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    payload[field] = value

    _assert_error(
        verifier,
        code,
        lambda: verifier.verify_attestation(
            forged,
            _policy(private_key, service_kind="ragflow"),
            expected=_expected(verifier, "ragflow"),
            now=NOW,
        ),
    )


def test_rejects_json_type_confusion_without_uncaught_type_errors(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key, service_kind="llm")
    policy = _policy(private_key, service_kind="llm")

    float_version = copy.deepcopy(valid)
    float_version["version"] = 1.0
    _assert_error(
        verifier,
        "attestation_schema_invalid",
        lambda: verifier.verify_attestation(
            float_version, policy, expected=_expected(verifier, "llm"), now=NOW
        ),
    )

    float_policy_version = copy.deepcopy(policy)
    float_policy_version["version"] = 1.0
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_attestation(
            valid,
            float_policy_version,
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )

    unhashable_service = copy.deepcopy(valid)
    payload = unhashable_service["payload"]
    assert isinstance(payload, dict)
    payload["service_kind"] = []
    _assert_error(
        verifier,
        "payload_invalid",
        lambda: verifier.verify_attestation(
            unhashable_service,
            policy,
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


def test_rejects_algorithm_confusion_untrusted_key_and_noncanonical_signature(
    verifier: ModuleType,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key, service_kind="llm")
    policy = _policy(private_key, service_kind="llm")

    confused = copy.deepcopy(valid)
    confused["algorithm"] = "HS256"
    _assert_error(
        verifier,
        "algorithm_invalid",
        lambda: verifier.verify_attestation(
            confused, policy, expected=_expected(verifier, "llm"), now=NOW
        ),
    )

    unknown = copy.deepcopy(valid)
    unknown["key_id"] = "unknown-owner-key"
    _assert_error(
        verifier,
        "untrusted_key",
        lambda: verifier.verify_attestation(
            unknown, policy, expected=_expected(verifier, "llm"), now=NOW
        ),
    )

    padded = copy.deepcopy(valid)
    padded["signature"] = str(padded["signature"]) + "="
    _assert_error(
        verifier,
        "signature_invalid",
        lambda: verifier.verify_attestation(
            padded, policy, expected=_expected(verifier, "llm"), now=NOW
        ),
    )


def test_rejects_key_substitution_duplicate_public_keys_and_policy_broadening(
    verifier: ModuleType,
) -> None:
    signer = Ed25519PrivateKey.generate()
    replacement = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, signer, service_kind="llm")
    substituted = _policy(replacement, service_kind="llm")
    _assert_error(
        verifier,
        "signature_invalid",
        lambda: verifier.verify_attestation(
            attestation,
            substituted,
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )

    duplicate = _policy(signer, service_kind="llm")
    keys = duplicate["keys"]
    assert isinstance(keys, list)
    duplicate_key = copy.deepcopy(keys[0])
    assert isinstance(duplicate_key, dict)
    duplicate_key["key_id"] = "test-only-duplicate-owner"
    keys.append(duplicate_key)
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_attestation(
            attestation,
            duplicate,
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )

    broadened = _policy(signer, service_kind="llm")
    broadened_keys = broadened["keys"]
    assert isinstance(broadened_keys, list)
    broadened_key = broadened_keys[0]
    assert isinstance(broadened_key, dict)
    broadened_key["allowed_operations"] = ["chat.completions.create", "models.delete"]
    _assert_error(
        verifier,
        "policy_invalid",
        lambda: verifier.verify_attestation(
            attestation,
            broadened,
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


@pytest.mark.parametrize("field", ["endpoint_url", "api_key", "raw_response", "prompt"])
def test_unknown_or_secret_bearing_payload_fields_are_rejected(
    verifier: ModuleType, field: str
) -> None:
    private_key = Ed25519PrivateKey.generate()
    valid = _attestation(verifier, private_key, service_kind="llm")
    forged = copy.deepcopy(valid)
    payload = forged["payload"]
    assert isinstance(payload, dict)
    payload[field] = "https://private.example/Bearer-super-secret"

    _assert_error(
        verifier,
        "attestation_schema_invalid",
        lambda: verifier.verify_attestation(
            forged,
            _policy(private_key, service_kind="llm"),
            expected=_expected(verifier, "llm"),
            now=NOW,
        ),
    )


def test_file_loader_rejects_duplicate_json_keys(verifier: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")

    _assert_error(
        verifier,
        "input_invalid",
        lambda: verifier._load_json(path, max_bytes=verifier.MAX_ATTESTATION_BYTES),
    )


def test_file_loader_rejects_deep_json_without_traceback(
    verifier: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "deep.json"
    path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")

    _assert_error(
        verifier,
        "input_invalid",
        lambda: verifier._load_json(path, max_bytes=verifier.MAX_ATTESTATION_BYTES),
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


def test_cli_failure_output_never_echoes_sensitive_payload(
    verifier: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_key = Ed25519PrivateKey.generate()
    attestation = _attestation(verifier, private_key, service_kind="llm")
    payload = attestation["payload"]
    assert isinstance(payload, dict)
    sensitive = "https://private.example/?token=super-secret"
    payload["raw_response"] = sensitive
    attestation_path = tmp_path / "attestation.json"
    policy_path = tmp_path / "policy.json"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    policy_path.write_text(json.dumps(_policy(private_key, service_kind="llm")), encoding="utf-8")

    result = verifier.main(
        [
            "--attestation",
            str(attestation_path),
            "--policy",
            str(policy_path),
            "--service-kind",
            "llm",
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
            "--endpoint-identity-sha256",
            ENDPOINT_HASH,
            "--tls-spki-sha256",
            SPKI_HASH,
            "--nonce",
            NONCE,
            "--provider-identity-sha256",
            PROVIDER_HASH,
            "--model-identity-sha256",
            MODEL_HASH,
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == (
        "endpoint owner attestation verification failed: attestation_schema_invalid\n"
    )
    assert sensitive not in captured.err
    assert "super-secret" not in captured.err


def test_versioned_example_policy_is_explicitly_test_only_and_contains_no_private_key(
    verifier: ModuleType,
) -> None:
    path = Path(__file__).parents[1] / "policies/endpoint-owner-attestation-policy.v1.example.json"
    raw = path.read_text(encoding="utf-8")
    policy = json.loads(raw)

    verifier._parse_policy(policy)
    assert policy["policy_id"].startswith("test-only-")
    assert {key["service_kind"] for key in policy["keys"]} == {"llm", "ragflow"}
    assert all(key["key_id"].startswith("test-only-") for key in policy["keys"])
    assert all(key["environment"] == "test" for key in policy["keys"])
    assert all(key["repository"] == REPOSITORY for key in policy["keys"])
    assert "private_key" not in raw.lower()
    assert "secret" not in raw.lower()
