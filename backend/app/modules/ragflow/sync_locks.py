from __future__ import annotations

import asyncio
import uuid

from redis.asyncio import from_url
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransaction

from .models import SyncTask
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency

SYNC_LOCK_TTL_SECONDS = 30
SYNC_LOCK_WAIT_SECONDS = 2.0
SYNC_LOCK_POLL_SECONDS = 0.05
RELEASE_SYNC_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""
_PENDING_LOCK_RELEASE_TASKS: set[asyncio.Task[None]] = set()


class RagflowSyncLockBusy(Exception):
    pass


async def wait_for_active_ragflow_upload_task(
    *,
    repository: RagflowTaskRepository,
    file_id: uuid.UUID,
) -> SyncTask | None:
    deadline = asyncio.get_running_loop().time() + SYNC_LOCK_WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(SYNC_LOCK_POLL_SECONDS)
        active_task = await repository.get_active_task(file_id=file_id, task_type="ragflow_upload")
        if active_task is not None:
            return active_task
    return None


async def acquire_sync_lock(*, redis_url: str, file_id: uuid.UUID, token: str) -> bool:
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        return bool(
            await client.set(
                sync_lock_key(file_id),
                token,
                nx=True,
                ex=SYNC_LOCK_TTL_SECONDS,
            )
        )
    finally:
        await client.aclose()


async def release_sync_lock(*, redis_url: str, file_id: uuid.UUID, token: str) -> None:
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await client.eval(RELEASE_SYNC_LOCK_SCRIPT, 1, sync_lock_key(file_id), token)
    finally:
        await client.aclose()


def release_sync_lock_after_transaction(
    *,
    session: AsyncSession,
    redis_url: str,
    file_id: uuid.UUID,
    token: str,
) -> None:
    sync_session = session.sync_session
    released = False

    def release_after_end(
        ended_session: Session,
        transaction: SessionTransaction,
    ) -> None:
        nonlocal released
        del ended_session
        if transaction.parent is not None:
            return
        if released:
            return
        released = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(release_sync_lock(redis_url=redis_url, file_id=file_id, token=token))
            return
        release_task = loop.create_task(
            release_sync_lock(redis_url=redis_url, file_id=file_id, token=token)
        )
        _PENDING_LOCK_RELEASE_TASKS.add(release_task)
        release_task.add_done_callback(_PENDING_LOCK_RELEASE_TASKS.discard)

    event.listen(sync_session, "after_transaction_end", release_after_end)


def sync_lock_key(file_id: uuid.UUID) -> str:
    return f"lock:sync:{file_id}"
