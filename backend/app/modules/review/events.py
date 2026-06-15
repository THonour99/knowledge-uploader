from __future__ import annotations

from typing import ClassVar

from app.core.events import DomainEvent

REVIEW_FILE_SUBMITTED = "review.file.submitted"
REVIEW_FILE_APPROVED = "review.file.approved"
REVIEW_FILE_REJECTED = "review.file.rejected"


class ReviewFileSubmitted(DomainEvent):
    ROUTING_KEY: ClassVar[str] = REVIEW_FILE_SUBMITTED


class ReviewFileApproved(DomainEvent):
    ROUTING_KEY: ClassVar[str] = REVIEW_FILE_APPROVED


class ReviewFileRejected(DomainEvent):
    ROUTING_KEY: ClassVar[str] = REVIEW_FILE_REJECTED
