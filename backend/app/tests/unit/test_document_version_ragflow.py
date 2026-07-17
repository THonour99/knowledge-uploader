from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class VersionSeed:
    predecessor_id: uuid.UUID
    candidate_id: uuid.UUID
    task_id: uuid.UUID


class MetadataClient:
    def __init__(
        self,
        *,
        fail_calls: set[int] | None = None,
        not_found_calls: set[int] | None = None,
    ) -> None:
        self.fail_calls = fail_calls or set()
        self.not_found_calls = not_found_calls or set()
        self.calls: list[dict[str, object]] = []

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        self.calls.append(
            {
                "dataset_id": dataset_id,
                "operation": "metadata",
                "document_id": document_id,
                "name": name,
                "metadata": dict(metadata),
            }
        )
        if len(self.calls) in self.fail_calls:
            raise RuntimeError("synthetic remote operation failure")

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        self.calls.append(
            {
                "operation": "delete",
                "dataset_id": dataset_id,
                "document_id": document_id,
            }
        )
        call_number = len(self.calls)
        if call_number in self.not_found_calls:
            from app.adapters.ragflow.base import RagflowDocumentNotFoundError

            raise RagflowDocumentNotFoundError
        if call_number in self.fail_calls:
            raise RuntimeError("synthetic remote cleanup failure")


async def _reset_database() -> None:
    import_module("app.db.models")
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
async def version_session() -> AsyncGenerator[AsyncSession, None]:
    from app.core.database import AsyncSessionFactory

    await _reset_database()
    async with AsyncSessionFactory() as session:
        yield session
    await _reset_database()


async def _seed_version_pair(
    session: AsyncSession,
    *,
    remote_action: str = "delete",
) -> VersionSeed:
    from app.modules.department.models import Department
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask
    from app.modules.user.models import User

    now = datetime.now(UTC)
    department_id = uuid.uuid4()
    user_id = uuid.uuid4()
    department = Department(
        id=department_id,
        name="版本治理部",
        code=f"version-{uuid.uuid4()}",
        status="active",
    )
    user = User(
        id=user_id,
        name="version-owner",
        email=f"version-{uuid.uuid4()}@company.com",
        email_domain="company.com",
        password_hash="x",
        department_id=department_id,
        department=department.name,
        role="employee",
        status="active",
        email_verified=True,
    )
    predecessor_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    predecessor = File(
        id=predecessor_id,
        original_name="policy-v1.pdf",
        title="policy-v1.pdf",
        stored_name=f"{predecessor_id}-policy-v1.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=128,
        hash="a" * 64,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{predecessor_id}.pdf",
        uploader_id=user_id,
        owner_id=user_id,
        department_id=department_id,
        department=department.name,
        visibility="department",
        description="must not leak to remote metadata",
        tags=["policy"],
        status="parsed",
        review_status="approved",
        ragflow_dataset_id="dataset-version",
        ragflow_document_id="remote-v1",
        ragflow_parse_status="DONE",
        ai_analysis_enabled_at_upload=False,
        series_id=predecessor_id,
        version_number=1,
        is_current_version=True,
        remote_visibility="current",
        version_switch_status="not_required",
        remote_version_activated_at=now,
    )
    candidate = File(
        id=candidate_id,
        original_name="policy-v2.pdf",
        title="policy-v2.pdf",
        stored_name=f"{candidate_id}-policy-v2.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=256,
        hash="b" * 64,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{candidate_id}.pdf",
        uploader_id=user_id,
        owner_id=user_id,
        department_id=department_id,
        department=department.name,
        visibility="department",
        description="must not leak to remote metadata",
        tags=["policy", "current"],
        status="parsed",
        review_status="approved",
        ragflow_dataset_id="dataset-version",
        ragflow_document_id="remote-v2",
        ragflow_parse_status="DONE",
        ai_analysis_enabled_at_upload=False,
        series_id=predecessor_id,
        version_number=2,
        replaces_file_id=predecessor_id,
        replacement_remote_action=remote_action,
        is_current_version=False,
        remote_visibility="candidate",
        version_switch_status="pending",
    )
    task = SyncTask(
        file_id=candidate_id,
        task_type="ragflow_upload",
        status="running",
        retry_count=0,
        max_retry_count=3,
        lease_token="version-test-lease",
        started_at=now,
        lease_heartbeat_at=now,
    )
    session.add_all([department, user])
    await session.commit()
    session.add_all([predecessor, candidate, task])
    await session.commit()
    return VersionSeed(
        predecessor_id=predecessor_id,
        candidate_id=candidate_id,
        task_id=task.id,
    )


