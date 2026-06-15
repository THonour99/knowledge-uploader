from __future__ import annotations

import csv
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from io import StringIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .repository import (  # noqa: TID251 - same-module repository dependency
    StatisticsFailedTaskRow,
    StatisticsFileRow,
    StatisticsFilters,
    StatisticsRepository,
)
from .schemas import (
    StatisticsCategoryListResponse,
    StatisticsCategoryRow,
    StatisticsDepartmentListResponse,
    StatisticsDepartmentRow,
    StatisticsExpiryResponse,
    StatisticsExpiryStatusCount,
    StatisticsFailureListResponse,
    StatisticsFailureRow,
    StatisticsOverviewResponse,
    StatisticsTrendPoint,
    StatisticsTrendResponse,
    StatisticsUserDetailResponse,
    StatisticsUserListResponse,
    StatisticsUserRow,
)

ADMIN_ROLES = {"knowledge_admin", "system_admin"}
VALID_GROUP_BY = {"day", "week", "month"}
VALID_SYNC_STATUS = {"synced", "failed", "syncing", "not_synced"}
VALID_SORT_FIELDS = {
    "total_files",
    "synced_files",
    "failed_files",
    "pending_review_files",
    "total_file_size",
    "last_upload_at",
}
VALID_SORT_ORDERS = {"asc", "desc"}
RISK_SENSITIVE = {"high", "critical"}


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


@dataclass(frozen=True)
class StatisticsQuery:
    start_date: date | None = None
    end_date: date | None = None
    department: str | None = None
    user_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    status: str | None = None
    review_status: str | None = None
    sync_status: str | None = None
    group_by: str = "day"
    page: int = 1
    page_size: int = 20
    sort_by: str = "total_files"
    sort_order: str = "desc"


