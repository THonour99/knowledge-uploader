from __future__ import annotations

from typing import ClassVar

from app.core.events import DomainEvent

RAGFLOW_SYNC_TASK_QUEUED = "ragflow.sync_task.queued"
RAGFLOW_SYNC_TASK_SUCCEEDED = "ragflow.sync_task.succeeded"
RAGFLOW_SYNC_TASK_FAILED = "ragflow.sync_task.failed"


class RagflowSyncTaskQueued(DomainEvent):
    ROUTING_KEY: ClassVar[str] = RAGFLOW_SYNC_TASK_QUEUED


class RagflowSyncTaskSucceeded(DomainEvent):
    ROUTING_KEY: ClassVar[str] = RAGFLOW_SYNC_TASK_SUCCEEDED


class RagflowSyncTaskFailed(DomainEvent):
    ROUTING_KEY: ClassVar[str] = RAGFLOW_SYNC_TASK_FAILED
