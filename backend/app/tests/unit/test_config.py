from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_FERNET_KEY = "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="


def test_development_allows_phase0_placeholder_secrets() -> None:
    settings = Settings(
        app_env="development",
        jwt_secret="change-me-change-me-change-me-change-me",
        encryption_key="change-me-fernet-key",
    )

    assert settings.app_env == "development"


def test_production_rejects_placeholder_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            app_env="production",
            jwt_secret="change-me-change-me-change-me-change-me",
            encryption_key=VALID_FERNET_KEY,
        )


def test_production_rejects_invalid_encryption_key() -> None:
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        Settings(
            app_env="production",
            jwt_secret="this-is-a-production-secret-with-32-bytes",
            encryption_key="change-me-fernet-key",
        )


def test_production_rejects_default_development_encryption_key() -> None:
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        Settings(
            app_env="production",
            jwt_secret="this-is-a-production-secret-with-32-bytes",
            encryption_key=VALID_FERNET_KEY,
        )
