from __future__ import annotations

import uuid
from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import runtime_config
from app.core.audit import record_admin_audit_log
from app.core.config import get_settings
from app.core.outbox import OutboxRepository
from app.core.security import decrypt_secret, encrypt_secret
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .defaults import CONFIG_GROUPS, DEFINITIONS_BY_KEY, ConfigDefinition, definitions_for_group
from .models import SystemConfig
from .permissions import ADMIN_ROLES, SYSTEM_ADMIN_ROLE
from .repository import ConfigRepository  # noqa: TID251 - same-module repository dependency
from .schemas import ConfigGroupResponse, ConfigItemResponse


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


class ConfigService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ConfigRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def get_group(
        self,
        *,
        group: str,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> ConfigGroupResponse:
        self._require_admin(current_user)
        self._require_known_group(group)
        response = await self._group_response(group)
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="config.view",
            target_type="system_config_group",
            target_id=_config_group_target_id(group),
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={"group": group, "result_count": response.total},
        )
        await self._session.commit()
        return response

    async def update_group(
        self,
        *,
        group: str,
        items: dict[str, object],
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> ConfigGroupResponse:
        self._require_system_admin(current_user)
        self._require_known_group(group)
        if not items:
            raise exceptions.empty_update()

        validated: dict[str, object] = {}
        for key in sorted(items):
            definition = DEFINITIONS_BY_KEY.get(key)
            if definition is None or definition.group != group:
                raise exceptions.unknown_config_key(key)
            validated[key] = self._validate_value(definition, items[key])

        existing = await self._repository.get_by_keys(list(validated))
        changes: dict[str, object] = {}
        for key, value in validated.items():
            definition = DEFINITIONS_BY_KEY[key]
            stored: object | None
            if definition.value_type == "secret":
                stored = self._encrypt_secret_value(str(value))
            else:
                stored = value
                row = existing.get(key)
                old_value = row.value if row is not None else definition.default
                changes[key] = {"old": old_value, "new": value}
            await self._repository.upsert_value(
                key=definition.key,
                group=definition.group,
                value_type=definition.value_type,
                is_secret=definition.is_secret,
                description=definition.description,
                value=stored,
                updated_by=current_user.id,
            )

        updated_keys = sorted(validated)
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="config.update",
            target_type="system_config_group",
            target_id=_config_group_target_id(group),
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={"group": group, "keys": updated_keys, "changes": changes},
        )
        await OutboxRepository(self._session).append(
            event_type=events.CONFIG_SETTINGS_UPDATED,
            aggregate_type="config",
            aggregate_id=group,
            payload={"group": group, "keys": updated_keys},
        )
        await self._session.commit()
        for key in updated_keys:
            runtime_config.invalidate(key)
        return await self._group_response(group)

    async def _group_response(self, group: str) -> ConfigGroupResponse:
        rows = {row.key: row for row in await self._repository.list_by_group(group)}
        items = [
            self._item_response(definition, rows.get(definition.key))
            for definition in definitions_for_group(group)
        ]
        return ConfigGroupResponse(group=group, items=items, total=len(items))

    def _item_response(
        self,
        definition: ConfigDefinition,
        row: SystemConfig | None,
    ) -> ConfigItemResponse:
        updated_at = row.updated_at if row is not None else None
        if definition.value_type == "secret":
            return ConfigItemResponse(
                key=definition.key,
                value=None,
                value_type=definition.value_type,
                is_secret=True,
                masked_value=self._masked_secret(row),
                description=definition.description,
                updated_at=updated_at,
            )
        return ConfigItemResponse(
            key=definition.key,
            value=row.value if row is not None else definition.default,
            value_type=definition.value_type,
            is_secret=False,
            masked_value=None,
            description=definition.description,
            updated_at=updated_at,
        )

    def _masked_secret(self, row: SystemConfig | None) -> str | None:
        if row is None or not isinstance(row.value, str) or not row.value:
            return None
        try:
            plaintext = decrypt_secret(row.value, get_settings().encryption_key)
        except InvalidToken:
            return None
        return mask_secret(plaintext)

    def _encrypt_secret_value(self, value: str) -> str | None:
        cleaned = value.strip()
        if not cleaned:
            return None
        return encrypt_secret(cleaned, get_settings().encryption_key)

    def _validate_value(self, definition: ConfigDefinition, value: object) -> object:
        value_type = definition.value_type
        if value_type == "int":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise exceptions.invalid_config_value(definition.key)
            if definition.min_value is not None and value < definition.min_value:
                raise exceptions.invalid_config_value(definition.key)
            if definition.max_value is not None and value > definition.max_value:
                raise exceptions.invalid_config_value(definition.key)
            return value
        if value_type == "bool":
            if not isinstance(value, bool):
                raise exceptions.invalid_config_value(definition.key)
            return value
        if value_type in {"string", "secret"}:
            if not isinstance(value, str):
                raise exceptions.invalid_config_value(definition.key)
            return value.strip()
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise exceptions.invalid_config_value(definition.key)
        return [item.strip() for item in value if item.strip()]

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()

    def _require_known_group(self, group: str) -> None:
        if group not in CONFIG_GROUPS:
            raise exceptions.group_not_found()


def _config_group_target_id(group: str) -> uuid.UUID:
    """配置组审计目标 ID: 由组名确定性派生, 与操作者解耦, 便于按组溯源。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"system-config-group:{group}")


def mask_secret(secret: str) -> str | None:
    if not secret:
        return None
    if len(secret) < 8:
        return "****"
    suffix = secret[-4:]
    prefix = "sk-" if secret.startswith("sk-") else ""
    return f"{prefix}****{suffix}"
