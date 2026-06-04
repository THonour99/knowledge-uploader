# P0 实施补充 Spec

> **本文档不替代任何已有文档，仅补足 `knowledge_uploader_docs/` 10 份文档的 4 个明显缺口与 4 个内部不一致。**

- 文档版本：v1.0
- 日期：2026-06-04
- 项目代号：Knowledge Uploader
- 上游文档：`knowledge_uploader_docs/01` ~ `knowledge_uploader_docs/10`
- 触发原因：用户明确"本机 Windows 开发 → DGX Spark (ARM64) 部署"的跨架构约束，原文档未覆盖
- 适用范围：项目实施前（阶段 0）必读；阶段 0~9 全程引用

---

## 0. 文档关系图

```text
┌──────────────────────────────────────────────────────┐
│   knowledge_uploader_docs/  (上游 10 份, v1.0)        │
│   ├── 01 PRD                                          │
│   ├── 02 ARCHITECTURE          ← 架构最终版本           │
│   ├── 03 BACKEND_SPEC          ← 后端模块规范           │
│   ├── 04 FRONTEND_SPEC                                │
│   ├── 05 DATABASE_API_SPEC                           │
│   ├── 06 AI_RAGFLOW_SPEC                             │
│   ├── 07 DEPLOYMENT_ENV                              │
│   ├── 08 TASK_BREAKDOWN        ← 9 阶段任务清单         │
│   ├── 09 CLAUDE_CODE_PROMPT                          │
│   └── 10 CODEX_IMPLEMENTATION_PROMPT                 │
└──────────────────────────────────────────────────────┘
                          ↑
                          │ 补充而非替代
                          │
┌──────────────────────────────────────────────────────┐
│   knowledge_platform_design_package/  (UI 权威源)     │
│   ├── design.md                ← 视觉/布局/组件/路由     │
│   └── images/                  ← 12 张高保真原稿        │
│         01_dashboard.png ~ 12_system_settings.png    │
└──────────────────────────────────────────────────────┘
                          ↑
                          │ 前端实现的视觉权威源
                          │
┌──────────────────────────────────────────────────────┐
│   docs/spark/2026-06-04-p0-implementation-supplement │
│   (本文档, v1.1)                                       │
│   ├── §2 跨平台 + 跨架构硬约束 ← 缺口 1                  │
│   ├── §3 域事件总线设计          ← 缺口 2                │
│   ├── §4 模块文件级目录结构      ← 缺口 3                │
│   ├── §5 CLAUDE.md 内容草案     ← 缺口 4                │
│   ├── §6 技术栈版本锁           ← 缺口 5                │
│   ├── §7 上游文档不一致修正建议                          │
│   ├── §8 阶段 0 启动检查表                              │
│   └── §9 前端设计实现指南        ← 新增 (整合 design 包)  │
└──────────────────────────────────────────────────────┘
```

阅读优先级：先读 02 → 03 → 07 → 08，再读 design 包 design.md，再读本补充 spec 的 §2 ~ §9。

---

## 1. 决策摘要（变更与新增）

| 决策项 | 上游文档 | 本补充 | 备注 |
|---|---|---|---|
| 数据库 | PostgreSQL | PostgreSQL **16** | 锁版本 |
| Python | 未指定 | **3.11.x** | ARM64 wheel 覆盖最稳 |
| Node | 未指定 | **20 LTS** | |
| psycopg | 隐含 | **psycopg v3 (binary)** | 替代 psycopg2，ARM64 原生 wheel |
| 文件类型检测 | 未指定 | **filetype + 扩展名白名单** | 避开 libmagic 的 Windows 兼容麻烦 |
| 任务脚本 | 未指定 | **invoke (Python)** | 不用 Makefile，跨平台 |
| 镜像构建 | Docker Compose | **buildx + multi-arch** | 输出 `linux/amd64` + `linux/arm64` manifest |
| 本机 Docker | 未指定 | **Docker Desktop**（推荐）<br>WSL2 + Docker Engine（备选） | |
| 事件总线实现 | 02 提"事件驱动"但无细节 | **Outbox Pattern + RabbitMQ Topic Exchange** | 详见 §3 |
| 模块间通信 | 03 提模块边界但无约束 | **禁止跨模块直接 import service**；只能走事件总线/共享 schemas/core | 详见 §4 |
| 行尾 | 未指定 | **全仓库 LF**，通过 `.gitattributes` 强制 | |
| 编码 | 未指定 | **PYTHONUTF8=1**，全仓库 UTF-8 无 BOM | |
| 工程目录 | `knowledge_uploader_docs/` | 项目根目录改名 **`knowledge_uploader/`**（详见 §4.1） | |
| UI 权威源 | 04_FRONTEND_SPEC（文字描述） | `knowledge_platform_design_package/`（高保真原稿 + design.md） | 视觉/布局/组件/路由以 design 包为准；详见 §9 |
| 前端页面数 | 04_FRONTEND_SPEC 16 个 | design.md **12 个**（AI 配置子页合并、统计合并、ForgotPassword/ResetPassword 暂列后续） | 与 design.md 对齐；详见 §7.5 |
| 设计色板 | 未指定 | **#1677FF 主色** + Ant Design 5 默认色系（design.md §2.2） | 锁定 |
| 前端组件库 | Ant Design | Ant Design **5 + @ant-design/pro-components**（design 包的 KPI 卡 / 表格 / 表单基于 Pro 组件） | |
| 设计包位置 | — | 阶段 0 实施时 `mv knowledge_platform_design_package/ knowledge_uploader/docs/design/` | |

**不变更的部分**（上游文档已确定）：
- 模块化单体 + 多 Worker 容器部署
- 11 个容器服务（02 §4 + 07 §2）
- RabbitMQ broker + Redis result/cache/lock
- MinIO 对象存储
- 17 个文件状态（05 §2）
- AI Provider 插件架构（06 §2）
- 9 个开发阶段（08）
- 整体布局：左侧导航 + 顶部工具栏 + 主内容（design.md §4.1）
- 卡片样式：圆角 12px + 白底 + 轻阴影 + #E5EAF2 边框（design.md §4.3）

---

## 2. 跨平台 + 跨架构硬约束 [缺口 1]

### 2.1 环境矩阵

| 维度 | 本机开发 | 生产部署 |
|---|---|---|
| OS | Windows 11 | Linux (DGX OS, Ubuntu 22.04 / 24.04) |
| 架构 | x86_64 (amd64) | aarch64 (arm64) |
| Docker | Docker Desktop 4.x **或** WSL2 + Docker Engine | Docker 24+ |
| Shell | PowerShell 7 / Git Bash | Bash |
| 文件系统 | NTFS（大小写不敏感） | ext4（大小写敏感） |
| 路径分隔符 | `\` 或 `/` | `/` |
| 默认编码 | GBK（中文 Windows） | UTF-8 |
| 行尾默认 | CRLF | LF |
| 内核 | NT | Linux + DGX 工具链 |
| GPU 可用 | 否（本机无 CUDA） | 是（Grace Blackwell） |

### 2.2 红线规则（强制）

1. **代码层禁止使用 OS 特定 API**
   - ❌ `os.path.join("a", "b")` → ✅ `Path("a") / "b"`
   - ❌ `open(path)` 不指定 encoding → ✅ `open(path, encoding="utf-8")`
   - ❌ `subprocess.run(["bash", ...])` → ✅ 用 Python 跨平台等价物
   - ❌ 在代码里写死 `/tmp/` → ✅ 用 `tempfile.gettempdir()` 或环境变量

2. **路径分隔符**
   - 所有路径用 `pathlib.Path`
   - 不在代码中拼接路径字符串
   - lint 规则：`ruff` 启用 `PTH`（pathlib）规则集

3. **文件名清洗**（上传文件名进入存储/数据库前必经）
   - 长度上限 200 字符
   - 禁止字符：`<>:"/\|?*` 以及控制字符 `0x00-0x1F`
   - 禁止 Windows 保留名：`CON, PRN, AUX, NUL, COM0-9, LPT0-9`（即使部署在 Linux 也要禁用，保留跨环境一致性）
   - 禁止以 `.` 开头或 ` ` 结尾
   - 实现位置：`backend/app/utils/filename.py`

4. **换行符**
   - `.gitattributes` 强制全仓库 LF（详见 §2.4）
   - `.editorconfig` 统一行尾、缩进、字符集

5. **编码**
   - Python 进程启动设置 `PYTHONUTF8=1`（Dockerfile、本机启动脚本）
   - 所有源文件 UTF-8 无 BOM
   - 数据库连接 `client_encoding=UTF8`
   - HTTP 响应统一 `charset=utf-8`

6. **依赖必须有 ARM64 wheel**
   - 引入新依赖前必须验证 `manylinux*_aarch64` wheel 存在
   - CI 提供自动化检查（详见 §2.6）

### 2.3 镜像 multi-arch 构建策略

#### 2.3.1 Dockerfile 模板

所有 Dockerfile 必须使用 `--platform=$BUILDPLATFORM`，避免在 ARM64 构建时再被 QEMU 仿真：

```dockerfile
# syntax=docker/dockerfile:1.6
FROM --platform=$BUILDPLATFORM python:3.11-slim-bookworm AS builder

ARG TARGETPLATFORM
ARG BUILDPLATFORM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ---- runtime stage ----
FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONUTF8=1 \
    PATH=/root/.local/bin:$PATH \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

COPY --from=builder /root/.local /root/.local
COPY . /app
WORKDIR /app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

关键点：
- `--platform=$BUILDPLATFORM` 让依赖安装走构建平台（amd64），避免 ARM64 模拟编译
- 运行时镜像（runtime stage）拉的是 `python:3.11-slim-bookworm`，buildx 会按目标架构选 amd64 或 arm64 manifest
- `PYTHONUTF8=1` 强制 Python 默认 UTF-8

#### 2.3.2 buildx 构建命令

本机开发（仅 amd64）：

```powershell
docker buildx build --platform linux/amd64 -t knowledge-backend:dev -f backend/Dockerfile backend/ --load
```

CI / 发布（multi-arch manifest）：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t registry.company.com/knowledge-backend:$VERSION \
  -f backend/Dockerfile backend/ \
  --push
```

#### 2.3.3 docker-compose 双套

- `docker-compose.yml`：本机开发默认（不指定 platform，跟随宿主机 amd64）
- `docker-compose.arm64.yml`：生产参考模板（`platform: linux/arm64`，挂载路径用 Linux 格式）

