# 保护发布与不可变 OCI 制品门禁

这条门禁只验证真实环境产生的证据和已由主 CI 构建的同一份制品，不会把单元测试、浏览器
mock、buildx 构建成功或手工填写的“通过”替代为上线结论。`staging` 与 `production` 必须在
GitHub Environments 中启用 required reviewers；没有环境审批的 workflow run 不构成发布授权。

## 不可变制品身份

候选制品只能由默认分支 `push` 触发的 `.github/workflows/knowledge-uploader.yml` 在主 CI
测试全部成功后构建。`build-release-oci` 对 backend/frontend 各执行一次 multi-platform
BuildKit solve，同时输出 `linux/amd64`、`linux/arm64` OCI layout、SBOM 和 mode=max
provenance。`release-oci-provenance.json` 严格记录并校验：

- repository、完整 Git SHA、`refs/heads/main`、workflow path、run id 与 run attempt；
- OCI archive SHA-256、index digest、每个平台 manifest/config digest 与 revision label；
- 每个平台 SBOM/provenance blob digest，以及 provenance 中实际使用的 base image digest；
- backend/frontend Dockerfile 与 Python/npm lock 输入的 SHA-256；
- 八小时有效期和由 SHA/run/attempt 构成的唯一 artifact 名。

Docker config image ID、本地 tag、相同 Git SHA 的再次构建都不是制品身份。可信身份是“主 CI
artifact id/digest + provenance checksum + OCI archive/index/platform manifest/config digest”
的组合。主 CI 同时生成：

| Artifact | 内容 | 用途 |
|---|---|---|
| `release-oci-bundle-<SHA>-<run>-<attempt>` | 两个 OCI archive + provenance/checksum | DGX 与部署消费 |
| `release-oci-provenance-<SHA>-<run>-<attempt>` | provenance/checksum | protected gate 轻量复核 |

两个 artifact 使用 GitHub Artifact v4 的不可变上传语义；workflow trust gate 还会通过 API 校验
唯一 artifact id、服务端 `sha256:` digest、来源 run、未过期状态和完整名称。

## 信任边界

| 证据包 | 受控来源 | 文件 |
|---|---|---|
| 主 CI 制品包 | `.github/workflows/knowledge-uploader.yml` | OCI archives、`release-oci-provenance.*` |
| DGX 实机包 | `.github/workflows/dgx-spark-device.yml` | infrastructure、DLQ、DGX、`dgx-oci-consumption.json`、主 CI provenance、DGX trust summary |
| 外部运维包 | `.github/workflows/protected-external-evidence.yml` | alert delivery、DR、email、Alertmanager、promtool |

`release_workflow_trust.py` 从 GitHub API 验证 repository id/full name、固定 workflow path、event、
exact `head_sha`、run attempt、success、时间窗口和 artifact digest。所有 run id 在一次授权中必须
互不相同。手工 workflow 只能从 GitHub 标记为 protected 的默认分支，或指向同一 commit 且
GitHub `verification.verified=true` 的受保护 annotated semver tag 运行；任意 dispatch ref、
lightweight/未签名 tag、失败/取消/缺失主 CI 均拒绝。

四个信任链 workflow 的远程 action 只允许 `actions/*` 与 `docker/*` 审核清单，并固定到从
官方仓库 tag ref 核验的完整 40-hex commit。workflow 默认只有 `contents: read`；需要查询 run
时才增加 `actions: read`。当前 OCI artifact 路径不请求 `id-token: write`、`packages: write`，
也不声称存在 registry push 或外部签名。

主 CI 所有 backend/frontend 构建（含 PR 预检和本地 act）都显式传入已核验的官方
manifest-list digest，不能回退 Dockerfile 的可变默认 tag。promtool/amtool 同样只允许
`prom/prometheus:v3.12.0@sha256:69f524…a8ac` 与
`prom/alertmanager:v0.28.1@sha256:27c475…5ba`。外部证据生成器会按 digest 主动 pull，
记录完整索引 digest、实际 image ID、OS、镜像架构和 Docker daemon 架构，并在检查前后复核
镜像身份；最终 gate 再与完整内置 digest 比对。可变 tag、其他 digest（包括直接传单平台
manifest）、架构错配或检查期间缓存身份变化都会失败。文档中的省略显示不能用于配置。

若以后改用 registry，必须只传递 `repo@sha256:<manifest>`，由主 CI 使用 GitHub OIDC 生成并
发布 artifact attestation；只有构建 job 可授予 `id-token: write`、`attestations: write` 和最小
`packages: write`。DGX、protected gate 与部署必须验证 issuer、repository、workflow path、
protected ref、Git SHA、subject digest，不能退回 tag 或重新构建。

## 一次发布的执行顺序

1. 候选 commit 合入受保护默认分支，等待 `Knowledge Uploader CI` 整个 run 成功；记录 main CI
   run id 与 attempt。没有两个不可变 OCI artifact 时停止。
