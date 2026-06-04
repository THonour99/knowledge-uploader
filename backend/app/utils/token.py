from __future__ import annotations

import secrets


def generate_urlsafe_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)