本机启动：
```powershell
docker compose up
```

DGX 启动：
```bash
docker compose -f docker-compose.yml -f docker-compose.arm64.yml up
```

### 2.4 `.gitattributes` 与 `.editorconfig`

仓库根 `.gitattributes`：

```gitattributes
* text=auto eol=lf

*.py    text eol=lf
*.pyi   text eol=lf
*.md    text eol=lf
*.yml   text eol=lf
*.yaml  text eol=lf
*.json  text eol=lf
*.toml  text eol=lf
*.ini   text eol=lf
*.cfg   text eol=lf
*.sh    text eol=lf
*.ts    text eol=lf
*.tsx   text eol=lf
*.js    text eol=lf
*.jsx   text eol=lf
*.css   text eol=lf
*.html  text eol=lf
*.sql   text eol=lf

# PowerShell 留 CRLF（Windows 上 ISE 兼容）
*.ps1   text eol=crlf

# 二进制
*.png   binary
*.jpg   binary
*.jpeg  binary
*.gif   binary
*.ico   binary
*.pdf   binary
*.woff  binary
*.woff2 binary
```

仓库根 `.editorconfig`：

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.{ts,tsx,js,jsx,json,yml,yaml,md,html,css}]
indent_size = 2

[*.{ps1,bat}]
end_of_line = crlf

[Makefile]
indent_style = tab
```

### 2.5 Python 依赖 ARM64 兼容性清单

#### 2.5.1 已验证 ARM64 原生 wheel（直接用）

| 依赖 | 版本 | 备注 |
|---|---|---|
| `fastapi` | ≥0.110 | 纯 Python |
| `uvicorn[standard]` | ≥0.27 | |
| `sqlalchemy` | ≥2.0 | |
| `asyncpg` | ≥0.29 | C 扩展，有 `manylinux2014_aarch64` wheel |
| `psycopg[binary]` | ≥3.1 | **代替 psycopg2**，原生支持 ARM64 |
| `alembic` | ≥1.13 | |
| `celery` | ≥5.3 | |
| `kombu` | ≥5.3 | |
| `pika` | ≥1.3 | RabbitMQ 直连客户端，备用 |
| `redis` | ≥5.0 | |
| `minio` | ≥7.2 | |
| `httpx[http2]` | ≥0.27 | |
| `pydantic` | ≥2.6 | C 扩展 pydantic-core，ARM64 wheel ✓ |
| `pydantic-settings` | ≥2.2 | |
| `argon2-cffi` | ≥23.1 | |
| `PyJWT[crypto]` | ≥2.8 | |
| `cryptography` | ≥42 | |
| `structlog` | ≥24.1 | |
| `filetype` | ≥1.2 | 纯 Python，**代替 python-magic** |
| `tiktoken` | ≥0.6 | Rust 扩展，ARM64 wheel ✓ |
| `email-validator` | ≥2.1 | |
| `slowapi` | ≥0.1.9 | |
| `tenacity` | ≥8.2 | 重试库 |
| `invoke` | ≥2.2 | 任务脚本 |

#### 2.5.2 禁用清单（已知 ARM64 麻烦）

| 依赖 | 问题 | 替代 |
|---|---|---|
| `psycopg2` / `psycopg2-binary` | 旧版 C 扩展，ARM64 编译麻烦 | `psycopg[binary]` v3 |
| `python-magic` | 依赖 libmagic 系统库，Windows 安装繁琐 | `filetype`（纯 Python）|
| `python-magic-bin` | Windows 专用 wheel，Linux 没意义 | `filetype` |
| `mysqlclient` | C 扩展 + libmysqlclient，跨平台坑 | 项目用 PG 不用 MySQL |
| `pycrypto` | 已弃用，不再维护 | `cryptography` |
| `m2crypto` | OpenSSL 绑定问题 | `cryptography` |

#### 2.5.3 二期/三期才会引入的，提前注意

| 依赖 | 风险 | 说明 |
|---|---|---|
| `unstructured` | OCR/文档抽取，ARM64 wheel 部分缺失 | P4 阶段重新评估 |
| `paddleocr` / `paddlepaddle` | ARM64 支持有限 | P4 阶段考虑 RapidOCR 或调用 RAGFlow 内置 |
| `pdfplumber` / `pypdf` | 纯 Python ✓ | 优先用这两个 |
| `openpyxl` / `python-docx` / `python-pptx` | 纯 Python ✓ | |
| `numpy` | ARM64 wheel ✓ | 但版本必须 ≥1.24 |
| `pandas` | ARM64 wheel ✓ | 仅在统计聚合用 |
| `scikit-learn` | ARM64 wheel ✓ | 相似度检测如启用 |

### 2.6 ARM64 wheel CI 自动化检查

新增 `scripts/check_arm64_wheels.py`，由 CI 在依赖变更时调用：

```python
"""
扫描 requirements*.txt，对每个依赖查 PyPI 是否有 manylinux*_aarch64 或
musllinux*_aarch64 wheel。无 wheel 的报错并退出 1。

用法:
    python scripts/check_arm64_wheels.py requirements.txt
"""
from __future__ import annotations
import sys
import re
from pathlib import Path
import urllib.request
import json

PYPI_JSON = "https://pypi.org/pypi/{pkg}/{ver}/json"

ARM64_TAG_PATTERNS = [
    re.compile(r"manylinux.*_aarch64"),
    re.compile(r"musllinux.*_aarch64"),
    re.compile(r"linux_aarch64"),
    re.compile(r"any\.whl$"),       # 纯 Python wheel
    re.compile(r"py3-none-any"),    # 纯 Python
]

def has_arm64_wheel(pkg: str, ver: str) -> tuple[bool, str]:
    url = PYPI_JSON.format(pkg=pkg, ver=ver)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        return False, f"PyPI 查询失败: {e}"
    files = data.get("urls", [])
    for f in files:
        filename = f.get("filename", "")
        if any(p.search(filename) for p in ARM64_TAG_PATTERNS):
            return True, filename
    # 没有 wheel 但有 sdist 也算 ok（能源码编译），加 warning
    has_sdist = any(f.get("packagetype") == "sdist" for f in files)
    if has_sdist:
        return True, "sdist (源码编译, 可能慢)"
    return False, "无 ARM64 wheel 且无 sdist"

def parse_requirements(path: Path) -> list[tuple[str, str | None]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # 简化: 仅处理 pkg==ver 或 pkg>=ver
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)\s*([=<>!~]=?)?\s*([0-9A-Za-z.\-+]+)?", line)
        if not m:
            continue
        out.append((m.group(1).split("[")[0], m.group(3)))
    return out

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: check_arm64_wheels.py <requirements.txt> [...]")
        return 2
    failed = []
    for fp in argv[1:]:
        deps = parse_requirements(Path(fp))
        print(f"\n=== {fp} ({len(deps)} packages) ===")
        for pkg, ver in deps:
            if ver is None:
                print(f"  ! {pkg}: 无版本锁，跳过")
                continue
            ok, why = has_arm64_wheel(pkg, ver)
            mark = "✓" if ok else "✗"
            print(f"  {mark} {pkg}=={ver}: {why}")
            if not ok:
                failed.append(f"{pkg}=={ver}")
    if failed:
        print(f"\n失败 ({len(failed)}):")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("\n全部依赖 ARM64 兼容 ✓")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

CI 集成（GitHub Actions 片段）：

```yaml
- name: Check ARM64 wheel coverage
  run: |
    python scripts/check_arm64_wheels.py \
      backend/requirements.txt \
      backend/requirements-dev.txt
```

### 2.7 本机 Docker 方案选择

**首选：Docker Desktop for Windows**
- 装好就能用 `docker compose up`
- buildx 已内置（含 QEMU 仿真，能构建 ARM64 镜像）
- 公司若 ≤250 员工免授权费；超过需检查 Docker Business 授权状态

**备选：WSL2 + Docker Engine**
- 适合避开 Docker Desktop 授权
- 必须将代码 clone 到 WSL2 文件系统内（如 `~/projects/`），**禁止放在 `/mnt/e/`** —— 跨文件系统 I/O 慢 10-30 倍
- 启动方式：在 WSL2 中执行 `docker compose up`
- 编辑器：VS Code Remote-WSL 扩展

**强烈不推荐：本机 Python venv + 本地 PG/Redis/RabbitMQ**
- Redis 无 Windows 官方版本
- Celery 在 Windows 上必须 `--pool=solo` 或 `--pool=threads`，行为与生产 Linux 不一致
- RabbitMQ 在 Windows 上的 Erlang 安装繁琐

**决策**：默认按 Docker Desktop 文档，若公司授权受限再切 WSL2。

### 2.8 跨平台开发任务脚本

不用 Makefile（Windows 装 GNU Make 麻烦），用 `invoke`（Python 任务执行器，跨 Win/Linux/Mac）。

`tasks.py` 示例：

```python
"""跨平台开发任务. 用法: invoke <task>"""
from invoke import task

@task
def up(c):
    """启动所有容器"""
    c.run("docker compose up -d")

@task
def down(c):
    """停止所有容器"""
    c.run("docker compose down")

@task
def logs(c, service=""):
    """查看日志: invoke logs --service=backend-api"""
    c.run(f"docker compose logs -f {service}")

@task
def migrate(c, msg=""):
    """创建或运行迁移: invoke migrate 运行; invoke migrate --msg='add users' 创建"""
    if msg:
        c.run(f"docker compose exec backend-api alembic revision --autogenerate -m \"{msg}\"")
    else:
        c.run("docker compose exec backend-api alembic upgrade head")

@task
def test(c, k=""):
    """运行后端测试: invoke test 或 invoke test -k 'test_login'"""
    cmd = "docker compose exec backend-api pytest"
    if k:
        cmd += f' -k "{k}"'
    c.run(cmd)

@task
def lint(c):
    """运行 ruff + mypy"""
    c.run("docker compose exec backend-api ruff check .")
    c.run("docker compose exec backend-api mypy app")

@task
def fmt(c):
    """格式化"""
    c.run("docker compose exec backend-api ruff format .")

@task
def check_arm64(c):
    """检查依赖 ARM64 兼容性"""
    c.run("python scripts/check_arm64_wheels.py backend/requirements.txt")

@task
def build_arm64(c, version="dev"):
    """构建 ARM64 镜像"""
    c.run(f"docker buildx build --platform linux/arm64 -t knowledge-backend:{version}-arm64 -f backend/Dockerfile backend/ --load")
```

本机和 DGX 一套脚本通用。

