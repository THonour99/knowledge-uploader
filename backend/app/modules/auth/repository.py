from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import EmailVerificationToken, PasswordResetToken


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_email_verification_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> EmailVerificationToken:
        await self.lock_email_verification_tokens(user_id)
        await self.invalidate_email_verification_tokens(user_id, invalidated_at=issued_at)
        token = EmailVerificationToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_email_verification_token(self, token_hash: str) -> EmailVerificationToken | None:
        result = await self._session.execute(
            select(EmailVerificationToken)
            .where(EmailVerificationToken.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_email_verification_tokens(self, user_id: uuid.UUID) -> None:
        await self._lock_token_family(user_id, purpose="email-verification")

    async def invalidate_email_verification_tokens(
        self,
        user_id: uuid.UUID,
        *,
        invalidated_at: datetime,
    ) -> None:
        await self._session.execute(
            update(EmailVerificationToken)
            .where(
                EmailVerificationToken.user_id == user_id,
                EmailVerificationToken.used_at.is_(None),
            )
            .values(used_at=invalidated_at)
        )

    async def replace_password_reset_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> PasswordResetToken:
        await self.lock_password_reset_tokens(user_id)
        await self.invalidate_password_reset_tokens(user_id, invalidated_at=issued_at)
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_password_reset_token(self, token_hash: str) -> PasswordResetToken | None:
        result = await self._session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_password_reset_tokens(self, user_id: uuid.UUID) -> None:
        await self._lock_token_family(user_id, purpose="password-reset")

    async def invalidate_password_reset_tokens(
        self,
        user_id: uuid.UUID,
        *,
        invalidated_at: datetime,
    ) -> None:
        await self._session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=invalidated_at)
        )

    async def _lock_token_family(self, user_id: uuid.UUID, *, purpose: str) -> None:
        lock_material = f"knowledge-uploader:auth-token:{purpose}:{user_id}".encode()
        lock_key = int.from_bytes(
            hashlib.sha256(lock_material).digest()[:8],
            byteorder="big",
            signed=True,
        )
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
