from __future__ import annotations

import os
import ssl
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast

import anyio
import certifi
from minio import Minio
from minio.error import MinioException, S3Error
from urllib3 import PoolManager
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.util import Retry, Timeout

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
STREAM_CHUNK_SIZE = 64 * 1024
MINIO_HTTP_TIMEOUT_SECONDS = 5 * 60
MINIO_HTTP_POOL_SIZE = 10
MINIO_HTTP_RETRY_COUNT = 5
MINIO_HTTP_RETRY_BACKOFF_FACTOR = 0.2
MINIO_HTTP_RETRY_STATUSES = (500, 502, 503, 504)


class _ObjectResponse(Protocol):
    def read(self, amt: int | None = None) -> bytes: ...

    def close(self) -> None: ...

    def release_conn(self) -> None: ...


class MinioObjectStream:
    def __init__(
        self,
        response: _ObjectResponse,
        *,
        content_length: int | None,
        chunk_size: int = STREAM_CHUNK_SIZE,
    ) -> None:
        self._response = response
        self._remaining = content_length
        self._chunk_size = chunk_size
        self._closed = False

    def __aiter__(self) -> MinioObjectStream:
        return self

    async def __anext__(self) -> bytes:
        if self._closed or self._remaining == 0:
            await self.aclose()
            raise StopAsyncIteration
        read_size = (
            self._chunk_size if self._remaining is None else min(self._chunk_size, self._remaining)
        )
        chunk = await anyio.to_thread.run_sync(self._response.read, read_size)
        if not chunk:
            await self.aclose()
            raise StopAsyncIteration
        if self._remaining is not None:
            self._remaining = max(0, self._remaining - len(chunk))
        return chunk

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await anyio.to_thread.run_sync(self._close_sync)

    def _close_sync(self) -> None:
        try:
            self._response.close()
        finally:
            self._response.release_conn()


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
        http_client: PoolManager | None = None
        if settings.minio_secure:
            ca_cert_file = (
                settings.minio_ca_cert_file.strip()
                or os.environ.get("SSL_CERT_FILE", "").strip()
                or certifi.where()
            )
            try:
                if not Path(ca_cert_file).is_file():
                    raise OSError
                ssl.create_default_context(cafile=ca_cert_file)
            except (OSError, ssl.SSLError, ValueError):
                msg = "MinIO CA certificate file is unavailable or invalid"
                raise ValueError(msg) from None
            http_client = PoolManager(
                timeout=Timeout(
                    connect=MINIO_HTTP_TIMEOUT_SECONDS,
                    read=MINIO_HTTP_TIMEOUT_SECONDS,
                ),
                maxsize=MINIO_HTTP_POOL_SIZE,
                cert_reqs="CERT_REQUIRED",
                ca_certs=ca_cert_file,
                retries=Retry(
                    total=MINIO_HTTP_RETRY_COUNT,
                    backoff_factor=MINIO_HTTP_RETRY_BACKOFF_FACTOR,
                    status_forcelist=list(MINIO_HTTP_RETRY_STATUSES),
                ),
            )
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=http_client,
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

    async def open_object(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> MinioObjectStream:
        response = await anyio.to_thread.run_sync(
            self._open_object_sync,
            bucket,
            object_key,
            offset,
            length,
        )
        return MinioObjectStream(response, content_length=length)

    def _open_object_sync(
        self,
        bucket: str,
        object_key: str,
        offset: int,
        length: int | None,
    ) -> _ObjectResponse:
        response = self._client.get_object(
            bucket,
            object_key,
            offset=offset,
            length=length or 0,
        )
        return cast(_ObjectResponse, response)
