"""Run the protected RAGFlow live probe or its independent janitor.

The command reads all credentials from step-scoped environment variables.  It emits only
hashed identities and bounded state assertions; URLs, credentials, JWTs, filenames,
documents and remote response bodies never enter evidence or error output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import re
import stat
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn

import httpx

from app.adapters.ragflow.base import (
    RagflowClientError,
    RagflowDocumentNotFoundError,
)
from app.adapters.ragflow.http import HttpRagflowClient
from app.adapters.ragflow.safe_transport import (
    SystemHostResolver,
    build_pinned_ragflow_transport,
    resolve_and_authorize_ragflow_endpoint,
)
from app.core.ragflow_endpoint import (
    normalize_ragflow_base_url,
    normalize_ragflow_spki_pin,
    ragflow_endpoint_identity,
)

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

CONTRACT_VERSION = _contract.CONTRACT_VERSION
JANITOR_SCHEMA = _contract.JANITOR_SCHEMA
PROBE_SCHEMA = _contract.PROBE_SCHEMA
REQUIREMENT_ID = _contract.REQUIREMENT_ID
WORKFLOW_PATH = _contract.WORKFLOW_PATH
EvidenceContractError = _contract.EvidenceContractError
sha256_text = _contract.sha256_text
validate_janitor = _contract.validate_janitor
validate_probe = _contract.validate_probe
write_json = _contract.write_json

TrustError = _trust.TrustError
validate_trust_summary = _trust.validate_trust_summary

MAX_DEPLOYMENT_ATTESTATION_BYTES = _deployment_verifier.MAX_ATTESTATION_BYTES
MAX_DEPLOYMENT_POLICY_BYTES = _deployment_verifier.MAX_POLICY_BYTES
DeploymentAttestationVerificationError = _deployment_verifier.DeploymentAttestationVerificationError
ExpectedDeploymentContext = _deployment_verifier.ExpectedDeploymentContext
verify_application_deployment_attestation = (
    _deployment_verifier.verify_application_deployment_attestation
)

MAX_ENDPOINT_ATTESTATION_BYTES = _owner_verifier.MAX_ATTESTATION_BYTES
MAX_ENDPOINT_POLICY_BYTES = _owner_verifier.MAX_POLICY_BYTES
AttestationVerificationError = _owner_verifier.AttestationVerificationError
ExpectedContext = _owner_verifier.ExpectedContext
verify_attestation = _owner_verifier.verify_attestation

MAX_APP_RESPONSE_BYTES: Final = 1024 * 1024
MAX_REMOTE_DOCUMENTS: Final = 1000
REMOTE_PAGE_SIZE: Final = 100
REMOTE_MAX_PAGES: Final = 20
SUCCESS_PARSE_RUNS: Final = frozenset({"3", "DONE"})
TERMINAL_TASK_STATUSES: Final = frozenset({"succeeded", "failed", "canceled"})
CANARY_CONTENT: Final = b"Knowledge Uploader protected RAGFlow live probe.\n"
CANARY_PREFIX: Final = "ku-ragflow-live"
RECONCILIATION_LOG: Final = "ragflow document reconciled after interrupted upload"
UPLOAD_LOGS: Final = frozenset(
    {"ragflow document remote upload requested", "ragflow document uploaded"}
)
PARSE_START_LOG: Final = "ragflow document parse started"
NONCE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{32,128}")
HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENVIRONMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{1,31}")

APP_BASE_URL_ENV: Final = "KU_APP_BASE_URL"
APP_TLS_PIN_ENV: Final = "KU_APP_TLS_SPKI_PIN"
EMPLOYEE_EMAIL_ENV: Final = "KU_EMPLOYEE_EMAIL"
EMPLOYEE_PASSWORD_ENV: Final = "KU_EMPLOYEE_PASSWORD"
ADMIN_EMAIL_ENV: Final = "KU_ADMIN_EMAIL"
ADMIN_PASSWORD_ENV: Final = "KU_ADMIN_PASSWORD"
DATASET_MAPPING_ID_ENV: Final = "KU_DATASET_MAPPING_ID"
RAGFLOW_BASE_URL_ENV: Final = "KU_RAGFLOW_BASE_URL"
RAGFLOW_API_KEY_ENV: Final = "KU_RAGFLOW_API_KEY"
RAGFLOW_DATASET_ID_ENV: Final = "KU_RAGFLOW_DATASET_ID"
RAGFLOW_TLS_PIN_ENV: Final = "KU_RAGFLOW_TLS_SPKI_PIN"


class LiveProbeError(RuntimeError):
    """Fail-closed error with a non-sensitive machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class RunContext:
    environment: str
    repository: str
    git_sha: str
    run_id: int
    run_attempt: int
    main_run_id: int
    main_run_attempt: int
    nonce: str
    workflow_trust_path: Path
    owner_attestation_path: Path
    owner_policy_path: Path
    owner_policy_sha256: str
    deployment_attestation_path: Path
    deployment_policy_path: Path
    deployment_policy_sha256: str
    deployment_identity_sha256: str
    timeout_seconds: int


@dataclass(frozen=True)
class TrustBinding:
    workflow_trust_sha256: str
    bundle_artifact_id: int
    bundle_artifact_digest: str


@dataclass(frozen=True)
class OwnerBinding:
    attestation_sha256: str
    policy_sha256: str
    nonce_sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class DeploymentBinding:
    attestation_sha256: str
    policy_sha256: str
    deployment_identity_sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class ProofDocuments:
    owner_attestation: Mapping[str, object]
    owner_policy: Mapping[str, object]
    deployment_attestation: Mapping[str, object]
    deployment_policy: Mapping[str, object]
    owner_attestation_sha256: str
    owner_policy_sha256: str
    deployment_attestation_sha256: str

    deployment_policy_sha256: str


@dataclass(frozen=True)
class Secrets:
    app_base_url: str
    app_tls_pin: bytes
    employee_email: str
    employee_password: str
    admin_email: str
    admin_password: str
    dataset_mapping_id: str
    ragflow_base_url: str
    ragflow_api_key: str
    ragflow_dataset_id: str
    ragflow_tls_pin: bytes


@dataclass(frozen=True)
class IdentityBinding:
    endpoint_identity_sha256: str
    tls_spki_sha256: str
    dataset_identity_sha256: str
    app_endpoint_identity_sha256: str
    app_tls_spki_sha256: str


@dataclass(frozen=True)
class RemoteDocument:
    document_id: str
    name: str


class Deadline:
    def __init__(self, seconds: int) -> None:
        self._expires = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._expires - time.monotonic())

    async def pause(self, seconds: float = 2.0) -> None:
        remaining = self.remaining()
        if remaining <= 0:
            raise LiveProbeError("probe_timeout")
        await asyncio.sleep(min(seconds, remaining))


