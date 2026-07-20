from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.modules.announcement.models import Announcement
from app.modules.announcement.schemas import (
    AnnouncementCreateRequest,
    AnnouncementPublicDetail,
    derive_state,
)
from app.modules.announcement.service import AnnouncementService


def _announcement(
    *, lifecycle: str, visible_from: datetime | None, expires_at: datetime | None
) -> Announcement:
    actor_id = uuid.uuid4()
    return Announcement(
        id=uuid.uuid4(),
        title="维护公告",
        body_markdown="# 内容",
        audience_type="all",
        lifecycle_state=lifecycle,
        visible_from=visible_from,
        expires_at=expires_at,
        is_pinned=False,
        created_by=actor_id,
        updated_by=actor_id,
        row_version=1,
        departments=[],
        roles=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_derived_lifecycle_honors_time_boundaries() -> None:
    now = datetime.now(UTC)
    assert (
        derive_state(_announcement(lifecycle="draft", visible_from=None, expires_at=None), now)
        == "draft"
    )
    assert (
        derive_state(_announcement(lifecycle="withdrawn", visible_from=now, expires_at=None), now)
        == "withdrawn"
    )
    assert (
        derive_state(
            _announcement(
                lifecycle="released", visible_from=now + timedelta(seconds=1), expires_at=None
            ),
            now,
        )
        == "scheduled"
    )
    assert (
        derive_state(_announcement(lifecycle="released", visible_from=now, expires_at=now), now)
        == "expired"
    )
    assert (
        derive_state(_announcement(lifecycle="released", visible_from=now, expires_at=None), now)
        == "published"
    )


def test_payload_rejects_invalid_targets_and_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        AnnouncementCreateRequest(
            title="公告",
            body_markdown="正文",
            audience_type="departments",
            department_ids=[],
        )
    with pytest.raises(ValidationError):
        AnnouncementCreateRequest(
            title="公告",
            body_markdown="正文",
            audience_type="all",
            roles=["employee"],
        )
    with pytest.raises(ValidationError):
        AnnouncementCreateRequest(
            title="公告",
            body_markdown="正文",
            visible_from=datetime.now(),
        )


def test_audit_metadata_never_contains_title_or_markdown_body() -> None:
    item = _announcement(lifecycle="released", visible_from=datetime.now(UTC), expires_at=None)
    metadata = AnnouncementService._safe_metadata(item)
    assert "title" not in metadata
    assert "body_markdown" not in metadata
    assert "# 内容" not in str(metadata)


def test_public_detail_excludes_admin_only_fields() -> None:
    item = _announcement(lifecycle="released", visible_from=datetime.now(UTC), expires_at=None)
    detail = AnnouncementPublicDetail(
        id=item.id,
        title=item.title,
        body_markdown=item.body_markdown,
        state="published",
        visible_from=item.visible_from,
        expires_at=item.expires_at,
        is_pinned=item.is_pinned,
        is_read=False,
    )

    assert set(detail.model_dump()) == {
        "id",
        "title",
        "body_markdown",
        "state",
        "visible_from",
        "expires_at",
        "is_pinned",
        "is_read",
    }
