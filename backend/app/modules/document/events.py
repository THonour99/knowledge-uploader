from __future__ import annotations

from typing import ClassVar

from app.core.events import DomainEvent

DOCUMENT_FILE_UPLOADED = "document.file.uploaded"
DOCUMENT_FILE_DELETED = "document.file.deleted"
DOCUMENT_FILE_ARCHIVED = "document.file.archived"
DOCUMENT_FILE_REANALYZE_REQUESTED = "document.file.reanalyze_requested"


class DocumentFileUploaded(DomainEvent):
    ROUTING_KEY: ClassVar[str] = DOCUMENT_FILE_UPLOADED


class DocumentFileDeleted(DomainEvent):
    ROUTING_KEY: ClassVar[str] = DOCUMENT_FILE_DELETED


class DocumentFileArchived(DomainEvent):
    ROUTING_KEY: ClassVar[str] = DOCUMENT_FILE_ARCHIVED


class DocumentFileReanalyzeRequested(DomainEvent):
    ROUTING_KEY: ClassVar[str] = DOCUMENT_FILE_REANALYZE_REQUESTED