def _raise(code: str) -> NoReturn:
    raise LiveProbeError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _raise(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, list):
        _raise(code)
    return value


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _raise(code)
    return value


def _required_env(name: str, *, strip: bool = False) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        _raise("protected_input_missing")
    cleaned = value.strip() if strip else value
    if not cleaned or "\x00" in cleaned:
        _raise("protected_input_invalid")
    return cleaned


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _load_secrets() -> Secrets:
    app_base_url = normalize_ragflow_base_url(_required_env(APP_BASE_URL_ENV, strip=True))
    ragflow_base_url = normalize_ragflow_base_url(_required_env(RAGFLOW_BASE_URL_ENV, strip=True))
    if not app_base_url.startswith("https://") or not ragflow_base_url.startswith("https://"):
        _raise("protected_https_required")
    app_pin = normalize_ragflow_spki_pin(_required_env(APP_TLS_PIN_ENV, strip=True))
    ragflow_pin = normalize_ragflow_spki_pin(_required_env(RAGFLOW_TLS_PIN_ENV, strip=True))
    if (
        ragflow_endpoint_identity(app_base_url)[1] != ragflow_endpoint_identity(ragflow_base_url)[1]
        and app_pin == ragflow_pin
    ):
        _raise("cross_host_pin_reuse")

    dataset_mapping_id = _required_env(DATASET_MAPPING_ID_ENV, strip=True)
    try:
        uuid.UUID(dataset_mapping_id)
    except ValueError:
        _raise("dataset_mapping_invalid")
    dataset_id = _required_env(RAGFLOW_DATASET_ID_ENV, strip=True)
    if len(dataset_id) > 256:
        _raise("dataset_identity_invalid")
    return Secrets(
        app_base_url=app_base_url,
        app_tls_pin=app_pin,
        employee_email=_required_env(EMPLOYEE_EMAIL_ENV, strip=True),
        employee_password=_required_env(EMPLOYEE_PASSWORD_ENV),
        admin_email=_required_env(ADMIN_EMAIL_ENV, strip=True),
        admin_password=_required_env(ADMIN_PASSWORD_ENV),
        dataset_mapping_id=dataset_mapping_id,
        ragflow_base_url=ragflow_base_url,
        ragflow_api_key=_required_env(RAGFLOW_API_KEY_ENV),
        ragflow_dataset_id=dataset_id,
        ragflow_tls_pin=ragflow_pin,
    )


def _identity_binding(secrets: Secrets) -> IdentityBinding:
    return IdentityBinding(
        endpoint_identity_sha256=sha256_text(secrets.ragflow_base_url),
        tls_spki_sha256=secrets.ragflow_tls_pin.hex(),
        dataset_identity_sha256=sha256_text(secrets.ragflow_dataset_id),
        app_endpoint_identity_sha256=sha256_text(secrets.app_base_url),
        app_tls_spki_sha256=secrets.app_tls_pin.hex(),
    )


def _read_stable_bytes(
    path: Path, *, max_bytes: int, code: str = "workflow_trust_invalid"
) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            _raise(code)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size < 1
            or opened.st_size > max_bytes
        ):
            _raise(code)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError:
        _raise(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    if (
        len(raw) != opened.st_size
        or len(raw) > max_bytes
        or identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        or not stat.S_ISREG(current.st_mode)
        or b"\x00" in raw
    ):
        _raise(code)
    return raw


def _load_json_proof(path: Path, *, max_bytes: int) -> tuple[Mapping[str, object], str]:
    raw = _read_stable_bytes(path, max_bytes=max_bytes, code="protected_proof_invalid")
    try:
        value: object = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
        _DuplicateJsonKey,
    ):
        _raise("protected_proof_invalid")
    return _mapping(value, "protected_proof_invalid"), hashlib.sha256(raw).hexdigest()


def _load_proof_documents(context: RunContext) -> ProofDocuments:
    owner_attestation, owner_attestation_sha256 = _load_json_proof(
        context.owner_attestation_path,
        max_bytes=MAX_ENDPOINT_ATTESTATION_BYTES,
    )
    owner_policy, owner_policy_sha256 = _load_json_proof(
        context.owner_policy_path,
        max_bytes=MAX_ENDPOINT_POLICY_BYTES,
    )
    deployment_attestation, deployment_attestation_sha256 = _load_json_proof(
        context.deployment_attestation_path,
        max_bytes=MAX_DEPLOYMENT_ATTESTATION_BYTES,
    )
    deployment_policy, deployment_policy_sha256 = _load_json_proof(
        context.deployment_policy_path,
        max_bytes=MAX_DEPLOYMENT_POLICY_BYTES,
    )
    if owner_policy_sha256 != context.owner_policy_sha256:
        _raise("owner_policy_trust_anchor_mismatch")
    if deployment_policy_sha256 != context.deployment_policy_sha256:
        _raise("deployment_policy_trust_anchor_mismatch")
    return ProofDocuments(
        owner_attestation=owner_attestation,
        owner_policy=owner_policy,
        deployment_attestation=deployment_attestation,
        deployment_policy=deployment_policy,
        owner_attestation_sha256=owner_attestation_sha256,
        owner_policy_sha256=owner_policy_sha256,
        deployment_attestation_sha256=deployment_attestation_sha256,
        deployment_policy_sha256=deployment_policy_sha256,
    )


def _load_trust_binding(context: RunContext) -> TrustBinding:
    raw = _read_stable_bytes(context.workflow_trust_path, max_bytes=1024 * 1024)
    digest = hashlib.sha256(raw).hexdigest()
    checksum_path = context.workflow_trust_path.with_suffix(
        context.workflow_trust_path.suffix + ".sha256"
    )
    checksum = _read_stable_bytes(checksum_path, max_bytes=512)
    expected_checksum = f"{digest}  {context.workflow_trust_path.name}\n".encode("ascii")
    if checksum != expected_checksum:
        _raise("workflow_trust_checksum_invalid")
    try:
        value: object = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        summary = validate_trust_summary(
            value,
            expected_repository=context.repository,
            expected_git_sha=context.git_sha,
            expected_current_role="ragflow_live",
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, TrustError, _DuplicateJsonKey):
        _raise("workflow_trust_invalid")
    current = _mapping(summary.get("current"), "workflow_trust_invalid")
    main = _mapping(summary.get("main_ci"), "workflow_trust_invalid")
    artifacts = _mapping(main.get("artifacts"), "workflow_trust_invalid")
    bundle = _mapping(artifacts.get("bundle"), "workflow_trust_invalid")
    if (
        current.get("workflow_path") != WORKFLOW_PATH
        or current.get("run_id") != context.run_id
        or current.get("run_attempt") != context.run_attempt
        or main.get("run_id") != context.main_run_id
        or main.get("run_attempt") != context.main_run_attempt
    ):
        _raise("workflow_trust_context_mismatch")
    artifact_id = bundle.get("id")
    artifact_digest = bundle.get("digest")
    if type(artifact_id) is not int or artifact_id < 1:
        _raise("workflow_trust_invalid")
    if (
        not isinstance(artifact_digest, str)
        or not artifact_digest.startswith("sha256:")
        or HASH_PATTERN.fullmatch(artifact_digest.removeprefix("sha256:")) is None
    ):
        _raise("workflow_trust_invalid")
    return TrustBinding(
        workflow_trust_sha256=hashlib.sha256(raw).hexdigest(),
        bundle_artifact_id=artifact_id,
        bundle_artifact_digest=artifact_digest,
    )