---

## 3. 域事件总线设计 [缺口 2]

### 3.1 事件 vs Celery Task 边界

| 类型 | 适用场景 | 实现 |
|---|---|---|
| **In-process domain event** | 同一事务内强一致；多模块订阅；不涉及外部 I/O | Python 函数派发（事件总线） |
| **Outbox + Celery task** | 事务后异步处理；可重试；涉及外部 I/O | 写 outbox 表 + dispatcher 投递 RabbitMQ + Celery worker 消费 |
| **直接调用 Celery task** | 单一长任务，无多订阅者 fanout 需求 | `task.delay()` 直接投递 |

**判断规则**：
1. 如果**多个模块**对同一业务事件感兴趣 → 用事件总线
2. 如果操作**必须在事务后**才能执行（如 RAGFlow 上传） → 走 Outbox
3. 如果操作**与上下文耦合且单一**（如生成单个文件的摘要） → 直接 Celery `delay()`

### 3.2 核心域事件清单

| 事件 | 发布模块 | 主要订阅者 | 触发动作 |
|---|---|---|---|
| `UserRegistered` | auth | notification | 发邮箱验证邮件 |
| `UserVerified` | auth | audit | 写审计日志 |
| `FileUploaded` | document | ai, audit, statistics | 触发文本抽取任务、记录审计、更新统计 |
| `TextExtracted` | ai | ai | 触发摘要/分类/标签/敏感检测任务 |
| `FileAnalyzed` | ai | document, statistics | 更新文件状态机、统计聚合 |
| `SensitiveDetected` | ai | review, audit | 进入 sensitive_review_required 状态、记录审计 |
| `FileSubmittedForReview` | document | review, statistics | 通知管理员（可选邮件）、统计聚合 |
| `FileApproved` | review | ragflow, statistics, audit | 触发 RAGFlow 上传任务、统计、审计 |
| `FileRejected` | review | document, notification, audit | 状态机变更、通知上传人、审计 |
| `RAGFlowDocumentUploaded` | ragflow | document | 状态从 syncing → uploaded_to_ragflow，触发解析 |
| `RAGFlowParseStarted` | ragflow | document | 状态 → parsing |
| `RAGFlowParseCompleted` | ragflow | document, statistics, audit | 状态 → parsed，统计 |
| `RAGFlowParseFailed` | ragflow | document, statistics, notification | 状态 → failed，通知 |
| `ConfigChanged` | config | audit | 写审计日志 |

### 3.3 实现方案：Outbox Pattern

#### 3.3.1 为什么需要 Outbox

普通做法：
```python
# 反例: 业务逻辑里直接发 broker
async with session.begin():
    file = await repo.create_file(...)
    await broker.publish(FileUploaded(file_id=file.id))  # 危险!
```

问题：如果 commit 失败但 broker 已发布 → **不一致**；如果 commit 成功但 broker 发布失败 → **丢消息**。

Outbox 解法：
```python
# 同一事务内写业务表 + outbox 表
async with session.begin():
    file = await repo.create_file(...)
    await outbox_repo.append(
        event_type="files.file.uploaded",
        payload={"file_id": str(file.id), ...},
    )
# 事务 commit 后, outbox dispatcher 异步把消息投递到 RabbitMQ
```

#### 3.3.2 outbox 表 schema

新增数据表：

```sql
CREATE TABLE event_outbox (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(120) NOT NULL,        -- 路由 key, 例如 "files.file.uploaded"
    aggregate_type  VARCHAR(80) NOT NULL,         -- 聚合根类型, 例如 "file"
    aggregate_id    VARCHAR(64) NOT NULL,         -- 聚合根 ID
    payload         JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,                  -- null = 待发布
    publish_attempts INT NOT NULL DEFAULT 0,
    last_error      TEXT,
    trace_id        VARCHAR(64)                   -- 用于链路追踪
);

CREATE INDEX idx_outbox_pending
    ON event_outbox (occurred_at)
    WHERE published_at IS NULL;
```

#### 3.3.3 Outbox Dispatcher

由 `scheduler` 容器或专门 `outbox-dispatcher` 容器跑：

```python
# backend/app/workers/outbox_dispatcher.py
async def dispatch_loop():
    while True:
        rows = await fetch_unpublished(limit=100)
        if not rows:
            await asyncio.sleep(0.5)
            continue
        for row in rows:
            try:
                await rabbitmq.publish(
                    exchange="knowledge.events",
                    routing_key=row.event_type,
                    body=row.payload,
                    headers={"trace_id": row.trace_id, "event_id": row.id},
                )
                await mark_published(row.id)
            except Exception as e:
                await mark_failed(row.id, str(e))
```

#### 3.3.4 RabbitMQ 拓扑

```text
Exchange: knowledge.events  (type=topic, durable=true)

Routing key 模式: <module>.<aggregate>.<action>
  files.file.uploaded
  files.file.submitted-for-review
  review.file.approved
  review.file.rejected
  ai.text.extracted
  ai.file.analyzed
  ai.file.sensitive-detected
  ragflow.document.uploaded
  ragflow.document.parsed
  ragflow.document.parse-failed
  auth.user.registered
  auth.user.verified
  config.setting.changed

Queues:
  ai.events           binds: files.file.uploaded, ai.text.extracted
  ragflow.events      binds: review.file.approved, ragflow.document.uploaded
  statistics.events   binds: #.# (catch-all)
  notification.events binds: review.file.rejected, ragflow.document.parse-failed, auth.user.registered
  audit.events        binds: #.# (catch-all 但只写审计表)
  document.events     binds: review.file.approved, review.file.rejected,
                             ragflow.document.parsed, ragflow.document.parse-failed
```

每个 worker 容器只绑定自己关心的 routing key。

#### 3.3.5 事件订阅装饰器

```python
# backend/app/core/events.py
from typing import Awaitable, Callable, TypeVar
from pydantic import BaseModel

class DomainEvent(BaseModel):
    """所有事件继承此基类. routing key 由类的 ROUTING_KEY 属性给出."""
    ROUTING_KEY: ClassVar[str]

class FileUploaded(DomainEvent):
    ROUTING_KEY = "files.file.uploaded"
    file_id: UUID
    uploader_id: UUID
    sha256: str
    size: int
    extension: str

# 模块订阅 (在 worker 启动时注册到 Celery/Kombu consumer)
EVENT_HANDLERS: dict[str, list[Callable]] = {}

def event_handler(event_cls: type[DomainEvent]):
    def decorator(fn: Callable[[DomainEvent], Awaitable[None]]):
        EVENT_HANDLERS.setdefault(event_cls.ROUTING_KEY, []).append(fn)
        return fn
    return decorator

# 使用
@event_handler(FileUploaded)
async def trigger_text_extraction(event: FileUploaded) -> None:
    from app.workers.ai_tasks import extract_text
    extract_text.delay(str(event.file_id))
```

注意：
- 装饰器只是注册，**实际派发由消费 RabbitMQ 队列的 worker 启动时建立**
- 模块的 `handlers.py` 中定义订阅，被 worker 启动时导入触发注册

### 3.4 事务边界规则

| 场景 | 规则 |
|---|---|
| API 请求内 | 一个 HTTP 请求 = 一个 DB 事务 + 写 outbox |
| Celery task 内 | 一个 task = 一个 DB 事务 + 写 outbox |
| 事件消费 | 一个事件 = 一个 DB 事务 + 写新的 outbox（必要时） |
| 失败处理 | 事务回滚 = outbox 也回滚（同一事务） |
| Outbox 重试 | 投递失败的事件最多重试 5 次（指数退避），仍失败进入死信队列 |

### 3.5 死信队列（DLQ）

```text
Exchange: knowledge.events.dlx (type=topic, durable=true)
Queue:    knowledge.events.dlq (绑定 #.#)
```

- Celery 任务失败超 max_retry → 进入 DLQ
- Outbox 投递失败超 5 次 → 进入 DLQ
- 管理员页面可以从 DLQ 查看、手动重试或丢弃
- 增加 admin API：`GET /api/admin/dlq`、`POST /api/admin/dlq/{id}/replay`、`POST /api/admin/dlq/{id}/discard`

---

## 4. 模块文件级目录结构 [缺口 3]

### 4.1 仓库总目录

```text
knowledge_uploader/                   ← 项目根（注意不是 knowledge_uploader_docs/）
├── .gitattributes                    ← §2.4
├── .editorconfig                     ← §2.4
├── .gitignore
├── .env.example                      ← 07 §4 所有环境变量
├── .dockerignore
├── CLAUDE.md                         ← §5
├── README.md
├── tasks.py                          ← invoke 任务 §2.8
├── pyproject.toml                    ← Python 工程元数据 + ruff + mypy + pytest
├── docker-compose.yml                ← 本机 amd64 默认
├── docker-compose.arm64.yml          ← DGX 部署模板
├── docker-compose.override.yml.example ← 本机覆盖样板（端口/卷映射）
│
├── docs/
│   ├── spark/
│   │   └── 2026-06-04-p0-implementation-supplement.md  ← 本文档
│   ├── adr/                          ← 后续架构决策记录
│   └── runbooks/                     ← 运维手册
│
├── scripts/
│   ├── check_arm64_wheels.py         ← §2.6
│   ├── seed_admin.py                 ← 创建首个 system_admin
│   ├── reset_password.py             ← 应急重置（直接读数据库）
│   └── healthcheck.sh
│
├── backend/
│   ├── Dockerfile                    ← multi-arch §2.3
│   ├── requirements.txt              ← 锁版本
│   ├── requirements-dev.txt
│   ├── alembic.ini
│   └── app/
│       ├── __init__.py
│       ├── main.py                   ← FastAPI 入口
│       ├── core/                     ← 共享内核 §4.3
│       ├── db/                       ← §4.4
│       ├── modules/                  ← 业务模块 §4.5
│       ├── adapters/                 ← 外部系统 §4.6
│       ├── workers/                  ← Celery + outbox dispatcher §4.7
│       ├── utils/                    ← §4.8
│       └── tests/                    ← §4.9
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── router/
│       ├── pages/
│       ├── components/
│       ├── api/
│       ├── store/
│       ├── types/
│       └── utils/
│
├── nginx/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── default.conf
│
└── deploy/
    ├── dgx-spark/                    ← DGX 专用配置/脚本
    │   ├── README.md
    │   └── compose.env.example
    └── ci/
        ├── github-actions.yml
        └── gitea-actions.yml
```

### 4.2 项目根改名说明

