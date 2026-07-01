# 修复普通用户功能可用性问题 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复三个影响普通用户（employee 角色）功能可用性的问题：上传配置 API 权限、删除按钮误展示、AI 分析详情字段缺失。

**Architecture:** 新增一个不需要管理员权限的公开上传策略端点 `GET /api/upload-policy`，直接通过 `runtime_config` 读取配置，绕过管理员 config 模块。前端上传页和我的文件页改为调用此端点。后端 `FileAnalysisDetail` schema 和 repository 补全已存在于 `DocumentAnalysis` 模型中的字段。前端删除按钮根据上传策略中新增的 `allow_user_delete` 字段条件展示。

**Tech Stack:** Python / FastAPI / SQLAlchemy (backend), TypeScript / React / Ant Design / TanStack Query (frontend), pytest-asyncio (tests)

## Global Constraints

- Python 行宽 100，字符串统一双引号，所有 public 函数必须完整类型注解
- 异步：API 路由、Service、Repository 全 `async def`
- 禁止跨模块 service/repository import，只能跨模块引用 schemas
- 前端所有 API 调用走 `api/client.ts`
- 提交格式：`type(scope): 中文描述`，不带 trailer
- 测试不依赖外网

---

### Task 1: 新增公开上传策略端点 `GET /api/upload-policy`

**Files:**
- Modify: `backend/app/modules/document/api.py` (新增路由)
- Modify: `backend/app/modules/document/schemas.py` (新增 UploadPolicyResponse)
- Create: `backend/app/tests/unit/test_upload_policy_api.py`

**Interfaces:**
- Consumes: `app.core.runtime_config.get_config(key)` — 读取 `upload.*` 配置
- Consumes: `app.core.deps.get_current_user` — 任何已认证用户
- Produces: `GET /api/upload-policy` → `UploadPolicyResponse(allowed_extensions, allow_multi_file, upload_enabled, max_file_size_mb, allow_user_delete)`

- [ ] **Step 1: 在 schemas.py 中新增 UploadPolicyResponse**

在 `backend/app/modules/document/schemas.py` 文件末尾追加：

```python
class UploadPolicyResponse(BaseModel):
    allowed_extensions: list[str]
    allow_multi_file: bool
    upload_enabled: bool
    max_file_size_mb: int
    allow_user_delete: bool
```

- [ ] **Step 2: 写失败测试 — 普通用户能访问上传策略**

创建 `backend/app/tests/unit/test_upload_policy_api.py`：

```python
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"startxref\n9\n%%EOF\n"
)


async def _reset_database() -> None:
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def policy_client() -> AsyncGenerator[AsyncClient, None]:
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
        upload_max_file_size_bytes=10 * 1024 * 1024,
        upload_rate_limit_per_minute=20,
        upload_allowed_extensions="pdf,docx,txt",
        upload_allowed_mime_types="application/pdf,text/plain",
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


async def _create_user(*, email: str, password: str, role: str = "employee") -> None:
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


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def test_employee_can_access_upload_policy(policy_client: AsyncClient) -> None:
    await _create_user(email="employee@company.com", password="password123")
    token = await _login(policy_client, email="employee@company.com", password="password123")

    response = await policy_client.get(
        "/api/upload-policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["allowed_extensions"], list)
    assert len(data["allowed_extensions"]) > 0
    assert isinstance(data["allow_multi_file"], bool)
    assert isinstance(data["upload_enabled"], bool)
    assert isinstance(data["max_file_size_mb"], int)
    assert isinstance(data["allow_user_delete"], bool)


async def test_upload_policy_returns_env_fallback_extensions(policy_client: AsyncClient) -> None:
    await _create_user(email="employee2@company.com", password="password123")
    token = await _login(policy_client, email="employee2@company.com", password="password123")

    response = await policy_client.get(
        "/api/upload-policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "pdf" in data["allowed_extensions"]
    assert "docx" in data["allowed_extensions"]
    assert "txt" in data["allowed_extensions"]


async def test_upload_policy_requires_auth(policy_client: AsyncClient) -> None:
    response = await policy_client.get("/api/upload-policy")
    assert response.status_code == 401
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd backend && python -m pytest app/tests/unit/test_upload_policy_api.py -v -x
```

预期：FAIL — 404（路由不存在）

- [ ] **Step 4: 在 document/api.py 中实现 upload-policy 端点**

在 `backend/app/modules/document/api.py` 文件中，在现有 import 区域末尾追加 runtime_config import，并在 `router` 的路由定义区域（`@router.post("/upload", ...)` 之前）添加新路由：