class StatisticsService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: StatisticsRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def overview(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsOverviewResponse:
        self._require_admin(current_user)
        files = await self._repository.list_files(to_filters(query))
        failed_tasks = await self._repository.list_failed_tasks(to_filters(query))
        response = StatisticsOverviewResponse(
            total_files=len(files),
            active_uploaders=len({file.uploader_id for file in files}),
            synced_files=sum(1 for file in files if is_synced(file)),
            pending_review_files=sum(1 for file in files if is_pending_review(file)),
            failed_files=sum(1 for file in files if is_failed_file(file)),
            failed_tasks=len(failed_tasks),
            rejected_files=sum(1 for file in files if is_rejected(file)),
            sensitive_files=sum(1 for file in files if is_sensitive(file)),
            total_file_size=sum(file.size for file in files),
            sync_success_rate=sync_success_rate(files),
        )
        await self._record_audit(
            current_user=current_user,
            action="statistics.overview.view",
            context=context,
            metadata_json=query_metadata(query),
        )
        await self._session.commit()
        return response

    async def users(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsUserListResponse:
        self._require_admin(current_user)
        files = await self._repository.list_files(to_filters(query))
        rows = sorted_user_rows(build_user_rows(files), query)
        total = len(rows)
        page = max(query.page, 1)
        page_size = max(query.page_size, 1)
        start = (page - 1) * page_size
        items = rows[start : start + page_size]
        await self._record_audit(
            current_user=current_user,
            action="statistics.users.list",
            context=context,
            metadata_json={**query_metadata(query), "result_count": len(items)},
        )
        await self._session.commit()
        return StatisticsUserListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def user_detail(
        self,
        *,
        current_user: AuthUserRecord,
        user_id: uuid.UUID,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsUserDetailResponse:
        self._require_admin(current_user)
        if not await self._repository.user_exists(user_id):
            raise exceptions.user_not_found()
        scoped_query = query_with_user(query, user_id)
        files = await self._repository.list_files(to_filters(scoped_query))
        user_rows = build_user_rows(files)
        user_row = user_rows[0] if user_rows else empty_user_row(user_id)
        await self._record_audit(
            current_user=current_user,
            action="statistics.users.get",
            context=context,
            metadata_json={**query_metadata(scoped_query), "user_id": str(user_id)},
        )
        await self._session.commit()
        return StatisticsUserDetailResponse(
            user=user_row,
            category_breakdown=build_category_rows(files),
        )

    async def departments(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsDepartmentListResponse:
        self._require_admin(current_user)
        files = await self._repository.list_files(to_filters(query))
        rows = build_department_rows(files)
        await self._record_audit(
            current_user=current_user,
            action="statistics.departments.list",
            context=context,
            metadata_json={**query_metadata(query), "result_count": len(rows)},
        )
        await self._session.commit()
        return StatisticsDepartmentListResponse(items=rows, total=len(rows))

    async def categories(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsCategoryListResponse:
        self._require_admin(current_user)
        files = await self._repository.list_files(to_filters(query))
        rows = build_category_rows(files)
        await self._record_audit(
            current_user=current_user,
            action="statistics.categories.list",
            context=context,
            metadata_json={**query_metadata(query), "result_count": len(rows)},
        )
        await self._session.commit()
        return StatisticsCategoryListResponse(items=rows, total=len(rows))

    async def trends(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsTrendResponse:
        self._require_admin(current_user)
        self._validate_group_by(query.group_by)
        files = await self._repository.list_files(to_filters(query))
        response = StatisticsTrendResponse(
            group_by=query.group_by,
            items=build_trend_points(files, query),
        )
        await self._record_audit(
            current_user=current_user,
            action="statistics.trends.list",
            context=context,
            metadata_json=query_metadata(query),
        )
        await self._session.commit()
        return response

    async def failures(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> StatisticsFailureListResponse:
        self._require_admin(current_user)
        tasks = await self._repository.list_failed_tasks(to_filters(query))
        rows = build_failure_rows(tasks)
        await self._record_audit(
            current_user=current_user,
            action="statistics.failures.list",
            context=context,
            metadata_json={**query_metadata(query), "result_count": len(rows)},
        )
        await self._session.commit()
        return StatisticsFailureListResponse(items=rows, total=len(rows))

    async def expiry(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
        as_of: datetime | None,
        remind_days: int,
    ) -> StatisticsExpiryResponse:
        self._require_admin(current_user)
        if remind_days < 0:
            raise exceptions.invalid_filter("invalid remind_days")
        effective_as_of = normalized_datetime(as_of)
        window_end = effective_as_of + timedelta(days=remind_days)
        counts = await self._repository.count_expiry_statuses(
            to_filters(query),
            as_of=effective_as_of,
            window_end=window_end,
        )
        response = StatisticsExpiryResponse(
            total=counts.total,
            active=counts.active,
            expiring=counts.expiring,
            expired=counts.expired,
            never=counts.never,
            remind_days=remind_days,
            as_of=effective_as_of,
            window_end=window_end,
            items=[
                StatisticsExpiryStatusCount(status="active", count=counts.active),
                StatisticsExpiryStatusCount(status="expiring", count=counts.expiring),
                StatisticsExpiryStatusCount(status="expired", count=counts.expired),
                StatisticsExpiryStatusCount(status="never", count=counts.never),
            ],
        )
        await self._record_audit(
            current_user=current_user,
            action="statistics.expiry.view",
            context=context,
            metadata_json={
                **query_metadata(query),
                "as_of": effective_as_of.isoformat(),
                "remind_days": remind_days,
            },
        )
        await self._session.commit()
        return response

    async def export_users_csv(
        self,
        *,
        current_user: AuthUserRecord,
        query: StatisticsQuery,
        context: RequestContext,
    ) -> str:
        self._require_admin(current_user)
        files = await self._repository.list_files(to_filters(query))
        rows = sorted_user_rows(build_user_rows(files), query)
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "用户",
                "部门",
                "上传文件总数",
                "已同步成功数量",
                "同步失败数量",
                "待审核数量",
                "总文件大小",
                "最近上传时间",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    csv_safe(row.user_name),
                    csv_safe(row.department or "未设置"),
                    row.total_files,
                    row.synced_files,
                    row.failed_files,
                    row.pending_review_files,
                    row.total_file_size,
                    row.last_upload_at.isoformat() if row.last_upload_at else "",
                ]
            )
        await self._record_audit(
            current_user=current_user,
            action="statistics.export",
            context=context,
            metadata_json=query_metadata(query),
        )
        await self._session.commit()
        return buffer.getvalue()

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    def _validate_group_by(self, group_by: str) -> None:
        if group_by not in VALID_GROUP_BY:
            raise exceptions.invalid_filter("invalid group_by")

    async def _record_audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=action,
            target_type="statistics",
            target_id=current_user.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json=metadata_json,
        )


def to_filters(query: StatisticsQuery) -> StatisticsFilters:
    if query.sync_status is not None and query.sync_status not in VALID_SYNC_STATUS:
        raise exceptions.invalid_filter("invalid sync_status")
    return StatisticsFilters(
        start_at=datetime.combine(query.start_date, time.min, UTC)
        if query.start_date is not None
        else None,
        end_before=datetime.combine(query.end_date + timedelta(days=1), time.min, UTC)
        if query.end_date is not None
        else None,
        department=query.department,
        user_id=query.user_id,
        category_id=query.category_id,
        status=query.status,
        review_status=query.review_status,
        sync_status=query.sync_status,
    )


def query_with_user(query: StatisticsQuery, user_id: uuid.UUID) -> StatisticsQuery:
    return StatisticsQuery(
        start_date=query.start_date,
        end_date=query.end_date,
        department=query.department,
        user_id=user_id,
        category_id=query.category_id,
        status=query.status,
        review_status=query.review_status,
        sync_status=query.sync_status,
        group_by=query.group_by,
        page=query.page,
        page_size=query.page_size,
        sort_by=query.sort_by,
        sort_order=query.sort_order,
    )


def query_metadata(query: StatisticsQuery) -> dict[str, object]:
    return {
        "start_date": query.start_date.isoformat() if query.start_date else None,
        "end_date": query.end_date.isoformat() if query.end_date else None,
        "department": query.department,
        "user_id": str(query.user_id) if query.user_id else None,
        "category_id": str(query.category_id) if query.category_id else None,
        "status": query.status,
        "review_status": query.review_status,
        "sync_status": query.sync_status,
        "group_by": query.group_by,
    }


def normalized_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_user_rows(files: Iterable[StatisticsFileRow]) -> list[StatisticsUserRow]:
    grouped: dict[uuid.UUID, list[StatisticsFileRow]] = defaultdict(list)
    for file in files:
        grouped[file.uploader_id].append(file)
    rows = [
        user_row_from_files(rank=index + 1, user_id=user_id, files=user_files)
        for index, (user_id, user_files) in enumerate(grouped.items())
    ]
    return sorted_user_rows(rows, StatisticsQuery())


def sorted_user_rows(
    rows: list[StatisticsUserRow],
    query: StatisticsQuery,
) -> list[StatisticsUserRow]:
    sort_by = query.sort_by if query.sort_by in VALID_SORT_FIELDS else "total_files"
    sort_order = query.sort_order if query.sort_order in VALID_SORT_ORDERS else "desc"
    reverse = sort_order == "desc"
    if sort_by == "last_upload_at":
        sorted_rows = sorted(
            rows,
            key=lambda row: (row.last_upload_at or datetime.min.replace(tzinfo=UTC), row.user_name),
            reverse=reverse,
        )
    else:
        sorted_rows = sorted(
            rows,
            key=lambda row: (numeric_user_sort_value(row, sort_by), row.user_name),
            reverse=reverse,
        )
    return [row.model_copy(update={"rank": index + 1}) for index, row in enumerate(sorted_rows)]


def numeric_user_sort_value(row: StatisticsUserRow, sort_by: str) -> int:
    value = getattr(row, sort_by)
    if isinstance(value, int):
        return value
    return 0


def csv_safe(value: str) -> str:
    if value and value[0] in {"=", "+", "-", "@", "\t", "\r"}:
        return f"'{value}"
    return value


def user_row_from_files(
    *,
    rank: int,
    user_id: uuid.UUID,
    files: list[StatisticsFileRow],
) -> StatisticsUserRow:
    first_file = files[0]
    return StatisticsUserRow(
        rank=rank,
        user_id=user_id,
        user_name=first_file.user_name,
        department=first_file.user_department or first_file.department,
        total_files=len(files),
        approved_files=sum(1 for file in files if file.review_status == "approved"),
        synced_files=sum(1 for file in files if is_synced(file)),
        failed_files=sum(1 for file in files if is_failed_file(file)),
        pending_review_files=sum(1 for file in files if is_pending_review(file)),
        rejected_files=sum(1 for file in files if is_rejected(file)),
        sensitive_files=sum(1 for file in files if is_sensitive(file)),
        total_file_size=sum(file.size for file in files),
        last_upload_at=max((file.uploaded_at for file in files), default=None),
        last_success_sync_at=max(
            (file.last_sync_at for file in files if file.last_sync_at is not None),
            default=None,
        ),
    )


def empty_user_row(user_id: uuid.UUID) -> StatisticsUserRow:
    return StatisticsUserRow(
        rank=1,
        user_id=user_id,
        user_name="未知用户",
        department=None,
        total_files=0,
        approved_files=0,
        synced_files=0,
        failed_files=0,
        pending_review_files=0,
        rejected_files=0,
        sensitive_files=0,
        total_file_size=0,
        last_upload_at=None,
        last_success_sync_at=None,
    )


def build_department_rows(files: Iterable[StatisticsFileRow]) -> list[StatisticsDepartmentRow]:
    grouped: dict[str, list[StatisticsFileRow]] = defaultdict(list)
    for file in files:
        grouped[file.department or file.user_department or "未设置"].append(file)
    rows = [
        StatisticsDepartmentRow(
            department=department,
            total_files=len(grouped_files),
            active_uploaders=len({file.uploader_id for file in grouped_files}),
            synced_files=sum(1 for file in grouped_files if is_synced(file)),
            failed_files=sum(1 for file in grouped_files if is_failed_file(file)),
            pending_review_files=sum(1 for file in grouped_files if is_pending_review(file)),
            total_file_size=sum(file.size for file in grouped_files),
        )
        for department, grouped_files in grouped.items()
    ]
    return sorted(rows, key=lambda row: (-row.total_files, row.department))


def build_category_rows(files: Iterable[StatisticsFileRow]) -> list[StatisticsCategoryRow]:
    grouped: dict[tuple[uuid.UUID | None, str], list[StatisticsFileRow]] = defaultdict(list)
    for file in files:
        grouped[(file.category_id, file.category_name or "未分类")].append(file)
    rows = [
        StatisticsCategoryRow(
            category_id=category_id,
            category_name=category_name,
            total_files=len(grouped_files),
            synced_files=sum(1 for file in grouped_files if is_synced(file)),
            failed_files=sum(1 for file in grouped_files if is_failed_file(file)),
            pending_review_files=sum(1 for file in grouped_files if is_pending_review(file)),
            total_file_size=sum(file.size for file in grouped_files),
        )
        for (category_id, category_name), grouped_files in grouped.items()
    ]
    return sorted(rows, key=lambda row: (-row.total_files, row.category_name))


def build_trend_points(
    files: Iterable[StatisticsFileRow],
    query: StatisticsQuery,
) -> list[StatisticsTrendPoint]:
    grouped: dict[str, list[StatisticsFileRow]] = defaultdict(list)
    for file in files:
        grouped[period_key(file.uploaded_at.date(), query.group_by)].append(file)
    periods = sorted(grouped.keys())
    if query.group_by == "day" and query.start_date is not None and query.end_date is not None:
        periods = [
            (query.start_date + timedelta(days=offset)).isoformat()
            for offset in range((query.end_date - query.start_date).days + 1)
        ]
    return [
        StatisticsTrendPoint(
            period=period,
            total_files=len(grouped[period]),
            synced_files=sum(1 for file in grouped[period] if is_synced(file)),
            failed_files=sum(1 for file in grouped[period] if is_failed_file(file)),
            pending_review_files=sum(1 for file in grouped[period] if is_pending_review(file)),
        )
        for period in periods
    ]


def period_key(value: date, group_by: str) -> str:
    if group_by == "month":
        return value.strftime("%Y-%m")
    if group_by == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    return value.isoformat()


def build_failure_rows(tasks: Iterable[StatisticsFailedTaskRow]) -> list[StatisticsFailureRow]:
    grouped: dict[str, set[uuid.UUID]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for task in tasks:
        grouped[task.reason].add(task.file_id)
        counts[task.reason] += 1
    rows = [
        StatisticsFailureRow(
            reason=reason,
            failed_tasks=counts[reason],
            failed_files=len(file_ids),
        )
        for reason, file_ids in grouped.items()
    ]
    return sorted(rows, key=lambda row: (-row.failed_tasks, row.reason))


def is_synced(file: StatisticsFileRow) -> bool:
    return file.status == "parsed"


def is_failed_file(file: StatisticsFileRow) -> bool:
    return file.status == "failed" or file.ragflow_parse_status in {"FAIL", "FAILED", "ERROR"}


def is_pending_review(file: StatisticsFileRow) -> bool:
    return file.status == "pending_review" or file.review_status == "pending"


def is_rejected(file: StatisticsFileRow) -> bool:
    return file.status == "rejected" or file.review_status == "rejected"


def is_sensitive(file: StatisticsFileRow) -> bool:
    return file.sensitive_risk_level in RISK_SENSITIVE


def sync_success_rate(files: Iterable[StatisticsFileRow]) -> float:
    rows = list(files)
    synced_files = sum(1 for file in rows if is_synced(file))
    failed_files = sum(1 for file in rows if is_failed_file(file))
    denominator = synced_files + failed_files
    if denominator == 0:
        return 0.0
    return round(synced_files / denominator, 4)