async def test_version_switch_recovers_old_remote_failure_and_records_candidate_timeline(
    version_session: AsyncSession,
) -> None:
    from app.modules.document.models import File
    from app.modules.ragflow.models import RagflowVersionOperation
    from app.modules.ragflow.repository import (  # noqa: TID251 - focused module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import (  # noqa: TID251 - focused module test
        RagflowTaskService,
        RagflowVersionSwitchError,
    )

    seed = await _seed_version_pair(version_session)
    repository = RagflowTaskRepository(version_session)
    service = RagflowTaskService(session=version_session, repository=repository)
    client = MetadataClient(fail_calls={1}, not_found_calls={2})
    task = await repository.get_task(seed.task_id)
    candidate = await repository.get_file(seed.candidate_id)
    assert task is not None and candidate is not None
    candidate_metadata = service._build_metadata(candidate)
    assert candidate_metadata["is_current_version"] is False
    assert "description" not in candidate_metadata
    assert "object_key" not in candidate_metadata

    with pytest.raises(RagflowVersionSwitchError):
        await service._complete_version_switch(
            task=task,
            file=candidate,
            ragflow_client=client,  # type: ignore[arg-type]
        )
    failed = await repository.get_file(seed.candidate_id)
    predecessor = await repository.get_file(seed.predecessor_id)
    assert failed is not None and predecessor is not None
    assert failed.version_switch_status == "failed_old_deactivate"
    assert failed.version_switch_error == "RuntimeError"
    assert failed.is_current_version is False
    assert predecessor.is_current_version is True
    assert predecessor.remote_visibility == "current"

    task = await repository.get_task(seed.task_id)
    candidate = await repository.get_file(seed.candidate_id)
    assert task is not None and candidate is not None
    completed = await service._complete_version_switch(
        task=task,
        file=candidate,
        ragflow_client=client,  # type: ignore[arg-type]
    )
    assert completed.version_switch_status == "completed"
    assert completed.version_switch_error is None
    assert completed.is_current_version is True
    assert completed.remote_visibility == "current"
    assert completed.predecessor_remote_deactivated_at is not None
    assert completed.local_version_activated_at is not None
    assert completed.remote_version_activated_at is not None

    predecessor_row = await version_session.get(File, seed.predecessor_id)
    assert predecessor_row is not None
    assert predecessor_row.is_current_version is False
    assert predecessor_row.remote_visibility == "not_current"
    assert predecessor_row.predecessor_remote_deactivated_at is None
    assert [call["document_id"] for call in client.calls] == [
        "remote-v1",
        "remote-v1",
        "remote-v2",
    ]
    assert client.calls[0] == client.calls[1]
    assert client.calls[0]["operation"] == client.calls[1]["operation"] == "delete"
    assert client.calls[2]["metadata"]["is_current_version"] is True  # type: ignore[index]
    assert "description" not in client.calls[2]["metadata"]  # type: ignore[operator]
    assert "object_key" not in client.calls[2]["metadata"]  # type: ignore[operator]

    operations = list(
        (
            await version_session.execute(
                select(RagflowVersionOperation).where(
                    RagflowVersionOperation.file_id == seed.candidate_id
                )
            )
        ).scalars()
    )
    by_operation = {operation.operation: operation for operation in operations}
    assert by_operation["deactivate_predecessor"].status == "succeeded"
    assert by_operation["deactivate_predecessor"].attempt_count == 2
    assert by_operation["activate_candidate"].status == "succeeded"
    assert by_operation["activate_candidate"].attempt_count == 1


async def test_unknown_predecessor_delete_outcome_is_persisted_for_reconciliation(
    version_session: AsyncSession,
) -> None:
    from app.adapters.ragflow.base import RagflowSubmissionOutcomeUnknownError
    from app.modules.ragflow.models import RagflowVersionOperation
    from app.modules.ragflow.repository import (  # noqa: TID251 - focused module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import (  # noqa: TID251 - focused module test
        RagflowTaskService,
        RagflowVersionSwitchError,
    )

    class UnknownDeleteClient(MetadataClient):
        async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
            self.calls.append(
                {
                    "operation": "delete",
                    "dataset_id": dataset_id,
                    "document_id": document_id,
                }
            )
            raise RagflowSubmissionOutcomeUnknownError("synthetic unknown delete outcome")

    seed = await _seed_version_pair(version_session)
    repository = RagflowTaskRepository(version_session)
    service = RagflowTaskService(session=version_session, repository=repository)
    task = await repository.get_task(seed.task_id)
    candidate = await repository.get_file(seed.candidate_id)
    assert task is not None and candidate is not None

    with pytest.raises(RagflowVersionSwitchError):
        await service._complete_version_switch(
            task=task,
            file=candidate,
            ragflow_client=UnknownDeleteClient(),
        )

    operation = (
        await version_session.execute(
            select(RagflowVersionOperation).where(
                RagflowVersionOperation.file_id == seed.candidate_id,
                RagflowVersionOperation.operation == "deactivate_predecessor",
            )
        )
    ).scalar_one()
    assert operation.status == "unknown"
    failed_candidate = await repository.get_file(seed.candidate_id)
    assert failed_candidate is not None
    assert failed_candidate.version_switch_status == "failed_old_deactivate"