在文件顶部的 import 区域追加：
```python
from app.core.runtime_config import get_config
```

在 `_raise_rate_limited` 函数之前添加新路由：

```python
@router.get("/upload-policy")
async def get_upload_policy(
    request: Request,
    current_user: CurrentUserDep,
) -> dict[str, object]:
    allowed_extensions = await get_config("upload.allowed_extensions")
    if not isinstance(allowed_extensions, list):
        allowed_extensions = []
    allow_multi_file = await get_config("upload.allow_multi_file")
    upload_enabled = await get_config("upload.enabled")
    if upload_enabled is None:
        upload_enabled = await get_config("upload.enable_upload")
    max_file_size_mb = await get_config("upload.max_file_size_mb")
    allow_user_delete = await get_config("upload.allow_user_delete")
    from .schemas import UploadPolicyResponse

    response = UploadPolicyResponse(
        allowed_extensions=allowed_extensions if isinstance(allowed_extensions, list) else [],
        allow_multi_file=allow_multi_file is not False,
        upload_enabled=upload_enabled is not False,
        max_file_size_mb=max_file_size_mb if isinstance(max_file_size_mb, int) else 50,
        allow_user_delete=allow_user_delete is True,
    )
    return success_response(response.model_dump(mode="json"), request)
```

- [ ] **Step 5: 运行测试确认全部通过**

```bash
cd backend && python -m pytest app/tests/unit/test_upload_policy_api.py -v -x
```

预期：3 个测试全部 PASS

- [ ] **Step 6: 提交**

```bash
git add backend/app/modules/document/api.py backend/app/modules/document/schemas.py backend/app/tests/unit/test_upload_policy_api.py
git commit -m "feat(document): 新增公开上传策略端点 GET /api/upload-policy"
```

---

### Task 2: 前端改用 upload-policy 端点，删除按钮按策略条件展示

**Files:**
- Modify: `frontend/src/api/client.ts` (新增 getUploadPolicy 函数)
- Modify: `frontend/src/utils/uploadConfig.ts` (新增从 policy 提取配置的函数)
- Modify: `frontend/src/pages/Upload/index.tsx` (改用 getUploadPolicy)
- Modify: `frontend/src/pages/MyFiles/index.tsx` (改用 getUploadPolicy + 条件展示删除按钮)
- Modify: `frontend/src/pages/FileManagement/index.tsx` (改用 getUploadPolicy)
- Modify: `frontend/src/pages/Upload/index.test.tsx` (更新 mock)
- Modify: `frontend/src/pages/MyFiles/index.test.tsx` (更新 mock)
- Modify: `frontend/src/pages/FileManagement/index.test.tsx` (更新 mock)

**Interfaces:**
- Consumes: `GET /api/upload-policy` → Task 1 产出的端点
- Produces: `getUploadPolicy()` 函数供所有上传相关页面使用
- Produces: `UploadPolicy` 类型定义
- Produces: `allowedExtensionsFromPolicy(policy)` / `allowMultiFileFromPolicy(policy)` / `uploadEnabledFromPolicy(policy)` / `allowUserDeleteFromPolicy(policy)`

- [ ] **Step 1: 在 api/client.ts 中新增 UploadPolicy 类型和 getUploadPolicy 函数**

在 `frontend/src/api/client.ts` 中 `UploadDocumentPayload` 接口之前追加：

```typescript
export interface UploadPolicy {
  allowed_extensions: string[];
  allow_multi_file: boolean;
  upload_enabled: boolean;
  max_file_size_mb: number;
  allow_user_delete: boolean;
}
```

在 `uploadDocument` 函数之前追加：

```typescript
export async function getUploadPolicy(): Promise<UploadPolicy> {
  const response = await apiClient.get<ApiEnvelope<UploadPolicy> | UploadPolicy>(
    "/upload-policy",
  );

  return unwrapResponse(response.data);
}
```

- [ ] **Step 2: 在 uploadConfig.ts 中新增从 UploadPolicy 提取配置的函数**

在 `frontend/src/utils/uploadConfig.ts` 文件末尾追加：

```typescript
import type { UploadPolicy } from "../api/client";

export function allowedExtensionsFromPolicy(policy: UploadPolicy | undefined): string[] {
  if (!policy || policy.allowed_extensions.length === 0) {
    return DEFAULT_ALLOWED_EXTENSIONS;
  }
  return policy.allowed_extensions;
}

export function allowMultiFileFromPolicy(policy: UploadPolicy | undefined): boolean {
  return policy?.allow_multi_file !== false;
}

export function uploadEnabledFromPolicy(policy: UploadPolicy | undefined): boolean {
  return policy?.upload_enabled !== false;
}

export function allowUserDeleteFromPolicy(policy: UploadPolicy | undefined): boolean {
  return policy?.allow_user_delete === true;
}
```