**当前**：`E:\知识库系统搭建\RAGFlow\` 下有 `knowledge_uploader_docs/`，但没有代码根。
**实施时**：在 `E:\知识库系统搭建\RAGFlow\` 下新建 `knowledge_uploader/` 作为代码根；`knowledge_uploader_docs/` 保留为文档区，可考虑后续 mv 进 `knowledge_uploader/docs/spec/`。

阶段 0 不强制迁移文档目录，避免改路径影响现有引用。

### 4.3 `backend/app/core/` —— 共享内核

```text
core/
├── __init__.py
├── config.py            ← pydantic-settings, 从 env 加载，分组（DB/Redis/RabbitMQ/Auth/AI/RAGFlow/SMTP）
├── database.py          ← async engine, async sessionmaker, get_session 依赖
├── security.py          ← Argon2 hash, JWT 签发与解析, API Key Fernet 加解密
├── permissions.py       ← Role enum, RBAC dependency: requires_role(*roles)
├── events.py            ← DomainEvent 基类, EVENT_HANDLERS, event_handler 装饰器
├── outbox.py            ← outbox 写入/读取/标记 helpers
├── exceptions.py        ← AppException 基类 + 统一错误码 enum
├── logging.py           ← structlog 配置 + 脱敏处理器
├── middlewares.py       ← request_id 注入, JWT 解析, 日志中间件
├── pagination.py        ← Cursor / Offset 分页 helpers
├── ratelimit.py         ← slowapi + Redis 后端
└── deps.py              ← FastAPI Depends 统一定义
```

### 4.4 `backend/app/db/`

```text
db/
├── __init__.py
├── base.py              ← declarative_base, Base.metadata
├── session.py           ← async_sessionmaker
├── types.py             ← 自定义类型 (UUID, JSONB helpers)
└── migrations/          ← Alembic env.py + versions/
    ├── env.py
    ├── script.py.mako
    └── versions/
```

### 4.5 `backend/app/modules/` —— 业务模块

每个模块**必须**遵循同一形态：

```text
modules/<module_name>/
├── __init__.py
├── api.py               ← FastAPI Router 路由定义
├── schemas.py           ← Pydantic 请求/响应/共享 DTO
├── models.py            ← SQLAlchemy ORM
├── repository.py        ← 数据访问，无业务逻辑
├── service.py           ← 业务编排，**只被本模块的 api.py / tasks.py / handlers.py 调用**
├── events.py            ← 本模块发布的域事件定义
├── handlers.py          ← 本模块订阅的事件处理函数
├── permissions.py       ← 模块特定的权限装饰器（可选）
├── tasks.py             ← 本模块 Celery task 定义（可选）
└── exceptions.py        ← 模块特定的异常子类（可选）
```

10 个模块的位置（与 03 §3 对齐）：

```text
modules/
├── auth/         ← 注册/登录/邮箱验证/重置密码/JWT 签发
├── user/         ← 用户管理/角色/禁用
├── document/     ← 上传/校验/去重/状态机/我的文件
├── review/       ← 审核工作流
├── ragflow/      ← Dataset 映射/RAGFlow 同步
├── ai/           ← 文本抽取/Provider/Prompt/敏感规则/分析
├── statistics/   ← 总览/用户/部门/分类/趋势/导出
├── notification/ ← 邮件发送（验证码、重置、通知）
├── config/       ← 系统配置 CRUD
└── audit/        ← 审计日志写入/查询
```

### 4.6 `backend/app/adapters/` —— 外部系统适配

```text
adapters/
├── __init__.py
├── ragflow/
│   ├── __init__.py
│   ├── base.py          ← 抽象基类 RagflowClient
│   ├── client.py        ← 真实实现（httpx）
│   └── mock.py          ← 测试用 Mock（在测试中替换）
├── llm/
│   ├── __init__.py
│   ├── base.py          ← 抽象基类 BaseLLMProvider
│   ├── registry.py      ← Provider 注册中心（按 provider_type 路由）
│   └── providers/
│       ├── __init__.py
│       ├── openai_compatible.py
│       ├── ollama.py
│       ├── vllm.py
│       ├── lmstudio.py
│       └── mock.py
├── storage/
│   ├── __init__.py
│   ├── base.py          ← 抽象基类 StorageAdapter
│   ├── minio_adapter.py ← MinIO 实现
│   └── mock.py          ← 测试用本地内存实现
└── email/
    ├── __init__.py
    ├── base.py          ← 抽象基类 EmailAdapter
    ├── smtp.py          ← SMTP 实现
    └── log.py           ← 开发环境实现（打日志不真发）
```

### 4.7 `backend/app/workers/`

```text
workers/
├── __init__.py
├── celery_app.py        ← Celery 实例 + 配置（broker=RabbitMQ, result_backend=Redis）
├── queues.py            ← 队列定义 + routing
├── document_tasks.py    ← 文本抽取等
├── ai_tasks.py          ← LLM 调用、敏感检测
├── ragflow_tasks.py     ← RAGFlow 上传/解析/轮询
├── statistics_tasks.py  ← 统计快照
├── notification_tasks.py ← 邮件发送
├── outbox_dispatcher.py ← Outbox → RabbitMQ
└── event_consumer.py    ← 启动事件消费器，调用 EVENT_HANDLERS
```

### 4.8 `backend/app/utils/`

```text
utils/
├── __init__.py
├── hash.py              ← 计算 SHA256（流式读取）
├── filename.py          ← 文件名清洗（Windows 保留名 + 危险字符过滤）
├── file_validate.py     ← 扩展名/MIME/大小校验
├── crypto.py            ← Fernet 加解密辅助
├── token.py             ← 安全随机 token (email verify / reset)
└── time.py              ← UTC 时间助手
```

### 4.9 `backend/app/tests/`

```text
tests/
├── __init__.py
├── conftest.py          ← pytest fixtures: 异步 client, db session, mock RAGFlow/LLM/Storage
├── factories/           ← factory-boy 工厂
├── unit/
│   ├── test_filename.py
│   ├── test_security.py
│   └── ...
├── integration/
│   ├── test_auth_flow.py
│   ├── test_upload_flow.py
│   └── ...
└── e2e/
    └── test_full_pipeline.py  ← 上传 → 审核 → 同步全链路（用 mock RAGFlow）
```

### 4.10 模块间通信约束（硬规则）

**禁止**：
1. ❌ `from app.modules.ai.service import AIService` 出现在 `app/modules/document/` 任何文件
2. ❌ `from app.modules.ragflow.repository import RagflowRepo` 出现在其他模块
3. ❌ 直接读其他模块的 ORM 模型字段做业务判断

**允许**：
1. ✅ `from app.modules.document.schemas import FileRef` —— 跨模块 schema 共享（schemas 是稳定的接口契约）
2. ✅ `from app.core.events import event_handler` —— 共享内核
3. ✅ `from app.adapters.ragflow.base import RagflowClient` —— Adapter 抽象基类
4. ✅ 在 `handlers.py` 订阅其他模块的事件，间接得到信息
5. ✅ Celery task 跨模块直接 `xxx_task.delay(...)`（task 本质是异步 RPC，比 service 调用更解耦）

**强制工具**：

`pyproject.toml` 中配置 `ruff` 的 `flake8-tidy-imports` 规则：

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"app.modules.ai.service".msg = "禁止跨模块直接 import service，请通过事件总线或 schemas"
"app.modules.document.service".msg = "同上"
"app.modules.review.service".msg = "同上"
"app.modules.ragflow.service".msg = "同上"
"app.modules.user.service".msg = "同上"
```

CI 阶段 ruff 检查自动阻止违规。

### 4.11 前端目录细化

> 路由与页面以 `knowledge_platform_design_package/design.md` §3 §7.1 为准（12 个页面），不再按 04_FRONTEND_SPEC §2 的 16 个。

```text
frontend/src/
├── main.tsx
├── App.tsx
├── router/
│   ├── index.tsx
│   ├── routes.ts          ← 路由表（带角色权限，对齐 design.md §7.1）
│   └── guards.tsx         ← 路由守卫（未登录跳转 + 角色检查）
├── layouts/               ← 全局 Layout（对应 design.md §4.1）
│   ├── AppShell.tsx       ← 左侧导航 + 顶部工具栏 + 主内容
│   ├── Sidebar.tsx        ← 9 项导航 + 角色过滤
│   ├── TopHeader.tsx      ← 全局搜索 + 通知 + 用户菜单
│   └── PageContainer.tsx  ← 页面标题 + 说明 + 主操作按钮（对应 design.md §4.2）
├── pages/                 ← 12 个页面 + 4 个辅助页（详见 §9.3）
│   ├── Login/             ← /login
│   ├── Register/          ← /register
│   ├── ForgotPassword/    ← /forgot-password
│   ├── ResetPassword/     ← /reset-password/:token
│   ├── Dashboard/         ← /dashboard
│   ├── Upload/            ← /upload
│   ├── MyFiles/           ← /my-files
│   ├── FileManagement/    ← /files
│   ├── FileDetail/        ← /files/:id
│   ├── DatasetConfig/     ← /datasets
│   ├── AiConfig/          ← /ai-config（含 4 个 tabs：功能开关/供应商/Prompt/敏感规则）
│   ├── Statistics/        ← /statistics
│   ├── Users/             ← /users
│   └── Settings/          ← /settings
├── components/            ← 跨页面共用组件（对应 design.md §7.2）
│   ├── StatCard.tsx       ← KPI 指标卡（design.md §4.3）
│   ├── StatusTag.tsx      ← 文件/审核/同步状态 Tag，统一颜色（§9.4）
│   ├── FileIcon.tsx       ← 按扩展名渲染图标
│   ├── UserAvatar.tsx
│   ├── DataTable/         ← 基于 ProTable，统一搜索/筛选/排序/分页/批量/导出
│   ├── FilterBar.tsx
│   ├── UploadDropzone.tsx ← 拖拽上传（design.md §5.4）
│   ├── ProgressList.tsx   ← 上传进度列表
│   ├── ChartCard.tsx      ← ECharts 容器
│   ├── ConfigCard.tsx     ← 配置卡片（开关/输入组合）
│   └── LogTimeline.tsx    ← 日志时间线
├── api/                   ← 后端 API 封装
│   ├── client.ts          ← axios 实例 + 拦截器（JWT 注入、401 跳登录、错误统一）
│   ├── auth.ts
│   ├── files.ts
│   ├── tasks.ts
│   ├── datasets.ts
│   ├── admin-ai.ts
│   ├── statistics.ts
│   ├── users.ts
│   └── system.ts
├── store/                 ← Zustand stores（仅 UI 状态）
│   ├── auth.store.ts      ← 当前用户、JWT、角色
│   └── ui.store.ts        ← 侧边栏收起/展开、主题等
├── hooks/                 ← TanStack Query hooks（所有服务端状态）
│   ├── useCurrentUser.ts
│   ├── useFiles.ts
│   ├── useFileDetail.ts
│   ├── useDatasets.ts
│   ├── useAiConfig.ts
│   ├── useStatistics.ts
│   └── ...
├── types/                 ← TypeScript 类型定义（与后端 schemas 一一对应）
│   ├── api.ts
│   ├── file.ts
│   ├── user.ts
│   ├── ai.ts
│   └── ...
├── theme/                 ← 设计 token（对应 design.md §2.2）
│   ├── tokens.ts          ← 色板/圆角/间距/字号
│   └── antd-theme.ts      ← Ant Design ConfigProvider 主题
├── utils/
│   ├── format.ts
│   ├── filesize.ts
│   └── validators.ts
└── styles/
    └── global.css
```

