from __future__ import annotations

RAGFLOW_OPERATIONS = frozenset(
    {
        "ping",
        "upload_document",
        "find_document_by_name",
        "update_document_metadata",
        "start_parse",
        "get_document_status",
        "delete_document",
    }
)

RAGFLOW_PERSISTED_RESULTS = frozenset({"started", "success", "failure"})
RAGFLOW_COMPLETED_RESULTS = frozenset({"success", "failure"})

RAGFLOW_FAILURE_CATEGORIES = frozenset(
    {
        "authentication",
        "authorization",
        "configuration",
        "conflict",
        "network",
        "not_found",
        "protocol",
        "rate_limited",
        "timeout",
        "unknown",
        "upstream_5xx",
    }
)
