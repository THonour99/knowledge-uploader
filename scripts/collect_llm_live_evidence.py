"""Revalidate a live LLM probe and assemble the public, hash-only evidence artifact."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from scripts.llm_live_evidence_contract import (
    ATTESTATION_FIELDS,
    IDENTITY_FIELDS,
    LLMLiveProbeError,
    ProbeContext,
    _exact_mapping,
    _hash,
    _sha256_bytes,
    _verify_owner_attestation,
    _write_new_file,
    load_strict_json_object,
    read_stable_regular_file,
    validate_probe_receipt,
    verify_policy_sha256,
    write_canonical_json,
)
from scripts.release_workflow_trust import (
    TrustError,
    _load_summary,
    validate_trust_summary,
)

EVIDENCE_SCHEMA: Final = "knowledge-uploader.llm-live-evidence.v1"
EVIDENCE_FILENAME: Final = "llm-live-evidence.json"
ATTESTATION_FILENAME: Final = "llm-owner-attestation.json"
POLICY_FILENAME: Final = "llm-owner-trust-policy.json"
TRUST_FILENAME: Final = "release-workflow-trust.json"
TRUST_CHECKSUM_FILENAME: Final = "release-workflow-trust.json.sha256"


def _prepare_output_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as error:
        raise LLMLiveProbeError("output_directory_invalid") from error


def _verify_source_digests(
    *,
    receipt: Mapping[str, object],
    attestation_bytes: bytes,
    policy_bytes: bytes,
    workflow_trust_bytes: bytes,
) -> None:
    owner = _exact_mapping(
        receipt.get("owner_attestation"),
        fields=ATTESTATION_FIELDS,
        code="probe_receipt_schema_invalid",
    )
    workflow = receipt.get("workflow")
    if not isinstance(workflow, dict):
        raise LLMLiveProbeError("probe_receipt_schema_invalid")
    if (
        owner.get("attestation_sha256") != _sha256_bytes(attestation_bytes)
        or owner.get("policy_sha256") != _sha256_bytes(policy_bytes)
        or workflow.get("trust_summary_sha256") != _sha256_bytes(workflow_trust_bytes)
    ):
        raise LLMLiveProbeError("source_digest_mismatch")


def _verify_trust_checksum(
    *,
    workflow_trust_bytes: bytes,
    checksum_bytes: bytes,
    source_name: str,
) -> None:
    expected = f"{_sha256_bytes(workflow_trust_bytes)}  {source_name}\n".encode("ascii")
    if checksum_bytes != expected:
        raise LLMLiveProbeError("workflow_trust_checksum_invalid")


def _verify_workflow_trust_context(
    *,
    workflow_trust_path: Path,
    parsed_from_stable_bytes: Mapping[str, object],
    context: ProbeContext,
    now: datetime,
) -> None:
    try:
        checksum_validated = _load_summary(workflow_trust_path)
        validated = validate_trust_summary(
            checksum_validated,
            expected_repository=context.repository,
            expected_git_sha=context.git_sha,
            expected_current_role="llm_live",
            now=now,
        )
    except (TrustError, OSError) as error:
        raise LLMLiveProbeError("workflow_trust_invalid") from error
    if validated != parsed_from_stable_bytes:
        raise LLMLiveProbeError("workflow_trust_changed")
    current = validated.get("current")
    main_ci = validated.get("main_ci")
    if not isinstance(current, dict) or not isinstance(main_ci, dict):
        raise LLMLiveProbeError("workflow_trust_invalid")
    if (
        current.get("run_id") != context.workflow_run_id
        or current.get("run_attempt") != context.workflow_run_attempt
        or current.get("workflow_path") != ".github/workflows/protected-llm-evidence.yml"
        or main_ci.get("run_id") != context.main_ci_run_id
        or main_ci.get("run_attempt") != context.main_ci_run_attempt
    ):
        raise LLMLiveProbeError("workflow_trust_context_mismatch")


def collect_live_evidence(
    *,
    context: ProbeContext,
    probe_receipt_path: Path,
    attestation_path: Path,
    policy_path: Path,
    expected_owner_policy_sha256: str,
    workflow_trust_path: Path,
    workflow_trust_checksum_path: Path,
    output_dir: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Mapping[str, object]:
    """Verify every source again before emitting the only success artifact directory."""

    receipt_bytes = read_stable_regular_file(probe_receipt_path)
    now = clock()
    receipt = validate_probe_receipt(
        load_strict_json_object(receipt_bytes),
        expected_context=context,
        now=now,
    )
    attestation_bytes = read_stable_regular_file(attestation_path)
    policy_bytes = read_stable_regular_file(policy_path)
    verify_policy_sha256(policy_bytes, expected_owner_policy_sha256)
    workflow_trust_bytes = read_stable_regular_file(workflow_trust_path)
    workflow_trust_checksum_bytes = read_stable_regular_file(
        workflow_trust_checksum_path,
        maximum=256,
    )
    attestation = load_strict_json_object(attestation_bytes)
    policy = load_strict_json_object(policy_bytes)
    parsed_workflow_trust = load_strict_json_object(workflow_trust_bytes)
    _verify_source_digests(
        receipt=receipt,
        attestation_bytes=attestation_bytes,
        policy_bytes=policy_bytes,
        workflow_trust_bytes=workflow_trust_bytes,
    )
    _verify_trust_checksum(
        workflow_trust_bytes=workflow_trust_bytes,
        checksum_bytes=workflow_trust_checksum_bytes,
        source_name=workflow_trust_path.name,
    )
    _verify_workflow_trust_context(
        workflow_trust_path=workflow_trust_path,
        parsed_from_stable_bytes=parsed_workflow_trust,
        context=context,
        now=now,
    )

    owner = _exact_mapping(
        receipt.get("owner_attestation"),
        fields=ATTESTATION_FIELDS,
        code="probe_receipt_schema_invalid",
    )
    identities = _exact_mapping(
        receipt.get("identities"),
        fields=IDENTITY_FIELDS,
        code="probe_receipt_schema_invalid",
    )
    _verify_owner_attestation(
        attestation=attestation,
        policy=policy,
        context=context,
        endpoint_sha256=_hash(
            identities.get("endpoint_sha256"), code="probe_receipt_schema_invalid"
        ),
        tls_spki_sha256=_hash(
            identities.get("tls_spki_sha256"), code="probe_receipt_schema_invalid"
        ),
        provider_sha256=_hash(
            identities.get("provider_sha256"), code="probe_receipt_schema_invalid"
        ),
        model_sha256=_hash(
            identities.get("requested_model_sha256"),
            code="probe_receipt_schema_invalid",
        ),
        now=now,
    )
    if owner.get("nonce") != context.nonce:
        raise LLMLiveProbeError("probe_receipt_context_invalid")

    evidence = dict(receipt)
    evidence["schema"] = EVIDENCE_SCHEMA
    _prepare_output_directory(output_dir)
    write_canonical_json(output_dir / EVIDENCE_FILENAME, evidence)
    _write_new_file(output_dir / ATTESTATION_FILENAME, attestation_bytes)
    _write_new_file(output_dir / POLICY_FILENAME, policy_bytes)
    _write_new_file(output_dir / TRUST_FILENAME, workflow_trust_bytes)
    _write_new_file(output_dir / TRUST_CHECKSUM_FILENAME, workflow_trust_checksum_bytes)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workflow-run-id", required=True, type=int)
    parser.add_argument("--workflow-run-attempt", required=True, type=int)
    parser.add_argument("--main-ci-run-id", required=True, type=int)
    parser.add_argument("--main-ci-run-attempt", required=True, type=int)
    parser.add_argument("--probe-receipt", required=True, type=Path)
    parser.add_argument("--owner-attestation", required=True, type=Path)
    parser.add_argument("--owner-policy", required=True, type=Path)
    parser.add_argument("--expected-owner-policy-sha256", required=True)
    parser.add_argument("--workflow-trust", required=True, type=Path)
    parser.add_argument("--workflow-trust-checksum", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    context = ProbeContext(
        environment=arguments.environment,
        repository=arguments.repository,
        git_sha=arguments.git_sha,
        nonce=arguments.nonce,
        workflow_run_id=arguments.workflow_run_id,
        workflow_run_attempt=arguments.workflow_run_attempt,
        main_ci_run_id=arguments.main_ci_run_id,
        main_ci_run_attempt=arguments.main_ci_run_attempt,
    )
    try:
        collect_live_evidence(
            context=context,
            probe_receipt_path=arguments.probe_receipt,
            attestation_path=arguments.owner_attestation,
            policy_path=arguments.owner_policy,
            expected_owner_policy_sha256=arguments.expected_owner_policy_sha256,
            workflow_trust_path=arguments.workflow_trust,
            workflow_trust_checksum_path=arguments.workflow_trust_checksum,
            output_dir=arguments.output_dir,
        )
    except LLMLiveProbeError as error:
        print(f"LLM live evidence collection failed: {error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("LLM live evidence collection failed: internal_error", file=sys.stderr)
        return 1
    print("LLM live evidence collected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
