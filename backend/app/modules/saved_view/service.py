from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_scope import get_department_scope_store
from app.core.audit import record_admin_audit_log
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .models import SavedView
from .repository import SavedViewAccess, SavedViewRepository
from .schemas import (
    ColumnPreferences,
    EffectiveDefinition,
    MyFilesQueryDefinition,
    ReviewFilesQueryDefinition,
    SavedViewCreateRequest,
    SavedViewItem,
    SavedViewListResponse,
    SavedViewUpdateRequest,
    StatisticsQueryDefinition,
    TaskLogsQueryDefinition,
)

CURRENT_DEFINITION_SCHEMA_VERSION: Final = 2
MAX_JSON_DEPTH: Final = 4
MAX_QUERY_BYTES: Final = 8192
MAX_COLUMN_BYTES: Final = 4096

PAGE_ROLES: Final[dict[str, frozenset[str]]] = {
    "my_files": frozenset({"employee", "dept_admin", "system_admin"}),
    "review_files": frozenset({"dept_admin", "system_admin"}),
    "task_logs": frozenset({"dept_admin", "system_admin"}),
    "statistics": frozenset({"system_admin"}),
}
PRIVATE_ONLY_PAGES: Final = frozenset({"my_files", "statistics"})
ADMIN_ROLES: Final = frozenset({"dept_admin", "system_admin"})

QUERY_MODELS: Final[dict[str, type[BaseModel]]] = {
    "my_files": MyFilesQueryDefinition,
    "review_files": ReviewFilesQueryDefinition,
    "task_logs": TaskLogsQueryDefinition,
    "statistics": StatisticsQueryDefinition,
}
PAGE_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "my_files": frozenset({"original_name", "status", "updated_at", "actions"}),
    "review_files": frozenset(
        {
            "original_name",
            "uploader_id",
            "department",
            "category_id",
            "size",
            "review_status",
            "risk",
            "review_due_at",
            "claimed_by",
            "actions",
        }
    ),
    "task_logs": frozenset(
        {
            "task_type",
            "file_id",
            "status",
            "retry_count",
            "started_at",
            "finished_at",
            "actions",
        }
    ),
    "statistics": frozenset(
        {
            "rank",
            "user_name",
            "department",
            "total_files",
            "synced_files",
            "failed_files",
            "pending_review_files",
            "total_file_size",
            "last_upload_at",
        }
    ),
}

_FORBIDDEN_KEY_TOKENS: Final = frozenset(
    {
        "items",
        "results",
        "rows",
        "fileids",
        "total",
        "url",
        "uri",
        "href",
        "path",
        "token",
        "accesstoken",
        "refreshtoken",
        "page",
        "deeplink",
        "permissions",
        "permissionscope",
        "authorizeddepartmentids",
        "manageddepartmentids",
        "resultcount",
    }
)
_URL_PREFIXES: Final = ("http://", "https://", "//", "javascript:", "data:")


@dataclass(frozen=True, slots=True)
class RequestContext:
    ip_address: str
    user_agent: str


@dataclass(frozen=True, slots=True)
class NormalizedDefinition:
    query_definition: dict[str, object]
    column_preferences: dict[str, object]


@dataclass(frozen=True, slots=True)
class DefinitionResolution:
    compatibility: str
    effective_schema_version: int | None
    definition: NormalizedDefinition | None


