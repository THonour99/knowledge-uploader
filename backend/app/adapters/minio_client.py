from __future__ import annotations

from io import BytesIO

import anyio
from minio import Minio

from app.core.config import Settings


class MinioDocumentStorage:
    def __init__(self, settings: Settings) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        await anyio.to_thread.run_sync(
            self._put_object_sync,
            bucket,
            object_key,
            data,
            content_type,
        )

    def _put_object_sync(
        self,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)
        self._client.put_object(
            bucket,
            object_key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        await anyio.to_thread.run_sync(self._delete_object_sync, bucket, object_key)

    def _delete_object_sync(self, bucket: str, object_key: str) -> None:
        self._client.remove_object(bucket, object_key)

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        return await anyio.to_thread.run_sync(self._get_object_sync, bucket, object_key)

    def _get_object_sync(self, bucket: str, object_key: str) -> bytes:
        response = self._client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
