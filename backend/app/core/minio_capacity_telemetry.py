from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

import anyio
import certifi
import httpx
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import RowMapping

from app.core.config import Settings
from app.core.database import AsyncSessionFactory
from app.core.jwt_validation import is_semantic_time_bound_jwt
from app.core.minio_endpoint import minio_metrics_url

_RAW_TOTAL_METRIC = "minio_cluster_capacity_raw_total_bytes"
_RAW_FREE_METRIC = "minio_cluster_capacity_raw_free_bytes"
_MAX_METRICS_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_BEARER_TOKEN_BYTES = 16 * 1024
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
_SNAPSHOT_SOURCE = "minio_cluster_metrics"
_SNAPSHOT_MIN_INTERVAL = timedelta(minutes=5)
_SNAPSHOT_MAX_CLOCK_SKEW = timedelta(minutes=1)
_SNAPSHOT_RETENTION = timedelta(days=90)
_SNAPSHOT_ADVISORY_LOCK_KEY = 5_570_757_665_974_681_936

STORAGE_CAPACITY_SNAPSHOTS = Table(
    "storage_capacity_snapshots",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("backend", String(20), nullable=False),
    Column("scope", String(20), nullable=False),
    Column("source_kind", String(40), nullable=False),
    Column("total_bytes", BigInteger, nullable=False),
    Column("used_bytes", BigInteger, nullable=False),
    Column("free_bytes", BigInteger, nullable=False),
    Column("evidence_sha256", String(64), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class MinioCapacityMeasurement:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    evidence_sha256: str
    captured_at: datetime


class MinioCapacityCollectionError(RuntimeError):
    """The trusted MinIO cluster endpoint did not yield a valid physical measurement."""


async def collect_and_persist_minio_capacity(settings: Settings) -> MinioCapacityMeasurement:
    """Collect trusted metrics and retain bounded snapshots in an independent transaction."""
    measurement = await collect_minio_capacity(settings)
    collected_at = datetime.now(UTC)
    if measurement.captured_at > collected_at:
        raise MinioCapacityCollectionError("MinIO capacity capture time is in the future")
    async with AsyncSessionFactory() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _SNAPSHOT_ADVISORY_LOCK_KEY},
        )
        latest_result = await session.execute(
            select(
                STORAGE_CAPACITY_SNAPSHOTS.c.total_bytes,
                STORAGE_CAPACITY_SNAPSHOTS.c.used_bytes,
                STORAGE_CAPACITY_SNAPSHOTS.c.free_bytes,
                STORAGE_CAPACITY_SNAPSHOTS.c.collected_at,
            )
            .where(STORAGE_CAPACITY_SNAPSHOTS.c.source_kind == _SNAPSHOT_SOURCE)
            .order_by(
                STORAGE_CAPACITY_SNAPSHOTS.c.collected_at.desc(),
                STORAGE_CAPACITY_SNAPSHOTS.c.id.desc(),
            )
            .limit(1)
        )
        latest = latest_result.mappings().one_or_none()
        if latest is not None and not _should_persist_snapshot(measurement, latest, collected_at):
            await session.commit()
            return measurement
        await session.execute(
            insert(STORAGE_CAPACITY_SNAPSHOTS).values(
                id=uuid.uuid4(),
                backend="minio",
                scope="cluster",
                source_kind=_SNAPSHOT_SOURCE,
                total_bytes=measurement.total_bytes,
                used_bytes=measurement.used_bytes,
                free_bytes=measurement.free_bytes,
                evidence_sha256=measurement.evidence_sha256,
                captured_at=measurement.captured_at,
                collected_at=collected_at,
            )
        )
        await session.execute(
            delete(STORAGE_CAPACITY_SNAPSHOTS).where(
                STORAGE_CAPACITY_SNAPSHOTS.c.source_kind == _SNAPSHOT_SOURCE,
                STORAGE_CAPACITY_SNAPSHOTS.c.captured_at < collected_at - _SNAPSHOT_RETENTION,
            )
        )
        await session.commit()
    return measurement


def _should_persist_snapshot(
    measurement: MinioCapacityMeasurement,
    latest: RowMapping | Mapping[str, object],
    collected_at: datetime,
) -> bool:
    latest_collected_at = latest["collected_at"]
    if not isinstance(latest_collected_at, datetime):
        raise MinioCapacityCollectionError("Persisted MinIO snapshot time is invalid")
    if latest_collected_at.tzinfo is None or latest_collected_at.utcoffset() is None:
        raise MinioCapacityCollectionError("Persisted MinIO snapshot time is invalid")
    latest_collected_at = latest_collected_at.astimezone(UTC)
    if latest_collected_at > collected_at + _SNAPSHOT_MAX_CLOCK_SKEW:
        raise MinioCapacityCollectionError("Persisted MinIO snapshot time is in the future")
    values_changed = (
        cast(int, latest["total_bytes"]) != measurement.total_bytes
        or cast(int, latest["used_bytes"]) != measurement.used_bytes
        or cast(int, latest["free_bytes"]) != measurement.free_bytes
    )
    interval_elapsed = collected_at - latest_collected_at >= _SNAPSHOT_MIN_INTERVAL
    return values_changed or interval_elapsed