class SavedViewService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: SavedViewRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def list_saved_views(
        self,
        *,
        current_user: AuthUserRecord,
        page_key: str,
        scope: str | None,
        page: int,
        page_size: int,
    ) -> SavedViewListResponse:
        access = await self._build_access(current_user)
        self._require_page_access(access=access, page_key=page_key)
        self._validate_requested_scope(access=access, page_key=page_key, scope=scope)
        views = await self._repository.list_visible(
            access=access,
            page_key=page_key,
            scope=scope,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await self._repository.count_visible(
            access=access,
            page_key=page_key,
            scope=scope,
        )
        items = [await self._to_item(view=view, access=access) for view in views]
        return SavedViewListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size,
        )

    async def get_saved_view(
        self,
        *,
        current_user: AuthUserRecord,
        saved_view_id: uuid.UUID,
    ) -> SavedViewItem:
        access = await self._build_access(current_user)
        view = await self._repository.get_visible(access=access, saved_view_id=saved_view_id)
        if view is None:
            raise exceptions.not_found()
        self._require_page_access(access=access, page_key=view.page_key, hide=True)
        return await self._to_item(view=view, access=access)

    async def create_saved_view(
        self,
        *,
        current_user: AuthUserRecord,
        request: SavedViewCreateRequest,
        context: RequestContext,
    ) -> SavedViewItem:
        access = await self._build_access(current_user)
        self._require_page_access(access=access, page_key=request.page_key)
        self._validate_requested_scope(
            access=access,
            page_key=request.page_key,
            scope=request.scope,
        )
        if request.definition_schema_version != CURRENT_DEFINITION_SCHEMA_VERSION:
            raise exceptions.invalid_definition(
                f"definition_schema_version must be {CURRENT_DEFINITION_SCHEMA_VERSION}"
            )
        department_id = await self._validate_target_department(
            access=access,
            scope=request.scope,
            department_id=request.department_id,
        )
        definition = await self._normalize_definition(
            page_key=request.page_key,
            scope=request.scope,
            department_id=department_id,
            access=access,
            query_definition=request.query_definition,
            column_preferences=request.column_preferences,
        )
        try:
            view = await self._repository.create(
                owner_id=access.actor_id,
                scope=request.scope,
                department_id=department_id,
                page_key=request.page_key,
                name=_clean_name(request.name),
                definition_schema_version=CURRENT_DEFINITION_SCHEMA_VERSION,
                query_definition=definition.query_definition,
                column_preferences=definition.column_preferences,
            )
            await self._record_mutation_audit(
                current_user=current_user,
                action="saved_view.created",
                view=view,
                context=context,
                definition=definition,
                previous_row_version=None,
            )
            await self._session.commit()
            await self._session.refresh(view)
        except IntegrityError as error:
            await self._session.rollback()
            raise exceptions.conflict("saved view name already exists in this scope") from error
        return await self._to_item(view=view, access=access)

    async def update_saved_view(
        self,
        *,
        current_user: AuthUserRecord,
        saved_view_id: uuid.UUID,
        request: SavedViewUpdateRequest,
        context: RequestContext,
    ) -> SavedViewItem:
        access = await self._build_access(current_user)
        view = await self._repository.get_visible(access=access, saved_view_id=saved_view_id)
        if view is None or not self._may_mutate(access=access, view=view):
            raise exceptions.not_found()
        self._require_page_access(access=access, page_key=view.page_key, hide=True)
        if view.definition_schema_version > CURRENT_DEFINITION_SCHEMA_VERSION:
            raise exceptions.unsupported_schema()
        if request.definition_schema_version not in {None, CURRENT_DEFINITION_SCHEMA_VERSION}:
            raise exceptions.invalid_definition(
                f"definition_schema_version must be {CURRENT_DEFINITION_SCHEMA_VERSION}"
            )

        current_resolution = await self._resolve_definition(view=view, access=access)
        if current_resolution.definition is None:
            raise exceptions.unsupported_schema()
        query_definition = (
            request.query_definition
            if request.query_definition is not None
            else current_resolution.definition.query_definition
        )
        column_preferences = (
            request.column_preferences
            if request.column_preferences is not None
            else current_resolution.definition.column_preferences
        )
        definition = await self._normalize_definition(
            page_key=view.page_key,
            scope=view.scope,
            department_id=view.department_id,
            access=access,
            query_definition=query_definition,
            column_preferences=column_preferences,
        )
        values: dict[str, object] = {
            "definition_schema_version": CURRENT_DEFINITION_SCHEMA_VERSION,
            "query_definition": definition.query_definition,
            "column_preferences": definition.column_preferences,
        }
        if request.name is not None:
            values["name"] = _clean_name(request.name)
        try:
            updated = await self._repository.update_if_version(
                saved_view_id=saved_view_id,
                expected_row_version=request.row_version,
                values=values,
            )
            if updated is None:
                await self._session.rollback()
                raise exceptions.conflict("saved view was modified by another request")
            await self._record_mutation_audit(
                current_user=current_user,
                action="saved_view.updated",
                view=updated,
                context=context,
                definition=definition,
                previous_row_version=request.row_version,
            )
            await self._session.commit()
        except IntegrityError as error:
            await self._session.rollback()
            raise exceptions.conflict("saved view name already exists in this scope") from error
        return await self._to_item(view=updated, access=access)

    async def delete_saved_view(
        self,
        *,
        current_user: AuthUserRecord,
        saved_view_id: uuid.UUID,
        context: RequestContext,
    ) -> None:
        access = await self._build_access(current_user)
        view = await self._repository.get_visible(access=access, saved_view_id=saved_view_id)
        if view is None or not self._may_mutate(access=access, view=view):
            raise exceptions.not_found()
        self._require_page_access(access=access, page_key=view.page_key, hide=True)
        resolution = await self._resolve_definition(view=view, access=access)
        definition = resolution.definition or NormalizedDefinition({}, {})
        deleted = await self._repository.delete_by_id(saved_view_id=saved_view_id)
        if not deleted:
            await self._session.rollback()
            raise exceptions.not_found()
        await self._record_mutation_audit(
            current_user=current_user,
            action="saved_view.deleted",
            view=view,
            context=context,
            definition=definition,
            previous_row_version=view.row_version,
        )
        await self._session.commit()

    async def _build_access(self, current_user: AuthUserRecord) -> SavedViewAccess:
        managed_department_ids: frozenset[uuid.UUID] = frozenset()
        if current_user.role == "dept_admin":
            managed_department_ids = await get_department_scope_store(
                self._session
            ).list_managed_department_ids(current_user.id)
        return SavedViewAccess(
            actor_id=current_user.id,
            actor_role=current_user.role,
            managed_department_ids=managed_department_ids,
        )

    @staticmethod
    def _require_page_access(
        *,
        access: SavedViewAccess,
        page_key: str,
        hide: bool = False,
    ) -> None:
        if page_key not in PAGE_ROLES or access.actor_role not in PAGE_ROLES[page_key]:
            if hide:
                raise exceptions.not_found()
            raise exceptions.invalid_scope("page is not available to the current role")

    @staticmethod
    def _validate_requested_scope(
        *,
        access: SavedViewAccess,
        page_key: str,
        scope: str | None,
    ) -> None:
        if scope is None:
            return
        if scope == "department":
            if page_key in PRIVATE_ONLY_PAGES or access.actor_role not in ADMIN_ROLES:
                raise exceptions.invalid_scope()
        elif scope != "private":
            raise exceptions.invalid_scope()

    async def _validate_target_department(
        self,
        *,
        access: SavedViewAccess,
        scope: str,
        department_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        if scope == "private":
            if department_id is not None:
                raise exceptions.invalid_scope("private saved views cannot target a department")
            return None
        if department_id is None:
            raise exceptions.invalid_scope("department_id is required for department views")
        if access.actor_role == "dept_admin" and department_id not in access.managed_department_ids:
            raise exceptions.invalid_scope()
        if access.actor_role not in ADMIN_ROLES:
            raise exceptions.invalid_scope()
        if not await self._repository.active_department_exists(department_id):
            raise exceptions.invalid_scope("department is not active")
        return department_id

    async def _normalize_definition(
        self,
        *,
        page_key: str,
        scope: str,
        department_id: uuid.UUID | None,
        access: SavedViewAccess,
        query_definition: dict[str, object],
        column_preferences: dict[str, object],
    ) -> NormalizedDefinition:
        _validate_json_payload(query_definition, maximum_bytes=MAX_QUERY_BYTES)
        _validate_json_payload(column_preferences, maximum_bytes=MAX_COLUMN_BYTES)
        raw_query = dict(query_definition)
        if scope == "department":
            assert department_id is not None
            stored_department = raw_query.get("department_id")
            if stored_department is not None and str(stored_department) != str(department_id):
                raise exceptions.invalid_definition(
                    "department filter must match the saved view department"
                )
            raw_query["department_id"] = str(department_id)
        try:
            query_model = QUERY_MODELS[page_key].model_validate(raw_query)
            columns_model = ColumnPreferences.model_validate(column_preferences)
        except (KeyError, ValidationError) as error:
            raise exceptions.invalid_definition(
                "saved view contains unsupported fields or values"
            ) from error

        normalized_query = query_model.model_dump(mode="json", exclude_none=True)
        normalized_columns = columns_model.model_dump(mode="json")
        allowed_columns = PAGE_COLUMNS[page_key]
        used_columns = {
            *normalized_columns["visible"],
            *normalized_columns["order"],
            *normalized_columns["widths"].keys(),
        }
        if not used_columns <= allowed_columns:
            raise exceptions.invalid_definition("column preferences contain unsupported columns")

        filter_department = normalized_query.get("department_id")
        if filter_department is not None:
            filter_department_id = uuid.UUID(str(filter_department))
            if access.actor_role == "dept_admin":
                if filter_department_id not in access.managed_department_ids:
                    raise exceptions.invalid_definition(
                        "department filter is outside the current management scope"
                    )
            elif access.actor_role == "system_admin":
                if not await self._repository.active_department_exists(filter_department_id):
                    raise exceptions.invalid_definition("department filter is not active")
            else:
                raise exceptions.invalid_definition("department filter is not available")
        _validate_json_payload(normalized_query, maximum_bytes=MAX_QUERY_BYTES)
        _validate_json_payload(normalized_columns, maximum_bytes=MAX_COLUMN_BYTES)
        return NormalizedDefinition(
            query_definition=normalized_query,
            column_preferences=normalized_columns,
        )

    async def _resolve_definition(
        self,
        *,
        view: SavedView,
        access: SavedViewAccess,
    ) -> DefinitionResolution:
        if view.definition_schema_version > CURRENT_DEFINITION_SCHEMA_VERSION:
            return DefinitionResolution("unsupported", None, None)
        query_definition = dict(view.query_definition)
        column_preferences = dict(view.column_preferences)
        compatibility = "current"
        if view.definition_schema_version == 1:
            compatibility = "migrated"
            query_definition, column_preferences = _migrate_v1_to_v2(
                query_definition=query_definition,
                column_preferences=column_preferences,
            )
        elif view.definition_schema_version != CURRENT_DEFINITION_SCHEMA_VERSION:
            return DefinitionResolution("unsupported", None, None)
        try:
            definition = await self._normalize_definition(
                page_key=view.page_key,
                scope=view.scope,
                department_id=view.department_id,
                access=access,
                query_definition=query_definition,
                column_preferences=column_preferences,
            )
        except (SavedViewErrorAlias, ValueError, TypeError):
            return DefinitionResolution("unsupported", None, None)
        return DefinitionResolution(
            compatibility=compatibility,
            effective_schema_version=CURRENT_DEFINITION_SCHEMA_VERSION,
            definition=definition,
        )

    async def _to_item(self, *, view: SavedView, access: SavedViewAccess) -> SavedViewItem:
        resolution = await self._resolve_definition(view=view, access=access)
        effective_definition = None
        if resolution.definition is not None:
            effective_definition = EffectiveDefinition(
                query_definition=resolution.definition.query_definition,
                column_preferences=resolution.definition.column_preferences,
            )
        return SavedViewItem(
            id=view.id,
            owner_id=view.owner_id,
            scope=view.scope,
            department_id=view.department_id,
            page_key=view.page_key,
            name=view.name,
            stored_schema_version=view.definition_schema_version,
            effective_schema_version=resolution.effective_schema_version,
            compatibility=resolution.compatibility,
            effective_definition=effective_definition,
            row_version=view.row_version,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )

    @staticmethod
    def _may_mutate(*, access: SavedViewAccess, view: SavedView) -> bool:
        return view.owner_id == access.actor_id or access.actor_role == "system_admin"

    async def _record_mutation_audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        view: SavedView,
        context: RequestContext,
        definition: NormalizedDefinition,
        previous_row_version: int | None,
    ) -> None:
        if current_user.role not in ADMIN_ROLES:
            return
        canonical = _canonical_json(
            {
                "query_definition": definition.query_definition,
                "column_preferences": definition.column_preferences,
            }
        )
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=action,
            target_type="saved_view",
            target_id=view.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "page_key": view.page_key,
                "scope": view.scope,
                "department_id": str(view.department_id) if view.department_id else None,
                "definition_schema_version": view.definition_schema_version,
                "previous_row_version": previous_row_version,
                "row_version": view.row_version,
                "definition_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            },
        )


