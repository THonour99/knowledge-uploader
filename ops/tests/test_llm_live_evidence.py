from __future__ import annotations

import base64
import copy
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.adapters.llm.base import LLMCompletion, LLMProviderError, LLMUsage
from scripts import collect_llm_live_evidence as collector
from scripts import run_llm_live_probe as probe
from scripts import verify_endpoint_owner_attestation as owner_verifier

ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/protected-llm-evidence.yml"
LOCK_PATH = ROOT / "ops/requirements-protected-llm-evidence.txt"
NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
REPOSITORY = "example/knowledge-uploader"
GIT_SHA = "a" * 40
NONCE = "N" * 32
BASE_URL = "https://llm.internal.example/v1"
MODEL = "private-model-v1"
API_KEY = "sk-unit-test-super-secret-value"
PIN_BYTES = bytes(range(32))
PIN = "sha256/" + base64.b64encode(PIN_BYTES).decode("ascii")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _context(**overrides: object) -> probe.ProbeContext:
    values: dict[str, object] = {
        "environment": "staging",
        "repository": REPOSITORY,
        "git_sha": GIT_SHA,
        "nonce": NONCE,
        "workflow_run_id": 2001,
        "workflow_run_attempt": 1,
        "main_ci_run_id": 1001,
        "main_ci_run_attempt": 2,
    }
    values.update(overrides)
    return probe.ProbeContext(**values)  # type: ignore[arg-type]


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        probe.ENV_BASE_URL: BASE_URL,
        probe.ENV_API_KEY: API_KEY,
        probe.ENV_MODEL: MODEL,
        probe.ENV_SPKI_PIN: PIN,
    }
    values.update(overrides)
    return values


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return _b64url(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )


