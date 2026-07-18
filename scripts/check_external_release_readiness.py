"""Fail closed when a required real external-service gate has no trusted binding."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from scripts import release_oci
else:
    try:
        from scripts import release_oci
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        release_oci = importlib.import_module("release_oci")


@dataclass(frozen=True)
class ExternalEvidenceBinding:
    """Existing evidence that is provenance-bound into release authorization."""

    filename: str
    evidence_schema: str
    source_schema: str
    collector: str
    workflow: str


REQUIRED_EXTERNAL_GATE_IDS: Final[frozenset[str]] = frozenset(
    {
        "EXT-SMTP-001",
        "EXT-WEBHOOK-001",
        "EXT-LLM-001",
        "EXT-RAGFLOW-001",
    }
)
EXTERNAL_GATE_BINDINGS: Final[dict[str, ExternalEvidenceBinding | None]] = {
    "EXT-SMTP-001": ExternalEvidenceBinding(
        filename="email-delivery.json",
        evidence_schema="knowledge-uploader.smtp-delivery-evidence.v1",
        source_schema="knowledge-uploader.smtp-delivery-source.v1",
        collector="smtp-delivery-probe",
        workflow=".github/workflows/protected-external-evidence.yml",
    ),
    "EXT-WEBHOOK-001": ExternalEvidenceBinding(
        filename="alertmanager-notification.json",
        evidence_schema="knowledge-uploader.alertmanager-webhook-evidence.v1",
        source_schema="knowledge-uploader.alertmanager-webhook-source.v1",
        collector="alertmanager-webhook-receiver",
        workflow=".github/workflows/protected-external-evidence.yml",
    ),
    # Do not replace these with a repository-local receipt. Each gate remains
    # unbound until an independent protected workflow and trusted attestation exist.
    "EXT-LLM-001": None,
    "EXT-RAGFLOW-001": None,
}


def contract_errors() -> list[str]:
    """Validate the binding registry without requiring all gates to be ready."""
    errors: list[str] = []
    declared_ids = frozenset(EXTERNAL_GATE_BINDINGS)
    if declared_ids != REQUIRED_EXTERNAL_GATE_IDS:
        missing = sorted(REQUIRED_EXTERNAL_GATE_IDS - declared_ids)
        unexpected = sorted(declared_ids - REQUIRED_EXTERNAL_GATE_IDS)
        if missing:
            errors.append("missing required external gate IDs: " + ", ".join(missing))
        if unexpected:
            errors.append("unexpected external gate IDs: " + ", ".join(unexpected))

    for gate_id, binding in sorted(EXTERNAL_GATE_BINDINGS.items()):
        if binding is None:
            continue
        expected_contract = (
            binding.evidence_schema,
            binding.source_schema,
            binding.collector,
        )
        if release_oci.EXTERNAL_EVIDENCE_CONTRACTS.get(binding.filename) != expected_contract:
            errors.append(f"{gate_id} evidence contract is not authorization-bound")
        if binding.filename not in release_oci.REQUIRED_RELEASE_EVIDENCE:
            errors.append(f"{gate_id} evidence is absent from the authorization inventory")
        if binding.workflow != release_oci.EXTERNAL_WORKFLOW:
            errors.append(f"{gate_id} evidence is not produced by the trusted external workflow")
    return errors


def readiness_errors() -> list[str]:
    """Return contract errors plus every intentionally unbound release blocker."""
    errors = contract_errors()
    unbound = sorted(
        gate_id for gate_id, binding in EXTERNAL_GATE_BINDINGS.items() if binding is None
    )
    if unbound:
        errors.append("unbound external release evidence gates: " + ", ".join(unbound))
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate trusted evidence bindings for real external-service release gates."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--contract-only",
        action="store_true",
        help="Validate the registry while allowing explicitly unbound gates.",
    )
    mode.add_argument(
        "--require-ready",
        action="store_true",
        help="Fail unless every required external gate has a trusted evidence binding.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    errors = readiness_errors() if arguments.require_ready else contract_errors()
    if errors:
        for error in errors:
            sys.stderr.write(f"external release readiness failed: {error}\n")
        return 1
    sys.stdout.write("external release evidence binding contract is valid\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
