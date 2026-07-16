"""红队:越权 / 数据外泄攻击。

靶心: GET /api/files/{id}(详情)与 GET /api/files(列表)的行级隔离。
攻击假设: repository 的 get/list 可能缺 uploader_id 过滤, 员工可读取 / 枚举他人文件。
铁律: 跑红 = 越权真实存在; 跑绿 = 隔离有效(证伪)。
需 postgres + redis(CI services);本地需 Docker + invoke up。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _create_user(*, email: str, password: str, role: str = "employee") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=email.split("@", 1)[0],
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(password),
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def _create_file_row(*, uploader_id: UUID, status: str = "uploaded") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name="victim.txt",
        title="victim.txt",
        stored_name="file-victim.txt",
        extension="txt",
        mime_type="text/plain",
        size=1024,
        hash=uuid4().hex + uuid4().hex,
        storage_type="minio",
        bucket="test-knowledge-files",
        object_key=f"uploads/{uploader_id}/{uuid4()}/file-victim.txt",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description=None,
        tags=[],
        status=status,
        review_status="pending",
        ai_analysis_enabled_at_upload=True,
        ragflow_document_id=None,
        ragflow_dataset_id=None,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def test_employee_cannot_read_others_file_detail(redteam_client: AsyncClient) -> None:
    """攻击: 员工 A 用 B 的 file_id 读详情。期望 403/404, 不泄露 B 的文件。

    跑红(返回 200 + B 的文件) = 越权读取漏洞。
    """
    owner_id = await _create_user(email="victim@company.com", password="password123")
    await _create_user(email="attacker@company.com", password="password123")
    attacker_token = await _login(
        redteam_client,
        email="attacker@company.com",
        password="password123",
    )
    victim_file_id = await _create_file_row(uploader_id=owner_id)

    response = await redteam_client.get(
        f"/api/files/{victim_file_id}",
        headers={"Authorization": f"Bearer {attacker_token}"},
    )

    assert response.status_code in (
        403,
        404,
    ), f"越权读取: attacker 拿到了 victim 的文件详情 (status={response.status_code})"


async def test_file_list_only_returns_own_files(redteam_client: AsyncClient) -> None:
    """攻击: 员工 A 列文件, 不应看到 B 的文件。

    跑红(列表含 B 的 file_id) = 越权枚举漏洞。
    """
    owner_id = await _create_user(email="owner2@company.com", password="password123")
    await _create_user(email="snooper@company.com", password="password123")
    snooper_token = await _login(
        redteam_client,
        email="snooper@company.com",
        password="password123",
    )
    victim_file_id = await _create_file_row(uploader_id=owner_id)

    response = await redteam_client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {snooper_token}"},
    )

    assert response.status_code == 200
    listed_ids = {item["id"] for item in response.json()["data"]["items"]}
    assert str(victim_file_id) not in listed_ids, "越权枚举: snooper 在列表里看到了他人文件"
