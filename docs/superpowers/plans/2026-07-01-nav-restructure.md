# 导航栏分组重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将侧边栏 13 个平铺菜单项重组为 4 个 Menu group（工作台/文档/知识库/系统），同时重命名 3 个菜单项使语义更清晰，修复图标复用问题。

**Architecture:** 在 `routes.tsx` 中为每个路由添加 `group` 属性，Sidebar 按 group 分组渲染 antd Menu 的 `type: "group"` 项。同步更新页面标题、面包屑文案、搜索别名和所有测试断言。

**Tech Stack:** React 18 + Ant Design 5 Menu (ItemGroup) + Vitest

## Global Constraints

- 颜色/圆角/间距/阴影使用 `theme/tokens.ts` CSS 变量
- 提交格式：`type(scope): 中文描述`（无 trailer）
- 测试：Vitest + React Testing Library

## 命名变更清单（全计划唯一事实源）

| 原名 | 新名 | 理由 |
|------|------|------|
| 仪表盘 | 运营总览 | 与"统计报表"形成对比：总览=实时，报表=历史 |
| 文件管理 | 文件审核 | 页面核心功能是审核+同步，不是 CRUD |
| 统计分析 | 统计报表 | 与"运营总览"区分，强调历史分析+导出 |

## 分组结构（全计划唯一事实源）

| 组名 | 路由路径 | 标签 | 图标 | 角色限制 |
|------|---------|------|------|---------|
| 工作台 | /dashboard | 运营总览 | DashboardOutlined | system_admin |
| 文档 | /upload | 上传文件 | CloudUploadOutlined | 全员 |
| 文档 | /my-files | 我的文件 | FileTextOutlined | 全员 |
| 文档 | /files | 文件审核 | FolderOpenOutlined | dept_admin, system_admin |
| 知识库 | /datasets | Dataset 配置 | DatabaseOutlined | system_admin |
| 知识库 | /categories | 分类管理 | AppstoreOutlined | system_admin |
| 知识库 | /tags | 标签管理 | TagsOutlined | system_admin |
| 系统 | /ai-config | AI 配置 | RobotOutlined | system_admin |
| 系统 | /users | 用户管理 | TeamOutlined | system_admin |
| 系统 | /settings | 系统设置 | SettingOutlined | system_admin |
| 系统 | /statistics | 统计报表 | BarChartOutlined | system_admin |
| 系统 | /audit-logs | 操作日志 | AuditOutlined | system_admin |
| 系统 | /task-logs | 任务日志 | OrderedListOutlined | dept_admin, system_admin |

---

### Task 1: 路由数据添加 group + 重命名 + 图标修复

修改 `routes.tsx`——为 `AppRoute` 接口添加 `group` 字段，重命名 3 个标签，修正分类管理图标。这是整个计划的数据基础。

**Files:**
- Modify: `frontend/src/router/routes.tsx`
- Modify: `frontend/src/router/routes.test.tsx`

**Interfaces:**
- Consumes: 无
- Produces: `AppRoute.nav` 新增可选 `group?: string`，导出的 `appRoutes` 数组包含分组和新标签

- [ ] **Step 1: 修改 AppRoute 接口，添加 group**

在 `frontend/src/router/routes.tsx` 中，将 `RouteNavigation` 接口修改为：

```tsx
export interface RouteNavigation {
  label: string;
  icon: ReactNode;
  group?: string;
}
```

- [ ] **Step 2: 添加 AppstoreOutlined import**

在 icon import 块中添加 `AppstoreOutlined`：

```tsx
import {
  AppstoreOutlined,
  AuditOutlined,
  // ...existing imports
```

- [ ] **Step 3: 更新所有 appRoutes 的 nav 属性**

按「分组结构」表逐项更新每个路由的 `nav` 对象。关键变更：

