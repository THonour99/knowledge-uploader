from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, func, insert
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession

AUDIT_LOGS = Table(
    "audit_logs",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("action", String(120), nullable=False),
    Column("target_type", String(80), nullable=False),
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("ip_address", String(45), nullable=False),
    Column("user_agent", String(512), nullable=False),
    Column("metadata_json", JSONB, nullable=False),
    Column("reason", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


async def record_audit_log(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    ip_address: str,
    user_agent: str,
    metadata_json: dict[str, object] | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    log_id = uuid.uuid4()
    await session.execute(
        insert(AUDIT_LOGS).values(
            id=log_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json=metadata_json or {},
            reason=reason,
        )
    )
    return log_id


async def record_admin_audit_log(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    ip_address: str,
    user_agent: str,
    metadata_json: dict[str, object] | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    return await record_audit_log(
        session,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=metadata_json,
        reason=reason,
    )