SavedViewErrorAlias = exceptions.SavedViewError


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 80:
        raise exceptions.invalid_definition("saved view name must contain at most 80 characters")
    if any(unicodedata.category(character).startswith("C") for character in cleaned):
        raise exceptions.invalid_definition("saved view name contains control characters")
    return cleaned


def _migrate_v1_to_v2(
    *,
    query_definition: dict[str, object],
    column_preferences: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    migrated_query = dict(query_definition)
    if "search" in migrated_query:
        if "q" in migrated_query:
            return {}, {"__migration_conflict__": True}
        migrated_query["q"] = migrated_query.pop("search")
    migrated_columns = dict(column_preferences)
    if "columns" in migrated_columns:
        if "visible" in migrated_columns:
            return {}, {"__migration_conflict__": True}
        migrated_columns["visible"] = migrated_columns.pop("columns")
    return migrated_query, migrated_columns


def _validate_json_payload(value: object, *, maximum_bytes: int) -> None:
    try:
        canonical = _canonical_json(value)
        encoded = canonical.encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise exceptions.invalid_definition("saved view definition must be valid JSON") from error
    if len(encoded) > maximum_bytes:
        raise exceptions.invalid_definition("saved view definition exceeds the size limit")
    _walk_json(value=value, depth=1)


def _walk_json(*, value: object, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        raise exceptions.invalid_definition("saved view definition exceeds the depth limit")
    if isinstance(value, dict):
        for key, child in value.items():
            token = re.sub(r"[^a-z0-9]", "", key.casefold())
            if token in _FORBIDDEN_KEY_TOKENS:
                raise exceptions.invalid_definition("saved view contains a forbidden field")
            _walk_json(value=child, depth=depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _walk_json(value=child, depth=depth + 1)
        return
    if isinstance(value, str) and value.strip().casefold().startswith(_URL_PREFIXES):
        raise exceptions.invalid_definition("saved view cannot contain URLs or deep links")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
