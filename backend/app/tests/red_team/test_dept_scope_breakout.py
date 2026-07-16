"""红队: dept-scoped 部门数据隔离 (P0) 的越权 / 信息泄露攻击。

攻击设计文档: docs/spark/2026-06-23-dept-scoped-review-and-admission-design.md (§4 / §6)。
靶心: review / ragflow / document 三模块的部门隔离与禁止自审。

铁律: 跑红 = 防御缺失 (越权 / 泄露真实存在); 跑绿 = 隔离有效 (假设被证伪, 保留为防回归)。

不重复 backend/app/tests/unit/test_department_scoped_p0.py 已覆盖的 9 个场景;
这里专攻其未覆盖的边角:
  1. 跨部门枚举一致性 (存在但不管辖 vs 不存在, 状态码是否泄露存在性)。
  2. 伪造 JWT role 是否被 DB 实际角色覆盖。
  3. 死锁豁免误触发 (存在非本人合格审核人时, system_admin 仍能自审?)。
  4. update_file_classification (PATCH) 的跨部门写 403 (已有测试只覆盖 approve/retry)。
  5. 多管辖部门组合下的越权 (管辖 A+B, 攻 C)。

需 postgres + redis(CI services);本地用 5433 / 16380。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
PASSWORD = "password123"


# ---------------------------------------------------------------------------
# 攻击载荷构造 helpers (直连 DB / 仿 test_data_exfiltration.py)
# ---------------------------------------------------------------------------


async def _create_department(*, name: str, code: str, status: str = "active") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=code, status=status)
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department.id


async def _create_user(
    *,
    email: str,
    role: str = "employee",
    department_id: UUID = UNASSIGNED_DEPARTMENT_ID,
    email_verified: bool = True,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=normalized_email.split("@", 1)[0],
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(PASSWORD),
        department_id=department_id,
        department="seed",
        role=role,
        status="active",
        email_verified=email_verified,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _assign_managed_departments(*, user_id: UUID, department_ids: list[UUID]) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        for department_id in department_ids:
            session.add(UserManagedDepartment(user_id=user_id, department_id=department_id))
        await session.commit()


async def _create_file(
    *,
    uploader_id: UUID,
    department_id: UUID,
    status: str = "pending_review",
    review_status: str = "pending",
    dataset_mapping_id: UUID | None = None,
    ragflow_dataset_id: str | None = None,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file_id = uuid4()
    submitted_at = datetime.now(UTC) if status == "pending_review" else None
    file = File(
        id=file_id,
        original_name=f"{file_id}.pdf",
        title=f"{file_id}.pdf",
        stored_name=f"{file_id}.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=128,
        hash=uuid4().hex + uuid4().hex,
        storage_type="minio",
        bucket="test-knowledge-files",
        object_key=f"uploads/{uploader_id}/{file_id}.pdf",
        uploader_id=uploader_id,
        department_id=department_id,
        department="seed",
        dataset_mapping_id=dataset_mapping_id,
        visibility="private",
        description="red-team dept scope target",
        tags=[],
        status=status,
        review_status=review_status,
        submitted_at=submitted_at,
        review_due_at=(
            submitted_at + timedelta(hours=24) if submitted_at is not None else None
        ),
        ragflow_dataset_id=ragflow_dataset_id,
        ai_analysis_enabled_at_upload=False,
        uploaded_at=datetime.now(UTC),
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        return file_id


async def _create_sync_task(*, file_id: UUID, status: str = "failed") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    task = SyncTask(
        file_id=file_id,
        task_type="ragflow_upload",
        status=status,
        retry_count=0,
        max_retry_count=3,
        error_message="network timeout" if status == "failed" else None,
    )
    async with AsyncSessionFactory() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def _create_category_and_dataset() -> tuple[UUID, UUID, str]:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Category, DatasetMapping

    category_id = uuid4()
    dataset_id = uuid4()
    ragflow_dataset_id = f"ragflow-{uuid4().hex[:8]}"
    category = Category(
        id=category_id,
        name=f"Category {uuid4().hex[:8]}",
        code=f"cat-{uuid4().hex[:8]}",
        require_review=True,
        auto_sync_enabled=True,
    )
    dataset = DatasetMapping(
        id=dataset_id,
        name=f"Dataset {uuid4().hex[:8]}",
        category_id=category_id,
        ragflow_dataset_id=ragflow_dataset_id,
        ragflow_dataset_name="RAGFlow Dataset",
        enabled=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(category)
        session.add(dataset)
        await session.commit()
        return category_id, dataset_id, ragflow_dataset_id


async def _login(client: AsyncClient, *, email: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["access_token"])


async def _password_hash(user_id: UUID) -> str:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.password_hash


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 攻击 1 + 2: 跨部门枚举一致性 (信息泄露 oracle)
# ---------------------------------------------------------------------------


async def test_file_detail_enumeration_is_consistent_for_dept_admin(
    redteam_client: AsyncClient,
) -> None:
    """攻击: dept_admin 用文件详情接口做存在性枚举。

    设计 §6.2 要求越权返回 403 或 (枚举防护) 404; 真正的枚举防护要求
    "存在但不管辖" 与 "不存在" 返回 *相同* 状态码, 否则攻击者可用状态码差异
    逐个 UUID 探测全公司哪些 file_id 真实存在 (跨部门存在性泄露)。

    已修复(2026-06-24): 越权分支统一抛 file_not_found, 越权与不存在均返回 404;
    本测试现为防回归 — out-of-scope 与 nonexistent 状态码必须一致。
    """
    finance_id = await _create_department(name="Enum Finance", code="enum-finance")
    legal_id = await _create_department(name="Enum Legal", code="enum-legal")
    legal_owner = await _create_user(email="enum-legal-owner@company.com", department_id=legal_id)
    admin_id = await _create_user(email="enum-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[finance_id])
    token = await _login(redteam_client, email="enum-admin@company.com")

    # 存在但在 legal (攻击者管辖 finance, 不管辖 legal)
    existing_out_of_scope = await _create_file(
        uploader_id=legal_owner,
        department_id=legal_id,
    )
    nonexistent_id = uuid4()

    out_of_scope_response = await redteam_client.get(
        f"/api/files/{existing_out_of_scope}",
        headers=_auth(token),
    )
    nonexistent_response = await redteam_client.get(
        f"/api/files/{nonexistent_id}",
        headers=_auth(token),
    )

    assert out_of_scope_response.status_code in (403, 404)
    assert nonexistent_response.status_code in (403, 404)
    assert out_of_scope_response.status_code == nonexistent_response.status_code, (
        "枚举 oracle: 跨部门'存在但不管辖'文件返回 "
        f"{out_of_scope_response.status_code}, '不存在'文件返回 "
        f"{nonexistent_response.status_code}; 状态码差异泄露了他部门文件的存在性"
    )


async def test_task_detail_enumeration_is_consistent_for_dept_admin(
    redteam_client: AsyncClient,
) -> None:
    """攻击: dept_admin 用同步任务详情接口做存在性枚举。

    已修复(2026-06-24): get_task 越权抛 task_not_found, 越权与不存在均返回 404;
    本测试现为防回归 — out-of-scope 与 nonexistent 状态码必须一致。
    """
    finance_id = await _create_department(name="Enum2 Finance", code="enum2-finance")
    legal_id = await _create_department(name="Enum2 Legal", code="enum2-legal")
    legal_owner = await _create_user(email="enum2-legal-owner@company.com", department_id=legal_id)
    admin_id = await _create_user(email="enum2-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[finance_id])
    token = await _login(redteam_client, email="enum2-admin@company.com")

    legal_file = await _create_file(uploader_id=legal_owner, department_id=legal_id)
    existing_out_of_scope_task = await _create_sync_task(file_id=legal_file)
    nonexistent_task_id = uuid4()

    out_of_scope_response = await redteam_client.get(
        f"/api/tasks/{existing_out_of_scope_task}",
        headers=_auth(token),
    )
    nonexistent_response = await redteam_client.get(
        f"/api/tasks/{nonexistent_task_id}",
        headers=_auth(token),
    )

    assert out_of_scope_response.status_code in (403, 404)
    assert nonexistent_response.status_code in (403, 404)
    assert out_of_scope_response.status_code == nonexistent_response.status_code, (
        "枚举 oracle: 跨部门'存在但不管辖'任务返回 "
        f"{out_of_scope_response.status_code}, '不存在'任务返回 "
        f"{nonexistent_response.status_code}; 状态码差异泄露了他部门同步任务的存在性"
    )


# ---------------------------------------------------------------------------
# 攻击 3: 伪造 JWT role 提权
# ---------------------------------------------------------------------------


async def test_forged_jwt_role_claim_is_ignored(redteam_client: AsyncClient) -> None:
    """攻击: employee 持一个合法签名但 role 篡改为 system_admin 的 JWT 打管理端点。

    登录令牌里带 role 声明 (auth/service.py:322), 若服务端信任令牌里的 role 而非 DB,
    则任何能改写 payload 的攻击者 (或拿到签名密钥的内鬼) 可一键提权。

    跑红(伪造 token 通过 system_admin-only 的 /api/admin/departments) = JWT role 提权漏洞。
    跑绿 = 服务端以 DB 角色为准, 令牌 role 声明被忽略 (保留为防回归)。
    """
    from app.core.security import create_jwt, password_fingerprint

    # 受害密钥与 redteam_client fixture 内 Settings 一致
    jwt_secret = "test-jwt-secret-with-more-than-32-bytes"
    attacker_id = await _create_user(email="jwt-forger@company.com", role="employee")
    password_hash = await _password_hash(attacker_id)

    forged_token = create_jwt(
        {
            "sub": str(attacker_id),
            "email": "jwt-forger@company.com",
            "role": "system_admin",  # 伪造提权声明
            "pwd": password_fingerprint(password_hash, jwt_secret),
            "sv": 0,
        },
        jwt_secret,
        60,
    )

    # system_admin-only 端点
    departments_response = await redteam_client.get(
        "/api/admin/departments",
        headers=_auth(forged_token),
    )
    create_dept_response = await redteam_client.post(
        "/api/admin/departments",
        headers=_auth(forged_token),
        json={"name": "Pwned", "code": "pwned"},
    )

    assert departments_response.status_code == 403, (
        "JWT 提权: 伪造 role=system_admin 的令牌读到了 system_admin-only 部门列表 "
        f"(status={departments_response.status_code})"
    )
    assert create_dept_response.status_code == 403, (
        "JWT 提权: 伪造 role=system_admin 的令牌创建了部门 "
        f"(status={create_dept_response.status_code})"
    )


# ---------------------------------------------------------------------------
# 攻击 4: 死锁豁免误触发 (禁止自审被绕过)
# ---------------------------------------------------------------------------


async def test_self_approval_deadlock_exemption_not_triggered_when_second_system_admin_exists(
    redteam_client: AsyncClient,
) -> None:
    """攻击: system_admin 上传文件到一个 *无 dept_admin* 的部门, 但系统里还有第二位
    system_admin (合格的非本人审核人), 试图自审自己上传的文件。

    设计 §6.3/§6.4: 仅当 *不存在* 非本人合格审核人时才放行死锁豁免。第二位 system_admin
    在场时豁免绝不可触发, 否则禁止自审形同虚设。已有测试的 system_admin 拒绝路径依赖
    "该部门有 dept_admin", 未覆盖 "靠第二位 system_admin 兜底" 这一支。

    跑红(自审成功 200/进入 approved) = 死锁豁免误触发, 禁止自审被绕过。
    跑绿(403) = has_non_self_reviewer 正确识别第二位 system_admin (保留为防回归)。
    """
    # 该部门刻意不分配任何 dept_admin -> dept_admin 兜底分支必为空
    orphan_dept = await _create_department(name="Orphan Dept", code="orphan-dept")
    _category_id, dataset_id, _ = await _create_category_and_dataset()

    uploader_admin = await _create_user(
        email="deadlock-uploader@company.com",
        role="system_admin",
        department_id=orphan_dept,
    )
    # 第二位 system_admin: 合格的非本人审核人, 死锁不成立
    await _create_user(
        email="deadlock-second-admin@company.com",
        role="system_admin",
        department_id=orphan_dept,
    )
    token = await _login(redteam_client, email="deadlock-uploader@company.com")
    file_id = await _create_file(
        uploader_id=uploader_admin,
        department_id=orphan_dept,
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-orphan",
    )

    approve_response = await redteam_client.post(
        f"/api/files/{file_id}/approve",
        headers=_auth(token),
        json={"sync_decision": "approve_only"},
    )

    assert approve_response.status_code == 403, (
        "禁止自审被绕过: 存在第二位 system_admin 时死锁豁免仍误触发, "
        f"上传者自审自己的文件成功 (status={approve_response.status_code})"
    )


@pytest.mark.parametrize("candidate_role", ["system_admin", "dept_admin"])
async def test_unverified_reviewer_does_not_block_deadlock_exemption(
    redteam_client: AsyncClient,
    candidate_role: str,
) -> None:
    del redteam_client  # fixture owns isolated database lifecycle
    from app.core.database import AsyncSessionFactory
    from app.modules.department.identity import SqlDepartmentScopeStore

    department_id = await _create_department(
        name=f"Unverified {candidate_role}",
        code=f"unverified-{candidate_role}",
    )
    uploader_id = await _create_user(
        email=f"unverified-uploader-{candidate_role}@company.com",
        role="system_admin",
        department_id=department_id,
    )
    unverified_id = await _create_user(
        email=f"unverified-candidate-{candidate_role}@company.com",
        role=candidate_role,
        department_id=department_id,
        email_verified=False,
    )
    if candidate_role == "dept_admin":
        await _assign_managed_departments(
            user_id=unverified_id,
            department_ids=[department_id],
        )

    async with AsyncSessionFactory() as session:
        store = SqlDepartmentScopeStore(session)
        assert not await store.has_non_self_reviewer(
            file_department_id=department_id,
            uploader_id=uploader_id,
        )

    verified_id = await _create_user(
        email=f"verified-candidate-{candidate_role}@company.com",
        role=candidate_role,
        department_id=department_id,
        email_verified=True,
    )
    if candidate_role == "dept_admin":
        await _assign_managed_departments(
            user_id=verified_id,
            department_ids=[department_id],
        )
    async with AsyncSessionFactory() as session:
        store = SqlDepartmentScopeStore(session)
        assert await store.has_non_self_reviewer(
            file_department_id=department_id,
            uploader_id=uploader_id,
        )


async def test_self_approval_deadlock_exemption_audits_when_genuinely_alone(
    redteam_client: AsyncClient,
) -> None:
    """防御验证: 真·死锁场景 (唯一 system_admin 上传到无 dept_admin 的部门) 放行,
    且必须写审计 metadata self_approval_deadlock_exempt=true。

    这是隔离正确性的另一面: 豁免不能既不放行又不可审计。跑红可能意味着:
    (a) 该放行却被拦(死锁), 或 (b) 放行但漏写豁免审计标记(审计缺失)。
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    lonely_dept = await _create_department(name="Lonely Dept", code="lonely-dept")
    _category_id, dataset_id, _ = await _create_category_and_dataset()
    # 全系统唯一 system_admin, 且该部门无 dept_admin -> 真死锁
    uploader_admin = await _create_user(
        email="lonely-admin@company.com",
        role="system_admin",
        department_id=lonely_dept,
    )
    token = await _login(redteam_client, email="lonely-admin@company.com")
    file_id = await _create_file(
        uploader_id=uploader_admin,
        department_id=lonely_dept,
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-lonely",
    )

    unclaimed_response = await redteam_client.post(
        f"/api/files/{file_id}/approve",
        headers=_auth(token),
        json={"sync_decision": "approve_only"},
    )
    claim_response = await redteam_client.post(
        f"/api/review/files/{file_id}/claim",
        headers=_auth(token),
    )
    approve_response = await redteam_client.post(
        f"/api/files/{file_id}/approve",
        headers=_auth(token),
        json={"sync_decision": "approve_only"},
    )

    assert unclaimed_response.status_code == 409
    assert unclaimed_response.json()["error_code"] == "REVIEW_CLAIM_REQUIRED"
    assert claim_response.status_code == 200
    assert claim_response.json()["data"]["claimed_by"] == str(uploader_admin)
    assert approve_response.status_code == 200, (
        f"死锁豁免应放行唯一 system_admin 的自审, 实际 {approve_response.status_code}: "
        f"{approve_response.text}"
    )
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.actor_id == uploader_admin,
                AuditLog.action == "file.approve",
            )
        )
        approve_logs = list(result.scalars())
    assert approve_logs, "死锁豁免自审未写 file.approve 审计"
    assert any(
        (log.metadata_json or {}).get("self_review_deadlock_exempt") is True for log in approve_logs
    ), "死锁豁免自审审计缺少 self_review_deadlock_exempt=true 标记 (审计红线)"