def _verify_owner_binding(
    *,
    context: RunContext,
    identities: IdentityBinding,
    documents: ProofDocuments,
) -> OwnerBinding:
    expected = ExpectedContext(
        service_kind="ragflow",
        environment=context.environment,
        repository=context.repository,
        git_sha=context.git_sha,
        endpoint_identity_sha256=identities.endpoint_identity_sha256,
        tls_spki_sha256=identities.tls_spki_sha256,
        nonce=context.nonce,
        workflow_run_id=context.run_id,
        workflow_run_attempt=context.run_attempt,
        dataset_identity_sha256=identities.dataset_identity_sha256,
    )
    try:
        verify_attestation(documents.owner_attestation, documents.owner_policy, expected=expected)
    except AttestationVerificationError:
        _raise("owner_attestation_invalid")
    payload = _mapping(documents.owner_attestation.get("payload"), "owner_attestation_invalid")
    expires_value = payload.get("expires_at")
    if not isinstance(expires_value, str):
        _raise("owner_attestation_invalid")
    try:
        expires_at = datetime.strptime(expires_value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _raise("owner_attestation_invalid")
    minimum_remaining = timedelta(seconds=context.timeout_seconds + 60)
    if expires_at - datetime.now(UTC) < minimum_remaining:
        _raise("owner_attestation_ttl_insufficient")
    return OwnerBinding(
        attestation_sha256=documents.owner_attestation_sha256,
        policy_sha256=documents.owner_policy_sha256,
        nonce_sha256=sha256_text(context.nonce),
        expires_at=expires_at,
    )


def _verify_deployment_binding(
    *,
    context: RunContext,
    trust: TrustBinding,
    identities: IdentityBinding,
    documents: ProofDocuments,
) -> DeploymentBinding:
    expected = ExpectedDeploymentContext(
        environment=context.environment,
        repository=context.repository,
        git_sha=context.git_sha,
        nonce=context.nonce,
        app_endpoint_identity_sha256=identities.app_endpoint_identity_sha256,
        app_tls_spki_sha256=identities.app_tls_spki_sha256,
        workflow_run_id=context.run_id,
        workflow_run_attempt=context.run_attempt,
        main_ci_run_id=context.main_run_id,
        main_ci_run_attempt=context.main_run_attempt,
        main_bundle_artifact_id=trust.bundle_artifact_id,
        main_bundle_artifact_digest=trust.bundle_artifact_digest,
        deployment_identity_sha256=context.deployment_identity_sha256,
    )
    try:
        verify_application_deployment_attestation(
            documents.deployment_attestation,
            documents.deployment_policy,
            expected=expected,
        )
    except DeploymentAttestationVerificationError:
        _raise("deployment_attestation_invalid")
    payload = _mapping(
        documents.deployment_attestation.get("payload"),
        "deployment_attestation_invalid",
    )
    expires_value = payload.get("expires_at")
    if not isinstance(expires_value, str):
        _raise("deployment_attestation_invalid")
    try:
        expires_at = datetime.strptime(expires_value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _raise("deployment_attestation_invalid")
    minimum_remaining = timedelta(seconds=context.timeout_seconds + 60)
    if expires_at - datetime.now(UTC) < minimum_remaining:
        _raise("deployment_attestation_ttl_insufficient")
    return DeploymentBinding(
        attestation_sha256=documents.deployment_attestation_sha256,
        policy_sha256=documents.deployment_policy_sha256,
        deployment_identity_sha256=context.deployment_identity_sha256,
        expires_at=expires_at,
    )


def _now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assert_owner_fresh(owner: OwnerBinding) -> None:
    if datetime.now(UTC) >= owner.expires_at:
        _raise("owner_attestation_expired")


def _assert_deployment_fresh(deployment: DeploymentBinding) -> None:
    if datetime.now(UTC) >= deployment.expires_at:
        _raise("deployment_attestation_expired")


class ProtectedAppClient:
    def __init__(self, *, base_url: str, tls_pin: bytes) -> None:
        self._base_url = base_url
        self._tls_pin = tls_pin
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ProtectedAppClient:
        endpoint = await resolve_and_authorize_ragflow_endpoint(
            base_url=self._base_url,
            protected_environment=True,
            tls_spki_pins=frozenset({self._tls_pin}),
            resolver=SystemHostResolver(),
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            transport=build_pinned_ragflow_transport(endpoint),
            trust_env=False,
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: object | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        params: Mapping[str, str | int] | None = None,
        allowed_statuses: frozenset[int] = frozenset({200}),
    ) -> object:
        if self._client is None or not path.startswith("/api/") or "://" in path:
            _raise("app_request_invalid")
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        status_code = 0
        try:
            async with self._client.stream(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json_body,
                data=data,
                files=files,
                params=params,
                follow_redirects=False,
            ) as response:
                status_code = response.status_code
                if response.status_code not in allowed_statuses:
                    _raise(f"app_http_{response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_APP_RESPONSE_BYTES:
                        _raise("app_response_oversized")
                    body.extend(chunk)
        except LiveProbeError:
            raise
        except httpx.HTTPError:
            _raise("app_transport_failed")
        if status_code == 404:
            return {}
        if not body:
            return {}
        try:
            value: object = json.loads(
                bytes(body).decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError, _DuplicateJsonKey):
            _raise("app_response_invalid")
        envelope = _mapping(value, "app_response_invalid")
        if envelope.get("success") is not True or "data" not in envelope:
            _raise("app_response_invalid")
        return envelope["data"]

    async def login(self, *, email: str, password: str, expected_roles: frozenset[str]) -> str:
        data = _mapping(
            await self.request(
                "POST",
                "/api/auth/login",
                json_body={"email": email, "password": password},
            ),
            "login_response_invalid",
        )
        token = _required_text(data.get("access_token"), "login_response_invalid")
        user = _mapping(data.get("user"), "login_response_invalid")
        if (
            user.get("role") not in expected_roles
            or user.get("status") != "active"
            or user.get("email_verified") is not True
            or user.get("department_assigned") is not True
        ):
            _raise("service_account_invalid")
        return token


class ProbeRagflowClient(HttpRagflowClient):
    async def list_documents(
        self,
        *,
        dataset_id: str,
        keywords: str | None = None,
    ) -> list[RemoteDocument]:
        documents: dict[str, RemoteDocument] = {}
        seen_pages: set[tuple[tuple[object, object], ...]] = set()
        for page in range(1, REMOTE_MAX_PAGES + 1):
            params: dict[str, str | int] = {"page": page, "page_size": REMOTE_PAGE_SIZE}
            if keywords is not None:
                params["keywords"] = keywords
            payload = await self._request(
                "GET",
                f"/api/v1/datasets/{dataset_id}/documents",
                params=params,
            )
            raw_documents = self._extract_documents(payload)
            if not raw_documents:
                return list(documents.values())
            signature = tuple((item.get("id"), item.get("name")) for item in raw_documents)
            if signature in seen_pages:
                _raise("remote_pagination_invalid")
            seen_pages.add(signature)
            for item in raw_documents:
                document_id = item.get("id")
                name = item.get("name")
                if not isinstance(document_id, str) or not document_id:
                    _raise("remote_document_invalid")
                if not isinstance(name, str) or not name:
                    _raise("remote_document_invalid")
                existing = documents.get(document_id)
                candidate = RemoteDocument(document_id=document_id, name=name)
                if existing is not None and existing != candidate:
                    _raise("remote_document_invalid")
                documents[document_id] = candidate
                if len(documents) > MAX_REMOTE_DOCUMENTS:
                    _raise("remote_dataset_oversized")
            total = self._extract_total(payload)
            if total is not None and len(documents) >= total:
                return list(documents.values())
        _raise("remote_pagination_limit")


def _remote_client(secrets: Secrets) -> ProbeRagflowClient:
    return ProbeRagflowClient(
        base_url=secrets.ragflow_base_url,
        api_key=secrets.ragflow_api_key,
        timeout_seconds=30.0,
        protected_environment=True,
        tls_spki_pins=frozenset({secrets.ragflow_tls_pin}),
    )


def _remote_counts(
    documents: Sequence[RemoteDocument],
    *,
    exact_name: str,
) -> tuple[int, list[RemoteDocument]]:
    matches = [document for document in documents if document.name == exact_name]
    return len(documents), matches


def _task_logs(task: Mapping[str, object]) -> tuple[bool, bool, bool]:
    messages: set[str] = set()
    for raw in _sequence(task.get("logs"), "task_logs_invalid"):
        log = _mapping(raw, "task_logs_invalid")
        message = log.get("message")
        if isinstance(message, str):
            messages.add(message)
    return (
        RECONCILIATION_LOG in messages,
        bool(messages & UPLOAD_LOGS),
        PARSE_START_LOG in messages,
    )


def _task_id(task: Mapping[str, object]) -> str:
    value = _required_text(task.get("id"), "task_invalid")
    try:
        uuid.UUID(value)
    except ValueError:
        _raise("task_invalid")
    return value


async def _get_task(
    app: ProtectedAppClient,
    *,
    admin_token: str,
    task_id: str,
) -> Mapping[str, object]:
    return _mapping(
        await app.request("GET", f"/api/tasks/{task_id}", token=admin_token),
        "task_invalid",
    )


async def _list_tasks(
    app: ProtectedAppClient,
    *,
    admin_token: str,
    file_id: str,
) -> list[Mapping[str, object]]:
    data = _mapping(
        await app.request(
            "GET",
            "/api/tasks",
            token=admin_token,
            params={"file_id": file_id},
        ),
        "task_list_invalid",
    )
    return [
        _mapping(item, "task_list_invalid")
        for item in _sequence(data.get("items"), "task_list_invalid")
    ]


async def _wait_task_terminal(
    app: ProtectedAppClient,
    *,
    admin_token: str,
    task_id: str,
    expected_task_type: str,
    deadline: Deadline,
) -> Mapping[str, object]:
    while True:
        task = await _get_task(app, admin_token=admin_token, task_id=task_id)
        if task.get("task_type") != expected_task_type:
            _raise("task_type_mismatch")
        status = task.get("status")
        if status in TERMINAL_TASK_STATUSES:
            if status != "succeeded":
                _raise("task_failed")
            return task
        if status not in {"queued", "running"}:
            _raise("task_invalid")
        await deadline.pause()


async def _wait_new_task(
    app: ProtectedAppClient,
    *,
    admin_token: str,
    file_id: str,
    expected_task_type: str,
    excluded_ids: frozenset[str],
    deadline: Deadline,
) -> Mapping[str, object]:
    while True:
        candidates = [
            task
            for task in await _list_tasks(app, admin_token=admin_token, file_id=file_id)
            if task.get("task_type") == expected_task_type and task.get("id") not in excluded_ids
        ]
        if len(candidates) > 1:
            _raise("duplicate_sync_task")
        if candidates:
            task_id = _task_id(candidates[0])
            return await _wait_task_terminal(
                app,
                admin_token=admin_token,
                task_id=task_id,
                expected_task_type=expected_task_type,
                deadline=deadline,
            )
        await deadline.pause()


def _file_detail(value: object, *, expected_file_id: str) -> Mapping[str, object]:
    detail = _mapping(value, "file_detail_invalid")
    if detail.get("id") != expected_file_id:
        _raise("file_detail_invalid")
    return detail


def _assert_synced_file(
    detail: Mapping[str, object],
    *,
    dataset_id: str,
    remote_document_id: str,
    dataset_mapping_id: str,
) -> str:
    run = detail.get("ragflow_parse_status")
    category_id = _required_text(detail.get("category_id"), "app_sync_state_invalid")
    try:
        uuid.UUID(category_id)
    except ValueError:
        _raise("app_sync_state_invalid")
    if (
        detail.get("status") != "parsed"
        or detail.get("review_status") != "approved"
        or detail.get("ragflow_dataset_id") != dataset_id
        or detail.get("dataset_mapping_id") != dataset_mapping_id
        or detail.get("ragflow_document_id") != remote_document_id
        or detail.get("is_current_version") is not True
        or detail.get("remote_visibility") != "current"
        or detail.get("version_switch_status") != "not_required"
        or not isinstance(run, str)
        or run.upper() not in SUCCESS_PARSE_RUNS
    ):
        _raise("app_sync_state_invalid")
    return run.upper()


def _base_binding(
    *,
    context: RunContext,
    trust: TrustBinding,
    owner: OwnerBinding,
    deployment: DeploymentBinding,
) -> dict[str, object]:
    return {
        "environment": context.environment,
        "repository": context.repository,
        "git_sha": context.git_sha,
        "workflow": {
            "path": WORKFLOW_PATH,
            "run_id": context.run_id,
            "run_attempt": context.run_attempt,
        },
        "main_ci": {
            "run_id": context.main_run_id,
            "run_attempt": context.main_run_attempt,
            "bundle_artifact_id": trust.bundle_artifact_id,
            "bundle_artifact_digest": trust.bundle_artifact_digest,
        },
        "trust": {"workflow_trust_sha256": trust.workflow_trust_sha256},
        "owner_attestation": {
            "attestation_sha256": owner.attestation_sha256,
            "policy_sha256": owner.policy_sha256,
            "nonce_sha256": owner.nonce_sha256,
        },
        "deployment_attestation": {
            "attestation_sha256": deployment.attestation_sha256,
            "policy_sha256": deployment.policy_sha256,
            "deployment_identity_sha256": deployment.deployment_identity_sha256,
        },
    }


def _replace_manifest(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    content = (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


async def _emergency_cleanup(
    *,
    app: ProtectedAppClient | None,
    admin_token: str | None,
    file_id: str | None,
    remote: ProbeRagflowClient,
    dataset_id: str,
    remote_name: str | None,
    owner: OwnerBinding,
) -> tuple[bool, int, int]:
    if app is not None and admin_token is not None and file_id is not None:
        try:
            await app.request(
                "DELETE",
                f"/api/files/{file_id}",
                token=admin_token,
                allowed_statuses=frozenset({200, 404}),
            )
        except Exception:
            pass
    if remote_name is None:
        return True, 0, 0
    try:
        _assert_owner_fresh(owner)
        documents = await remote.list_documents(dataset_id=dataset_id, keywords=remote_name)
        _, matches = _remote_counts(documents, exact_name=remote_name)
        for document in matches:
            _assert_owner_fresh(owner)
            try:
                await remote.delete_document(
                    dataset_id=dataset_id, document_id=document.document_id
                )
            except RagflowDocumentNotFoundError:
                pass
        _assert_owner_fresh(owner)
        remaining = await remote.list_documents(dataset_id=dataset_id)
        _, exact = _remote_counts(remaining, exact_name=remote_name)
        return len(exact) == 0, len(remaining), len(exact)
    except Exception:
        return False, -1, -1


async def run_probe(
    *,
    context: RunContext,
    manifest_path: Path,
) -> Mapping[str, object]:
    started_at = _now_text()
    trust = _load_trust_binding(context)
    documents = _load_proof_documents(context)
    secrets = _load_secrets()
    identities = _identity_binding(secrets)
    owner = _verify_owner_binding(
        context=context,
        identities=identities,
        documents=documents,
    )
    deployment = _verify_deployment_binding(
        context=context,
        trust=trust,
        identities=identities,
        documents=documents,
    )
    # No network call is permitted before every independent trust document passes.
    _assert_owner_fresh(owner)
    _assert_deployment_fresh(deployment)
    deadline = Deadline(context.timeout_seconds)
    filename = f"{CANARY_PREFIX}-{sha256_text(context.nonce)[:16]}.txt"
    remote = _remote_client(secrets)
    app: ProtectedAppClient | None = None
    admin_token: str | None = None
    file_id: str | None = None
    remote_name: str | None = None
    application_cleanup_confirmed = False
    emergency_cleanup_used = False
    manifest: dict[str, object] = {
        "schema": "knowledge-uploader.ragflow-live-janitor-manifest.v1",
        "version": CONTRACT_VERSION,
        **_base_binding(context=context, trust=trust, owner=owner, deployment=deployment),
        "identities": {
            "endpoint_identity_sha256": identities.endpoint_identity_sha256,
            "tls_spki_sha256": identities.tls_spki_sha256,
            "dataset_identity_sha256": identities.dataset_identity_sha256,
            "app_endpoint_identity_sha256": identities.app_endpoint_identity_sha256,
            "app_tls_spki_sha256": identities.app_tls_spki_sha256,
            "canary_filename_sha256": sha256_text(filename),
            "app_file_id_sha256": None,
            "remote_name_sha256": None,
        },
        "cleanup": {"confirmed": False, "emergency_direct_cleanup_used": False},
    }
    _replace_manifest(manifest_path, manifest)
    try:
        _assert_owner_fresh(owner)
        _assert_deployment_fresh(deployment)
        initial_documents = await remote.list_documents(dataset_id=secrets.ragflow_dataset_id)
        if initial_documents:
            _raise("dataset_not_initially_empty")

        async with ProtectedAppClient(
            base_url=secrets.app_base_url,
            tls_pin=secrets.app_tls_pin,
        ) as protected_app:
            app = protected_app
            employee_token = await app.login(
                email=secrets.employee_email,
                password=secrets.employee_password,
                expected_roles=frozenset({"employee"}),
            )
            admin_token = await app.login(
                email=secrets.admin_email,
                password=secrets.admin_password,
                expected_roles=frozenset({"dept_admin", "system_admin"}),
            )
            uploaded = _mapping(
                await app.request(
                    "POST",
                    "/api/files/upload",
                    token=employee_token,
                    data={
                        "submit_after_upload": "false",
                        "visibility": "private",
                        "ai_analysis_enabled": "false",
                        "description": "Protected RAGFlow live evidence",
                    },
                    files={"file": (filename, CANARY_CONTENT, "text/plain")},
                    allowed_statuses=frozenset({201}),
                ),
                "upload_response_invalid",
            )
            file_id = _required_text(uploaded.get("id"), "upload_response_invalid")
            try:
                uuid.UUID(file_id)
            except ValueError:
                _raise("upload_response_invalid")
            if uploaded.get("original_name") != filename or uploaded.get("status") != "uploaded":
                _raise("upload_response_invalid")
            remote_name = f"{file_id}-{filename}"
            manifest_identities = _mapping(manifest["identities"], "manifest_invalid")
            manifest_identities["app_file_id_sha256"] = sha256_text(file_id)  # type: ignore[index]
            manifest_identities["remote_name_sha256"] = sha256_text(remote_name)  # type: ignore[index]
            _replace_manifest(manifest_path, manifest)

            _assert_owner_fresh(owner)
            initial_named = await remote.list_documents(
                dataset_id=secrets.ragflow_dataset_id,
                keywords=remote_name,
            )
            _, initial_matches = _remote_counts(initial_named, exact_name=remote_name)
            if initial_matches:
                _raise("same_name_pollution")
            _assert_owner_fresh(owner)
            preseed = await remote.upload_document(
                dataset_id=secrets.ragflow_dataset_id,
                filename=remote_name,
                content=CANARY_CONTENT,
                content_type="text/plain",
            )
            remote_document_id = preseed.document_id
            _assert_owner_fresh(owner)
            preseed_documents = await remote.list_documents(dataset_id=secrets.ragflow_dataset_id)
            preseed_total, preseed_matches = _remote_counts(
                preseed_documents,
                exact_name=remote_name,
            )
            if (
                preseed_total != 1
                or len(preseed_matches) != 1
                or preseed_matches[0].document_id != remote_document_id
            ):
                _raise("preseed_commit_unconfirmed")

            submitted = _mapping(
                await app.request(
                    "POST",
                    f"/api/files/{file_id}/submit-review",
                    token=employee_token,
                    json_body={"acknowledge_sensitive_risk": False},
                ),
                "submit_response_invalid",
            )
            if submitted.get("status") != "pending_review":
                _raise("submit_response_invalid")
            await app.request(
                "POST",
                f"/api/review/files/{file_id}/claim",
                token=admin_token,
            )
            approved = _mapping(
                await app.request(
                    "POST",
                    f"/api/files/{file_id}/approve",
                    token=admin_token,
                    json_body={
                        "sync_decision": "sync",
                        "dataset_mapping_id": secrets.dataset_mapping_id,
                        "reason": "protected live preseed reconciliation",
                    },
                ),
                "approve_response_invalid",
            )
            if (
                approved.get("review_status") != "approved"
                or approved.get("ragflow_dataset_id") != secrets.ragflow_dataset_id
            ):
                _raise("approve_response_invalid")

            first_task = await _wait_new_task(
                app,
                admin_token=admin_token,
                file_id=file_id,
                expected_task_type="ragflow_upload",
                excluded_ids=frozenset(),
                deadline=deadline,
            )
            first_task_id = _task_id(first_task)
            first_reconciled, first_uploaded, first_parse_started = _task_logs(first_task)
            first_detail = _file_detail(
                await app.request("GET", f"/api/files/{file_id}", token=employee_token),
                expected_file_id=file_id,
            )
            first_app_run = _assert_synced_file(
                first_detail,
                dataset_id=secrets.ragflow_dataset_id,
                dataset_mapping_id=secrets.dataset_mapping_id,
                remote_document_id=remote_document_id,
            )
            category_id = _required_text(first_detail.get("category_id"), "app_sync_state_invalid")
            _assert_owner_fresh(owner)
            first_remote_status = await remote.get_document_status(
                dataset_id=secrets.ragflow_dataset_id,
                document_id=remote_document_id,
            )
            if first_remote_status.run.upper() not in SUCCESS_PARSE_RUNS:
                _raise("remote_parse_not_terminal")
            _assert_owner_fresh(owner)
            first_documents = await remote.list_documents(dataset_id=secrets.ragflow_dataset_id)
            first_total, first_matches = _remote_counts(first_documents, exact_name=remote_name)
            if (
                first_total != 1
                or len(first_matches) != 1
                or first_matches[0].document_id != remote_document_id
                or not first_reconciled
                or first_uploaded
                or not first_parse_started
            ):
                _raise("first_reconciliation_invalid")

            repeat_task = _mapping(
                await app.request(
                    "POST",
                    f"/api/admin/files/{file_id}/sync",
                    token=admin_token,
                    json_body={
                        "dataset_mapping_id": secrets.dataset_mapping_id,
                        "reason": "protected live idempotency reconciliation",
                    },
                ),
                "repeat_sync_response_invalid",
            )
            repeat_task_id = _task_id(repeat_task)
            if (
                repeat_task_id == first_task_id
                or repeat_task.get("task_type") != "ragflow_status_check"
            ):
                _raise("repeat_sync_response_invalid")
            repeat_task = await _wait_task_terminal(
                app,
                admin_token=admin_token,
                task_id=repeat_task_id,
                expected_task_type="ragflow_status_check",
                deadline=deadline,
            )
            repeat_reconciled, repeat_uploaded, repeat_parse_started = _task_logs(repeat_task)
            repeat_detail = _file_detail(
                await app.request("GET", f"/api/files/{file_id}", token=employee_token),
                expected_file_id=file_id,
            )
            repeat_app_run = _assert_synced_file(
                repeat_detail,
                dataset_id=secrets.ragflow_dataset_id,
                dataset_mapping_id=secrets.dataset_mapping_id,
                remote_document_id=remote_document_id,
            )
            repeat_category_id = _required_text(
                repeat_detail.get("category_id"), "app_sync_state_invalid"
            )
            if repeat_category_id != category_id:
                _raise("app_sync_classification_changed")
            _assert_owner_fresh(owner)
            repeat_status = await remote.get_document_status(
                dataset_id=secrets.ragflow_dataset_id,
                document_id=remote_document_id,
            )
            _assert_owner_fresh(owner)
            repeat_documents = await remote.list_documents(dataset_id=secrets.ragflow_dataset_id)
            repeat_total, repeat_matches = _remote_counts(repeat_documents, exact_name=remote_name)
            if (
                repeat_status.run.upper() not in SUCCESS_PARSE_RUNS
                or repeat_total != 1
                or len(repeat_matches) != 1
                or repeat_matches[0].document_id != remote_document_id
                or repeat_uploaded
                or repeat_parse_started
            ):
                _raise("repeat_sync_invalid")

            existing_task_ids = frozenset(
                _task_id(task)
                for task in await _list_tasks(app, admin_token=admin_token, file_id=file_id)
            )
            await app.request("DELETE", f"/api/files/{file_id}", token=admin_token)
            delete_task = await _wait_new_task(
                app,
                admin_token=admin_token,
                file_id=file_id,
                expected_task_type="ragflow_delete",
                excluded_ids=existing_task_ids,
                deadline=deadline,
            )
            delete_task_id = _task_id(delete_task)
            _assert_owner_fresh(owner)
            deleted_documents = await remote.list_documents(dataset_id=secrets.ragflow_dataset_id)
            deleted_total, deleted_matches = _remote_counts(
                deleted_documents, exact_name=remote_name
            )
            if deleted_total != 0 or deleted_matches:
                _raise("application_cleanup_unconfirmed")
            application_cleanup_confirmed = True
            _assert_deployment_fresh(deployment)

            evidence: dict[str, object] = {
                "schema": PROBE_SCHEMA,
                "version": CONTRACT_VERSION,
                "requirement_id": REQUIREMENT_ID,
                "verdict": "ready",
                "evidence_kind": "real_external_service",
                "probe_mode": "preseeded_remote_reconciliation",
                "network_timeout_simulation": False,
                "fault_injection": False,
                **_base_binding(
                    context=context,
                    trust=trust,
                    owner=owner,
                    deployment=deployment,
                ),
                "identities": {
                    "endpoint_identity_sha256": identities.endpoint_identity_sha256,
                    "tls_spki_sha256": identities.tls_spki_sha256,
                    "dataset_identity_sha256": identities.dataset_identity_sha256,
                    "dataset_mapping_id_sha256": sha256_text(secrets.dataset_mapping_id),
                    "category_id_sha256": sha256_text(category_id),
                    "app_endpoint_identity_sha256": identities.app_endpoint_identity_sha256,
                    "app_tls_spki_sha256": identities.app_tls_spki_sha256,
                    "app_file_id_sha256": sha256_text(file_id),
                    "remote_name_sha256": sha256_text(remote_name),
                    "remote_document_id_sha256": sha256_text(remote_document_id),
                    "first_task_id_sha256": sha256_text(first_task_id),
                    "repeat_task_id_sha256": sha256_text(repeat_task_id),
                    "delete_task_id_sha256": sha256_text(delete_task_id),
                },
                "stages": {
                    "initial_dataset": {"dataset_total": 0, "exact_name_count": 0},
                    "preseed": {
                        "dataset_total": preseed_total,
                        "exact_name_count": len(preseed_matches),
                        "remote_id_match": True,
                        "commit_observed": True,
                    },
                    "first_sync": {
                        "task_type": "ragflow_upload",
                        "task_status": first_task["status"],
                        "app_file_status": first_detail["status"],
                        "app_parse_status": first_app_run,
                        "dataset_total": first_total,
                        "exact_name_count": len(first_matches),
                        "remote_id_match": True,
                        "reconciliation_log_observed": first_reconciled,
                        "remote_upload_log_observed": first_uploaded,
                        "parse_start_log_observed": first_parse_started,
                    },
                    "repeat_sync": {
                        "request_mode": "new_task",
                        "task_type": "ragflow_status_check",
                        "task_status": repeat_task["status"],
                        "app_file_status": repeat_detail["status"],
                        "app_parse_status": repeat_app_run,
                        "dataset_total": repeat_total,
                        "exact_name_count": len(repeat_matches),
                        "remote_id_match": True,
                        "reconciliation_log_observed": repeat_reconciled,
                        "remote_upload_log_observed": repeat_uploaded,
                        "parse_start_log_observed": repeat_parse_started,
                    },
                    "parse": {
                        "app_terminal": True,
                        "remote_terminal": True,
                        "remote_run": repeat_status.run.upper(),
                        "task_terminal": True,
                    },
                    "application_delete": {
                        "requested": True,
                        "delete_task_status": delete_task["status"],
                        "dataset_total": deleted_total,
                        "exact_name_count": len(deleted_matches),
                        "confirmed": True,
                    },
                },
                "cleanup": {
                    "application_cleanup_confirmed": True,
                    "emergency_direct_cleanup_used": False,
                    "dataset_total": deleted_total,
                    "exact_name_count": len(deleted_matches),
                    "confirmed": True,
                },
                "started_at": started_at,
                "finished_at": _now_text(),
            }
            validate_probe(
                evidence,
                expected_repository=context.repository,
                expected_git_sha=context.git_sha,
                expected_environment=context.environment,
                expected_run_id=context.run_id,
                expected_run_attempt=context.run_attempt,
                expected_main_run_id=context.main_run_id,
                expected_main_run_attempt=context.main_run_attempt,
            )
            return evidence
    finally:
        if not application_cleanup_confirmed:
            emergency_cleanup_used = remote_name is not None
            confirmed, dataset_total, exact_count = await _emergency_cleanup(
                app=app,
                admin_token=admin_token,
                file_id=file_id,
                remote=remote,
                dataset_id=secrets.ragflow_dataset_id,
                remote_name=remote_name,
                owner=owner,
            )
            cleanup = _mapping(manifest["cleanup"], "manifest_invalid")
            cleanup["confirmed"] = confirmed  # type: ignore[index]
            cleanup["emergency_direct_cleanup_used"] = emergency_cleanup_used  # type: ignore[index]
            cleanup["dataset_total"] = dataset_total  # type: ignore[index]
            cleanup["exact_name_count"] = exact_count  # type: ignore[index]
        else:
            cleanup = _mapping(manifest["cleanup"], "manifest_invalid")
            cleanup["confirmed"] = True  # type: ignore[index]
            cleanup["emergency_direct_cleanup_used"] = False  # type: ignore[index]
            cleanup["dataset_total"] = 0  # type: ignore[index]
            cleanup["exact_name_count"] = 0  # type: ignore[index]
        _replace_manifest(manifest_path, manifest)


async def _list_employee_canaries(
    app: ProtectedAppClient,
    *,
    employee_token: str,
    filename: str,
) -> list[str]:
    data = _mapping(
        await app.request(
            "GET",
            "/api/files",
            token=employee_token,
            params={"page": 1, "page_size": 100, "q": filename},
        ),
        "file_list_invalid",
    )
    matches: list[str] = []
    for raw in _sequence(data.get("items"), "file_list_invalid"):
        item = _mapping(raw, "file_list_invalid")
        if item.get("original_name") != filename:
            continue
        file_id = _required_text(item.get("id"), "file_list_invalid")
        try:
            uuid.UUID(file_id)
        except ValueError:
            _raise("file_list_invalid")
        matches.append(file_id)
    if len(matches) > 4:
        _raise("janitor_candidate_limit")
    return matches


async def run_janitor(*, context: RunContext) -> Mapping[str, object]:
    started_at = _now_text()
    trust = _load_trust_binding(context)
    documents = _load_proof_documents(context)
    secrets = _load_secrets()
    identities = _identity_binding(secrets)
    owner = _verify_owner_binding(
        context=context,
        identities=identities,
        documents=documents,
    )
    _assert_owner_fresh(owner)
    deployment = _verify_deployment_binding(
        context=context,
        trust=trust,
        identities=identities,
        documents=documents,
    )
    _assert_deployment_fresh(deployment)
    filename = f"{CANARY_PREFIX}-{sha256_text(context.nonce)[:16]}.txt"
    remote = _remote_client(secrets)
    app_candidates: list[str] = []
    app_delete_requests = 0
    async with ProtectedAppClient(
        base_url=secrets.app_base_url,
        tls_pin=secrets.app_tls_pin,
    ) as app:
        employee_token = await app.login(
            email=secrets.employee_email,
            password=secrets.employee_password,
            expected_roles=frozenset({"employee"}),
        )
        admin_token = await app.login(
            email=secrets.admin_email,
            password=secrets.admin_password,
            expected_roles=frozenset({"dept_admin", "system_admin"}),
        )
        app_candidates = await _list_employee_canaries(
            app,
            employee_token=employee_token,
            filename=filename,
        )
        for file_id in app_candidates:
            try:
                await app.request(
                    "DELETE",
                    f"/api/files/{file_id}",
                    token=admin_token,
                    allowed_statuses=frozenset({200, 404}),
                )
                app_delete_requests += 1
            except LiveProbeError as error:
                if error.code != "app_http_404":
                    raise

    _assert_owner_fresh(owner)
    candidates = await remote.list_documents(
        dataset_id=secrets.ragflow_dataset_id,
        keywords=filename,
    )
    remote_candidates = [document for document in candidates if document.name.endswith(filename)]
    if len(remote_candidates) > 4:
        _raise("janitor_candidate_limit")
    remote_delete_requests = 0
    for document in remote_candidates:
        _assert_owner_fresh(owner)
        try:
            await remote.delete_document(
                dataset_id=secrets.ragflow_dataset_id,
                document_id=document.document_id,
            )
        except RagflowDocumentNotFoundError:
            pass
        remote_delete_requests += 1
    _assert_owner_fresh(owner)
    remaining = await remote.list_documents(dataset_id=secrets.ragflow_dataset_id)
    canary_remaining = [document for document in remaining if document.name.endswith(filename)]
    if remaining or canary_remaining:
        _raise("janitor_cleanup_unconfirmed")
    _assert_deployment_fresh(deployment)
    evidence: dict[str, object] = {
        "schema": JANITOR_SCHEMA,
        "version": CONTRACT_VERSION,
        **_base_binding(context=context, trust=trust, owner=owner, deployment=deployment),
        "identities": {
            "endpoint_identity_sha256": identities.endpoint_identity_sha256,
            "tls_spki_sha256": identities.tls_spki_sha256,
            "dataset_identity_sha256": identities.dataset_identity_sha256,
            "app_endpoint_identity_sha256": identities.app_endpoint_identity_sha256,
            "app_tls_spki_sha256": identities.app_tls_spki_sha256,
            "canary_filename_sha256": sha256_text(filename),
        },
        "cleanup": {
            "app_candidates_seen": len(app_candidates),
            "app_delete_requests": app_delete_requests,
            "remote_candidates_seen": len(remote_candidates),
            "remote_delete_requests": remote_delete_requests,
            "dataset_total": len(remaining),
            "canary_remote_count": len(canary_remaining),
            "confirmed": True,
        },
        "started_at": started_at,
        "finished_at": _now_text(),
    }
    validate_janitor(
        evidence,
        expected_repository=context.repository,
        expected_git_sha=context.git_sha,
        expected_environment=context.environment,
        expected_run_id=context.run_id,
        expected_run_attempt=context.run_attempt,
        expected_main_run_id=context.main_run_id,
        expected_main_run_attempt=context.main_run_attempt,
    )
    return evidence


def _positive(value: str, code: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        _raise(code)
    if parsed < 1:
        _raise(code)
    return parsed


def _context(args: argparse.Namespace) -> RunContext:
    if ENVIRONMENT_PATTERN.fullmatch(args.environment) is None:
        _raise("environment_invalid")
    if REPOSITORY_PATTERN.fullmatch(args.repository) is None:
        _raise("repository_invalid")
    if GIT_SHA_PATTERN.fullmatch(args.git_sha) is None:
        _raise("git_sha_invalid")
    if NONCE_PATTERN.fullmatch(args.nonce) is None:
        _raise("nonce_invalid")
    if HASH_PATTERN.fullmatch(args.owner_policy_sha256) is None:
        _raise("owner_policy_trust_anchor_invalid")
    if HASH_PATTERN.fullmatch(args.deployment_policy_sha256) is None:
        _raise("deployment_policy_trust_anchor_invalid")
    if HASH_PATTERN.fullmatch(args.deployment_identity_sha256) is None:
        _raise("deployment_identity_invalid")
    timeout = _positive(args.timeout_seconds, "timeout_invalid")
    if timeout > 1800:
        _raise("timeout_invalid")
    return RunContext(
        environment=args.environment,
        repository=args.repository,
        git_sha=args.git_sha,
        run_id=_positive(args.run_id, "run_id_invalid"),
        run_attempt=_positive(args.run_attempt, "run_attempt_invalid"),
        main_run_id=_positive(args.main_run_id, "main_run_id_invalid"),
        main_run_attempt=_positive(args.main_run_attempt, "main_run_attempt_invalid"),
        nonce=args.nonce,
        workflow_trust_path=args.workflow_trust,
        owner_attestation_path=args.owner_attestation,
        owner_policy_path=args.owner_policy,
        owner_policy_sha256=args.owner_policy_sha256,
        deployment_attestation_path=args.deployment_attestation,
        deployment_policy_path=args.deployment_policy,
        deployment_policy_sha256=args.deployment_policy_sha256,
        deployment_identity_sha256=args.deployment_identity_sha256,
        timeout_seconds=timeout,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("probe", "janitor"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--environment", required=True)
        subparser.add_argument("--repository", required=True)
        subparser.add_argument("--git-sha", required=True)
        subparser.add_argument("--run-id", required=True)
        subparser.add_argument("--run-attempt", required=True)
        subparser.add_argument("--main-run-id", required=True)
        subparser.add_argument("--main-run-attempt", required=True)
        subparser.add_argument("--nonce", required=True)
        subparser.add_argument("--workflow-trust", type=Path, required=True)
        subparser.add_argument("--owner-attestation", type=Path, required=True)
        subparser.add_argument("--owner-policy", type=Path, required=True)
        subparser.add_argument("--owner-policy-sha256", required=True)
        subparser.add_argument("--deployment-attestation", type=Path, required=True)
        subparser.add_argument("--deployment-policy", type=Path, required=True)
        subparser.add_argument("--deployment-policy-sha256", required=True)
        subparser.add_argument("--deployment-identity-sha256", required=True)
        subparser.add_argument("--timeout-seconds", default="480")
        subparser.add_argument("--output", type=Path, required=True)
    probe = subparsers.choices["probe"]
    probe.add_argument("--janitor-manifest", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        context = _context(args)
        if args.command == "probe":
            value = asyncio.run(run_probe(context=context, manifest_path=args.janitor_manifest))
        else:
            value = asyncio.run(run_janitor(context=context))
        write_json(args.output, value)
    except (
        EvidenceContractError,
        LiveProbeError,
        RagflowClientError,
        AttestationVerificationError,
        DeploymentAttestationVerificationError,
        TrustError,
        ValueError,
        OSError,
    ) as error:
        code = (
            error.code if isinstance(error, (EvidenceContractError, LiveProbeError)) else "failed"
        )
        print(f"protected RAGFlow evidence failed: {code}", file=sys.stderr)
        return 1
    except Exception:
        print("protected RAGFlow evidence failed: internal_error", file=sys.stderr)
        return 1
    print(f"protected RAGFlow {args.command} completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