---

## 5. CLAUDE.md 内容草案 [缺口 4]

**位置**：`knowledge_uploader/CLAUDE.md`（项目根，与 `backend/`、`frontend/` 同级）

**内容**：见下方完整草案。实施时直接复制为 `CLAUDE.md`。

````markdown
# CLAUDE.md — Knowledge Uploader

> 本文件是 Claude Code / Codex / Cursor 等 AI 工程师的项目级规则。每次工作前必读。

## 1. 项目一句话

公司员工通过 Web 上传文档 → 校验/去重/可选 AI 分析 → 管理员审核 → 同步到 RAGFlow → 喂给钉钉客服机器人。

## 2. 必读文档（按优先级）

1. `knowledge_uploader_docs/02_ARCHITECTURE_最终架构设计.md` — 架构定版
2. `knowledge_uploader_docs/03_BACKEND_SPEC_后端开发规范.md` — 后端模块边界
3. `knowledge_uploader_docs/05_DATABASE_API_SPEC_数据库与API规范.md` — 表与 API
4. `knowledge_uploader_docs/07_DEPLOYMENT_ENV_部署与环境配置.md` — 11 个服务
5. `knowledge_uploader_docs/08_TASK_BREAKDOWN_开发任务拆解.md` — 9 阶段任务
6. `docs/spark/2026-06-04-p0-implementation-supplement.md` — 跨平台/事件总线/目录结构/版本锁

## 3. 架构红线（不可逾越）

- 不使用 SQLite。数据库统一 **PostgreSQL 16**。
- 不使用本地文件系统作为正式存储。文件统一 **MinIO**。
- 不使用 FastAPI BackgroundTasks 承担核心长任务。长任务统一 **Celery + RabbitMQ**。
- 前端不直接访问 RAGFlow、AI 模型。
- RAGFlow API Key 与 AI Provider API Key **绝不返回前端**，**绝不打日志**。
- 文件状态机变更只能通过 service 层方法，不能直接 update ORM。
- 所有管理员操作必须写 `audit_logs`。
- AI 关闭时（`AI_ANALYSIS_ENABLED=false`），文件不能进入任何 AI 相关状态。

## 4. 跨平台规则

本机开发：Windows 11。生产部署：DGX Spark / ARM64 Linux。

- 路径用 `pathlib.Path`，禁止字符串拼接。
- 文件读写明确 `encoding="utf-8"`。
- 行尾全部 LF（`.gitattributes` 强制）。
- 新加 Python 依赖前必须检查 ARM64 wheel：`invoke check-arm64`。
- 禁用列表：`psycopg2*`、`python-magic*`、`mysqlclient`、`pycrypto`、`m2crypto`。
- 文件名清洗必须过滤 Windows 保留名（CON/PRN/AUX/NUL/COM*/LPT*）。

## 5. 模块边界（硬规则）

```
modules/<module>/
├── api.py / schemas.py / models.py
├── repository.py / service.py
├── events.py / handlers.py
├── permissions.py / tasks.py / exceptions.py
```

- ❌ 禁止跨模块 import service / repository
- ✅ 允许跨模块 import schemas
- ✅ 模块间通信只走：(1) 事件总线 (2) Celery task (3) 共享 schemas
- `ruff` 配置中已 ban 跨模块 service import，违规 CI 阻塞

## 6. 域事件规则

- 模块发布事件用 `outbox` 表（同事务），由 dispatcher 投递 RabbitMQ
- 模块订阅事件用 `@event_handler(EventClass)` 装饰器，定义在 `handlers.py`
- 事件命名：`<module>.<aggregate>.<action>`（routing key）
- 14 个核心事件清单见补充 spec §3.2

## 7. 文件状态机硬规则

完整状态见 05 §2（17 个状态）。规则：

- 状态变更只能通过 `DocumentStateMachine.transition(from, to)` 调用
- AI 关闭：跳过 `extracting_text` / `analysis_queued` / `analyzing` / `analysis_failed` / `analyzed`
- 敏感等级 `critical`：默认阻止同步 RAGFlow
- 同一文件不能同时存在多个 `ragflow_upload` 任务（用 Redis 分布式锁）

## 8. 安全规则

- 密码：Argon2id（`argon2-cffi`）
- JWT：HS256，secret 至少 32 字节随机；过期 24h（可配）
- API Key 字段级加密：Fernet（key 从环境变量加载）
- 邮箱验证 token / 重置密码 token：入库前 SHA256 hash，原文只在邮件中
- 文件上传：扩展名白名单 + filetype 二次校验 + 文件名清洗 + 大小限制
- 限流：登录失败 5 次锁 15 分钟；上传 10 次/分钟/用户

## 9. 代码规范

- Python：`ruff` (lint + format) + `mypy --strict`
- 行宽 100
- 字符串引号统一 `"`（ruff format 自动）
- 导入顺序：stdlib → 第三方 → app（ruff isort）
- 函数注解：所有 public 函数必须有类型注解
- 异步：优先 `async def`，DB 用 `AsyncSession`

## 10. 测试要求

- 单测覆盖：core / utils / repository
- 集成测试覆盖：每个 API 至少一个 happy path + 一个失败 path
- E2E：上传→审核→RAGFlow 同步全链路（用 mock RAGFlow / mock LLM）
- 测试命令：`invoke test`
- 测试不能依赖外网（CI 跑不通）

## 11. 提交规范

- 提交信息：Conventional Commits（feat/fix/refactor/docs/test/chore）
- 一个提交一个原子变更
- 涉及数据库变更必须含 Alembic 迁移
- 涉及依赖变更必须更新 `requirements.txt` + 跑 `invoke check-arm64`

## 12. 阶段化开发

按 08_TASK_BREAKDOWN 的 9 阶段顺序推进，**禁止跳阶段**。每阶段必须：
- `invoke up` 能起所有容器
- `/api/system/health` 返回 200
- Alembic 迁移可逐步前进
- 阶段验收点全部通过

## 13. 常用命令

```powershell
# 启停
invoke up
invoke down
invoke logs --service=backend-api

# 数据库
invoke migrate --msg="add users"  # 创建迁移
invoke migrate                     # 升级到最新

# 测试与质量
invoke test
invoke test -k "test_login"
invoke lint
invoke fmt

# 跨架构
invoke check-arm64
invoke build-arm64 --version=0.1.0
```

## 14. 找 Claude 求助前先看

- 文件状态相关：05 §2 + 03 §5
- API 设计：05 §3
- AI Provider：06 §2
- 部署/环境：07
- 测试 fixture：`backend/app/tests/conftest.py`
````

### 5.1 CLAUDE.md 维护原则

- 每阶段完成后 review 一次
- 发现 AI 反复犯同样错误 → 加入 §3-§9 对应规则
- 不写"建议"类规则，只写"必须 / 禁止"

---

## 6. 技术栈版本锁 [缺口 5]

### 6.1 运行时

| 类别 | 版本 | 备注 |
|---|---|---|
| Python | 3.11.x | 3.12 部分包 ARM64 wheel 还在追，2026 上半年仍以 3.11 为稳 |
| Node.js | 20 LTS | 22 尚未到 LTS |
| PostgreSQL | 16 | 17 已 GA 但生产采纳率仍低 |
| Redis | 7.2 | 7.4 可选，避免 8.x（许可证变更） |
| RabbitMQ | 3.13 | 4.x 较新 |
| MinIO | RELEASE.2024-01 以后任一稳定版 | |
| Nginx | 1.25 alpine | |

### 6.2 后端核心依赖（`backend/requirements.txt` 锁版本）

```text
# Web 框架
fastapi==0.110.1
uvicorn[standard]==0.29.0
python-multipart==0.0.9

# ORM / 迁移
sqlalchemy[asyncio]==2.0.30
asyncpg==0.29.0
psycopg[binary]==3.1.18
alembic==1.13.1

# 任务队列
celery==5.3.6
kombu==5.3.7
redis==5.0.4

# 数据校验 / 配置
pydantic==2.7.1
pydantic-settings==2.2.1
email-validator==2.1.1

# 安全
argon2-cffi==23.1.0
PyJWT[crypto]==2.8.0
cryptography==42.0.5

# HTTP / 外部调用
httpx[http2]==0.27.0
tenacity==8.2.3

# 文件
minio==7.2.5
filetype==1.2.0

# AI
tiktoken==0.7.0

# 日志 / 限流
structlog==24.1.0
slowapi==0.1.9

# 工具
python-dateutil==2.9.0.post0
orjson==3.10.3
```

`backend/requirements-dev.txt`：

```text
-r requirements.txt

pytest==8.2.0
pytest-asyncio==0.23.6
pytest-cov==5.0.0
httpx==0.27.0
factory-boy==3.3.0
faker==25.0.1
freezegun==1.5.0

ruff==0.4.4
mypy==1.10.0
types-python-dateutil==2.9.0.20240316

invoke==2.2.0
```