2. 在同一 commit/受保护 ref 手动运行 `DGX Spark physical device gate`，输入 main CI run id 与
   attempt。在线 trust fetch 先解析 bundle 的 immutable artifact id，DGX 只按该 ID 下载，
   再运行 `release_oci.py load-arm64` 并验证本地 Docker image
   ID 等于 OCI arm64 config digest；workflow 中禁止 `docker build`。
3. DGX 用这些 image alias 执行真实 Compose/E2E 与设备验证；`bind-dgx` 再核对
   `infrastructure-e2e.json`/`dgx-spark-evidence.json` 的 image ID 与原 OCI config digest，生成
   `dgx-oci-consumption.json`。本地 alias 只用于 Compose，结束即删除。
4. 按 [可观测性手册](observability.md)、[备份恢复手册](backup-restore.md) 和
   [认证邮件投递契约](../../docs/operations/email-delivery.md) 在同一 protected environment
   完成真实演练，再运行 `Protected external evidence collector`。其 artifact 名固定为
   `protected-release-external-evidence-<SHA>-<run>-<attempt>`，不接受用户自选名称；校验器
   镜像必须保持 workflow 中的完整 manifest-list digest，不得改成 tag 或平台子 manifest。
5. 手动运行 `Protected release evidence gate`，输入 main CI、DGX、外部证据各自的 run id 与
   attempt。在线 trust summary 固定三份来源 artifact 的 id/digest，下载步骤只消费这些 ID；
   门禁随后要求 main/DGX provenance 逐字节一致，白名单复制证据，运行
   `check_protected_release.py`，随后生成 30 分钟有效的 `release-authorization.json`。
6. 最终 artifact 名为
   `protected-release-validated-<SHA>-<environment>-<release-run>-<attempt>`。授权文件记录原始
   main CI bundle artifact id/digest、DGX/外部 artifact id/digest、每个 OCI digest、四个
   workflow run id/attempt 与全部证据 checksum。

通用运维证据新鲜度仍为两小时；OCI provenance 最长八小时；deployment authorization 只有
30 分钟。保留期不会延长授权有效期，过期必须重新执行真实证据和门禁，不得编辑时间戳。

## 部署交接契约

仓库当前没有获授权的生产部署 workflow，也没有可证明已使用的 OCI registry；因此这里只
提供 fail-closed 交接接口，不宣称部署完成。部署执行器必须通过 authorization 中的
`source_artifact.artifact_id` 从记录的 main CI run 下载原 bundle，而不是按 tag 搜索或重建；
下载后先执行：

```powershell
python scripts/release_oci.py verify-deployment `
  --authorization artifacts/release-authorization.json `
  --bundle-dir release-oci `
  --repository <owner/name> `
  --git-sha <full-sha> `
  --environment production
```

该命令复验 authorization checksum/TTL、protected ref、run 唯一性、artifact/provenance 身份，
并重新哈希两个 OCI archives、index、manifest、config、SBOM、provenance 与 base materials。
随后才可运行 `release_oci.py load-arm64`，并在启动后核对容器 `.Image`/本地 image ID 等于授权
中的 config digest。任一 digest 不同立即停止；禁止用同 SHA 重建、`:latest` 或本地 tag 兜底。

本地 sidecar checksum 只能证明文件传输一致性，不能证明 authorization 来自 GitHub 的
protected workflow。未来生产部署执行器必须在调用上述命令之前，通过 GitHub API 在线核验
exact protected-release run id/attempt、成功状态、protected ref 以及 validated artifact 的
server artifact id/digest，或验证等价的 OIDC 签名/attestation。仓库尚无该部署执行器，因此
这一来源认证仍为 **PENDING**，不得把 `verify-deployment` 单独当成生产授权。

## 本地证据检查

已有真实证据目录仍可执行：

```powershell
invoke ship `
  --evidence-dir=artifacts `
  --alertmanager-config=artifacts/alertmanager.yml `
  --git-sha=<40-or-64-character-sha> `
  --environment=staging `
  --backend-api-host=127.0.0.1
```

`invoke ship` 不会生成主 CI OCI artifact、GitHub run provenance、DGX/告警/邮件/DR 证据或生产
部署授权；它也不能把本地测试升级为物理发布证据。

## 当前证据状态与失败处置

- 代码契约与离线伪造用例已实现；真实 GitHub main-CI OCI artifact、DGX load/Compose、registry
  push、production deployment、真实 SMTP/告警接收人与隔离 DR 演练仍为 **PENDING**。
- DGX 流程失败：保留 run 日志；确认 E2E 清理后使用新的 DGX run，禁止设备端重建。
- 外部收集失败：修复真实演练或源目录，不要修改 JSON 伪造 `passed`。
- protected gate 失败：按 main/DGX/external run 身份或 digest 定位；禁止合并/重传证据绕过来源。
- 未执行物理证据时，验收矩阵 `E2E/DLQ/OBS/ARM/DR` 必须继续为“待执行”。
