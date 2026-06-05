from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ACTIVE_SYNC_TASK_STATUSES, SyncTask, SyncTaskLog


class RagflowTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_task(self, task: SyncTask) -> SyncTask:
        self._session.add(task)
        await self._session.flush()
        await self._session.refresh(task)
        return task

    async def get_active_task(self, *, file_id: uuid.UUID, task_type: str) -> SyncTask | None:
        result = await self._session.execute(
            select(SyncTask)
            .where(
                SyncTask.file_id == file_id,
                SyncTask.task_type == task_type,
                SyncTask.status.in_(ACTIVE_SYNC_TASK_STATUSES),
            )
            .order_by(SyncTask.created_at.asc(), SyncTask.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_tasks(self) -> list[SyncTask]:
        result = await self._session.execute(
            select(SyncTask).order_by(SyncTask.created_at.desc(), SyncTask.id.desc())
        )
        return list(result.scalars())

    async def get_task(self, task_id: uuid.UUID) -> SyncTask | None:
        result = await self._session.execute(select(SyncTask).where(SyncTask.id == task_id))
        return result.scalar_one_or_none()

    async def get_task_for_update(self, task_id: uuid.UUID) -> SyncTask | None:
        result = await self._session.execute(
            select(SyncTask).where(SyncTask.id == task_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_log(self, *, task_id: uuid.UUID, status: str, message: str) -> SyncTaskLog:
        log = SyncTaskLog(task_id=task_id, status=status, message=message)
        self._session.add(log)
        await self._session.flush()
        await self._session.refresh(log)
        return log

    async def list_logs(self, task_id: uuid.UUID) -> list[SyncTaskLog]:
        result = await self._session.execute(
            select(SyncTaskLog)
            .where(SyncTaskLog.task_id == task_id)
            .order_by(SyncTaskLog.created_at.asc(), SyncTaskLog.id.asc())
        )
        return list(result.scalars())
