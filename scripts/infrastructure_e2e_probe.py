"""HTTP business probe for the isolated real-infrastructure Compose gate.

The probe intentionally uses the public API for every business mutation. Secrets stay
in memory and are never included in returned evidence or exception messages.
"""

from __future__ import annotations

import json
import ssl
import time
import uuid
from dataclasses import dataclass, field
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InfrastructureProbeError(RuntimeError):
    """A bounded E2E assertion failure safe to print in CI."""


@dataclass(frozen=True)
class ProbeCredentials:
    email: str
    password: str = field(repr=False)
    token: str = field(repr=False)


@dataclass(frozen=True)
class BusinessProbeState:
    admin: ProbeCredentials = field(repr=False)
    employee: ProbeCredentials = field(repr=False)
    department_admin: ProbeCredentials = field(repr=False)
    department_id: uuid.UUID
    category_id: uuid.UUID
    dataset_mapping_id: uuid.UUID
    primary_file_id: uuid.UUID
    primary_file_name: str


@dataclass(frozen=True)
class ReplayTarget:
    file_id: uuid.UUID
    file_name: str


DRAFT_REVIEW_FACT_FIELDS = (
    "submitted_at",
    "review_due_at",
    "claimed_by",
    "claimed_at",
    "claim_expires_at",
)


def _require_explicit_draft(file: dict[str, object]) -> None:
    _require(file.get("status") == "uploaded", "upload did not remain an explicit draft")
    _require(
        all(file.get(field) is None for field in DRAFT_REVIEW_FACT_FIELDS),
        "draft contains persisted review submission facts",
    )


def _is_review_approval_notification(item: object, *, file_id: uuid.UUID) -> bool:
    if not isinstance(item, dict) or item.get("type") != "review_approved":
        return False
    metadata = item.get("metadata")
    return (
        isinstance(metadata, dict)
        and metadata.get("resource_type") == "file"
        and metadata.get("resource_id") == str(file_id)
    )


def _remote_documents_with_id(documents: list[object], document_id: str) -> list[dict[str, object]]:
    return [
        document
        for document in documents
        if isinstance(document, dict) and document.get("id") == document_id
    ]


class JsonApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        ca_cert_file: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._ssl_context = ssl.create_default_context(cafile=ca_cert_file)

    def json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: object | None = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> object:
        headers = {"Accept": "application/json", "User-Agent": "infrastructure-e2e-probe/1"}
        body = None
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, _headers, response_body = self._request(
            method,
            path,
            headers=headers,
            body=body,
            expected_statuses=expected_statuses,
        )
        if status == 204:
            return None
        try:
            decoded = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InfrastructureProbeError(
                f"{method} {_safe_path(path)} returned invalid JSON"
            ) from error
        if not isinstance(decoded, dict) or decoded.get("success") is not True:
            raise InfrastructureProbeError(
                f"{method} {_safe_path(path)} returned an invalid success envelope"
            )
        return decoded.get("data")

    def multipart(
        self,
        path: str,
        *,
        token: str,
        fields: dict[str, str],
        filename: str,
        content_type: str,
        content: bytes,
    ) -> object:
        boundary = f"knowledge-uploader-e2e-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                (
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                )
            )
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            )
        )
        _status, _headers, response_body = self._request(
            "POST",
            path,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "infrastructure-e2e-probe/1",
            },
            body=b"".join(chunks),
            expected_statuses=(201,),
        )
        try:
            decoded = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InfrastructureProbeError("upload returned invalid JSON") from error
        if not isinstance(decoded, dict) or decoded.get("success") is not True:
            raise InfrastructureProbeError("upload returned an invalid success envelope")
        return decoded.get("data")

    def binary(
        self,
        path: str,
        *,
        token: str,
        range_header: str,
        expected_status: int,
    ) -> tuple[Message, bytes]:
        _status, headers, body = self._request(
            "GET",
            path,
            headers={
                "Accept": "*/*",
                "Authorization": f"Bearer {token}",
                "Range": range_header,
                "User-Agent": "infrastructure-e2e-probe/1",
            },
            body=None,
            expected_statuses=(expected_status,),
        )
        return headers, body

    def external_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        expected_status: int = 200,
    ) -> object:
        request = Request(url, method="GET", headers=headers)
        try:
            with urlopen(
                request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                status = response.status
                body = response.read()
        except (HTTPError, URLError, TimeoutError) as error:
            raise InfrastructureProbeError(
                "mock dependency state endpoint is unavailable"
            ) from error
        if status != expected_status:
            raise InfrastructureProbeError("mock dependency state endpoint returned an error")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InfrastructureProbeError(
                "mock dependency state endpoint returned invalid JSON"
            ) from error

    def expect_error_code(
        self,
        method: str,
        path: str,
        *,
        payload: object,
        expected_status: int,
        expected_error_code: str,
    ) -> None:
        status, _headers, body = self._request(
            method,
            path,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "infrastructure-e2e-probe/1",
            },
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            expected_statuses=(expected_status,),
        )
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InfrastructureProbeError(
                "expected API rejection returned invalid JSON"
            ) from error
        if (
            status != expected_status
            or not isinstance(decoded, dict)
            or decoded.get("success") is not False
            or decoded.get("error_code") != expected_error_code
        ):
            raise InfrastructureProbeError("expected API rejection contract was not enforced")

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        expected_statuses: tuple[int, ...],
    ) -> tuple[int, Message, bytes]:
        request = Request(f"{self._base_url}{path}", data=body, method=method, headers=headers)
        try:
            with urlopen(
                request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                status = response.status
                response_headers = response.headers
                response_body = response.read()
        except HTTPError as error:
            response_body = error.read()
            if error.code in expected_statuses:
                return error.code, error.headers, response_body
            raise InfrastructureProbeError(
                f"{method} {_safe_path(path)} returned HTTP {error.code}"
            ) from None
        except (URLError, TimeoutError) as error:
            raise InfrastructureProbeError(
                f"{method} {_safe_path(path)} could not reach the API"
            ) from error
        if status not in expected_statuses:
            raise InfrastructureProbeError(
                f"{method} {_safe_path(path)} returned unexpected HTTP {status}"
            )
        return status, response_headers, response_body


class InfrastructureBusinessProbe:
    def __init__(
        self,
        *,
        api_base_url: str,
        mock_ragflow_state_url: str,
        mock_smtp_state_url: str,
        probe_token: str,
        run_id: uuid.UUID,
        admin_email: str,
        admin_password: str,
        employee_password: str,
        ragflow_internal_base_url: str,
        ragflow_api_key: str,
        dataset_id: str,
        ca_cert_file: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._client = JsonApiClient(api_base_url, ca_cert_file=ca_cert_file)
        self._mock_ragflow_state_url = mock_ragflow_state_url
        self._mock_smtp_state_url = mock_smtp_state_url
        self._probe_token = probe_token
        self._run_id = run_id
        self._admin_email = admin_email
        self._email_domain = admin_email.rpartition("@")[2]
        if not self._email_domain:
            raise InfrastructureProbeError("admin email domain is missing")
        self._admin_password = admin_password
        self._employee_password = employee_password
        self._ragflow_internal_base_url = ragflow_internal_base_url
        self._ragflow_api_key = ragflow_api_key
        self._dataset_id = dataset_id
        self._timeout_seconds = timeout_seconds

    def run_primary_flow(self) -> tuple[BusinessProbeState, dict[str, object]]:
        suffix = self._run_id.hex[:12]
        admin = self._login(self._admin_email, self._admin_password)
        security_config = _mapping(
            self._client.json(
                "PUT",
                "/api/admin/configs/security",
                token=admin.token,
                payload={"items": {"security.allowed_email_domains": [self._email_domain]}},
            ),
            "security config",
        )
        _require(
            _config_value(security_config, "security.allowed_email_domains")
            == [self._email_domain],
            "security email-domain allowlist was not applied",
        )
        persisted_security_config = _mapping(
            self._client.json(
                "GET",
                "/api/admin/configs?group=security",
                token=admin.token,
            ),
            "persisted security config",
        )
        _require(
            _config_value(persisted_security_config, "security.allowed_email_domains")
            == [self._email_domain],
            "security email-domain allowlist was not persisted",
        )
        department = _mapping(
            self._client.json(
                "POST",
                "/api/admin/departments",
                token=admin.token,
                payload={"name": f"E2E Department {suffix}", "code": f"e2e-{suffix}"},
                expected_statuses=(201,),
            ),
            "department",
        )
        department_id = _uuid_value(department.get("id"), "department id")

        employee_email = f"employee-{suffix}@{self._email_domain}"
        department_admin_email = f"reviewer-{suffix}@{self._email_domain}"
        for name, email in (
            ("E2E Employee", employee_email),
            ("E2E Department Admin", department_admin_email),
        ):
            accepted = _mapping(
                self._client.json(
                    "POST",
                    "/api/auth/register",
                    payload={
                        "name": name,
                        "email": email,
                        "password": self._employee_password,
                        "department_id": str(department_id),
                    },
                    expected_statuses=(201,),
                ),
                "registration result",
            )
            _require(accepted.get("accepted") is True, "registration was not accepted")
            self._client.expect_error_code(
                "POST",
                "/api/auth/login",
                payload={"email": email, "password": self._employee_password},
                expected_status=403,
                expected_error_code="EMAIL_NOT_VERIFIED",
            )
            verification_token = self._wait_for_verification_token(email)
            verified = _mapping(
                self._client.json(
                    "POST",
                    "/api/auth/verify-email",
                    payload={"token": verification_token},
                ),
                "email verification",
            )
            _require(verified.get("email_verified") is True, "email was not verified")

        employee = self._login(employee_email, self._employee_password)
        initial_reviewer = self._login(department_admin_email, self._employee_password)
        reviewer_user = self._find_user(admin.token, department_admin_email)
        reviewer_id = _uuid_value(reviewer_user.get("id"), "reviewer user id")
        self._client.json(
            "PATCH",
            f"/api/users/{reviewer_id}/role",
            token=admin.token,
            payload={"role": "dept_admin"},
        )
        self._client.json(
            "PUT",
            f"/api/admin/users/{reviewer_id}/managed-departments",
            token=admin.token,
            payload={"department_ids": [str(department_id)]},
        )
        department_admin = self._login(initial_reviewer.email, self._employee_password)

        self._client.json(
            "PUT",
            "/api/admin/configs/ragflow",
            token=admin.token,
            payload={
                "items": {
                    "ragflow.base_url": self._ragflow_internal_base_url,
                    "ragflow.api_key": self._ragflow_api_key,
                    "ragflow.sync_timeout_seconds": 15,
                    "ragflow.sync_max_retries": 1,
                    "ragflow.parse_poll_timeout_seconds": 120,
                }
            },
        )
        category = _mapping(
            self._client.json(
                "POST",
                "/api/categories",
                token=admin.token,
                payload={
                    "name": f"E2E Category {suffix}",
                    "code": f"e2e-category-{suffix}",
                    "description": "isolated infrastructure E2E",
                },
                expected_statuses=(201,),
            ),
            "category",
        )
        category_id = _uuid_value(category.get("id"), "category id")
        dataset_mapping = _mapping(
            self._client.json(
                "POST",
                "/api/datasets",
                token=admin.token,
                payload={
                    "name": f"E2E Dataset {suffix}",
                    "category_id": str(category_id),
                    "ragflow_dataset_id": self._dataset_id,
                    "ragflow_dataset_name": "E2E Dataset",
                    "enabled": True,
                },
                expected_statuses=(201,),
            ),
            "dataset mapping",
        )
        dataset_mapping_id = _uuid_value(dataset_mapping.get("id"), "dataset mapping id")

        primary_name = f"e2e-primary-{suffix}.txt"
        primary_content = f"Knowledge Uploader real infrastructure probe {self._run_id}\n".encode()
        primary = self._upload_draft(employee, primary_name, primary_content)
        primary_id = _uuid_value(primary.get("id"), "primary file id")
        _require_explicit_draft(primary)
        self._require_not_in_review_queue(
            department_admin,
            file_id=primary_id,
            search=primary_name,
        )

        headers, ranged_content = self._client.binary(
            f"/api/files/{primary_id}/content?disposition=inline",
            token=employee.token,
            range_header="bytes=0-7",
            expected_status=206,
        )
        _require(ranged_content == primary_content[:8], "original content range does not match")
        _require(
            headers.get("Content-Range") == f"bytes 0-7/{len(primary_content)}", "invalid range"
        )
        _require(
            str(headers.get("Content-Disposition", "")).startswith("inline;"),
            "safe original preview was forced to attachment",
        )

        draft_version = primary.get("review_version")
        _require(isinstance(draft_version, int), "draft version is missing")
        updated = _mapping(
            self._client.json(
                "PATCH",
                f"/api/files/{primary_id}",
                token=employee.token,
                payload={
                    "expected_version": draft_version,
                    "title": f"E2E Primary {suffix}",
                    "visibility": "department",
                },
            ),
            "draft update",
        )
        _require(updated.get("status") == "uploaded", "draft edit changed lifecycle state")
        self._submit_review(employee, primary_id)
        self._claim_and_approve(
            department_admin,
            file_id=primary_id,
            search=primary_name,
            category_id=category_id,
            dataset_mapping_id=dataset_mapping_id,
        )
        parsed = self._wait_for_file(employee, primary_id, expected_status="parsed")
        _require(parsed.get("ragflow_dataset_id") == self._dataset_id, "RAGFlow target changed")
        _require(
            str(parsed.get("ragflow_parse_status", "")).upper() in {"3", "DONE"},
            "RAGFlow parse did not reach a terminal success",
        )
        notification_id = self._wait_for_notification(employee, primary_id)
        read_notification = _mapping(
            self._client.json(
                "POST",
                f"/api/notifications/{notification_id}/read",
                token=employee.token,
                payload={},
            ),
            "notification read result",
        )
        _require(
            read_notification.get("read_at") is not None, "notification did not close the loop"
        )
        self._require_audit(admin, action="file.approve", target_id=primary_id)
        mock_state = self._mock_state()
        _require(
            _int_value(mock_state.get("upload_count"), "mock upload count") >= 1,
            "mock RAGFlow saw no upload",
        )
        _require(
            _int_value(mock_state.get("parse_count"), "mock parse count") >= 1,
            "mock RAGFlow saw no parse",
        )
        _require(
            _int_value(mock_state.get("authorization_failures"), "mock auth failures") == 0,
            "RAGFlow authentication contract failed",
        )

        state = BusinessProbeState(
            admin=admin,
            employee=employee,
            department_admin=department_admin,
            department_id=department_id,
            category_id=category_id,
            dataset_mapping_id=dataset_mapping_id,
            primary_file_id=primary_id,
            primary_file_name=primary_name,
        )
        return state, {
            "status": "passed",
            "department_id": str(department_id),
            "primary_file_id": str(primary_id),
            "draft_state": "passed",
            "original_preview": "passed",
            "review_claim": "passed",
            "explicit_ragflow_decision": "sync",
            "ragflow_terminal_state": "parsed",
            "notification_loop": "passed",
            "audit_loop": "passed",
            "email_verification_floor": "passed",
            "mock_smtp_delivery": "passed",
            "gateway_https": "passed",
            "ragflow_https": "passed",
            "smtp_starttls": "passed",
        }

    def create_replay_target(self, state: BusinessProbeState) -> ReplayTarget:
        suffix = self._run_id.hex[:12]
        file_name = f"e2e-replay-{suffix}.txt"
        content = f"RabbitMQ clean-room replay target {self._run_id}\n".encode()
        uploaded = self._upload_draft(state.employee, file_name, content)
        file_id = _uuid_value(uploaded.get("id"), "replay target file id")
        self._submit_review(state.employee, file_id)
        self._claim_and_approve(
            state.department_admin,
            file_id=file_id,
            search=file_name,
            category_id=state.category_id,
            dataset_mapping_id=state.dataset_mapping_id,
        )
        return ReplayTarget(file_id=file_id, file_name=file_name)

    def create_fault_target(
        self,
        state: BusinessProbeState,
        *,
        dependency: str,
    ) -> ReplayTarget:
        if dependency not in {"rabbitmq", "redis", "minio", "ragflow"}:
            raise InfrastructureProbeError("unsupported fault dependency")
        suffix = self._run_id.hex[:12]
        file_name = f"e2e-fault-{dependency}-{suffix}.txt"
        content = f"Dependency recovery target {dependency} {self._run_id}\n".encode()
        uploaded = self._upload_draft(state.employee, file_name, content)
        file_id = _uuid_value(uploaded.get("id"), "fault target file id")
        self._submit_review(state.employee, file_id)
        self._claim_and_approve(
            state.department_admin,
            file_id=file_id,
            search=file_name,
            category_id=state.category_id,
            dataset_mapping_id=state.dataset_mapping_id,
        )
        return ReplayTarget(file_id=file_id, file_name=file_name)

    def ragflow_upload_count(self) -> int:
        state = self._mock_state()
        return _int_value(state.get("upload_count"), "mock upload count")

    def require_remote_unchanged(
        self,
        _target: ReplayTarget,
        *,
        baseline_upload_count: int,
    ) -> None:
        mock_state = self._mock_state()
        upload_count = _int_value(mock_state.get("upload_count"), "mock upload count")
        _require(upload_count == baseline_upload_count, "remote upload occurred during outage")

    def fault_database_diagnostics(
        self,
        state: BusinessProbeState,
        target: ReplayTarget,
    ) -> dict[str, object]:
        file = _mapping(
            self._client.json(
                "GET",
                f"/api/files/{target.file_id}",
                token=state.employee.token,
            ),
            "fault diagnostic file",
        )
        tasks = _mapping(
            self._client.json(
                "GET",
                f"/api/tasks?file_id={target.file_id}",
                token=state.admin.token,
            ),
            "fault diagnostic tasks",
        )
        items = tasks.get("items")
        task_items = (
            [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        )
        status_counts: dict[str, int] = {}
        for item in task_items:
            status = item.get("status")
            if isinstance(status, str) and status in {
                "queued",
                "running",
                "succeeded",
                "failed",
                "canceled",
            }:
                status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "file_status": str(file.get("status", "unknown"))[:40],
            "remote_document_id_present": bool(file.get("ragflow_document_id")),
            "sync_task_count": len(task_items),
            "sync_task_status_counts": status_counts,
        }

    def wait_for_failed_sync_task(
        self,
        state: BusinessProbeState,
        target: ReplayTarget,
    ) -> uuid.UUID:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            file = _mapping(
                self._client.json(
                    "GET",
                    f"/api/files/{target.file_id}",
                    token=state.employee.token,
                ),
                "fault file detail",
            )
            tasks = _mapping(
                self._client.json(
                    "GET",
                    f"/api/tasks?file_id={target.file_id}",
                    token=state.admin.token,
                ),
                "fault task list",
            )
            items = tasks.get("items")
            failed = (
                [
                    item
                    for item in items
                    if isinstance(item, dict)
                    and item.get("task_type") == "ragflow_upload"
                    and item.get("status") == "failed"
                ]
                if isinstance(items, list)
                else []
            )
            if file.get("status") == "failed" and len(failed) == 1:
                return _uuid_value(failed[0].get("id"), "failed sync task id")
            time.sleep(0.5)
        raise InfrastructureProbeError("dependency outage did not persist a failed sync task")

    def retry_failed_sync_task(
        self,
        state: BusinessProbeState,
        *,
        task_id: uuid.UUID,
    ) -> str:
        retried = _mapping(
            self._client.json(
                "POST",
                f"/api/tasks/{task_id}/retry",
                token=state.admin.token,
                payload={},
            ),
            "fault task retry",
        )
        _require(retried.get("status") == "queued", "failed sync task was not queued")
        return "queued"

    def verify_fault_restored(
        self,
        state: BusinessProbeState,
        target: ReplayTarget,
        *,
        baseline_upload_count: int,
    ) -> dict[str, object]:
        parsed = self._wait_for_file(state.employee, target.file_id, expected_status="parsed")
        _require(parsed.get("ragflow_dataset_id") == self._dataset_id, "fault target changed")
        mock_state = self._mock_state()
        upload_count = _int_value(mock_state.get("upload_count"), "mock upload count")
        documents = mock_state.get("documents")
        if not isinstance(documents, list):
            raise InfrastructureProbeError("mock documents are missing")
        remote_document_id = parsed.get("ragflow_document_id")
        if not isinstance(remote_document_id, str) or not remote_document_id.strip():
            raise InfrastructureProbeError("fault target did not persist remote document identity")
        matching_documents = _remote_documents_with_id(documents, remote_document_id)
        upload_delta = upload_count - baseline_upload_count
        _require(upload_delta == 1, "fault recovery repeated the remote upload")
        _require(len(matching_documents) == 1, "fault recovery did not converge on one identity")
        return {
            "target_file_id": str(target.file_id),
            "remote_upload_delta": upload_delta,
            "remote_document_count": len(matching_documents),
            "terminal_state": "parsed",
            "event_loss_detected": False,
            "duplicate_remote_document": False,
        }

    def replay_next_dead_letter(
        self,
        state: BusinessProbeState,
        *,
        original_task_id: uuid.UUID,
        original_correlation_id: str,
    ) -> dict[str, object]:
        response = _mapping(
            self._client.json(
                "POST",
                "/api/admin/rabbitmq/dead-letters/ragflow_queue/replay-next",
                token=state.admin.token,
                payload={"reason": "isolated E2E clean-room recovery drill"},
            ),
            "RabbitMQ replay response",
        )
        _require(
            response.get("original_task_id") == str(original_task_id), "replay identity changed"
        )
        _require(response.get("raw_payload_copied") is False, "replay copied a raw payload")
        _require(response.get("replay_queued") is True, "replay was not queued")
        audit_id = _uuid_value(response.get("audit_log_id"), "RabbitMQ replay audit id")
        self._require_audit(
            state.admin,
            action="rabbitmq.dead_letter.replay_completed",
            audit_id=audit_id,
        )
        replay_task_id = _uuid_value(response.get("replay_task_id"), "RabbitMQ replay task id")
        return {
            "queue_name": "ragflow_queue",
            "task_name": response.get("task_name"),
            "probe_run_id": str(self._run_id),
            "original_task_id": str(original_task_id),
            "original_correlation_id": original_correlation_id,
            "replay_task_id": str(replay_task_id),
            "replay_correlation_id": str(replay_task_id),
            "raw_payload_copied": False,
            "persistent_message": True,
            "replay_policy": response.get("replay_policy"),
            "audit_log_id": str(audit_id),
            "result": "queued",
        }

    def verify_replay_restored(
        self,
        state: BusinessProbeState,
        target: ReplayTarget,
    ) -> dict[str, object]:
        parsed = self._wait_for_file(state.employee, target.file_id, expected_status="parsed")
        _require(parsed.get("ragflow_dataset_id") == self._dataset_id, "replayed target changed")
        mock_state = self._mock_state()
        _require(
            _int_value(mock_state.get("upload_count"), "mock upload count") >= 2,
            "replayed upload did not execute",
        )
        _require(
            _int_value(mock_state.get("parse_count"), "mock parse count") >= 2,
            "replayed parse did not execute",
        )
        return {
            "file_id": str(target.file_id),
            "domain_state": "passed",
            "ragflow_terminal_state": "parsed",
        }

    def _login(self, email: str, password: str) -> ProbeCredentials:
        data = _mapping(
            self._client.json(
                "POST",
                "/api/auth/login",
                payload={"email": email, "password": password},
            ),
            "login",
        )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise InfrastructureProbeError("login token is missing")
        return ProbeCredentials(email=email, password=password, token=token)

    def _wait_for_verification_token(self, email: str) -> str:
        deadline = time.monotonic() + self._timeout_seconds
        normalized_email = email.lower()
        while time.monotonic() < deadline:
            state = self._client.external_json(
                self._mock_smtp_state_url,
                headers={"X-E2E-Probe-Token": self._probe_token},
            )
            if isinstance(state, dict):
                messages = state.get("messages")
                if isinstance(messages, list):
                    tokens = {
                        str(message.get("verification_token", ""))
                        for message in messages
                        if isinstance(message, dict)
                        and str(message.get("recipient", "")).lower() == normalized_email
                        and str(message.get("verification_token", ""))
                    }
                    if len(tokens) == 1:
                        return next(iter(tokens))
            time.sleep(0.5)
        raise InfrastructureProbeError("verification email was not delivered by the SMTP sink")

    def _find_user(self, admin_token: str, email: str) -> dict[str, object]:
        query = urlencode({"search": email, "page": 1, "page_size": 20})
        data = _mapping(
            self._client.json("GET", f"/api/users?{query}", token=admin_token),
            "user list",
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise InfrastructureProbeError("user list items are missing")
        matches = [item for item in items if isinstance(item, dict) and item.get("email") == email]
        if len(matches) != 1:
            raise InfrastructureProbeError("registered user was not uniquely searchable")
        return matches[0]

    def _upload_draft(
        self,
        actor: ProbeCredentials,
        filename: str,
        content: bytes,
    ) -> dict[str, object]:
        return _mapping(
            self._client.multipart(
                "/api/files/upload",
                token=actor.token,
                fields={
                    "submit_after_upload": "false",
                    "description": "isolated infrastructure E2E",
                    "visibility": "private",
                    "ai_analysis_enabled": "false",
                },
                filename=filename,
                content_type="text/plain",
                content=content,
            ),
            "upload",
        )

    def _submit_review(self, actor: ProbeCredentials, file_id: uuid.UUID) -> None:
        submitted = _mapping(
            self._client.json(
                "POST",
                f"/api/files/{file_id}/submit-review",
                token=actor.token,
                payload={"acknowledge_sensitive_risk": False},
            ),
            "review submission",
        )
        _require(submitted.get("status") == "pending_review", "file did not enter pending review")
        _require(submitted.get("review_status") == "pending", "review status is not pending")
        _require(submitted.get("review_due_at") is not None, "persisted review SLA is missing")

    def _claim_and_approve(
        self,
        actor: ProbeCredentials,
        *,
        file_id: uuid.UUID,
        search: str,
        category_id: uuid.UUID,
        dataset_mapping_id: uuid.UUID,
    ) -> None:
        query = urlencode(
            {"queue": "unclaimed", "q": search, "page": 1, "page_size": 5, "sort": "submitted_at"}
        )
        review_page = _mapping(
            self._client.json("GET", f"/api/review/files?{query}", token=actor.token),
            "review queue",
        )
        _require(review_page.get("page") == 1, "review pagination page is invalid")
        _require(review_page.get("page_size") == 5, "review pagination size is invalid")
        items = review_page.get("items")
        _require(
            isinstance(items, list)
            and any(isinstance(item, dict) and item.get("id") == str(file_id) for item in items),
            "department-scoped review target is not searchable",
        )
        claimed = _mapping(
            self._client.json(
                "POST",
                f"/api/review/files/{file_id}/claim",
                token=actor.token,
                payload={},
            ),
            "review claim",
        )
        _require(claimed.get("review_status") == "in_review", "review claim was not persisted")
        _require(claimed.get("claim_expires_at") is not None, "review claim timeout is missing")
        approved = _mapping(
            self._client.json(
                "POST",
                f"/api/files/{file_id}/approve",
                token=actor.token,
                payload={
                    "sync_decision": "sync",
                    "category_id": str(category_id),
                    "dataset_mapping_id": str(dataset_mapping_id),
                    "reason": "isolated E2E explicit RAGFlow sync decision",
                },
            ),
            "review approval",
        )
        _require(approved.get("sync_decision") == "sync", "approval lost explicit sync decision")
        _require(approved.get("review_status") == "approved", "review was not approved")

    def _require_not_in_review_queue(
        self,
        actor: ProbeCredentials,
        *,
        file_id: uuid.UUID,
        search: str,
    ) -> None:
        query = urlencode({"queue": "unclaimed", "q": search, "page": 1, "page_size": 5})
        review_page = _mapping(
            self._client.json("GET", f"/api/review/files?{query}", token=actor.token),
            "draft review queue exclusion",
        )
        items = review_page.get("items")
        if not isinstance(items, list):
            raise InfrastructureProbeError("review queue items are missing")
        _require(
            not any(isinstance(item, dict) and item.get("id") == str(file_id) for item in items),
            "draft appeared in department review queue before submission",
        )

    def _wait_for_file(
        self,
        actor: ProbeCredentials,
        file_id: uuid.UUID,
        *,
        expected_status: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + self._timeout_seconds
        last_status = "unknown"
        while time.monotonic() < deadline:
            file = _mapping(
                self._client.json("GET", f"/api/files/{file_id}", token=actor.token),
                "file detail",
            )
            last_status = str(file.get("status", "unknown"))
            if last_status == expected_status:
                return file
            if last_status in {"failed", "analysis_failed", "ragflow_cleanup_failed"}:
                break
            time.sleep(0.5)
        raise InfrastructureProbeError(
            f"file did not reach {expected_status}; last bounded state was {last_status}"
        )

    def _wait_for_notification(
        self,
        actor: ProbeCredentials,
        file_id: uuid.UUID,
    ) -> uuid.UUID:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            data = _mapping(
                self._client.json(
                    "GET",
                    "/api/notifications?page=1&page_size=20&unread_only=true",
                    token=actor.token,
                ),
                "notifications",
            )
            items = data.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and _is_review_approval_notification(
                        item, file_id=file_id
                    ):
                        return _uuid_value(item.get("id"), "notification id")
            time.sleep(0.5)
        raise InfrastructureProbeError("review notification was not persisted")

    def _require_audit(
        self,
        actor: ProbeCredentials,
        *,
        action: str,
        target_id: uuid.UUID | None = None,
        audit_id: uuid.UUID | None = None,
    ) -> None:
        query = urlencode({"action": action, "page": 1, "page_size": 100})
        data = _mapping(
            self._client.json("GET", f"/api/admin/audit-logs?{query}", token=actor.token),
            "audit log",
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise InfrastructureProbeError("audit log items are missing")
        matching = [
            item
            for item in items
            if isinstance(item, dict)
            and item.get("action") == action
            and (target_id is None or item.get("target_id") == str(target_id))
            and (audit_id is None or item.get("id") == str(audit_id))
        ]
        _require(bool(matching), "required administrator audit record is missing")

    def _mock_state(self) -> dict[str, object]:
        return _mapping(
            self._client.external_json(
                self._mock_ragflow_state_url,
                headers={"X-E2E-Probe-Token": self._probe_token},
            ),
            "mock RAGFlow state",
        )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InfrastructureProbeError(f"{label} is not an object")
    return value


def _config_value(group: dict[str, Any], key: str) -> object:
    items = group.get("items")
    if not isinstance(items, list):
        raise InfrastructureProbeError("config group items are invalid")
    for item in items:
        if isinstance(item, dict) and item.get("key") == key:
            return item.get("value")
    raise InfrastructureProbeError(f"config key is missing: {key}")


def _uuid_value(value: object, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise InfrastructureProbeError(f"{label} is invalid") from error


def _int_value(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InfrastructureProbeError(f"{label} is invalid")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InfrastructureProbeError(message)


def _safe_path(path: str) -> str:
    return path.partition("?")[0]
