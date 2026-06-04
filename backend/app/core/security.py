from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password_hash, password)


def create_jwt(payload: dict[str, Any], secret: str, expire_minutes: int) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=expire_minutes)
    return jwt.encode({**payload, "exp": expires_at}, secret, algorithm="HS256")


def decrypt_api_key(encrypted_value: str, key: str) -> str:
    return Fernet(key.encode("utf-8")).decrypt(encrypted_value.encode("utf-8")).decode("utf-8")


def encrypt_api_key(value: str, key: str) -> str:
    return Fernet(key.encode("utf-8")).encrypt(value.encode("utf-8")).decode("utf-8")
