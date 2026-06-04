---
description: 前端 React / TypeScript / Ant Design 代码规则
paths:
  - frontend/**
---

# 前端代码规则

## 1. 技术栈版本（已锁，详见补充 spec §6.3）

- React 18.3+
- TypeScript 5.4+
- Vite 5.2+
- Ant Design 5.16+ + @ant-design/pro-components 2.7+
- React Router 6.23+
- Zustand 4.5+ + TanStack Query 5.32+
- Axios 1.6+
- ECharts 5.5+ + echarts-for-react 3.0+
- dayjs 1.11+

## 2. 状态管理分工

- **UI 状态**（侧边栏收起、模态框开关、当前 tab 等）→ Zustand store（`store/`）
- **服务端状态**（用户、文件列表、统计数据等）→ TanStack Query hooks（`hooks/`）
- ❌ 不要把服务端数据塞进 Zustand（缓存、loading、错误处理 React Query 已经做了）
- ❌ 不要用 React `useState` 跨组件共享状态（要么 Zustand 要么 Query）

## 3. API 调用约定

- 所有 API 调用走 `src/api/client.ts`（axios 实例）
- 拦截器统一处理：JWT 注入 / 401 跳登录 / 错误信息标准化
- 每个领域一个 API 文件：`api/auth.ts` / `api/files.ts` / `api/admin-ai.ts` 等
- 函数签名：`async function getFiles(filter: FileFilter): Promise<FileResponse[]>`
- 错误处理统一通过 `notification.error` / `message.error` 渲染

## 4. 设计 token（不可硬编码）

- 颜色 / 圆角 / 间距全部从 `src/theme/tokens.ts` 引用
- Ant Design 组件主题通过 `theme/antd-theme.ts` 注入 `ConfigProvider`
- ❌ 禁止在组件中硬编码 `color: '#1677FF'` 或 `borderRadius: 12`
- ✅ 用 `tokens.colors.primary` 或让 Ant Design 自动接管

## 5. 状态展示（强制）

- 文件 / 审核 / 同步 / 风险 / 用户状态**统一走 `<StatusTag kind="..." value="..." />`**
- 状态颜色映射在 `components/StatusTag.tsx` 中维护（参考补充 spec §9.4）
- ❌ 不要在页面中直接 `<Tag color="green">...`

## 6. 路由与权限

- 路由表定义在 `src/router/routes.ts`，含每条路由的可见角色
- 守卫 `src/router/guards.tsx` 统一处理未登录跳转和角色拒绝
- 角色枚举从 `src/types/user.ts` 导入
- ❌ 不要在页面内手动 `if (role !== 'admin') return null`

## 7. 表格统一

- 管理类表格用 `components/DataTable/`（基于 `@ant-design/pro-components`）
- 提供：搜索、筛选、排序、分页、批量、导出、列配置
- 列宽自适应，操作列 fixed right

## 8. 表单

- 长表单用 Ant Design `Form` + `ProForm`
- 提交按钮 disabled until 表单 valid
- 复杂校验逻辑写在 `utils/validators.ts`
- 提交失败必须保留输入值

## 9. 文件大小 / 时间 / 数字格式化

- 用 `utils/format.ts` 统一函数：`formatFileSize`、`formatDateTime`、`formatNumber`
- 时间默认 `YYYY-MM-DD HH:mm`
- 文件大小 KB / MB / GB 自动选

## 10. 代码风格

- TypeScript strict 模式，禁用 `any`（必须时用 `unknown` + 类型守卫）
- 函数组件，全部用 `function ComponentName(props: Props)` 不用 arrow
- 组件 props 类型在文件内定义，命名 `XxxProps`
- 不导出未使用的类型 / 组件
- ESLint + Prettier 自动 fix

## 11. 文件命名

- 组件：`PascalCase.tsx`（如 `StatusTag.tsx`）
- Hook：`camelCase.ts` 以 `use` 开头（如 `useFiles.ts`）
- 工具：`camelCase.ts`（如 `format.ts`）
- 类型：`camelCase.ts`（如 `api.ts`）

## 12. 测试

- 单元测试：Vitest + React Testing Library
- 测试文件：`Component.test.tsx`，与源文件同级
- E2E（可选）：Playwright
- 命令：`npm run test`（容器内 `invoke test-frontend`）

## 13. 国际化预留

- 不引入 i18n 框架（增大包体积）
- 但所有用户可见文本通过 `src/constants/copy.ts` 集中导出
- 后期接入 i18n 时只需替换 copy 来源
