from __future__ import annotations


def login_rate_limit_key(email: str) -> str:
    normalized = email.strip().lower()
    return f"ratelimit:login:{normalized}"
