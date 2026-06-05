from __future__ import annotations

from redis.asyncio import from_url


def login_rate_limit_key(email: str) -> str:
    normalized = email.strip().lower()
    return f"ratelimit:login:{normalized}"


def login_ip_rate_limit_key(client_ip: str) -> str:
    return f"ratelimit:login-ip:{client_ip}"


def register_rate_limit_key(client_ip: str) -> str:
    return f"ratelimit:auth:register:{client_ip}"


def password_reset_rate_limit_key(email: str) -> str:
    normalized = email.strip().lower()
    return f"ratelimit:auth:password-reset:{normalized}"


def email_verification_rate_limit_key(email: str) -> str:
    normalized = email.strip().lower()
    return f"ratelimit:auth:email-verification:{normalized}"


def jwt_blacklist_key(jti: str) -> str:
    return f"jwt:blacklist:{jti}"


async def is_within_rate_limit(
    *,
    redis_url: str,
    key: str,
    limit: int,
    window_seconds: int,
) -> bool:
    if limit <= 0:
        return False
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        return int(count) <= limit
    finally:
        await client.aclose()


async def blacklist_jwt(
    *,
    redis_url: str,
    jti: str,
    ttl_seconds: int,
) -> None:
    if ttl_seconds <= 0:
        return
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await client.set(jwt_blacklist_key(jti), "1", ex=ttl_seconds)
    finally:
        await client.aclose()


async def is_jwt_blacklisted(*, redis_url: str, jti: str) -> bool:
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        return bool(await client.exists(jwt_blacklist_key(jti)))
    finally:
        await client.aclose()
