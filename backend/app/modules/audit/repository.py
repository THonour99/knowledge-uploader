from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog

# ---------------------------------------------------------------------------
# Shadow table definition for users — allows LEFT JOIN without crossing the
# module service/repository boundary (same pattern as review/repository.py).
# ---------------------------------------------------------------------------

USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("email", String(255), nullable=False),
)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_log(
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
    ) -> AuditLog:
        log = AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json=metadata_json or {},
            reason=reason,
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def search_logs(
        self,
        *,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        target_type: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[_AuditLogRow], int]:
        """Return (rows, total_count).

        Performs a LEFT JOIN to the users table so that logs for deleted actors
        still appear with actor_name=NULL and actor_email=NULL.
        """
        audit = AuditLog.__table__

        base_query = select(
            audit.c.id,
            audit.c.actor_id,
            USERS.c.name.label("actor_name"),
            USERS.c.email.label("actor_email"),
            audit.c.action,
            audit.c.target_type,
            audit.c.target_id,
            audit.c.ip_address,
            audit.c.user_agent,
            audit.c.reason,
            audit.c.metadata_json,
            audit.c.created_at,
        ).select_from(audit.outerjoin(USERS, audit.c.actor_id == USERS.c.id))

        # apply filters dynamically
        if actor_id is not None:
            base_query = base_query.where(audit.c.actor_id == actor_id)
        if action is not None:
            base_query = base_query.where(audit.c.action == action)
        if target_type is not None:
            base_query = base_query.where(audit.c.target_type == target_type)
        if created_from is not None:
            base_query = base_query.where(audit.c.created_at >= created_from)
        if created_to is not None:
            base_query = base_query.where(audit.c.created_at <= created_to)

        # count total before pagination
        count_query = select(func.count()).select_from(base_query.subquery())
        total = (await self._session.execute(count_query)).scalar_one()

        # paginate + sort
        offset = (page - 1) * page_size
        data_query = base_query.order_by(audit.c.created_at.desc()).offset(offset).limit(page_size)
        rows = (await self._session.execute(data_query)).mappings().all()

        return [dict(row) for row in rows], int(total)


# Type alias for the row dict returned by search_logs
_AuditLogRow = dict[str, object]
