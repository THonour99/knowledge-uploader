from __future__ import annotations

import pytest
from scripts.alertmanager_secret_scan import sensitive_http_header_paths


@pytest.mark.parametrize(
    "header_name",
    (
        "X-Webhook-Secret",
        "x_webhook_secret",
        "X-WEBHOOK-SIGNATURE",
        "Cookie",
        "X-Credential",
        "X-Custom-Metadata",
    ),
)
@pytest.mark.parametrize("delivery_type", ("webhook_configs", "email_configs", "slack_configs"))
def test_scan_rejects_sensitive_or_unknown_inline_values_in_nested_receivers(
    header_name: str,
    delivery_type: str,
) -> None:
    marker = "must-not-be-reported"
    config = {
        "receivers": [
            {
                "name": "ops",
                delivery_type: [
                    {
                        "http_config": {
                            "http_headers": {header_name: {"values": [marker]}}
                        }
                    }
                ],
            }
        ]
    }

    paths = sensitive_http_header_paths(config)

    assert len(paths) == 1
    assert paths[0].endswith(f"{header_name}.values")
    assert marker not in paths[0]


@pytest.mark.parametrize("header_name", ("Accept", "Content-Type", "content_type", "User-Agent"))
def test_scan_allows_public_inline_header_values(header_name: str) -> None:
    config = {
        "http_config": {
            "http_headers": {header_name: {"values": ["application/json"]}}
        }
    }

    assert sensitive_http_header_paths(config) == ()


@pytest.mark.parametrize("values", ([], [""], ["   "], None))
def test_scan_ignores_empty_inline_values(values: object) -> None:
    config = {
        "http_config": {
            "http_headers": {"X-Webhook-Secret": {"values": values}}
        }
    }

    assert sensitive_http_header_paths(config) == ()


def test_scan_allows_file_backed_sensitive_header() -> None:
    config = {
        "http_config": {
            "http_headers": {
                "Authorization": {"files": ["/run/secrets/authorization-header"]}
            }
        }
    }

    assert sensitive_http_header_paths(config) == ()


def test_scan_rejects_inline_secrets_even_for_public_header() -> None:
    config = {
        "http_config": {
            "http_headers": {"Content-Type": {"secrets": ["opaque"]}}
        }
    }

    assert sensitive_http_header_paths(config) == (
        "http_config.http_headers.Content-Type.secrets",
    )
