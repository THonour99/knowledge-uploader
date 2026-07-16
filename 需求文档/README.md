# Knowledge Uploader 权威文档索引

> 恢复基线：2026-07-16。历史文档已经过合并，不应从删除提交直接整批恢复覆盖本目录。

## 阅读顺序与优先级

1. [01 PRD](./01_PRD_产品需求文档.md)：角色、范围、主链与完成定义。
2. [05 数据库/API](./05_DATABASE_API_SPEC_数据库与API规范.md)：唯一状态机与 HTTP 目标契约。
3. [02 架构](./02_ARCHITECTURE_最终架构设计.md)：不可改变的系统边界。
4. [03 后端规范](./03_BACKEND_SPEC_后端开发规范.md) 与 [04 前端规范](./04_FRONTEND_SPEC_前端开发规范.md)：实现方式。
5. [06 AI/RAGFlow](./06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md)：外部处理、幂等与失败。
6. [07 部署](./07_DEPLOYMENT_ENV_部署与环境配置.md)：环境、ARM64、DLQ、观测与恢复。
7. [08 任务](./08_TASK_BREAKDOWN_开发任务拆解.md)：真实阶段与五个整改工作流。
8. [视觉设计](../docs/design/design.md)、[角色 IA](../docs/product/IA_ROLE_WORKBENCH.md)、[配置契约](../docs/product/CONFIG_CONTRACT.md)、[验收矩阵](../docs/product/ACCEPTANCE_MATRIX.md)。

## 权威与实现快照

- 本目录写“应当如何工作”，每项未实现状态由验收矩阵管理。
- `docs/api.md`、`docs/deployment.md` 是历史实现快照，不得反向覆盖本目录；实现变化后应由对应代码提交同步更新。
- `frontend/src/theme/tokens.ts` 是视觉代码单一源，但目标 palette 和页面结构由 `docs/design/design.md` 决定。
- AGENTS.md 的安全/架构红线高于普通建议；若发现冲突，停止实现并先更新决策记录与受影响测试。

## 当前阶段

阶段 9（联调、上线与文档）尚未完成，当前处于验收整改。只有 [验收矩阵](../docs/product/ACCEPTANCE_MATRIX.md) 满足最低发布判定并归档证据后，README 才能声明完成。