def _owner_documents(
    private_key: Ed25519PrivateKey,
    *,
    context: probe.ProbeContext,
    payload_overrides: dict[str, object] | None = None,
    algorithm: str = "Ed25519",
) -> tuple[dict[str, object], dict[str, object]]:
    endpoint_hash = probe._identity_hash(BASE_URL)
    provider_hash = probe._identity_hash(probe.PROVIDER_IDENTITY)
    model_hash = probe._identity_hash(MODEL)
    payload: dict[str, object] = {
        "service_kind": "llm",
        "environment": context.environment,
        "repository": context.repository,
        "git_sha": context.git_sha,
        "workflow_run_id": context.workflow_run_id,
        "workflow_run_attempt": context.workflow_run_attempt,
        "endpoint_identity_sha256": endpoint_hash,
        "tls_spki_sha256": PIN_BYTES.hex(),
        "nonce": context.nonce,
        "issued_at": "2026-07-18T07:59:00Z",
        "not_before": "2026-07-18T07:59:00Z",
        "expires_at": "2026-07-18T08:10:00Z",
        "allowed_operations": ["chat.completions.create"],
        "internal": True,
        "private": True,
        "nonbillable": True,
        "provider_identity_sha256": provider_hash,
        "model_identity_sha256": model_hash,
        "zero_pricing_policy": {
            "currency": "USD",
            "unit": "million_tokens",
            "input_price": 0,
            "output_price": 0,
        },
    }
    if payload_overrides:
        payload.update(payload_overrides)
    signed: dict[str, object] = {
        "schema": owner_verifier.ATTESTATION_SCHEMA,
        "version": 1,
        "algorithm": algorithm,
        "key_id": "test-only-llm-owner",
        "payload": payload,
    }
    canonical = json.dumps(
        {key: signed[key] for key in sorted(owner_verifier.SIGNED_FIELDS)},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = private_key.sign(canonical)
    attestation = {**signed, "signature": _b64url(signature)}
    policy = {
        "schema": owner_verifier.POLICY_SCHEMA,
        "version": 1,
        "policy_id": "test-only-llm-live-policy",
        "max_attestation_lifetime_seconds": 900,
        "max_attestation_age_seconds": 900,
        "keys": [
            {
                "key_id": "test-only-llm-owner",
                "algorithm": "Ed25519",
                "public_key_base64url": _public_key(private_key),
                "service_kind": "llm",
                "environment": context.environment,
                "repository": context.repository,
                "allowed_operations": ["chat.completions.create"],
                "not_before": "2026-01-01T00:00:00Z",
                "expires_at": "2027-01-01T00:00:00Z",
            }
        ],
    }
    return attestation, policy


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _files(
    tmp_path: Path,
    *,
    context: probe.ProbeContext,
    payload_overrides: dict[str, object] | None = None,
    algorithm: str = "Ed25519",
) -> dict[str, Path]:
    attestation, policy = _owner_documents(
        Ed25519PrivateKey.generate(),
        context=context,
        payload_overrides=payload_overrides,
        algorithm=algorithm,
    )
    attestation_path = tmp_path / "attestation.json"
    policy_path = tmp_path / "policy.json"
    trust_path = tmp_path / "release-workflow-trust.json"
    trust_checksum_path = tmp_path / "release-workflow-trust.json.sha256"
    _write_json(attestation_path, attestation)
    _write_json(policy_path, policy)
    _write_json(trust_path, {"test_only": True})
    trust_hash = probe._sha256_bytes(trust_path.read_bytes())
    trust_checksum_path.write_text(
        f"{trust_hash}  {trust_path.name}\n", encoding="utf-8", newline="\n"
    )
    return {
        "attestation": attestation_path,
        "policy": policy_path,
        "trust": trust_path,
        "trust_checksum": trust_checksum_path,
        "probe": tmp_path / "llm-live-probe.json",
        "evidence": tmp_path / "evidence",
    }


class _FakeProvider:
    def __init__(
        self,
        calls: list[dict[str, object]],
        *,
        completion: LLMCompletion | None = None,
        error: Exception | None = None,
    ) -> None:
        self._calls = calls
        self._completion = completion or LLMCompletion(
            content=probe.EXPECTED_RESPONSE,
            model=MODEL,
            usage=LLMUsage(prompt_tokens=30, completion_tokens=5),
            latency_ms=17,
        )
        self._error = error

    async def complete(self, prompt_text: str, **kwargs: object) -> LLMCompletion:
        self._calls.append({"prompt": prompt_text, **kwargs})
        if self._error is not None:
            raise self._error
        return self._completion


class _Factory:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.arguments: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> _FakeProvider:
        self.arguments.append(kwargs)
        return self.provider


def _clock(*values: datetime) -> Callable[[], datetime]:
    iterator = iter(values)
    return lambda: next(iterator)


def _policy_sha256(path: Path) -> str:
    return probe._sha256_bytes(path.read_bytes())


async def _run_success(
    tmp_path: Path,
    *,
    context: probe.ProbeContext | None = None,
    completion: LLMCompletion | None = None,
) -> tuple[dict[str, Path], _Factory, dict[str, object]]:
    selected_context = context or _context()
    paths = _files(tmp_path, context=selected_context)
    calls: list[dict[str, object]] = []
    factory = _Factory(_FakeProvider(calls, completion=completion))
    receipt = await probe.run_live_probe(
        context=selected_context,
        attestation_path=paths["attestation"],
        policy_path=paths["policy"],
        expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
        workflow_trust_path=paths["trust"],
        output_path=paths["probe"],
        environment=_environment(),
        provider_factory=factory,
        clock=_clock(NOW, NOW + timedelta(seconds=1)),
    )
    return paths, factory, dict(receipt)


@pytest.mark.asyncio
async def test_probe_uses_one_pinned_private_nonbillable_request_and_writes_hashes_only(
    tmp_path: Path,
) -> None:
    paths, factory, receipt = await _run_success(tmp_path)

    assert len(factory.arguments) == 1
    provider_args = factory.arguments[0]
    assert provider_args["allow_external"] is False
    assert provider_args["is_internal"] is True
    assert provider_args["require_tls_spki_pin"] is True
    assert provider_args["raw_allowed_base_urls"] == BASE_URL
    assert provider_args["timeout_seconds"] == probe.TIMEOUT_SECONDS
    assert len(factory.provider._calls) == 1
    request = factory.provider._calls[0]
    assert request["prompt"] == probe.USER_PROMPT
    assert request["max_output_tokens"] == probe.MAX_OUTPUT_TOKENS
    assert request["temperature"] == 0.0
    assert request["top_p"] == 1.0
    assert receipt["schema"] == probe.PROBE_SCHEMA
    assert receipt["expires_at"] == "2026-07-18T08:10:00Z"
    assert paths["probe"].is_file()

    serialized = paths["probe"].read_text(encoding="utf-8")
    for forbidden in (BASE_URL, MODEL, API_KEY, probe.USER_PROMPT, probe.EXPECTED_RESPONSE):
        assert forbidden not in serialized
    assert PIN not in serialized
    assert PIN_BYTES.hex() in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_overrides", "algorithm"),
    [
        ({"nonbillable": False}, "Ed25519"),
        ({"internal": False}, "Ed25519"),
        ({"workflow_run_id": 9999}, "Ed25519"),
        ({"workflow_run_attempt": 9}, "Ed25519"),
        (
            {
                "zero_pricing_policy": {
                    "currency": "USD",
                    "unit": "million_tokens",
                    "input_price": 1,
                    "output_price": 0,
                }
            },
            "Ed25519",
        ),
        ({}, "HS256"),
    ],
)
async def test_owner_constraints_and_algorithm_confusion_fail_before_provider_creation(
    tmp_path: Path,
    payload_overrides: dict[str, object],
    algorithm: str,
) -> None:
    context = _context()
    paths = _files(
        tmp_path,
        context=context,
        payload_overrides=payload_overrides,
        algorithm=algorithm,
    )
    calls: list[dict[str, object]] = []
    factory = _Factory(_FakeProvider(calls))

    with pytest.raises(probe.LLMLiveProbeError, match="owner_attestation_invalid"):
        await probe.run_live_probe(
            context=context,
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
            workflow_trust_path=paths["trust"],
            output_path=paths["probe"],
            environment=_environment(),
            provider_factory=factory,
            clock=_clock(NOW),
        )

    assert factory.arguments == []
    assert calls == []
    assert not paths["probe"].exists()


