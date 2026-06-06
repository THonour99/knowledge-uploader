from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class StatisticsOverviewResponse(BaseModel):
    total_files: int
    active_uploaders: int
    synced_files: int
    pending_review_files: int
    failed_files: int
    failed_tasks: int
    rejected_files: int
    sensitive_files: int
    total_file_size: int
    sync_success_rate: float


class StatisticsUserRow(BaseModel):
    rank: int
    user_id: uuid.UUID
    user_name: str
    department: str | None
    total_files: int
    approved_files: int
    synced_files: int
    failed_files: int
    pending_review_files: int
    rejected_files: int
    sensitive_files: int
    total_file_size: int
    last_upload_at: datetime | None
    last_success_sync_at: datetime | None


class StatisticsUserListResponse(BaseModel):
    items: list[StatisticsUserRow]
    total: int
    page: int
    page_size: int


class StatisticsDepartmentRow(BaseModel):
    department: str
    total_files: int
    active_uploaders: int
    synced_files: int
    failed_files: int
    pending_review_files: int
    total_file_size: int


class StatisticsDepartmentListResponse(BaseModel):
    items: list[StatisticsDepartmentRow]
    total: int


class StatisticsCategoryRow(BaseModel):
    category_id: uuid.UUID | None
    category_name: str
    total_files: int
    synced_files: int
    failed_files: int
    pending_review_files: int
    total_file_size: int


class StatisticsCategoryListResponse(BaseModel):
    items: list[StatisticsCategoryRow]
    total: int


class StatisticsTrendPoint(BaseModel):
    period: str
    total_files: int
    synced_files: int
    failed_files: int
    pending_review_files: int


class StatisticsTrendResponse(BaseModel):
    group_by: str
    items: list[StatisticsTrendPoint]


class StatisticsFailureRow(BaseModel):
    reason: str
    failed_tasks: int
    failed_files: int


class StatisticsFailureListResponse(BaseModel):
    items: list[StatisticsFailureRow]
    total: int


class StatisticsUserDetailResponse(BaseModel):
    user: StatisticsUserRow
    category_breakdown: list[StatisticsCategoryRow] = Field(default_factory=list)
