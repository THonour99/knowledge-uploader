from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import EmailVerificationToken, PasswordResetToken


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_email_verification_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> EmailVerificationToken:
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
            select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def create_password_reset_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> PasswordResetToken:
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
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()
