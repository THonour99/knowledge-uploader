from __future__ import annotations

from app.core.logging import mask_log_value, mask_secret


def test_mask_secret_redacts_provider_keys_and_bearer_tokens() -> None:
    value = (
        "openai=sk-test-secret-value-abcd "
        "ragflow=ragflow-NmJf-gpHFxV2yOM47wfumTLeMkUJOjgNWDBBCc53KI4 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret"
    )

    masked = mask_secret(value)

    assert "sk-test-secret-value-abcd" not in masked
    assert "ragflow-NmJf-gpHFxV2yOM47wfumTLeMkUJOjgNWDBBCc53KI4" not in masked
    assert "eyJhbGciOiJIUzI1NiJ9.secret" not in masked
    assert "sk-****abcd" in masked
    assert "ragflow-****3KI4" in masked
    assert "Bearer ***" in masked


def test_mask_log_value_redacts_sensitive_keys_recursively() -> None:
    payload = {
        "headers": {"Authorization": "Bearer top-secret-token"},
        "nested": [{"ragflow_api_key": "ragflow-NmJf-gpHFxV2yOM47wfumTLeMkUJOjgNWDBBCc53KI4"}],
        "message": "safe sk-test-secret-value-abcd",
    }

    masked = mask_log_value("payload", payload)

    assert masked["headers"]["Authorization"] == "***"
    assert masked["nested"][0]["ragflow_api_key"] == "***"
    assert masked["message"] == "safe sk-****abcd"
