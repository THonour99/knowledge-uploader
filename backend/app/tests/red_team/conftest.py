"""红队测试专用 fixtures 与攻击载荷。

复用全局 conftest 的 DB/Redis 准备；这里提供红队特有的：
- redteam_client: 带依赖覆盖的 AsyncClient（仿 unit/test_document_lifecycle.py）
- malicious_filenames: 文件名清洗攻击载荷（Windows 保留名 / 路径穿越 / 双扩展名）

需 postgres + redis（CI services 提供；本地需 Docker + invoke up）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def redteam_client() -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="test-knowledge-files",
        upload_allowed_extensions="pdf,txt",
        upload_allowed_mime_types="application/pdf,text/plain",
        ai_analysis_enabled=True,
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def malicious_filenames() -> list[str]:
    """文件名清洗攻击载荷：上传攻击测试用。"""
    return [
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "LPT1",
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "invoice.pdf.exe",
        "report\x00.txt",
        "a" * 300 + ".txt",
    ]
