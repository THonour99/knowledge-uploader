from __future__ import annotations

import uuid
from datetime import datetime

from app.core.permissions import Role

from .repository import AuditRepository
from .schemas import AuditLogItemResponse, AuditLogListResponse

# Roles allowed to query audit logs.
_AUDIT_READ_ROLES: frozenset[str] = frozenset(
    {Role.KNOWLEDGE_ADMIN.value, Role.SYSTEM_ADMIN.value}
)

# Maximum allowed page_size for audit log queries.
_MAX_PAGE_SIZE = 100


class AuditPermissionError(Exception):
    """Raised when a caller lacks permission to query audit logs."""


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record_admin_action(
        self,
        *,
        actor_id: uuid.UUID,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        ip_address: str,
        user_agent: str,
        metadata_json: dict[str, object] | None = None,
        reason: str | None = None,
    ) -> None:
        await self._repository.create_log(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json=metadata_json,
            reason=reason,
        )

    async def search_logs(
        self,
        *,
        caller_role: str,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        target_type: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AuditLogListResponse:
        """Query audit logs with optional filters.

        NOTE: This read operation intentionally does NOT write an audit entry.
        Writing a log for every read would cause an unbounded cascade
        (read → write → read → write …) and pollute the audit trail with
        non-operational noise.  The decision is deliberate and reviewed.
        """
        if caller_role not in _AUDIT_READ_ROLES:
            raise AuditPermissionError(
                f"role '{caller_role}' is not allowed to read audit logs"
            )

        # Clamp page_size to the configured maximum.
        effective_page_size = min(page_size, _MAX_PAGE_SIZE)

        rows, total = await self._repository.search_logs(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=effective_page_size,
        )

        items = [AuditLogItemResponse.model_validate(row) for row in rows]
        return AuditLogListResponse(
            items=items,
            total=total,
            page=page,
            page_size=effective_page_size,
        )
