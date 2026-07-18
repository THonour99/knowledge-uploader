"""Collect independently validated RAGFlow probe and janitor evidence."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from scripts import ragflow_live_evidence_contract as _contract
    from scripts import release_workflow_trust as _trust
    from scripts import verify_application_deployment_attestation as _deployment_verifier
    from scripts import verify_endpoint_owner_attestation as _owner_verifier
elif __package__:
    from scripts import ragflow_live_evidence_contract as _contract
    from scripts import release_workflow_trust as _trust
    from scripts import verify_application_deployment_attestation as _deployment_verifier
    from scripts import verify_endpoint_owner_attestation as _owner_verifier
else:  # pragma: no cover - direct script execution
    _contract = importlib.import_module("ragflow_live_evidence_contract")
    _trust = importlib.import_module("release_workflow_trust")
    _deployment_verifier = importlib.import_module("verify_application_deployment_attestation")
    _owner_verifier = importlib.import_module("verify_endpoint_owner_attestation")

WORKFLOW_PATH = _contract.WORKFLOW_PATH
EvidenceContractError = _contract.EvidenceContractError
collect_evidence = _contract.collect_evidence
read_json_document = _contract.read_json_document
read_stable_bytes = _contract.read_stable_bytes
write_bytes = _contract.write_bytes
write_checksum = _contract.write_checksum
write_json = _contract.write_json

TrustError = _trust.TrustError
validate_trust_summary = _trust.validate_trust_summary

DeploymentAttestationVerificationError = _deployment_verifier.DeploymentAttestationVerificationError
ExpectedDeploymentContext = _deployment_verifier.ExpectedDeploymentContext
verify_application_deployment_attestation = (
    _deployment_verifier.verify_application_deployment_attestation
)

AttestationVerificationError = _owner_verifier.AttestationVerificationError
ExpectedContext = _owner_verifier.ExpectedContext
verify_attestation = _owner_verifier.verify_attestation

EVIDENCE_FILENAME: Final = "ragflow-live-evidence.json"
OWNER_ATTESTATION_FILENAME: Final = "ragflow-owner-attestation.json"
OWNER_POLICY_FILENAME: Final = "ragflow-owner-trust-policy.json"
DEPLOYMENT_ATTESTATION_FILENAME: Final = "application-deployment-owner-attestation.json"
DEPLOYMENT_POLICY_FILENAME: Final = "application-deployment-owner-trust-policy.json"
TRUST_FILENAME: Final = "release-workflow-trust.json"
TRUST_CHECKSUM_FILENAME: Final = "release-workflow-trust.json.sha256"


def _positive(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise EvidenceContractError("context_invalid") from error
    if parsed < 1:
        raise EvidenceContractError("context_invalid")
    return parsed


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise EvidenceContractError(code)
    return value


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceContractError(code)
    return value


def _integer(value: object, code: str) -> int:
    if type(value) is not int or value < 1:
        raise EvidenceContractError(code)
    return value


def _require_digest(
    binding: Mapping[str, object],
    field: str,
    actual: str,
    code: str,
) -> None:
    if binding.get(field) != actual:
        raise EvidenceContractError(code)


def _verify_sources(
    probe: Mapping[str, object],
    *,
    nonce: str,
    deployment_identity_sha256: str,
    owner_attestation: Mapping[str, object],
    owner_policy: Mapping[str, object],
    owner_attestation_sha256: str,
    owner_policy_sha256: str,
    deployment_attestation: Mapping[str, object],
    deployment_policy: Mapping[str, object],
    deployment_attestation_sha256: str,
    deployment_policy_sha256: str,
    workflow_trust: Mapping[str, object],
    workflow_trust_sha256: str,
    workflow_trust_checksum: bytes,
    workflow_trust_name: str,
) -> None:
    identities = _mapping(probe.get("identities"), "collector_source_invalid")
    main_ci = _mapping(probe.get("main_ci"), "collector_source_invalid")
    workflow = _mapping(probe.get("workflow"), "collector_source_invalid")
    owner_binding = _mapping(probe.get("owner_attestation"), "collector_source_invalid")
    deployment_binding = _mapping(
        probe.get("deployment_attestation"),
        "collector_source_invalid",
    )
    trust_binding = _mapping(probe.get("trust"), "collector_source_invalid")

    _require_digest(
        owner_binding,
        "attestation_sha256",
        owner_attestation_sha256,
        "owner_attestation_digest_mismatch",
    )
    _require_digest(
        owner_binding,
        "policy_sha256",
        owner_policy_sha256,
        "owner_policy_digest_mismatch",
    )
    verify_attestation(
        owner_attestation,
        owner_policy,
        expected=ExpectedContext(
            service_kind="ragflow",
            environment=_text(probe.get("environment"), "collector_source_invalid"),
            repository=_text(probe.get("repository"), "collector_source_invalid"),
            git_sha=_text(probe.get("git_sha"), "collector_source_invalid"),
            endpoint_identity_sha256=_text(
                identities.get("endpoint_identity_sha256"),
                "collector_source_invalid",
            ),
            tls_spki_sha256=_text(
                identities.get("tls_spki_sha256"),
                "collector_source_invalid",
            ),
            nonce=nonce,
            workflow_run_id=_integer(workflow.get("run_id"), "collector_source_invalid"),
            workflow_run_attempt=_integer(workflow.get("run_attempt"), "collector_source_invalid"),
            dataset_identity_sha256=_text(
                identities.get("dataset_identity_sha256"),
                "collector_source_invalid",
            ),
        ),
    )

    _require_digest(
        deployment_binding,
        "attestation_sha256",
        deployment_attestation_sha256,
        "deployment_attestation_digest_mismatch",
    )
    _require_digest(
        deployment_binding,
        "policy_sha256",
        deployment_policy_sha256,
        "deployment_policy_digest_mismatch",
    )
    if deployment_binding.get("deployment_identity_sha256") != deployment_identity_sha256:
        raise EvidenceContractError("deployment_identity_mismatch")
    verify_application_deployment_attestation(
        deployment_attestation,
        deployment_policy,
        expected=ExpectedDeploymentContext(
            environment=_text(probe.get("environment"), "collector_source_invalid"),
            repository=_text(probe.get("repository"), "collector_source_invalid"),
            git_sha=_text(probe.get("git_sha"), "collector_source_invalid"),
            nonce=nonce,
            app_endpoint_identity_sha256=_text(
                identities.get("app_endpoint_identity_sha256"),
                "collector_source_invalid",
            ),
            app_tls_spki_sha256=_text(
                identities.get("app_tls_spki_sha256"),
                "collector_source_invalid",
            ),
            workflow_run_id=_integer(workflow.get("run_id"), "collector_source_invalid"),
            workflow_run_attempt=_integer(workflow.get("run_attempt"), "collector_source_invalid"),
            main_ci_run_id=_integer(main_ci.get("run_id"), "collector_source_invalid"),
            main_ci_run_attempt=_integer(
                main_ci.get("run_attempt"),
                "collector_source_invalid",
            ),
            main_bundle_artifact_id=_integer(
                main_ci.get("bundle_artifact_id"),
                "collector_source_invalid",
            ),
            main_bundle_artifact_digest=_text(
                main_ci.get("bundle_artifact_digest"),
                "collector_source_invalid",
            ),
            deployment_identity_sha256=deployment_identity_sha256,
        ),
    )

    if trust_binding.get("workflow_trust_sha256") != workflow_trust_sha256:
        raise EvidenceContractError("workflow_trust_digest_mismatch")
    expected_checksum = f"{workflow_trust_sha256}  {workflow_trust_name}\n".encode("ascii")
    if workflow_trust_checksum != expected_checksum:
        raise EvidenceContractError("workflow_trust_checksum_invalid")
    summary = validate_trust_summary(
        workflow_trust,
        expected_repository=_text(probe.get("repository"), "collector_source_invalid"),
        expected_git_sha=_text(probe.get("git_sha"), "collector_source_invalid"),
        expected_current_role="ragflow_live",
    )
    current = _mapping(summary.get("current"), "workflow_trust_context_mismatch")
    trusted_main = _mapping(summary.get("main_ci"), "workflow_trust_context_mismatch")
    artifacts = _mapping(trusted_main.get("artifacts"), "workflow_trust_context_mismatch")
    bundle = _mapping(artifacts.get("bundle"), "workflow_trust_context_mismatch")
    if (
        current.get("workflow_path") != WORKFLOW_PATH
        or current.get("run_id") != workflow.get("run_id")
        or current.get("run_attempt") != workflow.get("run_attempt")
        or trusted_main.get("run_id") != main_ci.get("run_id")
        or trusted_main.get("run_attempt") != main_ci.get("run_attempt")
        or bundle.get("id") != main_ci.get("bundle_artifact_id")
        or bundle.get("digest") != main_ci.get("bundle_artifact_digest")
    ):
        raise EvidenceContractError("workflow_trust_context_mismatch")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--janitor", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--main-run-id", required=True)
    parser.add_argument("--main-run-attempt", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--deployment-identity-sha256", required=True)
    parser.add_argument("--owner-attestation", type=Path, required=True)
    parser.add_argument("--owner-policy", type=Path, required=True)
    parser.add_argument("--owner-policy-sha256", required=True)
    parser.add_argument("--deployment-attestation", type=Path, required=True)
    parser.add_argument("--deployment-policy", type=Path, required=True)
    parser.add_argument("--deployment-policy-sha256", required=True)
    parser.add_argument("--workflow-trust", type=Path, required=True)
    parser.add_argument("--workflow-trust-checksum", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if (
            args.workflow_trust.name != TRUST_FILENAME
            or args.workflow_trust_checksum.name != TRUST_CHECKSUM_FILENAME
        ):
            raise EvidenceContractError("workflow_trust_path_invalid")
        probe, probe_digest, _probe_raw = read_json_document(args.probe)
        janitor, janitor_digest, _janitor_raw = read_json_document(args.janitor)
        owner_attestation, owner_attestation_digest, owner_attestation_raw = read_json_document(
            args.owner_attestation
        )
        owner_policy, owner_policy_digest, owner_policy_raw = read_json_document(args.owner_policy)
        deployment_attestation, deployment_attestation_digest, deployment_attestation_raw = (
            read_json_document(args.deployment_attestation)
        )
        deployment_policy, deployment_policy_digest, deployment_policy_raw = read_json_document(
            args.deployment_policy
        )
        if owner_policy_digest != args.owner_policy_sha256:
            raise EvidenceContractError("owner_policy_trust_anchor_mismatch")
        if deployment_policy_digest != args.deployment_policy_sha256:
            raise EvidenceContractError("deployment_policy_trust_anchor_mismatch")
        workflow_trust, workflow_trust_digest, workflow_trust_raw = read_json_document(
            args.workflow_trust,
            max_bytes=1024 * 1024,
        )
        workflow_trust_checksum_raw, _checksum_digest = read_stable_bytes(
            args.workflow_trust_checksum,
            max_bytes=512,
        )
        evidence = collect_evidence(
            probe,
            janitor,
            probe_sha256=probe_digest,
            janitor_sha256=janitor_digest,
            expected_repository=args.repository,
            expected_git_sha=args.git_sha,
            expected_environment=args.environment,
            expected_run_id=_positive(args.run_id),
            expected_run_attempt=_positive(args.run_attempt),
            expected_main_run_id=_positive(args.main_run_id),
            expected_main_run_attempt=_positive(args.main_run_attempt),
        )
        _verify_sources(
            probe,
            nonce=args.nonce,
            deployment_identity_sha256=args.deployment_identity_sha256,
            owner_attestation=owner_attestation,
            owner_policy=owner_policy,
            owner_attestation_sha256=owner_attestation_digest,
            owner_policy_sha256=owner_policy_digest,
            deployment_attestation=deployment_attestation,
            deployment_policy=deployment_policy,
            deployment_attestation_sha256=deployment_attestation_digest,
            deployment_policy_sha256=deployment_policy_digest,
            workflow_trust=workflow_trust,
            workflow_trust_sha256=workflow_trust_digest,
            workflow_trust_checksum=workflow_trust_checksum_raw,
            workflow_trust_name=args.workflow_trust.name,
        )

        args.output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        evidence_path = args.output_dir / EVIDENCE_FILENAME
        digest = write_json(evidence_path, evidence)
        write_checksum(
            evidence_path.with_suffix(evidence_path.suffix + ".sha256"),
            digest=digest,
            target_name=evidence_path.name,
        )
        write_bytes(args.output_dir / OWNER_ATTESTATION_FILENAME, owner_attestation_raw)
        write_bytes(args.output_dir / OWNER_POLICY_FILENAME, owner_policy_raw)
        write_bytes(
            args.output_dir / DEPLOYMENT_ATTESTATION_FILENAME,
            deployment_attestation_raw,
        )
        write_bytes(args.output_dir / DEPLOYMENT_POLICY_FILENAME, deployment_policy_raw)
        write_bytes(
            args.output_dir / TRUST_FILENAME,
            workflow_trust_raw,
            max_bytes=1024 * 1024,
        )
        write_bytes(
            args.output_dir / TRUST_CHECKSUM_FILENAME,
            workflow_trust_checksum_raw,
            max_bytes=512,
        )
    except (
        AttestationVerificationError,
        DeploymentAttestationVerificationError,
        EvidenceContractError,
        TrustError,
        UnicodeError,
        OSError,
        ValueError,
    ):
        print("RAGFlow live evidence collection failed", file=sys.stderr)
        return 1
    print("RAGFlow live evidence collected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