1. `/dashboard`: `label: "运营总览"`, 添加 `group: "工作台"`
2. `/upload`: `label: "上传文件"`, 添加 `group: "文档"`
3. `/my-files`: `label: "我的文件"`, 添加 `group: "文档"`
4. `/files`: `label: "文件审核"`, 添加 `group: "文档"`
5. `/datasets`: 添加 `group: "知识库"`
6. `/categories`: `icon: <AppstoreOutlined />`, 添加 `group: "知识库"`
7. `/tags`: 添加 `group: "知识库"`
8. `/ai-config`: 添加 `group: "系统"`
9. `/statistics`: `label: "统计报表"`, 添加 `group: "系统"`
10. `/users`: 添加 `group: "系统"`
11. `/settings`: 添加 `group: "系统"`
12. `/audit-logs`: 添加 `group: "系统"`
13. `/task-logs`: 添加 `group: "系统"`

完整的 `appRoutes` 数组：

```tsx
export const appRoutes: AppRoute[] = [
  {
    path: "/dashboard",
    element: routeElement(DashboardPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "运营总览", icon: <DashboardOutlined />, group: "工作台" },
  },
  {
    path: "/upload",
    element: routeElement(UploadPage),
    nav: { label: "上传文件", icon: <CloudUploadOutlined />, group: "文档" },
  },
  {
    path: "/my-files",
    element: routeElement(MyFilesPage),
    nav: { label: "我的文件", icon: <FileTextOutlined />, group: "文档" },
  },
  {
    path: "/files",
    element: routeElement(FileManagementPage),
    roles: [Roles.DEPT_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "文件审核", icon: <FolderOpenOutlined />, group: "文档" },
  },
  {
    path: "/files/:id",
    element: routeElement(FileDetailPage),
  },
  {
    path: "/datasets",
    element: routeElement(DatasetConfigPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "Dataset 配置", icon: <DatabaseOutlined />, group: "知识库" },
  },
  {
    path: "/ai-config",
    element: routeElement(AiConfigPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "AI 配置", icon: <RobotOutlined />, group: "系统" },
  },
  {
    path: "/statistics",
    element: routeElement(StatisticsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "统计报表", icon: <BarChartOutlined />, group: "系统" },
  },
  {
    path: "/users",
    element: routeElement(UsersPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "用户管理", icon: <TeamOutlined />, group: "系统" },
  },
  {
    path: "/settings",
    element: routeElement(SettingsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "系统设置", icon: <SettingOutlined />, group: "系统" },
  },
  {
    path: "/audit-logs",
    element: routeElement(AuditLogsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "操作日志", icon: <AuditOutlined />, group: "系统" },
  },
  {
    path: "/task-logs",
    element: routeElement(TaskLogsPage),
    roles: [Roles.DEPT_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "任务日志", icon: <OrderedListOutlined />, group: "系统" },
  },
  {
    path: "/categories",
    element: routeElement(CategoriesPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "分类管理", icon: <AppstoreOutlined />, group: "知识库" },
  },
  {
    path: "/tags",
    element: routeElement(TagsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "标签管理", icon: <TagsOutlined />, group: "知识库" },
  },
  {
    path: "/profile",
    element: routeElement(ProfilePage),
  },
];
```

- [ ] **Step 4: 更新 routes.test.tsx**

将 `routes.test.tsx` 中的旧标签替换为新标签：

```tsx
  it("limits dept admin navigation to scoped file and task surfaces", () => {
    const labels = navigationLabelsForRole(Roles.DEPT_ADMIN);

    expect(labels).toEqual(
      expect.arrayContaining(["上传文件", "我的文件", "文件审核", "任务日志"]),
    );

    for (const hiddenLabel of [
      "运营总览",
      "Dataset 配置",
      "AI 配置",
      "统计报表",
      "用户管理",
      "系统设置",
      "操作日志",
      "分类管理",
      "标签管理",
    ]) {
      expect(labels).not.toContain(hiddenLabel);
    }

    expect(appRoutes.find((route) => route.path === "/profile")?.roles).toBeUndefined();
  });
```

