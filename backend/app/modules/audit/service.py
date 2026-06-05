from __future__ import annotations

import uuid

from app.modules.audit.repository import AuditRepository


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
