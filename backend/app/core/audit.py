from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog


async def record_admin_audit_log(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    ip_address: str | None,
    metadata_json: dict[str, object] | None = None,
    reason: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            metadata_json=metadata_json or {},
            reason=reason,
        )
    )