- [ ] **Step 5: 运行 routes 测试**

```bash
cd frontend && npx vitest run src/router/routes.test.tsx --reporter=verbose
```

Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/router/routes.tsx frontend/src/router/routes.test.tsx
git commit -m "refactor(frontend): 路由添加 group 分组、重命名三个菜单项、修复分类图标"
```

---

### Task 2: Sidebar 按 group 分组渲染

修改 Sidebar 组件，将平铺的 `Menu.Item` 列表转为按 `group` 分组的 `ItemGroup` 结构。只渲染当前角色有权限看到的组。

**Files:**
- Modify: `frontend/src/layouts/Sidebar.tsx`
- Modify: `frontend/src/layouts/Sidebar.test.tsx`
- Modify: `frontend/src/styles.css` (添加分组标题样式)

**Interfaces:**
- Consumes: `AppRoute.nav.group` 字段（Task 1 产出）
- Produces: Sidebar 渲染分组菜单，空组不显示

- [ ] **Step 1: 重构 Sidebar 菜单项构建逻辑**

在 `frontend/src/layouts/Sidebar.tsx` 中，将现有的 `menuItems` 构建逻辑：

```tsx
const menuItems = appNavigationRoutes
  .filter((route) => !route.roles || (role ? route.roles.includes(role) : false))
  .map((route) => ({
    key: route.path,
    icon: route.nav?.icon,
    label: route.nav?.label,
  }));