### 6.3 前端核心依赖（`frontend/package.json` 锁版本，节选）

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.23.0",
    "antd": "^5.16.5",
    "@ant-design/icons": "^5.3.7",
    "@ant-design/pro-components": "^2.7.5",
    "axios": "^1.6.8",
    "zustand": "^4.5.2",
    "@tanstack/react-query": "^5.32.1",
    "echarts": "^5.5.0",
    "echarts-for-react": "^3.0.2",
    "dayjs": "^1.11.11"
  },
  "devDependencies": {
    "typescript": "^5.4.5",
    "vite": "^5.2.10",
    "@vitejs/plugin-react": "^4.2.1",
    "@types/react": "^18.3.1",
    "@types/react-dom": "^18.3.0",
    "vitest": "^1.5.3",
    "@testing-library/react": "^15.0.6",
    "eslint": "^8.57.0",
    "@typescript-eslint/eslint-plugin": "^7.7.1",
    "@typescript-eslint/parser": "^7.7.1",
    "prettier": "^3.2.5"
  }
}
```

### 6.4 Docker base image 锁

| 用途 | image | 备注 |
|---|---|---|
| 后端 Python | `python:3.11-slim-bookworm` | 官方多架构 |
| 前端构建 | `node:20-alpine` | 构建阶段 |
| 前端运行 | `nginx:1.25-alpine` | 静态服务 |
| 数据库 | `postgres:16-alpine` | |
| 缓存 | `redis:7.2-alpine` | |
| 队列 | `rabbitmq:3.13-management-alpine` | |
| 对象存储 | `minio/minio:RELEASE.2024-04-18T19-09-19Z` | 锁具体 tag 避免 latest 漂移 |

---

## 7. 上游文档不一致修正建议 [新增]

### 7.1 不一致 1：05 §1 缺表

05 列了 9 张表，但 03 §4 模块职责覆盖以下额外表（按原始 v1.0 spec），**05 应补**：

- `email_verification_tokens` （auth 模块需要）
- `password_reset_tokens` （auth 模块需要）
- `ai_feature_configs` （ai 模块需要 — 各 AI 子能力开关）
- `prompt_templates` （ai 模块需要 — Prompt 模板管理）
- `sensitive_rules` （ai 模块需要 — 敏感规则配置）
- `ai_usage_logs` （ai 模块需要 — token 消耗审计）
- `sync_logs` （ragflow 模块需要 — 同步详细日志）
- `audit_logs` （audit 模块需要 — 全局审计）
- `system_configs` （config 模块需要 — 系统配置 KV）
- `event_outbox` **（本补充新增）** — Outbox Pattern 见 §3.3
- `dead_letter_events` **（本补充新增）** — DLQ 见 §3.5

**修正方式**：在 05 §1 末尾追加上述表 schema，或在阶段 0 实施时直接补充到 Alembic 第一次迁移中。

### 7.2 不一致 2：状态颜色规范分散在三处

颜色规范分别出现在：
- **04_FRONTEND_SPEC §5**：10 个文件主状态的颜色
- **design.md §6.2 / §6.3 / §6.4**：按"审核状态 / 同步状态 / 风险等级"分类的颜色
- 都不完整，且互相不重叠

05 §2 有 17 个文件主状态，04 只列了 10 个，剩余 7 个无颜色规范。design.md 又提出了"审核状态 / 同步状态 / 风险等级"三个独立维度的颜色，这是新增维度。

**实施时以本补充 spec §9.4 状态色板统一表为唯一数据源**，已合并三处规范并补全所有状态，并约定通过 `StatusTag` 组件作为唯一渲染入口。

### 7.3 不一致 3：02 vs 07 部署角色对齐

| 02 §4 | 07 §2 | 对齐建议 |
|---|---|---|
| backend-api | backend-api | ✅ 一致 |
| worker-document | worker-document | ✅ 一致 |
| worker-ai | worker-ai | ✅ 一致 |
| worker-ragflow | worker-ragflow | ✅ 一致 |
| worker-statistics | worker-statistics | ✅ 一致 |
| worker-notification | worker-notification | ✅ 一致 |
| scheduler | scheduler | ✅ 一致 |
| — | nginx | 02 未列，应补 |
| — | frontend | 02 未列，应补 |
| — | postgres | 02 §2 架构图有，§4 表格漏 |
| — | rabbitmq | 同上 |
| — | redis | 同上 |
| — | minio | 同上 |
| — **新增 outbox-dispatcher** | — **新增 outbox-dispatcher** | 本补充 §3.3.3 新增 |

**修正方式**：阶段 0 实施 `docker-compose.yml` 时，按 07 §2 + 新增 `outbox-dispatcher` 共 **12 个服务**为准。

### 7.4 不一致 4：表名/模块名映射明确

03 §3 模块名是 `document`（单数），但 05 §1.2 表名是 `files`（复数）—— 这是合理的（一个文档/文件可以有多个版本），但**需在 03 末尾追加映射表**：

| 模块 | 主表 | 关联表 |
|---|---|---|
| auth | users | email_verification_tokens, password_reset_tokens |
| user | users | — |
| document | files | categories（仅引用） |
| review | files（状态变更）, sync_tasks | audit_logs |
| ragflow | files（同步字段）, sync_tasks, sync_logs, dataset_mappings | — |
| ai | document_analysis, ai_providers, ai_feature_configs, prompt_templates, sensitive_rules, ai_usage_logs | — |
| statistics | statistics_snapshots, user_upload_statistics | — |
| notification | — | — |
| config | system_configs | — |
| audit | audit_logs | — |

### 7.5 不一致 5：04_FRONTEND_SPEC 页面 vs design.md 页面

| 维度 | 04_FRONTEND_SPEC §2 | design.md §3 §7.1 | 实施结论 |
|---|---|---|---|
| 页面总数 | 16 | 12 | **以 design.md 为准**：12 个主页面 + 4 个辅助页（ForgotPassword / ResetPassword / 邮箱验证成功 / 弹窗类） |
| AI 配置 | 4 个独立页面（AiConfig / AiProviders / PromptTemplates / SensitiveRules） | **1 个页面 4 个 tabs**（功能开关 / 模型供应商 / Prompt 模板 / 敏感规则） | 合并为 `/ai-config` 单页 + Tabs |
| 用户管理 | `Users/` | `用户管理` | 路由统一为 `/users` |
| 系统设置 | `SystemConfig/` | `系统设置` | 路由统一为 `/settings` |
| 忘记密码/重置 | `ForgotPassword/` + `ResetPassword/` 独立页 | 列在 §10 后续可补充 | 保留 04 的独立页（阶段 1 必需），路由 `/forgot-password` + `/reset-password/:token` |
| 路由命名 | 未指定 | `/dashboard` `/upload` `/my-files` `/files` `/files/:id` `/datasets` `/ai-config` `/statistics` `/users` `/settings` | **以 design.md §7.1 为准** |

**修正方式**：本补充 spec §4.11（已更新） + §9.3 路由表已落地此修正。04_FRONTEND_SPEC 的页面清单作为"功能清单"参考，路由 / 页面合并以 design.md 为准。

### 7.6 不一致 6：左侧导航菜单与 04 §4 权限映射差异

04 §4 给出了三种角色的菜单可见映射，但 design.md §4.1 列出的左侧导航是 **9 项固定结构**（仪表盘 / 文件上传 / 我的文件 / 文件管理 / Dataset 配置 / AI 配置 / 统计分析 / 用户管理 / 系统设置）。

实施时按 design.md 的 9 项导航作为完整菜单，每项配置可见角色：

| 导航项 | employee | knowledge_admin | system_admin |
|---|:---:|:---:|:---:|
| 仪表盘 | — | ✅ | ✅ |
| 文件上传 | ✅ | ✅ | ✅ |
| 我的文件 | ✅ | ✅ | ✅ |
| 文件管理 | — | ✅ | ✅ |
| Dataset 配置 | — | — | ✅ |
| AI 配置 | — | — | ✅ |
| 统计分析 | 仅"我的统计"子项 | ✅ | ✅ |
| 用户管理 | — | — | ✅ |
| 系统设置 | — | — | ✅ |

普通员工登录后默认进入 `/my-files` 而非 `/dashboard`。

---

## 8. 阶段 0 启动检查表

按 08 阶段 0 + 本补充 spec 的要求，整理出可执行 checklist。**只有全部勾选才能进入阶段 1**。

### 8.1 仓库与规范

- [ ] 在 `E:\知识库系统搭建\RAGFlow\` 下创建 `knowledge_uploader/` 项目根
- [ ] `git init`
- [ ] `.gitattributes` 写入（§2.4 模板）
- [ ] `.editorconfig` 写入（§2.4 模板）
- [ ] `.gitignore`（含 Python/Node/IDE/OS 常见忽略）
- [ ] `.dockerignore`
- [ ] `.env.example`（按 07 §4 所有变量 + 本补充 §6 新增）
- [ ] `CLAUDE.md`（§5 完整草案）
- [ ] `README.md`（简介 + 启动命令）
- [ ] `pyproject.toml`（ruff + mypy + pytest 配置）
- [ ] `tasks.py`（invoke 任务，§2.8 模板）
- [ ] 迁移 design 包：`mv knowledge_platform_design_package/ knowledge_uploader/docs/design/`

### 8.2 后端骨架

- [ ] `backend/` 目录创建
- [ ] `backend/requirements.txt` 写入（§6.2 锁版本）
- [ ] `backend/requirements-dev.txt`
- [ ] `backend/Dockerfile`（multi-arch，§2.3.1 模板）
- [ ] `backend/alembic.ini`
- [ ] `backend/app/main.py`（最小 FastAPI app + `/api/system/health`）
- [ ] `backend/app/core/`（config/database/security/permissions/events/outbox/exceptions/logging/middlewares/deps，先写空骨架）
- [ ] `backend/app/db/base.py` + `session.py`
- [ ] `backend/app/db/migrations/env.py`
- [ ] 10 个 `modules/<name>/` 空目录（按 §4.5）
- [ ] `backend/app/adapters/` 四个子目录骨架（ragflow/llm/storage/email，各含 base.py + mock.py）
- [ ] `backend/app/workers/celery_app.py`
- [ ] `backend/app/utils/` 五个工具文件骨架
- [ ] `backend/app/tests/conftest.py`

### 8.3 前端骨架

- [ ] `frontend/` 目录创建
- [ ] `frontend/package.json`（§6.3 锁版本，含 `@ant-design/pro-components`、`echarts-for-react`）
- [ ] `frontend/vite.config.ts`
- [ ] `frontend/tsconfig.json`（strict）
- [ ] `frontend/Dockerfile`（多阶段：node 构建 → nginx 服务）
- [ ] `frontend/src/main.tsx` + `App.tsx`
- [ ] `frontend/src/theme/tokens.ts`（color/radius/spacing，来自 design.md §2.2）
- [ ] `frontend/src/theme/antd-theme.ts`（Ant Design `ConfigProvider` 主题）
- [ ] `frontend/src/layouts/AppShell.tsx` + `Sidebar.tsx` + `TopHeader.tsx`（design.md §4.1）
- [ ] `frontend/src/router/index.tsx` + `routes.ts`（12 个主页面 + 4 个辅助页路由占位，详见 §9.3）
- [ ] `frontend/src/router/guards.tsx`（未登录跳转 + 角色守卫）
- [ ] `frontend/src/api/client.ts`（axios + 拦截器：JWT 注入、401 跳登录、错误统一）
- [ ] `frontend/src/store/auth.store.ts`
- [ ] `frontend/src/components/StatusTag.tsx`（状态色板统一，详见 §9.4）
- [ ] 登录页占位（不要求样式精美，只需路由通） + 健康检查页（POST `/api/auth/login` 占位）

### 8.4 Docker Compose

- [ ] `docker-compose.yml`（12 个服务：nginx, frontend, backend-api, worker-document, worker-ai, worker-ragflow, worker-statistics, worker-notification, scheduler, outbox-dispatcher, postgres, rabbitmq, redis, minio）
- [ ] `docker-compose.arm64.yml`（platform 覆盖）
- [ ] `docker-compose.override.yml.example`（本机端口/卷映射样板）
- [ ] `nginx/nginx.conf` + `nginx/default.conf`（反代 backend-api + frontend）

### 8.5 CI 雏形

- [ ] `deploy/ci/github-actions.yml`（lint + test + ARM64 wheel check + buildx amd64）
- [ ] `scripts/check_arm64_wheels.py`（§2.6 完整脚本）

### 8.6 验收

- [ ] `invoke up` 12 个容器全部 healthy
- [ ] `curl http://localhost:8000/api/system/health` 返回 `{"status": "ok"}`
- [ ] `http://localhost:5173`（或 nginx 端口）能访问前端登录页占位
- [ ] `docker compose exec backend-api alembic upgrade head` 执行成功（即使没有迁移文件也不报错）
- [ ] `invoke check-arm64` 通过
- [ ] `invoke lint` 无错误
- [ ] `invoke test` 至少能跑（即使 0 测试用例）
- [ ] CI 在本地能模拟运行（`act` 或 `gitea-runner local`）