- [ ] **Step 3: Upload 页面改用 getUploadPolicy**

在 `frontend/src/pages/Upload/index.tsx` 中：

将 import 中的 `getConfigs` 替换为 `getUploadPolicy`：
```typescript
import { type KnowledgeFile, getUploadPolicy, uploadDocument } from "../../api/client";
```

将 import 中的 `uploadConfig` 函数替换：
```typescript
import {
  allowMultiFileFromPolicy,
  allowedExtensionsFromPolicy,
  extensionAcceptValue,
  uploadEnabledFromPolicy,
} from "../../utils/uploadConfig";
```

将 `uploadConfigQuery` 改为：
```typescript
const uploadPolicyQuery = useQuery({
  queryKey: ["upload-policy"],
  queryFn: getUploadPolicy,
});
```

将三个 `useMemo` / 直接调用改为使用 policy 数据：
```typescript
const allowedExtensions = useMemo(
  () => allowedExtensionsFromPolicy(uploadPolicyQuery.data),
  [uploadPolicyQuery.data],
);
const allowMultiFile = allowMultiFileFromPolicy(uploadPolicyQuery.data);
const uploadEnabled = uploadEnabledFromPolicy(uploadPolicyQuery.data);
```

- [ ] **Step 4: MyFiles 页面改用 getUploadPolicy + 条件展示删除按钮**

在 `frontend/src/pages/MyFiles/index.tsx` 中：

将 import 中的 `getConfigs` 替换为 `getUploadPolicy`：
```typescript
import {
  type KnowledgeFile,
  deleteFile,
  getUploadPolicy,
  listDocuments,
  listTags,
  submitFileForReview,
} from "../../api/client";
```

将 `uploadConfig` import 替换：
```typescript
import {
  allowUserDeleteFromPolicy,
  allowedExtensionsFromPolicy,
} from "../../utils/uploadConfig";
```

将 `uploadConfigQuery` 改为：
```typescript
const uploadPolicyQuery = useQuery({
  queryKey: ["upload-policy"],
  queryFn: getUploadPolicy,
});
```

将 `allowedExtensions` 的 `useMemo` 改为：
```typescript
const allowedExtensions = useMemo(
  () => allowedExtensionsFromPolicy(uploadPolicyQuery.data),
  [uploadPolicyQuery.data],
);
```

将 `uploadPolicyStatus` 的判定改为使用 `uploadPolicyQuery`：
```typescript
const uploadPolicyStatus = uploadPolicyQuery.isLoading
  ? "unknown"
  : uploadPolicyQuery.isError
    ? "error"
    : "ok";
```

将 `pageHealthStatus` 改为使用 `uploadPolicyQuery`：
```typescript
const pageHealthStatus =
  filesQuery.isError || tagsQuery.isError || uploadPolicyQuery.isError ? "error" : "ok";
```

新增 `allowUserDelete` 变量：
```typescript
const allowUserDelete = allowUserDeleteFromPolicy(uploadPolicyQuery.data);
```

在 table columns 的操作列中，将删除按钮包裹在条件判断中：
```typescript
{allowUserDelete && (
  <Popconfirm
    title="删除文件"
    description="确认删除该文件？此操作不可撤销。"
    okText="确定"
    cancelText="取消"
    onConfirm={() => deleteMutation.mutate(record.id)}
  >
    <Button
      type="text"
      danger
      icon={<DeleteOutlined />}
      loading={deleteMutation.isPending && deleteMutation.variables === record.id}
      aria-label={`删除 ${record.original_name}`}
    >
      删除
    </Button>
  </Popconfirm>
)}
```

- [ ] **Step 5: FileManagement 页面改用 getUploadPolicy**

在 `frontend/src/pages/FileManagement/index.tsx` 中进行类似的替换：将 `getConfigs` 改为 `getUploadPolicy`，将 `allowedExtensionsFromConfig` 改为 `allowedExtensionsFromPolicy`，将查询改为：

```typescript
const uploadPolicyQuery = useQuery({
  queryKey: ["upload-policy"],
  queryFn: getUploadPolicy,
});
```

- [ ] **Step 6: 更新三个页面的测试文件中的 mock**

在 `frontend/src/pages/Upload/index.test.tsx`、`frontend/src/pages/MyFiles/index.test.tsx`、`frontend/src/pages/FileManagement/index.test.tsx` 中：