# ---------------------------------------------------------------------------
# 攻击 5: 跨部门写操作 (单条 PATCH / 多管辖部门组合)
# ---------------------------------------------------------------------------


async def test_dept_admin_cannot_patch_classification_across_department(
    redteam_client: AsyncClient,
) -> None:
    """攻击: 管辖 A+B 两部门的 dept_admin 对 C 部门文件打 PATCH /api/files/{id} 改分类。

    已有测试只验了 approve / retry 的跨部门越权, 未验 update_file_classification (PATCH)
    这条写路径。若 list 拦了而单条写漏拦, 攻击者可借改分类把他部门文件挂到任意 dataset。

    跑红(改分类成功 200) = 跨部门写越权。跑绿(403) = 单条写也按部门约束 (保留为防回归)。
    """
    dept_a = await _create_department(name="Multi A", code="multi-a")
    dept_b = await _create_department(name="Multi B", code="multi-b")
    dept_c = await _create_department(name="Multi C", code="multi-c")
    victim_owner = await _create_user(email="multi-c-owner@company.com", department_id=dept_c)
    admin_id = await _create_user(email="multi-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[dept_a, dept_b])
    token = await _login(redteam_client, email="multi-admin@company.com")
    category_id, dataset_id, _ = await _create_category_and_dataset()

    victim_file = await _create_file(uploader_id=victim_owner, department_id=dept_c)

    patch_response = await redteam_client.patch(
        f"/api/files/{victim_file}",
        headers=_auth(token),
        json={"category_id": str(category_id), "dataset_mapping_id": str(dataset_id)},
    )

    assert patch_response.status_code in (403, 404), (
        "跨部门写越权: 管辖 A+B 的 dept_admin 改了 C 部门文件的分类 "
        f"(status={patch_response.status_code})"
    )


async def test_dept_admin_cannot_manual_sync_across_department(
    redteam_client: AsyncClient,
) -> None:
    """攻击: dept_admin 对非管辖部门的已审批文件打手动同步 /api/admin/files/{id}/sync。

    手动同步是把内容推进公司库的写动作。已有测试覆盖了 retry, 未单独覆盖 manual_sync 的跨部门 403。

    跑红(同步被受理) = 跨部门同步越权。跑绿(403) = manual_sync 按部门约束 (保留为防回归)。
    """
    finance_id = await _create_department(name="Sync Finance", code="sync-finance")
    legal_id = await _create_department(name="Sync Legal", code="sync-legal")
    legal_owner = await _create_user(email="sync-legal-owner@company.com", department_id=legal_id)
    admin_id = await _create_user(email="sync-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[finance_id])
    token = await _login(redteam_client, email="sync-admin@company.com")
    _, dataset_id, ragflow_dataset_id = await _create_category_and_dataset()

    # legal 部门一个已审批、可手动同步的文件
    legal_file = await _create_file(
        uploader_id=legal_owner,
        department_id=legal_id,
        status="approved",
        review_status="approved",
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id=ragflow_dataset_id,
    )

    sync_response = await redteam_client.post(
        f"/api/admin/files/{legal_file}/sync",
        headers=_auth(token),
        json={"dataset_mapping_id": str(dataset_id)},
    )

    assert sync_response.status_code in (403, 404), (
        "跨部门同步越权: dept_admin 对非管辖 legal 部门文件触发了手动同步 "
        f"(status={sync_response.status_code})"
    )
