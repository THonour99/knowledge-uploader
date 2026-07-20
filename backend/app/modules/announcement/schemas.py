from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Announcement

AudienceType = Literal["all", "departments", "roles"]
AnnouncementState = Literal["draft", "scheduled", "published", "expired", "withdrawn"]
PublicStateFilter = Literal["active", "expired", "all"]
LifecycleFilter = Literal["draft", "scheduled", "published", "expired", "withdrawn", "all"]
TargetRole = Literal["employee", "dept_admin", "system_admin"]


class AnnouncementDraftPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=50_000)
    audience_type: AudienceType = "all"
    department_ids: list[uuid.UUID] = Field(default_factory=list)
    roles: list[TargetRole] = Field(default_factory=list)
    visible_from: datetime | None = None
    expires_at: datetime | None = None
    is_pinned: bool = False

    @model_validator(mode="after")
    def validate_targets_and_time(self) -> Self:
        self.title = self.title.strip()
        self.body_markdown = self.body_markdown.strip()
        if not self.title or not self.body_markdown:
            raise ValueError("title and body_markdown must not be blank")
        self.department_ids = list(dict.fromkeys(self.department_ids))
        self.roles = list(dict.fromkeys(self.roles))
        if self.audience_type == "all" and (self.department_ids or self.roles):
            raise ValueError("all audience must not include targets")
        if self.audience_type == "departments" and (not self.department_ids or self.roles):
            raise ValueError("department audience requires department_ids only")
        if self.audience_type == "roles" and (not self.roles or self.department_ids):
            raise ValueError("role audience requires roles only")
        _validate_time(self.visible_from, "visible_from")
        _validate_time(self.expires_at, "expires_at")
        if self.visible_from and self.expires_at and self.expires_at <= self.visible_from:
            raise ValueError("expires_at must be later than visible_from")
        return self


class AnnouncementCreateRequest(AnnouncementDraftPayload):
    pass


class AnnouncementUpdateRequest(AnnouncementDraftPayload):
    row_version: int = Field(ge=1)


class AnnouncementVersionRequest(BaseModel):
    row_version: int = Field(ge=1)


class AnnouncementPublishRequest(AnnouncementVersionRequest):
    visible_from: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_time_window(self) -> Self:
        _validate_time(self.visible_from, "visible_from")
        _validate_time(self.expires_at, "expires_at")
        if self.visible_from and self.expires_at and self.expires_at <= self.visible_from:
            raise ValueError("expires_at must be later than visible_from")
        return self


class AnnouncementWithdrawRequest(AnnouncementVersionRequest):
    reason: str = Field(min_length=1, max_length=500)


class AnnouncementSummary(BaseModel):
    id: uuid.UUID
    title: str
    state: AnnouncementState
    visible_from: datetime | None
    expires_at: datetime | None
    is_pinned: bool
    is_read: bool = False


class AnnouncementPublicDetail(AnnouncementSummary):
    body_markdown: str


class AnnouncementDetail(AnnouncementPublicDetail):
    audience_type: AudienceType
    row_version: int
    created_at: datetime
    updated_at: datetime
    department_ids: list[uuid.UUID]
    roles: list[TargetRole]
    lifecycle_state: Literal["draft", "released", "withdrawn"]
    published_at: datetime | None
    withdrawn_at: datetime | None
    withdraw_reason: str | None

    @classmethod
    def from_model(
        cls, announcement: Announcement, *, now: datetime, is_read: bool = False
    ) -> AnnouncementDetail:
        return cls(
            id=announcement.id,
            title=announcement.title,
            body_markdown=announcement.body_markdown,
            audience_type=announcement.audience_type,
            department_ids=[target.department_id for target in announcement.departments],
            roles=[target.role for target in announcement.roles],
            lifecycle_state=announcement.lifecycle_state,
            state=derive_state(announcement, now),
            visible_from=announcement.visible_from,
            expires_at=announcement.expires_at,
            is_pinned=announcement.is_pinned,
            is_read=is_read,
            row_version=announcement.row_version,
            created_at=announcement.created_at,
            updated_at=announcement.updated_at,
            published_at=announcement.published_at,
            withdrawn_at=announcement.withdrawn_at,
            withdraw_reason=announcement.withdraw_reason,
        )


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementSummary]
    total: int
    unread_count: int
    page: int
    page_size: int


class AnnouncementAdminListResponse(BaseModel):
    items: list[AnnouncementDetail]
    total: int
    page: int
    page_size: int


class AnnouncementReadResponse(BaseModel):
    announcement_id: uuid.UUID
    read_at: datetime


class AnnouncementStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    announcement_id: uuid.UUID
    target_user_count: int
    read_user_count: int
    unread_user_count: int
    read_rate: float = Field(ge=0, le=1)


def derive_state(announcement: Announcement, now: datetime) -> AnnouncementState:
    if announcement.lifecycle_state == "draft":
        return "draft"
    if announcement.lifecycle_state == "withdrawn":
        return "withdrawn"
    if announcement.visible_from is not None and announcement.visible_from > now:
        return "scheduled"
    if announcement.expires_at is not None and announcement.expires_at <= now:
        return "expired"
    return "published"


def _validate_time(value: datetime | None, field: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
