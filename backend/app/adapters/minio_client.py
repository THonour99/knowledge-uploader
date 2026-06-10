from __future__ import annotations

from io import BytesIO

import anyio
from minio import Minio
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from app.core.config import Settings

#: 对象存储调用可能抛出的网络 / 存储类异常。视为瞬态、可重试。
STORAGE_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    MinioException,
    Urllib3HTTPError,
    OSError,
)

#: 永久性 S3 错误码。重试不可能成功、不应进入重试编排。
PERMANENT_S3_ERROR_CODES: frozenset[str] = frozenset(
    {
        "NoSuchKey",
        "NoSuchBucket",
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidBucketName",
    }
)


def is_transient_storage_error(error: BaseException) -> bool:
    """判断存储异常是否为瞬态(可重试)。

    永久性 S3 错误(对象不存在 / 凭证错误等)返回 False、
    其余 ``STORAGE_TRANSIENT_ERRORS`` 实例返回 True。
    """
    if isinstance(error, S3Error) and error.code in PERMANENT_S3_ERROR_CODES:
        return False
    return isinstance(error, STORAGE_TRANSIENT_ERRORS)


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
