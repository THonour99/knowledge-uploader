from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.modules.user.schemas import AuthUserRecord

from .exceptions import (
    AnnouncementConflictError,
    AnnouncementNotFoundError,
    AnnouncementValidationError,
)
from .models import Announcement
from .repository import AnnouncementRepository
from .schemas import (
    AnnouncementAdminListResponse,
    AnnouncementCreateRequest,
    AnnouncementDetail,
    AnnouncementDraftPayload,
    AnnouncementListResponse,
    AnnouncementPublicDetail,
    AnnouncementPublishRequest,
    AnnouncementReadResponse,
    AnnouncementStats,
    AnnouncementSummary,
    AnnouncementUpdateRequest,
    AnnouncementWithdrawRequest,
    derive_state,
)

LIST_AUDIT_TARGET_ID = uuid.UUID(int=0)


@dataclass(frozen=True, slots=True)
class RequestAuditContext:
    ip_address: str
    user_agent: str


class AnnouncementService:
    def __init__(self, *, session: AsyncSession, repository: AnnouncementRepository) -> None:
        self._session = session
        self._repository = repository

    async def list_public(
        self,
        *,
        current_user: AuthUserRecord,
        state: str,
        unread_only: bool,
        page: int,
        page_size: int,
    ) -> AnnouncementListResponse:
        now = datetime.now(UTC)
        items, total, unread_count = await self._repository.list_public(
            current_user=current_user,
            state=state,
            unread_only=unread_only,
            now=now,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return AnnouncementListResponse(
            items=[self._summary(item, now=now, is_read=is_read) for item, is_read in items],
            total=total,
            unread_count=unread_count,
            page=page,
            page_size=page_size,
        )

    async def get_public(
        self, *, announcement_id: uuid.UUID, current_user: AuthUserRecord
    ) -> AnnouncementPublicDetail:
        now = datetime.now(UTC)
        result = await self._repository.get_public(
            announcement_id=announcement_id, current_user=current_user, now=now
        )
        if result is None:
            raise AnnouncementNotFoundError
        item, is_read = result
        return AnnouncementPublicDetail(
            **self._summary(item, now=now, is_read=is_read).model_dump(),
            body_markdown=item.body_markdown,
        )

    async def mark_read(
        self, *, announcement_id: uuid.UUID, current_user: AuthUserRecord
    ) -> AnnouncementReadResponse:
        now = datetime.now(UTC)
        visible = await self._repository.get_public(
            announcement_id=announcement_id, current_user=current_user, now=now
        )
        if visible is None:
            raise AnnouncementNotFoundError
        read_at = await self._repository.mark_read(
            announcement_id=announcement_id, user_id=current_user.id, now=now
        )
        await self._session.commit()
        return AnnouncementReadResponse(announcement_id=announcement_id, read_at=read_at)

    async def list_admin(
        self,
        *,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
        state: str,
        search: str | None,
        page: int,
        page_size: int,
    ) -> AnnouncementAdminListResponse:
        now = datetime.now(UTC)
        items, total = await self._repository.list_admin(
            state=state,
            search=search,
            now=now,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.list",
            target_id=LIST_AUDIT_TARGET_ID,
            metadata={"state": state, "page": page, "page_size": page_size},
        )
        await self._session.commit()
        return AnnouncementAdminListResponse(
            items=[AnnouncementDetail.from_model(item, now=now) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_admin(
        self,
        *,
        announcement_id: uuid.UUID,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementDetail:
        item = await self._require_admin(announcement_id)
        await self._audit(
            actor=actor, audit=audit, action="announcement.view", target_id=announcement_id
        )
        await self._session.commit()
        return AnnouncementDetail.from_model(item, now=datetime.now(UTC))

    async def create(
        self,
        *,
        payload: AnnouncementCreateRequest,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementDetail:
        await self._validate_targets(payload)
        item = await self._repository.create(
            title=payload.title,
            body_markdown=payload.body_markdown,
            audience_type=payload.audience_type,
            department_ids=payload.department_ids,
            roles=list(payload.roles),
            visible_from=payload.visible_from,
            expires_at=payload.expires_at,
            is_pinned=payload.is_pinned,
            actor_id=actor.id,
        )
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.create",
            target_id=item.id,
            metadata=self._safe_metadata(item),
        )
        await self._session.commit()
        return AnnouncementDetail.from_model(item, now=datetime.now(UTC))

    async def update(
        self,
        *,
        announcement_id: uuid.UUID,
        payload: AnnouncementUpdateRequest,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementDetail:
        item = await self._require_admin(announcement_id, for_update=True)
        self._require_version(item, payload.row_version)
        self._require_draft(item)
        await self._validate_targets(payload)
        item.title = payload.title
        item.body_markdown = payload.body_markdown
        item.audience_type = payload.audience_type
        item.visible_from = payload.visible_from
        item.expires_at = payload.expires_at
        item.is_pinned = payload.is_pinned
        item.updated_by = actor.id
        item.row_version += 1
        self._repository.replace_targets(
            item, department_ids=payload.department_ids, roles=list(payload.roles)
        )
        await self._session.flush()
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.update",
            target_id=item.id,
            metadata=self._safe_metadata(item),
        )
        await self._session.commit()
        return AnnouncementDetail.from_model(item, now=datetime.now(UTC))

    async def publish(
        self,
        *,
        announcement_id: uuid.UUID,
        payload: AnnouncementPublishRequest,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementDetail:
        item = await self._require_admin(announcement_id, for_update=True)
        self._require_version(item, payload.row_version)
        self._require_draft(item)
        visible_from = payload.visible_from or item.visible_from or datetime.now(UTC)
        expires_at = payload.expires_at if payload.expires_at is not None else item.expires_at
        if expires_at is not None and expires_at <= visible_from:
            raise AnnouncementValidationError("expires_at must be later than visible_from")
        item.visible_from = visible_from
        item.expires_at = expires_at
        item.lifecycle_state = "released"
        item.published_by = actor.id
        item.published_at = datetime.now(UTC)
        item.updated_by = actor.id
        item.row_version += 1
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.publish",
            target_id=item.id,
            metadata=self._safe_metadata(item),
        )
        await self._session.commit()
        return AnnouncementDetail.from_model(item, now=datetime.now(UTC))

    async def withdraw(
        self,
        *,
        announcement_id: uuid.UUID,
        payload: AnnouncementWithdrawRequest,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementDetail:
        item = await self._require_admin(announcement_id, for_update=True)
        self._require_version(item, payload.row_version)
        if item.lifecycle_state != "released":
            raise AnnouncementConflictError("only released announcements can be withdrawn")
        item.lifecycle_state = "withdrawn"
        item.withdrawn_by = actor.id
        item.withdrawn_at = datetime.now(UTC)
        item.withdraw_reason = payload.reason.strip()
        item.updated_by = actor.id
        item.row_version += 1
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.withdraw",
            target_id=item.id,
            metadata=self._safe_metadata(item),
            reason=item.withdraw_reason,
        )
        await self._session.commit()
        return AnnouncementDetail.from_model(item, now=datetime.now(UTC))

    async def clone(
        self,
        *,
        announcement_id: uuid.UUID,
        row_version: int,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementDetail:
        source = await self._require_admin(announcement_id, for_update=True)
        self._require_version(source, row_version)
        source.updated_by = actor.id
        source.row_version += 1
        clone = await self._repository.create(
            title=f"{source.title} (副本)"[:200],
            body_markdown=source.body_markdown,
            audience_type=source.audience_type,
            department_ids=[target.department_id for target in source.departments],
            roles=[target.role for target in source.roles],
            visible_from=None,
            expires_at=None,
            is_pinned=source.is_pinned,
            actor_id=actor.id,
        )
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.clone",
            target_id=clone.id,
            metadata={"source_id": str(source.id), **self._safe_metadata(clone)},
        )
        await self._session.commit()
        return AnnouncementDetail.from_model(clone, now=datetime.now(UTC))

    async def delete(
        self,
        *,
        announcement_id: uuid.UUID,
        row_version: int,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> None:
        item = await self._require_admin(announcement_id, for_update=True)
        self._require_version(item, row_version)
        self._require_draft(item)
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.delete",
            target_id=item.id,
            metadata=self._safe_metadata(item),
        )
        await self._session.delete(item)
        await self._session.commit()

    async def stats(
        self,
        *,
        announcement_id: uuid.UUID,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
    ) -> AnnouncementStats:
        item = await self._require_admin(announcement_id)
        target_count, read_count = await self._repository.stats(item)
        await self._audit(
            actor=actor,
            audit=audit,
            action="announcement.stats.view",
            target_id=item.id,
            metadata={"target_user_count": target_count, "read_user_count": read_count},
        )
        await self._session.commit()
        return AnnouncementStats(
            announcement_id=item.id,
            target_user_count=target_count,
            read_user_count=read_count,
            unread_user_count=max(target_count - read_count, 0),
            read_rate=read_count / target_count if target_count else 0,
        )

    async def _require_admin(
        self, announcement_id: uuid.UUID, *, for_update: bool = False
    ) -> Announcement:
        item = await self._repository.get_admin(announcement_id, for_update=for_update)
        if item is None:
            raise AnnouncementNotFoundError
        return item

    async def _validate_targets(self, payload: AnnouncementDraftPayload) -> None:
        if (
            payload.audience_type == "departments"
            and not await self._repository.validate_departments(payload.department_ids)
        ):
            raise AnnouncementValidationError("one or more departments are invalid or disabled")

    @staticmethod
    def _require_version(item: Announcement, row_version: int) -> None:
        if item.row_version != row_version:
            raise AnnouncementConflictError("announcement was changed by another request")

    @staticmethod
    def _require_draft(item: Announcement) -> None:
        if item.lifecycle_state != "draft":
            raise AnnouncementConflictError("only draft announcements can be changed")

    @staticmethod
    def _summary(item: Announcement, *, now: datetime, is_read: bool) -> AnnouncementSummary:
        return AnnouncementSummary(
            id=item.id,
            title=item.title,
            state=derive_state(item, now),
            visible_from=item.visible_from,
            expires_at=item.expires_at,
            is_pinned=item.is_pinned,
            is_read=is_read,
        )

    @staticmethod
    def _safe_metadata(item: Announcement) -> dict[str, object]:
        return {
            "audience_type": item.audience_type,
            "department_count": len(item.departments),
            "role_count": len(item.roles),
            "is_pinned": item.is_pinned,
            "lifecycle_state": item.lifecycle_state,
            "row_version": item.row_version,
        }

    async def _audit(
        self,
        *,
        actor: AuthUserRecord,
        audit: RequestAuditContext,
        action: str,
        target_id: uuid.UUID,
        metadata: dict[str, object] | None = None,
        reason: str | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action=action,
            target_type="announcement",
            target_id=target_id,
            ip_address=audit.ip_address,
            user_agent=audit.user_agent,
            metadata_json=metadata,
            reason=reason,
        )
