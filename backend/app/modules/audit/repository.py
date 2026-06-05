from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_log(
        self,
        *,
        actor_id: uuid.UUID | None,
        action: str,
        target_type: str,
        target_id: uuid.UUID | None,
        ip_address: str | None,
        metadata_json: dict[str, object] | None = None,
        reason: str | None = None,
    ) -> AuditLog:
        log = AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            metadata_json=metadata_json or {},
            reason=reason,
        )
        self._session.add(log)
        await self._session.flush()
        return log