async def collect_minio_capacity(settings: Settings) -> MinioCapacityMeasurement:
    captured_at = datetime.now(UTC)
    url = _metrics_url(settings)
    headers = await _metrics_headers(settings)
    verify: bool | str = True
    if settings.minio_secure:
        ca_file = settings.minio_ca_cert_file.strip() or certifi.where()
        if not await anyio.Path(ca_file).is_file():
            raise MinioCapacityCollectionError("MinIO metrics CA file is unavailable")
        verify = ca_file
    try:
        async with httpx.AsyncClient(
            verify=verify,
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise MinioCapacityCollectionError(
                        "MinIO capacity metrics returned a non-success status"
                    )
                content = await _read_limited_metrics_body(response)
    except (httpx.HTTPError, OSError, ValueError) as error:
        raise MinioCapacityCollectionError("MinIO capacity metrics request failed") from error
    try:
        body = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise MinioCapacityCollectionError("MinIO capacity metrics are not valid UTF-8") from error
    total_bytes = _single_cluster_value(body, _RAW_TOTAL_METRIC)
    free_bytes = _single_cluster_value(body, _RAW_FREE_METRIC)
    if total_bytes <= 0 or free_bytes < 0 or free_bytes > total_bytes:
        raise MinioCapacityCollectionError("MinIO capacity metrics are inconsistent")
    return MinioCapacityMeasurement(
        total_bytes=total_bytes,
        used_bytes=total_bytes - free_bytes,
        free_bytes=free_bytes,
        evidence_sha256=hashlib.sha256(content).hexdigest(),
        captured_at=captured_at,
    )


async def _read_limited_metrics_body(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in response.aiter_bytes():
        total_bytes += len(chunk)
        if total_bytes > _MAX_METRICS_RESPONSE_BYTES:
            raise MinioCapacityCollectionError("MinIO capacity metrics response size is invalid")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise MinioCapacityCollectionError("MinIO capacity metrics response size is invalid")
    return content


async def _metrics_headers(settings: Settings) -> dict[str, str]:
    token_file = settings.minio_metrics_bearer_token_file.strip()
    if not token_file:
        raise MinioCapacityCollectionError("MinIO metrics bearer token file is not configured")
    try:
        async with await anyio.open_file(token_file, mode="rb") as token_stream:
            raw_token_bytes = await token_stream.read(_MAX_BEARER_TOKEN_BYTES + 1)
    except OSError as error:
        raise MinioCapacityCollectionError(
            "MinIO metrics bearer token file is unavailable"
        ) from error
    if not raw_token_bytes or len(raw_token_bytes) > _MAX_BEARER_TOKEN_BYTES:
        raise MinioCapacityCollectionError("MinIO metrics bearer token file size is invalid")
    if not raw_token_bytes.endswith(b"\n") or raw_token_bytes.count(b"\n") != 1:
        raise MinioCapacityCollectionError("MinIO metrics bearer token is invalid")
    try:
        token = raw_token_bytes[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise MinioCapacityCollectionError("MinIO metrics bearer token is invalid") from error
    if not _is_semantic_jwt(token):
        raise MinioCapacityCollectionError("MinIO metrics bearer token is invalid")
    return {
        "Accept": "text/plain",
        "Authorization": f"Bearer {token}",
    }


def _is_semantic_jwt(token: str) -> bool:
    return is_semantic_time_bound_jwt(token)


def _metrics_url(settings: Settings) -> str:
    try:
        return minio_metrics_url(
            settings.minio_endpoint,
            secure=settings.minio_secure,
        )
    except ValueError as error:
        raise MinioCapacityCollectionError("MinIO endpoint is invalid") from error


def _single_cluster_value(body: str, metric_name: str) -> int:
    values: set[int] = set()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        sample_name = parts[0].partition("{")[0]
        if sample_name != metric_name:
            continue
        values.add(_parse_metric_integer(parts[1]))
    if len(values) != 1:
        raise MinioCapacityCollectionError(
            "MinIO cluster capacity metric is missing or inconsistent across reporters"
        )
    return values.pop()


def _parse_metric_integer(raw_value: str) -> int:
    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise MinioCapacityCollectionError("MinIO capacity metric is not numeric") from error
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        raise MinioCapacityCollectionError("MinIO capacity metric is not a non-negative integer")
    integer = int(value)
    if integer > _MAX_POSTGRES_BIGINT:
        raise MinioCapacityCollectionError("MinIO capacity metric exceeds the persistence limit")
    return integer
