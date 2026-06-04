---
description: 检查后端依赖是否有 ARM64 wheel。当修改了 backend/requirements.txt 或 requirements-dev.txt 时自动调用；也可手动调用以预检某个包。
---

# Check ARM64

检查依赖在 ARM64（DGX Spark 部署目标）上的可用性。

## 使用时机

- 修改了 `backend/requirements.txt` / `backend/requirements-dev.txt`
- 引入新依赖前预检（"我能用 xx 包吗？"）
- CI 在 PR 阶段自动跑

## 命令

```powershell
# 默认检查 backend/requirements.txt + requirements-dev.txt
invoke check-arm64

# 等价于
python scripts/check_arm64_wheels.py backend/requirements.txt backend/requirements-dev.txt

# 单包检查
python scripts/check_arm64_wheels.py --package=psycopg --version=3.1.18
```

## 输出解读

```
=== backend/requirements.txt (32 packages) ===
  ✓ fastapi==0.110.1: fastapi-0.110.1-py3-none-any.whl
  ✓ uvicorn[standard]==0.29.0: uvicorn-0.29.0-py3-none-any.whl
  ✓ sqlalchemy[asyncio]==2.0.30: SQLAlchemy-2.0.30-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
  ✓ asyncpg==0.29.0: asyncpg-0.29.0-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
  ✓ psycopg[binary]==3.1.18: psycopg_binary-3.1.18-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
  ✗ python-magic==0.4.27: 无 ARM64 wheel 且无 sdist
  ...

失败 (1):
  - python-magic==0.4.27

退出码: 1
```

## 失败处理

| 失败情况 | 解决 |
|---|---|
| 无 wheel 但有 sdist | 视为通过（容器内编译，但增加构建时间） |
| 无 wheel 无 sdist | 必须替换（看补充 spec §2.5.2 替代清单） |
| 包不存在 | 检查拼写 / 是否私有包 |
| PyPI 查询超时 | 重试，或离线模式（如有镜像） |

## 替代清单

参考补充 spec §2.5.2：

| 禁用 | 替代 |
|---|---|
| `psycopg2*` | `psycopg[binary]` v3 |
| `python-magic` / `python-magic-bin` | `filetype` |
| `mysqlclient` | 项目用 PG（不应出现） |
| `pycrypto` | `cryptography` |
| `m2crypto` | `cryptography` |

## 引入新依赖的完整流程

```
1. 评估必要性
   - 现有依赖能不能搞定？（避免依赖膨胀）
   - 替代方案？（纯 Python > C 扩展）

2. 锁定版本
   - 在 requirements.txt 中加 `<pkg>==<version>`
   - 不用 `>=` 或 `~=`（确定性优先）

3. 检查 ARM64
   - invoke check-arm64
   - 失败 → 找替代

4. 检查 license
   - 必须 OSI 兼容
   - 商业 license（如 MongoDB SSPL）需法务确认

5. 检查 security
   - pip-audit（如有）
   - 查 PyPI 维护活跃度（最后发布日期、issue 数量）

6. 文档
   - 如果是关键依赖，在补充 spec §6.2 表格中加一行（说明用途）
```

## 自动化（已在 settings.json）

`.Codex/scripts/check-arm64-on-deps-change.ps1` 作为 PostToolUse hook：
- 当 Codex 编辑了 `backend/requirements*.txt`
- 自动跑 `invoke check-arm64`
- 失败 → 在结果中报错，提示替换

## 不要做

- ❌ 引入 `>=` 不锁版本
- ❌ 因为方便用 ARM64 不兼容的包
- ❌ 跳过 check（"待会再检查"）
- ❌ 在 Dockerfile 装系统包绕过（增加镜像大小 + 编译时间）
