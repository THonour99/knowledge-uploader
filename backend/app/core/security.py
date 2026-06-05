from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, VerifyMismatchError):
        return False


def create_jwt(payload: dict[str, Any], secret: str, expire_minutes: int) -> str:
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=expire_minutes)
    return jwt.encode(
        {**payload, "iat": issued_at, "jti": str(uuid4()), "exp": expires_at},
        secret,
        algorithm="HS256",
    )


def decode_jwt(token: str, secret: str) -> dict[str, Any]:
    return cast(dict[str, Any], jwt.decode(token, secret, algorithms=["HS256"]))


def password_fingerprint(password_hash: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        password_hash.encode("utf-8"),
        sha256,
    ).hexdigest()


def decrypt_api_key(encrypted_value: str, key: str) -> str:
    return Fernet(key.encode("utf-8")).decrypt(encrypted_value.encode("utf-8")).decode("utf-8")


def encrypt_api_key(value: str, key: str) -> str:
    return Fernet(key.encode("utf-8")).encrypt(value.encode("utf-8")).decode("utf-8")