---

## 9. 前端设计实现指南 [整合 design 包]

> 本章整合 `knowledge_platform_design_package/`，把视觉与交互规范落地为可执行的前端实现约定。设计稿是视觉权威源，本章是工程权威源。

### 9.1 设计权威源与维护

| 资产 | 位置（阶段 0 后） | 性质 |
|---|---|---|
| `design.md` | `knowledge_uploader/docs/design/design.md` | 视觉/交互规范文档 |
| `images/*.png` | `knowledge_uploader/docs/design/images/` | 12 张高保真原稿（仅参考方向） |
| `theme/tokens.ts` | `frontend/src/theme/tokens.ts` | 设计 token 代码化 |
| `theme/antd-theme.ts` | `frontend/src/theme/antd-theme.ts` | Ant Design 主题对接 |

修改规则：
- 视觉/布局变更：先改 `design.md`，再改前端代码
- 高保真原稿（PNG）**只作方向参考**，最终以代码为准（design.md §8.1 已明确）
- 文案以中文为主，未来支持多语言时统一从 `frontend/src/i18n/` 加载

### 9.2 设计 token（color / radius / spacing / typography）

**位置**：`frontend/src/theme/tokens.ts`

```ts
// 直接对应 design.md §2.2 色板，作为单一数据源
export const colors = {
  // 主色
  primary: '#1677FF',
  primaryHover: '#4096FF',
  primaryLight: '#E6F4FF',

  // 背景与卡片
  bgBase: '#F5F7FA',
  bgCard: '#FFFFFF',
  border: '#E5EAF2',

  // 文本
  textPrimary: '#1F2937',
  textSecondary: '#667085',
  textDisabled: '#98A2B3',

  // 状态色
  success: '#16A34A',
  warning: '#F59E0B',
  danger: '#EF4444',
  info: '#3B82F6',
  purple: '#7C3AED',
  orange: '#F97316',
} as const;

export const radius = {
  card: 12,
  control: 8,
  tag: 4,
} as const;

export const spacing = {
  cardPadding: 24,
  cardPaddingSm: 20,
  pageGutter: 24,
  sectionGap: 16,
} as const;

export const typography = {
  fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
  // size / line-height 走 Ant Design 默认即可
} as const;
```

**Ant Design 主题对接**（`frontend/src/theme/antd-theme.ts`）：

```ts
import type { ThemeConfig } from 'antd';
import { colors, radius } from './tokens';

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.danger,
    colorInfo: colors.info,
    colorBgLayout: colors.bgBase,
    colorBgContainer: colors.bgCard,
    colorBorder: colors.border,
    colorText: colors.textPrimary,
    colorTextSecondary: colors.textSecondary,
    borderRadius: radius.control,
    borderRadiusLG: radius.card,
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
  },
  components: {
    Card: { borderRadiusLG: radius.card, paddingLG: 24 },
    Tag: { borderRadiusSM: radius.tag },
  },
};
```

`App.tsx` 中包一层：`<ConfigProvider theme={antdTheme}>...</ConfigProvider>`。

### 9.3 路由表（12 主页面 + 4 辅助页）

**位置**：`frontend/src/router/routes.ts`

| 路径 | 页面目录 | 角色 | 说明 | 设计稿 |
|---|---|---|---|---|
| `/login` | `pages/Login/` | 公开 | 登录页 | `02_login.png` |
| `/register` | `pages/Register/` | 公开 | 注册页 | `03_register.png` |
| `/forgot-password` | `pages/ForgotPassword/` | 公开 | 忘记密码（输入邮箱） | 需补充 |
| `/reset-password/:token` | `pages/ResetPassword/` | 公开 | 重置密码 | 需补充 |
| `/dashboard` | `pages/Dashboard/` | knowledge_admin, system_admin | 仪表盘 | `01_dashboard.png` |
| `/upload` | `pages/Upload/` | 全部 | 文件上传 | `04_file_upload.png` |
| `/my-files` | `pages/MyFiles/` | 全部 | 我的文件（员工默认首页） | `05_my_files.png` |
| `/files` | `pages/FileManagement/` | knowledge_admin, system_admin | 文件管理 | `06_file_management.png` |
| `/files/:id` | `pages/FileDetail/` | 全部（受权限过滤） | 文件详情 | `07_file_detail.png` |
| `/datasets` | `pages/DatasetConfig/` | system_admin | Dataset 配置 | `08_dataset_config.png` |
| `/ai-config` | `pages/AiConfig/` | system_admin | AI 配置（4 tabs） | `09_ai_config.png` |
| `/statistics` | `pages/Statistics/` | knowledge_admin, system_admin | 统计分析 | `10_statistics.png` |
| `/users` | `pages/Users/` | system_admin | 用户管理 | `11_user_management.png` |
| `/settings` | `pages/Settings/` | system_admin | 系统设置 | `12_system_settings.png` |

**路由代码示例**：

```ts
import type { RouteObject } from 'react-router-dom';
import { lazy } from 'react';

export const Roles = {
  EMPLOYEE: 'employee',
  KNOWLEDGE_ADMIN: 'knowledge_admin',
  SYSTEM_ADMIN: 'system_admin',
} as const;

export type Role = typeof Roles[keyof typeof Roles];

export interface AppRoute extends Omit<RouteObject, 'children'> {
  path: string;
  element: React.ReactNode;
  roles?: Role[]; // 不填 = 公开（仅登录）；空数组 = 不需要登录
  children?: AppRoute[];
}

export const publicRoutes: AppRoute[] = [
  { path: '/login', element: <Login />, roles: [] },
  { path: '/register', element: <Register />, roles: [] },
  { path: '/forgot-password', element: <ForgotPassword />, roles: [] },
  { path: '/reset-password/:token', element: <ResetPassword />, roles: [] },
];

export const appRoutes: AppRoute[] = [
  { path: '/dashboard', element: <Dashboard />, roles: ['knowledge_admin', 'system_admin'] },
  { path: '/upload', element: <Upload /> },
  { path: '/my-files', element: <MyFiles /> },
  { path: '/files', element: <FileManagement />, roles: ['knowledge_admin', 'system_admin'] },
  { path: '/files/:id', element: <FileDetail /> },
  { path: '/datasets', element: <DatasetConfig />, roles: ['system_admin'] },
  { path: '/ai-config', element: <AiConfig />, roles: ['system_admin'] },
  { path: '/statistics', element: <Statistics />, roles: ['knowledge_admin', 'system_admin'] },
  { path: '/users', element: <Users />, roles: ['system_admin'] },
  { path: '/settings', element: <Settings />, roles: ['system_admin'] },
];

// 登录后默认首页
export const defaultRouteForRole: Record<Role, string> = {
  employee: '/my-files',
  knowledge_admin: '/dashboard',
  system_admin: '/dashboard',
};
```

### 9.4 状态色板统一表（合并 04 §5 + design §6.2-6.4 + 本补充 §7.2 §7.5）

`StatusTag` 组件作为唯一渲染入口，按"状态类型 + 状态值"映射颜色。

