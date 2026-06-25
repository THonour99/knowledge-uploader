from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.runtime_config import get_config


@dataclass(frozen=True)
class RagflowRuntimeSettings:
    base_url: str
    api_key: str
    timeout_seconds: float
    allowed_dataset_ids: frozenset[str]

    @property
    def integration_enabled(self) -> bool:
        return bool(self.api_key.strip())


def normalized_dataset_ids(raw_value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in raw_value.split(",") if item.strip())


def is_ragflow_dataset_allowed(
    dataset_id: str,
    runtime_settings: RagflowRuntimeSettings,
) -> bool:
    cleaned_dataset_id = dataset_id.strip()
    if not cleaned_dataset_id:
        return False
    if runtime_settings.allowed_dataset_ids:
        return cleaned_dataset_id in runtime_settings.allowed_dataset_ids
    return not runtime_settings.integration_enabled


async def resolve_ragflow_runtime_settings(
    settings: Settings | None = None,
) -> RagflowRuntimeSettings:
    resolved_settings = settings or get_settings()
    base_url_value = await get_config("ragflow.base_url")
    api_key_value = await get_config("ragflow.api_key")
    timeout_value = await get_config("ragflow.sync_timeout_seconds")

    base_url = (
        str(base_url_value).strip()
        if base_url_value is not None
        else resolved_settings.ragflow_base_url
    )
    api_key = (
        str(api_key_value).strip()
        if api_key_value is not None
        else resolved_settings.ragflow_api_key
    )
    timeout_seconds = (
        float(timeout_value)
        if isinstance(timeout_value, int | float)
        else resolved_settings.ragflow_request_timeout
    )
    return RagflowRuntimeSettings(
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        allowed_dataset_ids=normalized_dataset_ids(
            resolved_settings.ragflow_allowed_dataset_ids
        ),
    )