async def test_candidate_remote_activation_is_idempotent_after_database_failure(
    version_session: AsyncSession,
) -> None:
    from app.modules.ragflow.models import RagflowVersionOperation
    from app.modules.ragflow.repository import (  # noqa: TID251 - focused module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import RagflowTaskService  # noqa: TID251 - focused module test

    class FailCandidateFinishRepository(RagflowTaskRepository):
        fail_candidate_finish = True

        async def finish_version_operation(
            self,
            *,
            file_id: uuid.UUID,
            operation: str,
            succeeded: bool,
            finished_at: datetime,
            error_type: str | None = None,
            outcome_unknown: bool = False,
        ) -> RagflowVersionOperation:
            if operation == "activate_candidate" and self.fail_candidate_finish:
                self.fail_candidate_finish = False
                raise RuntimeError("synthetic database finish failure")
            return await super().finish_version_operation(
                file_id=file_id,
                operation=operation,
                succeeded=succeeded,
                finished_at=finished_at,
                error_type=error_type,
                outcome_unknown=outcome_unknown,
            )

    seed = await _seed_version_pair(version_session)
    failing_repository = FailCandidateFinishRepository(version_session)
    service = RagflowTaskService(session=version_session, repository=failing_repository)
    client = MetadataClient()
    task = await failing_repository.get_task(seed.task_id)
    candidate = await failing_repository.get_file(seed.candidate_id)
    assert task is not None and candidate is not None

    with pytest.raises(RuntimeError, match="database finish failure"):
        await service._complete_version_switch(
            task=task,
            file=candidate,
            ragflow_client=client,  # type: ignore[arg-type]
        )
    await version_session.rollback()

    repository = RagflowTaskRepository(version_session)
    interrupted = await repository.get_file(seed.candidate_id)
    task = await repository.get_task(seed.task_id)
    assert interrupted is not None and task is not None
    assert interrupted.version_switch_status == "local_switched"
    assert interrupted.is_current_version is True
    assert interrupted.remote_visibility == "candidate"
    operation = (
        await version_session.execute(
            select(RagflowVersionOperation).where(
                RagflowVersionOperation.file_id == seed.candidate_id,
                RagflowVersionOperation.operation == "activate_candidate",
            )
        )
    ).scalar_one()
    assert operation.status == "running"
    assert operation.attempt_count == 1

    retry_service = RagflowTaskService(session=version_session, repository=repository)
    completed = await retry_service._complete_version_switch(
        task=task,
        file=interrupted,
        ragflow_client=client,  # type: ignore[arg-type]
    )
    assert completed.version_switch_status == "completed"
    assert [call["document_id"] for call in client.calls] == [
        "remote-v1",
        "remote-v2",
        "remote-v2",
    ]
    assert client.calls[1]["metadata"] == client.calls[2]["metadata"]
    operation = (
        await version_session.execute(
            select(RagflowVersionOperation).where(
                RagflowVersionOperation.file_id == seed.candidate_id,
                RagflowVersionOperation.operation == "activate_candidate",
            )
        )
    ).scalar_one()
    assert operation.status == "succeeded"
    assert operation.attempt_count == 2


async def test_archive_snapshot_preserves_predecessor_and_marks_it_non_current(
    version_session: AsyncSession,
) -> None:
    from app.modules.ragflow.models import RagflowVersionOperation
    from app.modules.ragflow.repository import (  # noqa: TID251 - focused module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import RagflowTaskService  # noqa: TID251

    seed = await _seed_version_pair(version_session, remote_action="archive")
    repository = RagflowTaskRepository(version_session)
    service = RagflowTaskService(session=version_session, repository=repository)
    task = await repository.get_task(seed.task_id)
    candidate = await repository.get_file(seed.candidate_id)
    assert task is not None and candidate is not None
    assert candidate.replacement_remote_action == "archive"

    client = MetadataClient()
    completed = await service._complete_version_switch(
        task=task,
        file=candidate,
        ragflow_client=client,  # type: ignore[arg-type]
    )

    assert completed.version_switch_status == "completed"
    assert [call["operation"] for call in client.calls] == ["metadata", "metadata"]
    assert [call["document_id"] for call in client.calls] == ["remote-v1", "remote-v2"]
    assert client.calls[0]["metadata"]["is_current_version"] is False  # type: ignore[index]
    assert client.calls[1]["metadata"]["is_current_version"] is True  # type: ignore[index]
    operation = (
        await version_session.execute(
            select(RagflowVersionOperation).where(
                RagflowVersionOperation.file_id == seed.candidate_id,
                RagflowVersionOperation.operation == "deactivate_predecessor",
            )
        )
    ).scalar_one()
    assert operation.status == "succeeded"