@pytest.mark.asyncio
async def test_untrusted_owner_policy_anchor_fails_before_provider_creation(
    tmp_path: Path,
) -> None:
    context = _context()
    paths = _files(tmp_path, context=context)
    calls: list[dict[str, object]] = []
    factory = _Factory(_FakeProvider(calls))

    with pytest.raises(probe.LLMLiveProbeError, match="owner_policy_anchor_invalid"):
        await probe.run_live_probe(
            context=context,
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256="0" * 64,
            workflow_trust_path=paths["trust"],
            output_path=paths["probe"],
            environment=_environment(),
            provider_factory=factory,
            clock=_clock(NOW),
        )

    assert factory.arguments == []
    assert calls == []
    assert not paths["probe"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_pin", ["", "sha256/not-base64", "sha256/AAAA"])
async def test_invalid_or_missing_single_spki_pin_fails_before_provider_creation(
    tmp_path: Path, invalid_pin: str
) -> None:
    context = _context()
    paths = _files(tmp_path, context=context)
    factory = _Factory(_FakeProvider([]))

    with pytest.raises(probe.LLMLiveProbeError, match="runtime_configuration_invalid"):
        await probe.run_live_probe(
            context=context,
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
            workflow_trust_path=paths["trust"],
            output_path=paths["probe"],
            environment=_environment(**{probe.ENV_SPKI_PIN: invalid_pin}),
            provider_factory=factory,
            clock=_clock(NOW),
        )
    assert factory.arguments == []
    assert not paths["probe"].exists()


@pytest.mark.asyncio
async def test_network_failure_never_writes_a_success_receipt(tmp_path: Path) -> None:
    context = _context()
    paths = _files(tmp_path, context=context)
    calls: list[dict[str, object]] = []
    factory = _Factory(
        _FakeProvider(
            calls,
            error=LLMProviderError("connection_error", retryable=True),
        )
    )
    with pytest.raises(probe.LLMLiveProbeError, match="provider_request_failed"):
        await probe.run_live_probe(
            context=context,
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
            workflow_trust_path=paths["trust"],
            output_path=paths["probe"],
            environment=_environment(),
            provider_factory=factory,
            clock=_clock(NOW),
        )
    assert len(calls) == 1
    assert not paths["probe"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "completion",
    [
        LLMCompletion(
            content="not-the-contract",
            model=MODEL,
            usage=LLMUsage(prompt_tokens=30, completion_tokens=5),
            latency_ms=5,
        ),
        LLMCompletion(
            content=probe.EXPECTED_RESPONSE,
            model="different-model",
            usage=LLMUsage(prompt_tokens=30, completion_tokens=5),
            latency_ms=5,
        ),
        LLMCompletion(
            content=probe.EXPECTED_RESPONSE,
            model=MODEL,
            usage=LLMUsage(prompt_tokens=0, completion_tokens=5),
            latency_ms=5,
        ),
        LLMCompletion(
            content=probe.EXPECTED_RESPONSE,
            model=MODEL,
            usage=LLMUsage(prompt_tokens=30, completion_tokens=17),
            latency_ms=5,
        ),
    ],
)
async def test_response_model_usage_or_output_contract_mismatch_never_writes_receipt(
    tmp_path: Path, completion: LLMCompletion
) -> None:
    with pytest.raises(probe.LLMLiveProbeError, match="provider_contract_mismatch"):
        await _run_success(tmp_path, completion=completion)
    assert not (tmp_path / "llm-live-probe.json").exists()


@pytest.mark.asyncio
async def test_collector_reverifies_sources_and_emits_only_public_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context()
    paths, _factory, _receipt = await _run_success(tmp_path, context=context)
    monkeypatch.setattr(collector, "_verify_workflow_trust_context", lambda **_kwargs: None)

    evidence = collector.collect_live_evidence(
        context=context,
        probe_receipt_path=paths["probe"],
        attestation_path=paths["attestation"],
        policy_path=paths["policy"],
        expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
        workflow_trust_path=paths["trust"],
        workflow_trust_checksum_path=paths["trust_checksum"],
        output_dir=paths["evidence"],
        clock=lambda: NOW + timedelta(seconds=2),
    )

    assert evidence["schema"] == collector.EVIDENCE_SCHEMA
    assert {path.name for path in paths["evidence"].iterdir()} == {
        collector.EVIDENCE_FILENAME,
        collector.ATTESTATION_FILENAME,
        collector.POLICY_FILENAME,
        collector.TRUST_FILENAME,
        collector.TRUST_CHECKSUM_FILENAME,
    }
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in paths["evidence"].iterdir())
    for forbidden in (BASE_URL, MODEL, API_KEY, probe.USER_PROMPT, probe.EXPECTED_RESPONSE):
        assert forbidden not in serialized
    assert "private_key" not in serialized.lower()


