from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.config import (
    Settings,
    approved_ragflow_base_url,
    get_settings,
    is_protected_environment,
)
from app.core.ragflow_endpoint import (
    ragflow_endpoint_identity,
    ragflow_tls_spki_pins_for_endpoint,
)
from app.core.runtime_config import get_config

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RagflowRuntimeSettings:
    base_url: str
    api_key: str
    timeout_seconds: float
    allowed_dataset_ids: frozenset[str]
    protected_environment: bool
    tls_spki_pins: frozenset[bytes]

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

    requested_base_url = (
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
    protected_environment = is_protected_environment(
        resolved_settings.app_env,
        resolved_settings.app_base_url,
    )
    tls_spki_pins: frozenset[bytes] = frozenset()
    try:
        base_url = approved_ragflow_base_url(requested_base_url, resolved_settings)
        if base_url:
            tls_spki_pins = ragflow_tls_spki_pins_for_endpoint(
                base_url,
                resolved_settings.ragflow_tls_spki_pins,
            )
    except ValueError:
        logger.error(
            "ragflow_runtime_endpoint_not_approved",
            error_type="EndpointNotApproved",
        )
        base_url = ""
        api_key = ""
    if api_key and protected_environment and base_url:
        scheme = ragflow_endpoint_identity(base_url)[0]
        if scheme != "https" or not tls_spki_pins:
            logger.error(
                "ragflow_runtime_transport_not_approved",
                error_type="ProtectedTransportNotApproved",
            )
            base_url = ""
            api_key = ""
            tls_spki_pins = frozenset()
    if not base_url:
        api_key = ""
        tls_spki_pins = frozenset()
    return RagflowRuntimeSettings(
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        allowed_dataset_ids=normalized_dataset_ids(resolved_settings.ragflow_allowed_dataset_ids),
        protected_environment=protected_environment,
        tls_spki_pins=tls_spki_pins,
    )