将 mock 中的 `getConfigs` 替换为 `getUploadPolicy`，将 mock 返回值从 `ConfigGroupResponse` 格式改为 `UploadPolicy` 格式：

```typescript
// 旧：
vi.mocked(getConfigs).mockResolvedValue(uploadConfigResponse);

// 新：
const uploadPolicy = {
  allowed_extensions: ["pdf", "docx", "txt"],
  allow_multi_file: true,
  upload_enabled: true,
  max_file_size_mb: 50,
  allow_user_delete: false,
};
vi.mocked(getUploadPolicy).mockResolvedValue(uploadPolicy);
```

- [ ] **Step 7: 运行前端测试确认通过**

```bash
cd frontend && npx vitest run --reporter=verbose
```

预期：全部 PASS

- [ ] **Step 8: 提交**

```bash
git add frontend/src/api/client.ts frontend/src/utils/uploadConfig.ts frontend/src/pages/Upload/index.tsx frontend/src/pages/Upload/index.test.tsx frontend/src/pages/MyFiles/index.tsx frontend/src/pages/MyFiles/index.test.tsx frontend/src/pages/FileManagement/index.tsx frontend/src/pages/FileManagement/index.test.tsx
git commit -m "fix(frontend): 改用公开上传策略端点，修复普通用户 403 问题"
```

---

### Task 3: 补全 AI 分析详情字段（quality_score / tables_json / similar_file_ids）

**Files:**
- Modify: `backend/app/modules/document/schemas.py` (FileAnalysisDetail 扩展字段)
- Modify: `backend/app/modules/document/repository.py` (DocumentAnalysisRecord 扩展字段 + 查询扩展)
- Modify: `backend/app/modules/document/service.py` (传递新字段到 schema)
- Modify: `backend/app/tests/unit/test_document_api.py` (新增分析详情字段测试)

**Interfaces:**
- Consumes: `ai.models.DocumentAnalysis` 模型 — 已有 `quality_score`、`tables_json`、`table_count`、`similar_file_ids`、`quality_detail` 列
- Produces: `FileAnalysisDetail` 扩展后包含 `quality_score`、`tables_json`、`table_count`、`similar_file_ids` 字段

- [ ] **Step 1: 写失败测试 — 文件详情返回完整分析字段**

在 `backend/app/tests/unit/test_document_api.py` 文件末尾追加：

```python
async def test_file_detail_returns_analysis_fields(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory

    client, _storage = document_client
    user_id = await _create_user(email="analyst@company.com", password="password123")
    token = await _login(client, email="analyst@company.com", password="password123")

    upload_response = await client.post(
        "/api/files/upload",
        files={"file": ("test.pdf", PDF_BYTES, "application/pdf")},
        data={"visibility": "private"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload_response.status_code == 201
    file_id = upload_response.json()["data"]["id"]

    async with AsyncSessionFactory() as session:
        from app.modules.ai.models import DocumentAnalysis

        analysis = DocumentAnalysis(
            file_id=file_id,
            status="succeeded",
            summary="Test summary",
            sensitive_risk_level="low",
            quality_score=85,
            tables_json=[{"title": "Table 1", "markdown": "| A | B |"}],
            table_count=1,
            similar_file_ids=["some-file-id"],
            extracted_text="Sample extracted text for preview",
        )
        session.add(analysis)
        await session.commit()

    detail_response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 200
    data = detail_response.json()["data"]
    analysis_data = data["analysis"]
    assert analysis_data is not None
    assert analysis_data["quality_score"] == 85
    assert analysis_data["table_count"] == 1
    assert len(analysis_data["tables_json"]) == 1
    assert analysis_data["tables_json"][0]["title"] == "Table 1"
    assert analysis_data["similar_file_ids"] == ["some-file-id"]
    assert analysis_data["summary"] == "Test summary"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest app/tests/unit/test_document_api.py::test_file_detail_returns_analysis_fields -v -x
```

预期：FAIL — KeyError 或 assertion error（字段不在返回中）

- [ ] **Step 3: 扩展 DocumentAnalysisRecord dataclass**

在 `backend/app/modules/document/repository.py` 的 `DocumentAnalysisRecord` dataclass 中追加字段：

```python
@dataclass(frozen=True)
class DocumentAnalysisRecord:
    status: str
    summary: str | None
    sensitive_risk_level: str
    extracted_text: str | None
    error_message: str | None
    finished_at: datetime | None
    quality_score: int | None
    tables_json: list[dict[str, object]]
    table_count: int
    similar_file_ids: list[str]
```