@pytest.mark.asyncio
async def test_collector_rejects_cross_run_replay_before_creating_output(
    tmp_path: Path,
) -> None:
    paths, _factory, _receipt = await _run_success(tmp_path)
    replay_context = _context(workflow_run_id=9999)
    with pytest.raises(probe.LLMLiveProbeError, match="probe_receipt_context_invalid"):
        collector.collect_live_evidence(
            context=replay_context,
            probe_receipt_path=paths["probe"],
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
            workflow_trust_path=paths["trust"],
            workflow_trust_checksum_path=paths["trust_checksum"],
            output_dir=paths["evidence"],
            clock=lambda: NOW + timedelta(seconds=2),
        )
    assert not paths["evidence"].exists()


@pytest.mark.asyncio
async def test_expired_or_nonzero_cost_receipt_cannot_be_collected(tmp_path: Path) -> None:
    paths, _factory, receipt = await _run_success(tmp_path)
    cost = receipt["cost"]
    assert isinstance(cost, dict)
    cost["estimated_cost_microusd"] = 1
    _write_json(paths["probe"], receipt)
    with pytest.raises(probe.LLMLiveProbeError, match="probe_receipt_cost_invalid"):
        collector.collect_live_evidence(
            context=_context(),
            probe_receipt_path=paths["probe"],
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
            workflow_trust_path=paths["trust"],
            workflow_trust_checksum_path=paths["trust_checksum"],
            output_dir=paths["evidence"],
            clock=lambda: NOW + timedelta(seconds=2),
        )
    assert not paths["evidence"].exists()

    valid = copy.deepcopy(receipt)
    valid_cost = valid["cost"]
    assert isinstance(valid_cost, dict)
    valid_cost["estimated_cost_microusd"] = 0
    _write_json(tmp_path / "expired.json", valid)
    with pytest.raises(probe.LLMLiveProbeError, match="probe_receipt_time_invalid"):
        collector.collect_live_evidence(
            context=_context(),
            probe_receipt_path=tmp_path / "expired.json",
            attestation_path=paths["attestation"],
            policy_path=paths["policy"],
            expected_owner_policy_sha256=_policy_sha256(paths["policy"]),
            workflow_trust_path=paths["trust"],
            workflow_trust_checksum_path=paths["trust_checksum"],
            output_dir=paths["evidence"],
            clock=lambda: NOW + timedelta(minutes=11),
        )