```

替换为按 group 分组的逻辑：

```tsx
const menuItems = useMemo(() => {
  const visibleRoutes = appNavigationRoutes.filter(
    (route) => !route.roles || (role ? route.roles.includes(role) : false),
  );

  const grouped = new Map<string, typeof visibleRoutes>();
  for (const route of visibleRoutes) {
    const group = route.nav?.group ?? "";
    const list = grouped.get(group) ?? [];
    list.push(route);
    grouped.set(group, list);
  }

  const items: MenuProps["items"] = [];
  for (const [group, routes] of grouped) {
    if (!group) {
      for (const route of routes) {
        items.push({ key: route.path, icon: route.nav?.icon, label: route.nav?.label });
      }
      continue;
    }
    items.push({
      type: "group" as const,
      label: group,
      children: routes.map((route) => ({
        key: route.path,
        icon: route.nav?.icon,
        label: route.nav?.label,
      })),
    });
  }
  return items;
}, [role]);
```

添加 `useMemo` import（如果尚未导入），添加 `type MenuProps` from antd import。

- [ ] **Step 2: 添加分组标题 CSS**

在 `frontend/src/styles.css` 中，在 `.sidebar-menu` 规则后添加：

```css
.sidebar-menu .ant-menu-item-group-title {
  padding: 16px 12px 4px;
  color: var(--ku-text-disabled);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.sidebar-menu .ant-menu-item-group:first-child .ant-menu-item-group-title {
  padding-top: 0;
}
```

Sidebar 折叠时隐藏分组标题：

```css
.app-shell__sider.ant-layout-sider-collapsed .ant-menu-item-group-title {
  display: none;
}
```

- [ ] **Step 3: 更新 Sidebar.test.tsx**

将断言中的旧标签替换为新标签：

```tsx
expect(screen.getByText("运营总览")).toBeInTheDocument();
expect(screen.getByText("统计报表")).toBeInTheDocument();
```

- [ ] **Step 4: 运行 Sidebar 测试**

```bash
cd frontend && npx vitest run src/layouts/Sidebar.test.tsx --reporter=verbose
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/layouts/Sidebar.tsx frontend/src/layouts/Sidebar.test.tsx frontend/src/styles.css
git commit -m "refactor(frontend): Sidebar 按 group 分组渲染菜单项"
```

---

### Task 3: 同步页面标题、面包屑、搜索别名和测试

3 个菜单重命名需要在页面标题（PageContainer title）、面包屑（breadcrumb label）、TopHeader 搜索别名和所有测试文件中同步。

**Files:**
- Modify: `frontend/src/pages/FileManagement/index.tsx` (PageContainer title "文件管理" → "文件审核")
- Modify: `frontend/src/pages/Statistics/index.tsx` (PageContainer title "统计分析" → "统计报表")
- Modify: `frontend/src/pages/FileDetail/index.tsx` (breadcrumb label "文件管理" → "文件审核")
- Modify: `frontend/src/layouts/TopHeader.tsx` (搜索别名添加新名)
- Modify: `frontend/src/layouts/PageContainer.test.tsx`
- Modify: `frontend/src/layouts/TopHeader.test.tsx`
- Modify: `frontend/src/pages/Statistics/index.test.tsx`

**Interfaces:**
- Consumes: 新标签名（见计划顶部"命名变更清单"）
- Produces: 全站标签一致

- [ ] **Step 1: FileManagement 页标题**

`frontend/src/pages/FileManagement/index.tsx` 中：

```tsx
// 原
title="文件管理"
// 改为
title="文件审核"
```

- [ ] **Step 2: Statistics 页标题**

`frontend/src/pages/Statistics/index.tsx` 中：

```tsx
// 原
title="统计分析"
// 改为
title="统计报表"
```

- [ ] **Step 3: FileDetail 面包屑**

`frontend/src/pages/FileDetail/index.tsx` 中：

```tsx
// 原
{ label: "文件管理", path: "/files" },
// 改为
{ label: "文件审核", path: "/files" },
```

- [ ] **Step 4: TopHeader 搜索别名**

`frontend/src/layouts/TopHeader.tsx` 中，更新 `GLOBAL_SEARCH_ALIASES`：

```tsx
"/dashboard": ["运营", "看板", "总览", "概览", "仪表盘"],
"/files": ["文件审核", "文件管理", "审核", "同步", "RAGFlow"],
"/statistics": ["统计", "报表", "贡献排行", "统计分析"],
```

为 `/dashboard` 添加 "仪表盘" 别名（向后兼容搜索习惯），为 `/statistics` 添加 "统计分析"，为 `/files` 保留 "文件管理"。

- [ ] **Step 5: 更新 PageContainer.test.tsx**

```tsx
// 面包屑测试中
{ label: "文件审核", path: "/files" },
// 以及
expect(screen.getByText("文件审核")).toBeInTheDocument();
```

注意：`title="仪表盘"` 的测试用例不需要改——那个测试的目的是验证"不传 breadcrumb 时不渲染"，title 内容不影响断言。

- [ ] **Step 6: 更新 TopHeader.test.tsx**

第 147 行 `expect(screen.getByText("仪表盘"))` → `expect(screen.getByText("运营总览"))`

第 187 行搜索 "文件管理" 的测试——搜索别名中保留了 "文件管理"，所以这个测试不需要改。

- [ ] **Step 7: 更新 Statistics/index.test.tsx**

第 279 行和第 349 行：`"统计分析"` → `"统计报表"`

- [ ] **Step 8: 运行受影响的测试**

```bash
cd frontend && npx vitest run src/layouts/PageContainer.test.tsx src/layouts/TopHeader.test.tsx src/pages/Statistics/index.test.tsx src/pages/FileManagement/index.test.tsx src/pages/FileDetail/index.test.tsx --reporter=verbose
```

Expected: 全部 PASS

- [ ] **Step 9: 提交**

```bash
git add frontend/src/pages/FileManagement/index.tsx frontend/src/pages/Statistics/index.tsx frontend/src/pages/FileDetail/index.tsx frontend/src/layouts/TopHeader.tsx frontend/src/layouts/PageContainer.test.tsx frontend/src/layouts/TopHeader.test.tsx frontend/src/pages/Statistics/index.test.tsx
git commit -m "refactor(frontend): 同步页面标题、面包屑和搜索别名到新菜单名"
```