- [ ] **Step 4: 扩展 repository 查询，select 新增列**

在 `backend/app/modules/document/repository.py` 的 `get_analysis_for_file` 方法中，修改 select 和 return：

```python
async def get_analysis_for_file(self, file_id: uuid.UUID) -> DocumentAnalysisRecord | None:
    result = await self._session.execute(
        select(
            DOCUMENT_ANALYSIS.c.status,
            DOCUMENT_ANALYSIS.c.summary,
            DOCUMENT_ANALYSIS.c.sensitive_risk_level,
            DOCUMENT_ANALYSIS.c.extracted_text,
            DOCUMENT_ANALYSIS.c.error_message,
            DOCUMENT_ANALYSIS.c.finished_at,
            DOCUMENT_ANALYSIS.c.quality_score,
            DOCUMENT_ANALYSIS.c.tables_json,
            DOCUMENT_ANALYSIS.c.table_count,
            DOCUMENT_ANALYSIS.c.similar_file_ids,
        ).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return DocumentAnalysisRecord(
        status=cast(str, row["status"]),
        summary=cast(str | None, row["summary"]),
        sensitive_risk_level=cast(str, row["sensitive_risk_level"]),
        extracted_text=cast(str | None, row["extracted_text"]),
        error_message=cast(str | None, row["error_message"]),
        finished_at=cast(datetime | None, row["finished_at"]),
        quality_score=cast(int | None, row["quality_score"]),
        tables_json=cast(list[dict[str, object]], row["tables_json"]),
        table_count=cast(int, row["table_count"]),
        similar_file_ids=cast(list[str], row["similar_file_ids"]),
    )
```

- [ ] **Step 5: 扩展 FileAnalysisDetail schema**

在 `backend/app/modules/document/schemas.py` 的 `FileAnalysisDetail` 中追加字段：

```python
class FileAnalysisDetail(BaseModel):
    status: str
    summary: str | None
    sensitive_risk_level: str
    quality_score: float | None = None
    extracted_text_preview: str | None
    tables_json: list[dict[str, object]] = []
    table_count: int = 0
    similar_file_ids: list[str] = []
    error_message: str | None
    finished_at: datetime | None
```

- [ ] **Step 6: 在 service 层传递新字段**

在 `backend/app/modules/document/service.py` 的 `get_file_detail` 方法中，修改 `FileAnalysisDetail` 构造：

```python
FileAnalysisDetail(
    status=analysis.status,
    summary=analysis.summary,
    sensitive_risk_level=analysis.sensitive_risk_level,
    quality_score=analysis.quality_score,
    extracted_text_preview=(
        analysis.extracted_text[:EXTRACTED_TEXT_PREVIEW_CHARS]
        if analysis.extracted_text is not None
        else None
    ),
    tables_json=analysis.tables_json,
    table_count=analysis.table_count,
    similar_file_ids=analysis.similar_file_ids,
    error_message=analysis.error_message,
    finished_at=analysis.finished_at,
)
```

- [ ] **Step 7: 运行测试确认通过**

```bash
cd backend && python -m pytest app/tests/unit/test_document_api.py::test_file_detail_returns_analysis_fields -v -x
```

预期：PASS

- [ ] **Step 8: 运行全量文档模块测试确认无回归**

```bash
cd backend && python -m pytest app/tests/unit/test_document_api.py -v
```

预期：全部 PASS

- [ ] **Step 9: 提交**

```bash
git add backend/app/modules/document/schemas.py backend/app/modules/document/repository.py backend/app/modules/document/service.py backend/app/tests/unit/test_document_api.py
git commit -m "fix(document): 补全文件详情 AI 分析字段（quality_score/tables_json/similar_file_ids）"
```

---

### Task 4: 运行全量测试确认无回归

**Files:** 无新增/修改

**Interfaces:**
- Consumes: Task 1-3 的所有变更

- [ ] **Step 1: 运行后端全量测试**

```bash
cd backend && python -m pytest app/tests/ -v --timeout=120
```

预期：全部 PASS

- [ ] **Step 2: 运行前端全量测试**

```bash
cd frontend && npx vitest run --reporter=verbose
```

预期：全部 PASS

- [ ] **Step 3: 运行后端 lint**

```bash
cd backend && ruff check . && ruff format --check .
```

预期：无错误

- [ ] **Step 4: 运行前端 lint**

```bash
cd frontend && npx tsc --noEmit
```

预期：无错误

- [ ] **Step 5: 如有 lint 或类型问题则修复并提交**

```bash
git add -A
git commit -m "fix: 修复 lint 与类型检查问题"
```
