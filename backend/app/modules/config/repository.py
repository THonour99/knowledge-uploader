from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SystemConfig


class ConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_group(self, group: str) -> list[SystemConfig]:
        result = await self._session.execute(
            select(SystemConfig).where(SystemConfig.group == group).order_by(SystemConfig.key)
        )
        return list(result.scalars())

    async def get_by_keys(self, keys: Sequence[str]) -> dict[str, SystemConfig]:
        if not keys:
            return {}
        result = await self._session.execute(
            select(SystemConfig).where(SystemConfig.key.in_(list(keys)))
        )
        return {row.key: row for row in result.scalars()}

    async def upsert_value(
        self,
        *,
        key: str,
        group: str,
        value_type: str,
        is_secret: bool,
        description: str,
        value: object | None,
        updated_by: uuid.UUID,
    ) -> SystemConfig:
        result = await self._session.execute(select(SystemConfig).where(SystemConfig.key == key))
        row = result.scalar_one_or_none()
        if row is None:
            row = SystemConfig(
                key=key,
                group=group,
                value_type=value_type,
                is_secret=is_secret,
                description=description,
                value=value,
                updated_by=updated_by,
            )
            self._session.add(row)
        else:
            row.value = value
            row.updated_by = updated_by
        await self._session.flush()
        return row