| 状态类型 | 状态值 | 中文 | Tag 颜色（Ant Design preset） | hex |
|---|---|---|---|---|
| 文件主状态 | uploaded | 已上传 | `blue` | #1677FF |
| 文件主状态 | extracting_text | 文本抽取中 | `purple` | #7C3AED |
| 文件主状态 | analysis_queued | 等待分析 | `geekblue` | #2F54EB |
| 文件主状态 | analyzing | AI 分析中 | `purple` | #7C3AED |
| 文件主状态 | analysis_failed | 分析失败 | `orange` | #F97316 |
| 文件主状态 | analyzed | 分析完成 | `cyan` | #06B6D4 |
| 文件主状态 | pending_review | 待审核 | `gold` | #F59E0B |
| 文件主状态 | sensitive_review_required | 敏感审核 | `red` | #EF4444 |
| 文件主状态 | approved | 已审核 | `green` | #16A34A |
| 文件主状态 | rejected | 已拒绝 | `volcano` | #DC2626 |
| 文件主状态 | queued | 等待同步 | `default` | #98A2B3 |
| 文件主状态 | syncing | 同步中 | `processing`（带动画） | #3B82F6 |
| 文件主状态 | uploaded_to_ragflow | 已上传至 RAGFlow | `cyan` | #06B6D4 |
| 文件主状态 | parsing | 解析中 | `processing` | #3B82F6 |
| 文件主状态 | parsed | 解析完成 | `success` | #16A34A |
| 文件主状态 | failed | 失败 | `error` | #EF4444 |
| 文件主状态 | disabled | 已禁用 | `default` | #98A2B3 |
| 文件主状态 | deleted | 已删除 | `default`（斜体） | #98A2B3 |
| 审核状态 | pending | 待审核 | `gold` | #F59E0B |
| 审核状态 | in_review | 审核中 | `blue` | #1677FF |
| 审核状态 | approved | 已通过 | `success` | #16A34A |
| 审核状态 | rejected | 未通过 | `error` | #EF4444 |
| 同步状态 | not_synced | 未同步 | `default` | #98A2B3 |
| 同步状态 | queued | 待同步 | `blue` | #1677FF |
| 同步状态 | syncing | 同步中 | `processing` | #3B82F6 |
| 同步状态 | synced | 已同步 | `success` | #16A34A |
| 同步状态 | failed | 同步失败 | `error` | #EF4444 |
| 风险等级 | low | 低风险 | `success` | #16A34A |
| 风险等级 | medium | 中风险 | `warning` | #F59E0B |
| 风险等级 | high | 高风险 | `error` | #EF4444 |
| 风险等级 | critical | 严重风险 | `magenta`（深色调） | #9D174D |
| 用户状态 | active | 正常 | `success` | #16A34A |
| 用户状态 | pending_email_verification | 待激活 | `gold` | #F59E0B |
| 用户状态 | disabled | 已禁用 | `default` | #98A2B3 |
| 用户状态 | locked | 锁定中 | `error` | #EF4444 |

**StatusTag 接口契约**：

```ts
type StatusKind = 'file' | 'review' | 'sync' | 'risk' | 'user';
interface StatusTagProps {
  kind: StatusKind;
  value: string;
  /** 是否使用带动画的 processing 状态 */
  processing?: boolean;
}
```

### 9.5 全局 Layout 规范

完整对应 design.md §4.1。

**`AppShell` 结构**：

```text
┌────────────────────────────────────────────────────────┐
│                    TopHeader (高 56px)                  │
│  ┌──────┐                          ┌──────┐ ┌──┐ ┌──┐ │
│  │ Logo │   全局搜索（中间居中）       │ 通知 │ │UA│ │↓│ │
│  └──────┘                          └──────┘ └──┘ └──┘ │
├────────┬───────────────────────────────────────────────┤
│        │ PageContainer                                  │
│Sidebar │  ┌───────────────────────────────────────┐    │
│(宽 220)│  │  页面标题 + 副标题 + 操作按钮          │    │
│        │  ├───────────────────────────────────────┤    │
│  9 项  │  │                                       │    │
│  导航  │  │      主内容（多卡片网格 / 表格）          │    │
│        │  │                                       │    │
│        │  └───────────────────────────────────────┘    │
└────────┴───────────────────────────────────────────────┘
```

**实现要点**：
- `AppShell` 用 Ant Design 的 `Layout` + 自定义 Sidebar
- Sidebar 收起后宽 64px，展开 220px；状态写入 `ui.store.ts` 持久化到 localStorage
- TopHeader 全局搜索阶段 0 留空 placeholder，后期接入
- `PageContainer` 统一渲染页面标题/副标题/操作区，避免每个页面重复实现
- 移动端不优先支持，但 1280px 以下需要保证 Sidebar 自动收起

### 9.6 12 个页面的关键交互模式

每个页面用 1-3 句话描述与设计稿对应的核心交互。

| 页面 | 关键交互 |
|---|---|
| **Login** | 邮箱 + 密码登录；失败 5 次锁定提示；记住我（写 refresh token 到 httpOnly cookie） |
| **Register** | 公司邮箱域名实时校验；提交后跳"邮件已发送"提示页（不暴露邮箱是否存在） |
| **ForgotPassword** | 输入邮箱 → 提交 → 统一文案"如已注册，会发送邮件"；不区分邮箱是否存在 |
| **ResetPassword** | URL 含 token；表单两次输入；提交后跳登录 |
| **Dashboard** | KPI 4 卡 + 上传趋势折线 + 部门贡献柱状 + 分类环形 + 最近上传/失败动态；自动刷新 60s |
| **Upload** | 左：拖拽区 + 上传进度列表；右：元数据表单（标题/分类/Dataset/标签/可见范围/立即同步开关）；批量上传共用元数据 |
| **MyFiles** | 顶部 5 KPI（我的上传/待审/已同步/解析中/失败）；表格行内"重新提交"和"申请删除"；按状态筛选 |
| **FileManagement** | 顶部 4 KPI；高级筛选（上传人/分类/审核状态/同步状态/敏感等级）；批量审核/同步/导出；行内"通过/驳回/手动同步" |
| **FileDetail** | 左主区：基本信息 + AI 分析摘要/分类/标签/敏感/质量评分 + 同步日志；右栏：操作按钮（通过/驳回/手动同步/禁用） + 操作历史 |
| **DatasetConfig** | 顶部 4 KPI；表格行：分类 / 编码 / 目标 Dataset / 是否需审核 / 默认可见 / 是否允员工选 / 启停开关；"测试连接"按钮调用 RAGFlow ping |
| **AiConfig** | 4 个 Tabs：功能开关（含 8 个子开关）/ 模型供应商（CRUD + 测试连接）/ Prompt 模板（编辑 + 测试）/ 敏感规则（CRUD + 测试）；API Key 输入时脱敏，仅显示 `sk-****abcd` |
| **Statistics** | 时间范围 + 部门/分类筛选；KPI 5 + 上传趋势 + 部门排行柱状 + 分类环形 + 用户上传明细表（支持排序、导出） |
| **Users** | KPI 4（总数/活跃/待激活/已禁）；用户表 + 角色筛选；批量导出；新增用户走弹窗；操作含"重置密码/禁用/启用" |
| **Settings** | 6 个 Tabs：基础设置 / 安全认证 / 存储与上传 / RAGFlow 集成 / 邮件通知 / 系统监控；左侧 4 KPI 显示版本/环境/服务状态/最近备份；保存前二次确认 |

### 9.7 表格通用能力（对齐 design.md §7.3）

所有管理类表格基于 `components/DataTable/`（封装 `@ant-design/pro-components` 的 `ProTable`）：

- 搜索框（顶部全局 + 列搜索）
- 高级筛选（可收起/展开的卡片）
- 列排序
- 分页（页大小 10/20/50/100）
- 批量选择 + 批量操作按钮
- 导出（CSV / Excel，调用后端 `/api/admin/statistics/export` 或专门导出接口）
- 列宽自适应 + 列配置（显示/隐藏列、保存到 localStorage）
- 行内操作（最多 3 个按钮，超出收进"更多"下拉）
- 状态列统一渲染为 `<StatusTag>`
- 空状态文案统一（"暂无数据"）
- 加载状态使用骨架屏（非 spinner）

### 9.8 ECharts 使用约定

- 仪表盘 + 统计页 + 文件管理顶部 KPI 趋势用 `echarts-for-react`
- 图表统一调用 `useChartTheme()` hook，主题色与 `tokens.colors` 对齐
- 图表容器统一用 `<ChartCard title="..." extra={...}>`
- 不在前端做复杂数据聚合，所有聚合在后端 `statistics_service` 完成
- 仪表盘 4 类图：折线（趋势）/ 柱状（排行）/ 环形（占比）/ 动态列表（最近）

### 9.9 响应式适配

design.md §8.3 已规定：

| 断点 | 行为 |
|---|---|
| ≥1440px | 主体布局，KPI 4 列、Dashboard 卡片网格 4×N |
| 1280-1440px | KPI 4 列，但卡片内字号自动缩小 8% |
| 1024-1280px | KPI 自动堆 2×2；Sidebar 默认收起 |
| <1024px | 表格横向滚动；不主动适配，但不能崩溃 |

实现：用 Ant Design 的 `Grid` 系统 + CSS Container Queries（modern browser）。

### 9.10 文案与可访问性

- 中文文案直接 hard-code（不引入 i18n 框架，避免阶段 0 包大小膨胀）
- 但所有用户可见文本通过 `src/constants/copy.ts` 集中导出，便于后期 i18n
- 错误提示统一通过 `notification.error` / `message.error` 渲染，避免散落
- 所有交互按钮都有 hover / focus 视觉反馈（Ant Design 默认即可）
- 颜色不能是唯一信息传达手段，状态 Tag 同时含文字

### 9.11 阶段 0 对前端的最小要求

阶段 0 不要求实现所有页面，只需：

- [x] `AppShell` + `Sidebar` + `TopHeader` 跑通
- [x] 路由占位 12 主页面 + 4 辅助页（页面内可显示"待实现"）
- [x] `theme/tokens.ts` + `antd-theme.ts` 落地
- [x] `StatusTag` 组件实现（即使没数据也能 storybook 演示）
- [x] `Login` 页能调通后端 `/api/auth/login`（即使后端只返 mock）

设计稿中的复杂页面（Dashboard / FileManagement / AiConfig / Statistics）在阶段 1-7 对应阶段实现。

---

## 10. 修订记录

| 版本 | 日期 | 修改 |
|---|---|---|
| v1.0 | 2026-06-04 | 初版。覆盖跨平台跨架构、域事件总线、文件级目录、CLAUDE.md、版本锁、上游不一致修正、阶段 0 checklist |
| v1.1 | 2026-06-04 | 整合 `knowledge_platform_design_package/`。新增 §9 前端设计实现指南、§7.5 / §7.6 前端不一致修正、§1 决策摘要 5 项新增、§4.11 前端结构对齐 12 路由 + 全局 Layout、§8 checklist 加入设计 token / Layout 实现项 |

---

## 11. 下一步

完成本补充 spec 的 review 后：

1. **阶段 0 实施**：按 §8 checklist 逐项落地
2. **每阶段开始前**：重读 02、03、design.md、本补充 spec 的相关章节
3. **每阶段结束**：跑通验收，提交 PR
4. **阶段 4（任务队列）启动时**：本补充 §3 域事件总线的实现细化为代码
5. **阶段 5（RAGFlow 集成）启动时**：RagflowClient 严格按 06 §6 实现，所有 API Key 走 §5 安全规则
6. **阶段 1-7 前端页面实施**：每个页面参考 `images/<n>_<name>.png`，按 §9.6 的关键交互模式 + §9.7 表格通用能力实现