def test_workflow_trust_revalidation_binds_current_and_main_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context()
    summary: dict[str, object] = {
        "current": {
            "run_id": context.workflow_run_id,
            "run_attempt": context.workflow_run_attempt,
            "workflow_path": probe.WORKFLOW_PATH,
        },
        "main_ci": {
            "run_id": context.main_ci_run_id,
            "run_attempt": context.main_ci_run_attempt,
        },
    }
    validation_arguments: list[dict[str, object]] = []

    def _validate(value: object, **kwargs: object) -> object:
        validation_arguments.append(kwargs)
        return value

    monkeypatch.setattr(collector, "_load_summary", lambda _path: summary)
    monkeypatch.setattr(collector, "validate_trust_summary", _validate)
    collector._verify_workflow_trust_context(
        workflow_trust_path=tmp_path / "trust.json",
        parsed_from_stable_bytes=summary,
        context=context,
        now=NOW,
    )
    assert validation_arguments == [
        {
            "expected_repository": context.repository,
            "expected_git_sha": context.git_sha,
            "expected_current_role": "llm_live",
            "now": NOW,
        }
    ]

    tampered = copy.deepcopy(summary)
    current = tampered["current"]
    assert isinstance(current, dict)
    current["run_id"] = 9999
    monkeypatch.setattr(collector, "_load_summary", lambda _path: tampered)
    with pytest.raises(probe.LLMLiveProbeError, match="workflow_trust_context_mismatch"):
        collector._verify_workflow_trust_context(
            workflow_trust_path=tmp_path / "trust.json",
            parsed_from_stable_bytes=tampered,
            context=context,
            now=NOW,
        )

    with pytest.raises(probe.LLMLiveProbeError, match="workflow_trust_changed"):
        collector._verify_workflow_trust_context(
            workflow_trust_path=tmp_path / "trust.json",
            parsed_from_stable_bytes=summary,
            context=context,
            now=NOW,
        )


