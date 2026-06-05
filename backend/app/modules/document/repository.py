from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.document.models import File


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, file_id: uuid.UUID) -> File | None:
        result = await self._session.execute(select(File).where(File.id == file_id))
        return result.scalar_one_or_none()

    async def get_for_uploader(self, *, file_id: uuid.UUID, uploader_id: uuid.UUID) -> File | None:
        result = await self._session.execute(
            select(File).where(File.id == file_id, File.uploader_id == uploader_id)
        )
        return result.scalar_one_or_none()

    async def list_for_uploader(self, uploader_id: uuid.UUID) -> list[File]:
        result = await self._session.execute(
            select(File).where(File.uploader_id == uploader_id).order_by(File.uploaded_at.desc())
        )
        return list(result.scalars())

    async def find_first_by_hash_for_uploader(
        self,
        *,
        file_hash: str,
        uploader_id: uuid.UUID,
    ) -> File | None:
        result = await self._session.execute(
            select(File)
            .where(File.hash == file_hash, File.uploader_id == uploader_id)
            .order_by(File.uploaded_at.asc())
        )
        return result.scalars().first()

    async def add(self, file: File) -> File:
        self._session.add(file)
        await self._session.flush()
        await self._session.refresh(file)
        return file
