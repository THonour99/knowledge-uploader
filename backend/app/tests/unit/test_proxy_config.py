from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_compose_defaults_do_not_trust_all_forwarded_ips() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "UVICORN_FORWARDED_ALLOW_IPS:-*" not in compose
    assert "UVICORN_FORWARDED_ALLOW_IPS:-127.0.0.1" in compose


def test_nginx_overwrites_forwarded_for_header() -> None:
    configs = [
        REPO_ROOT / "nginx" / "default.conf",
        REPO_ROOT / "frontend" / "nginx.conf",
    ]

    for config_path in configs:
        config = config_path.read_text(encoding="utf-8")
        assert "$proxy_add_x_forwarded_for" not in config
        assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