def test_cli_failure_is_detail_free_and_never_echoes_runtime_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    context = _context()
    paths = _files(tmp_path, context=context)
    for name, value in _environment(**{probe.ENV_SPKI_PIN: "not-a-pin"}).items():
        monkeypatch.setenv(name, value)
    result = probe.main(
        [
            "--environment",
            context.environment,
            "--repository",
            context.repository,
            "--git-sha",
            context.git_sha,
            "--nonce",
            context.nonce,
            "--workflow-run-id",
            str(context.workflow_run_id),
            "--workflow-run-attempt",
            str(context.workflow_run_attempt),
            "--main-ci-run-id",
            str(context.main_ci_run_id),
            "--main-ci-run-attempt",
            str(context.main_ci_run_attempt),
            "--owner-attestation",
            str(paths["attestation"]),
            "--owner-policy",
            str(paths["policy"]),
            "--expected-owner-policy-sha256",
            _policy_sha256(paths["policy"]),
            "--workflow-trust",
            str(paths["trust"]),
            "--output",
            str(paths["probe"]),
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "LLM live probe failed: runtime_configuration_invalid\n"
    assert BASE_URL not in captured.err
    assert MODEL not in captured.err
    assert API_KEY not in captured.err


def _workflow() -> tuple[str, dict[str, Any]]:
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return raw, parsed


def test_live_workflow_is_protected_hash_locked_and_fail_closed() -> None:
    raw, workflow = _workflow()
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["collect-protected-llm-evidence"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "protected-llm-evidence"]
    assert job["environment"] == {"name": "${{ inputs.environment }}"}
    assert job["env"]["PYTHONPATH"] == "${{ github.workspace }}/backend"
    steps = {step["name"]: step for step in job["steps"]}
    order = [step["name"] for step in job["steps"]]
    assert order.index("Verify protected ref and main CI provenance") < order.index(
        "Run one owner-authorized private LLM probe"
    )
    assert order.index("Run one owner-authorized private LLM probe") < order.index(
        "Revalidate and collect hash-only LLM evidence"
    )
    assert order.index("Revalidate and collect hash-only LLM evidence") < order.index(
        "Upload protected LLM evidence"
    )

    trust = str(steps["Verify protected ref and main CI provenance"]["run"])
    assert "release_workflow_trust.py fetch" in trust
    assert "--current-role llm_live" in trust
    assert f"--current-workflow {probe.WORKFLOW_PATH}" in trust
    assert '--main-run-id "${MAIN_CI_RUN_ID}"' in trust
    assert '--main-run-attempt "${MAIN_CI_RUN_ATTEMPT}"' in trust
    assert '--git-sha "${GITHUB_SHA}"' in trust
    assert '--ref-protected "${{ github.ref_protected }}"' in trust

    probe_step = steps["Run one owner-authorized private LLM probe"]
    probe_environment = probe_step["env"]
    assert probe_environment[probe.ENV_BASE_URL] == "${{ secrets.PROTECTED_LLM_BASE_URL }}"
    assert probe_environment[probe.ENV_API_KEY] == "${{ secrets.PROTECTED_LLM_API_KEY }}"
    assert probe_environment[probe.ENV_MODEL] == "${{ secrets.PROTECTED_LLM_MODEL }}"
    assert "private_key" not in json.dumps(probe_environment).lower()
    assert "Ed25519PrivateKey" not in raw

    upload = steps["Upload protected LLM evidence"]
    assert upload["if"] == "${{ success() }}"
    assert upload["with"] == {
        "name": (
            "protected-llm-evidence-${{ github.sha }}-${{ github.run_id }}-"
            "${{ github.run_attempt }}"
        ),
        "path": "artifacts/llm",
        "if-no-files-found": "error",
        "retention-days": 7,
    }
    assert raw.count("actions/upload-artifact@") == 1
    for forbidden in (
        "PROTECTED_EVIDENCE_SOURCE_DIR",
        "openssl",
        "curl ",
        "requests.",
        "mock",
        "BEGIN PRIVATE KEY",
    ):
        assert forbidden.lower() not in raw.lower()
    for action in re.findall(r"uses:\s+[^@\s]+@([^\s]+)", raw):
        assert re.fullmatch(r"[0-9a-f]{40}", action)


def test_dependency_lock_matches_python311_x64_runner_and_has_one_hash_per_wheel() -> None:
    raw, workflow = _workflow()
    lock = LOCK_PATH.read_text(encoding="utf-8")
    job = workflow["jobs"]["collect-protected-llm-evidence"]
    setup = next(step for step in job["steps"] if step["name"] == "Set up pinned Python")
    install = next(
        step for step in job["steps"] if step["name"] == "Install hash-locked LLM evidence runtime"
    )
    assert setup["with"]["python-version"] == "3.11"
    assert "X64" in job["runs-on"]
    assert "--require-hashes" in install["run"]
    assert "--only-binary=:all:" in install["run"]
    assert lock.count("--hash=sha256:") == 10
    assert lock.count("==") == 10
    assert "cryptography==42.0.5" in lock
    assert "httpx==0.27.0" in lock
    assert "cffi-1.16.0-cp311" not in lock
    assert "CPython 3.11 / Linux x86_64" in lock
    assert "PYTHONPATH: ${{ github.workspace }}/backend" in raw


def test_workflow_declares_the_unbypassable_llm_live_trust_role_dependency() -> None:
    raw, _workflow_document = _workflow()
    trust_source = (ROOT / "scripts/release_workflow_trust.py").read_text(encoding="utf-8")
    assert "--current-role llm_live" in raw
    if '"llm_live"' not in trust_source:
        assert raw.index("--current-role llm_live") < raw.index("scripts.run_llm_live_probe")
