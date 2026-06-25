# deploy

本目录用于生产和共享环境部署资产管理，后续可放置：

- Kubernetes manifests 或 Helm values。
- TLS / Nginx 生产入口配置样例。
- DGX Spark ARM64 部署脚本和挂载点示例。
- 环境分层说明和发布 runbook。

当前 Compose 本地部署仍以仓库根目录的 `docker-compose.yml`、`docker-compose.arm64.yml`、`nginx/` 和 `docs/deployment.md` 为准。本次只补充目录管理说明，不新增生产级 manifest。