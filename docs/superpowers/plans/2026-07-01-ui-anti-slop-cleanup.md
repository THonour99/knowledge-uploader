# UI Anti-Slop 清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除前端 UI 中的 AI-slop 模式（重复 strip 组件、无意义 KPI 卡、筛选器过载、操作列拥挤），提升信息层次和专业感。

**Architecture:** 5 个独立任务，全部是前端变更。每个任务产出可独立验证的 UI 改进。从提取共享组件开始，然后逐页清理，最后优化表格交互。

**Tech Stack:** React 18 + Ant Design 5 + CSS (BEM) + Vitest + React Testing Library

## Global Constraints

- 颜色/圆角/间距/阴影必须使用 `theme/tokens.ts` 中的 CSS 变量
- 状态展示必须使用 `<StatusTag>` 组件
- UI 状态管理使用 Zustand
- 所有 API 调用走 `api/client.ts`
- 提交格式：`type(scope): 中文描述`
- 测试：Vitest + React Testing Library

---

### Task 1: 删除 Upload、MyFiles、Profile 页面的冗余 Strip

Upload 的 `UploadPipelineStrip`（6 步流水线）、MyFiles 的 `my-files-status-strip`、Profile 的 `profile-status-strip` 对用户没有决策价值——它们展示的信息与同页面的 KPI 卡片完全重复。直接删除。

**Files:**
- Modify: `frontend/src/pages/Upload/index.tsx` (删除 UploadPipelineStrip 组件定义和调用)
- Modify: `frontend/src/pages/MyFiles/index.tsx` (删除 my-files-status-strip 整段 JSX)
- Modify: `frontend/src/pages/Profile/index.tsx` (删除 profile-status-strip 整段 JSX)
- Modify: `frontend/src/styles.css` (删除对应的 CSS 块)
- Modify: `frontend/src/pages/Upload/index.test.tsx`
- Modify: `frontend/src/pages/MyFiles/index.test.tsx`
- Modify: `frontend/src/pages/Profile/index.test.tsx`

**Interfaces:**
- Consumes: 无外部依赖
- Produces: 三个页面不再渲染 strip 组件，CSS 文件缩减约 300 行

- [ ] **Step 1: 修改 Upload 页——删除 UploadPipelineStrip 组件**

从 `frontend/src/pages/Upload/index.tsx` 中：

1. 删除 `UploadPipelineStrip` 函数组件定义（第 81–193 行）及其接口 `UploadPipelineStep` 和类型 `PipelineTone`（第 67–79 行）。
2. 删除 JSX 中对 `UploadPipelineStrip` 的调用（第 434–443 行）。
3. 删除不再使用的 import：`CloudSyncOutlined`、`CheckCircleOutlined`、`FileSearchOutlined`、`SafetyCertificateOutlined`。
4. 保留 `StatusTag` import（上传队列仍在使用）。

- [ ] **Step 2: 修改 MyFiles 页——删除 strip 段落**

从 `frontend/src/pages/MyFiles/index.tsx` 中：

1. 删除 `<section className="my-files-status-strip">` 整段 JSX（第 374–458 行）。
2. 删除仅被 strip 使用的派生数据变量：`uploadPolicyPreview`（第 237–242 行）、`uploadPolicyStatus`（第 243–247 行）、`pageHealthStatus`（第 248–249 行）、`syncHealthStatus`（第 250 行）、`draftCount`（第 161 行）、`activeFilterCount`（第 229–236 行）。
3. 删除不再使用的 import：`SearchOutlined`（如果筛选栏内还在用则保留——它在第 476 行作为 Input prefix 使用，保留）。
4. 删除 `uploadPolicyQuery`（第 114–117 行）和 `allowedExtensions` 派生（第 152–155 行）只在 strip 和 extensionFilter 中使用。注意：extensionFilter 在筛选栏的 Select options 中也用到了 `allowedExtensions`（第 511 行），所以如果 extensionFilter Select 仍需要 options，`uploadPolicyQuery` 和 `allowedExtensions` 必须保留。检查后确认保留。

最终只删 strip JSX 和仅被 strip 引用的 5 个变量。

- [ ] **Step 3: 修改 Profile 页——删除 strip 段落**

从 `frontend/src/pages/Profile/index.tsx` 中：

1. 删除 `<section className="profile-status-strip">` 整段 JSX（第 104–179 行之间的整个 section）。
2. 删除仅被 strip 使用的派生变量：`emailVerifiedValue`（第 62 行——但注意它在 Descriptions 和 "账号安全" Card 中也被使用，不能删）、`profileStatusValue`、`roleLabel`、`roleScopeLabel`、`contactHealthValue`。检查哪些仅在 strip 中使用：
   - `profileStatusValue`：在 strip 内使用 2 次。检查 strip 外是否使用——否，仅 strip。**可删。**
   - `roleLabel`：在 strip 内 1 次，在 Descriptions 里没有直接使用（Descriptions 用的是 `ROLE_LABELS[profile.role]`）。检查 strip 外——strip 第 115 行。无其他引用。**可删。**
   - `roleScopeLabel`：在 strip 内 1 次（第 159 行）、在 KPI 卡中 1 次（第 89 行）。**不能删。**
   - `contactHealthValue`：仅在 strip 内（第 172 行）。**可删。**

删除 `profileStatusValue`、`roleLabel`、`contactHealthValue` 三个变量。

- [ ] **Step 4: 删除 styles.css 中对应的 CSS 块**

从 `frontend/src/styles.css` 中删除以下 CSS 规则块：

1. `.upload-pipeline-strip` 系列（第 619–755 行，包括所有响应式变体）
2. `.upload-pipeline-step` 系列（包含在上面范围内）
3. `.my-files-status-strip` 系列（第 922–1039 行，包括响应式）
4. `.my-files-status-lane` 系列（包含在上面范围内）
5. `.profile-status-strip` 系列（第 1045–1210 行，包括响应式）
6. `.profile-status-lane` 系列（包含在上面范围内）

同时检查响应式 `@media` 块中的对应选择器（1023px 和 640px 断点内），一并删除。

- [ ] **Step 5: 修复测试文件**

更新测试文件，移除对已删除组件/文本的断言：

- `frontend/src/pages/Upload/index.test.tsx`：删除任何包含"上传流水线"、"pipeline"、"格式校验"、"去重入库" 等 strip 相关文本断言。
- `frontend/src/pages/MyFiles/index.test.tsx`：删除包含"我的知识库状态"、"个人知识库"、"上传规范" 等 strip 文本断言。
- `frontend/src/pages/Profile/index.test.tsx`：删除包含"账号治理"、"账号运行状态" 等 strip 文本断言。

- [ ] **Step 6: 运行测试验证**

```bash
cd frontend && npx vitest run src/pages/Upload/index.test.tsx src/pages/MyFiles/index.test.tsx src/pages/Profile/index.test.tsx --reporter=verbose
```

Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/pages/Upload/index.tsx frontend/src/pages/MyFiles/index.tsx frontend/src/pages/Profile/index.tsx frontend/src/styles.css frontend/src/pages/Upload/index.test.tsx frontend/src/pages/MyFiles/index.test.tsx frontend/src/pages/Profile/index.test.tsx
git commit -m "refactor(frontend): 删除 Upload、MyFiles、Profile 页面的冗余 Strip 组件"
```

---

### Task 2: 简化 Upload 页 KPI 卡片

Upload 页的 4 张 KPI 卡中，"支持格式"和"并发上传"是系统常量，不应占卡片位置。改为上传拖拽区顶部的内联文案。保留"当前队列"和"成功/失败"在上传队列卡片内部展示。

**Files:**
- Modify: `frontend/src/pages/Upload/index.tsx`
- Modify: `frontend/src/styles.css` (微调上传区样式)
- Modify: `frontend/src/pages/Upload/index.test.tsx`

**Interfaces:**
- Consumes: `allowedExtensions` 来自 `uploadConfig.ts`，`CONCURRENCY_LIMIT` 常量
- Produces: Upload 页不再渲染 KPI 卡片 grid，改为上传卡片内的描述文案

- [ ] **Step 1: 删除 KPI 卡片引用并添加内联描述**

在 `frontend/src/pages/Upload/index.tsx` 中：

1. 删除 `<div className="metric-grid">` 整段（第 403–432 行），包括 4 个 `KpiCard` 调用。
2. 删除 `KpiCard` 和 `KpiTone` 的 import（如果不再使用）。确认：`KpiCard` 在此文件中只有 metric-grid 使用——删除 import。
3. 删除不再使用的 import：`FileTextOutlined`（确认：只被 KPI 卡使用，但在上传队列 `upload-queue-row__icon` 中也使用——保留）、`InboxOutlined`（在 Upload.Dragger 中使用——保留）。仅删除 `KpiCard` 的 import。
4. 在 `Upload.Dragger` 的提示文案中增强描述，将原来的：

```tsx
<p className="ant-upload-hint">
  支持 {allowedExtensionText}
  {allowMultiFile ? "，可同时选择多个文件。" : "，当前仅允许单文件上传。"}
</p>
```

改为：

```tsx
<p className="ant-upload-hint">
  支持 {allowedExtensionText}
  {allowMultiFile ? "，可同时选择多个文件" : "，当前仅允许单文件上传"}
  ，最多 {CONCURRENCY_LIMIT} 个并发上传。
</p>
```

5. 删除 `supportedFormatValue`（第 386–387 行）变量，不再被使用。

- [ ] **Step 2: 更新测试**

`frontend/src/pages/Upload/index.test.tsx` 中：
- 删除任何对"支持格式"、"并发上传"等 KPI 卡标题的断言。
- 如果有对 `metric-grid` class 或 KpiCard 渲染的断言，删除或替换。

- [ ] **Step 3: 运行测试验证**

```bash
cd frontend && npx vitest run src/pages/Upload/index.test.tsx --reporter=verbose
```

Expected: 全部 PASS。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/Upload/index.tsx frontend/src/pages/Upload/index.test.tsx
git commit -m "refactor(frontend): 简化 Upload 页，移除常量型 KPI 卡改为内联文案"
```

---

### Task 3: FileManagement 筛选器分层

当前 9 个筛选控件同时展示导致视觉过载。改为默认展示 3 个（搜索 + 审核状态 + 同步状态），其余收进"更多筛选"展开区域。

**Files:**
- Modify: `frontend/src/pages/FileManagement/index.tsx` (添加展开/收起逻辑)
- Modify: `frontend/src/styles.css` (添加展开/收起动画样式)
- Modify: `frontend/src/pages/FileManagement/index.test.tsx`

**Interfaces:**
- Consumes: 现有的所有 filter state 变量保持不变
- Produces: 筛选器区域支持展开/收起，默认仅显示 3 个控件

- [ ] **Step 1: 写测试——筛选器默认收起，点击展开**

在 `frontend/src/pages/FileManagement/index.test.tsx` 中新增测试：

```tsx
it("hides advanced filters by default and shows them on toggle", async () => {
  render(<FileManagementPage />);

  // 默认可见：搜索框 + 审核状态 + 同步状态
  expect(screen.getByPlaceholderText("搜索文件名称、关键词")).toBeInTheDocument();

  // 默认隐藏的控件不应可见
  expect(screen.queryByText("上传人：全部")).not.toBeInTheDocument();

  // 点击展开
  await userEvent.click(screen.getByRole("button", { name: "更多筛选" }));

  // 现在应该可见
  expect(screen.getByText("上传人：全部")).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd frontend && npx vitest run src/pages/FileManagement/index.test.tsx -t "hides advanced filters" --reporter=verbose
```

Expected: FAIL — 因为"更多筛选"按钮不存在。

- [ ] **Step 3: 实现筛选器分层**

在 `frontend/src/pages/FileManagement/index.tsx` 中：

1. 在 filter state 区域添加：

```tsx
const [filtersExpanded, setFiltersExpanded] = useState(false);
```

2. 添加 import：

```tsx
import { FilterOutlined, DownOutlined, UpOutlined } from "@ant-design/icons";
```

（`FilterOutlined` 已导入，只需确认 `DownOutlined` 和 `UpOutlined`。）

3. 将筛选栏 JSX（第 915–992 行）重构为：

```tsx
<div className="filter-toolbar filter-toolbar--management">
  {/* 始终可见的核心筛选器 */}
  <Input.Search
    className="filter-toolbar__search"
    placeholder="搜索文件名称、关键词"
    value={searchText}
    onChange={(event) => setSearchText(event.target.value)}
    allowClear
  />
  <Select
    className="filter-toolbar__control"
    value={reviewFilter}
    options={[
      { label: "审核状态：全部", value: "all" },
      { label: "待审核", value: "pending_review" },
      { label: "已通过", value: "approved" },
      { label: "未通过", value: "rejected" },
    ]}
    onChange={setReviewFilter}
  />
  <Select
    className="filter-toolbar__control"
    value={syncFilter}
    options={[
      { label: "同步状态：全部", value: "all" },
      { label: "未同步", value: "not_synced" },
      { label: "同步中", value: "syncing" },
      { label: "已同步", value: "synced" },
      { label: "同步失败", value: "failed" },
    ]}
    onChange={setSyncFilter}
  />
  <Button
    type="text"
    icon={filtersExpanded ? <UpOutlined /> : <DownOutlined />}
    onClick={() => setFiltersExpanded((prev) => !prev)}
    aria-label="更多筛选"
  >
    更多筛选
  </Button>

  {/* 展开后可见的高级筛选器 */}
  {filtersExpanded ? (
    <>
      <Select
        className="filter-toolbar__control"
        value={uploaderFilter}
        options={[{ label: "上传人：全部", value: "all" }, ...uploaderOptions]}
        onChange={setUploaderFilter}
      />
      <Select
        className="filter-toolbar__control"
        value={categoryFilter}
        options={[{ label: "分类：全部", value: "all" }, ...categoryOptions]}
        onChange={setCategoryFilter}
      />
      <Select
        className="filter-toolbar__control"
        value={riskFilter}
        options={[
          { label: "风险等级：全部", value: "all" },
          { label: "低风险", value: "low" },
          { label: "中风险", value: "medium" },
          { label: "高风险", value: "high" },
        ]}
        onChange={setRiskFilter}
      />
      <Select
        className="filter-toolbar__control"
        value={extensionFilter ?? "all"}
        options={extensionOptions}
        onChange={(value) => setExtensionFilter(value === "all" ? undefined : value)}
        placeholder="文件类型：全部"
      />
      <Select
        className="filter-toolbar__control"
        value={tagIdFilter ?? "all"}
        options={tagOptions}
        onChange={(value) => setTagIdFilter(value === "all" ? undefined : value)}
        loading={tagsQuery.isLoading}
        placeholder="标签：全部"
      />
      <RangePicker
        className="filter-toolbar__range"
        placeholder={["开始日期", "结束日期"]}
        value={uploadedRange}
        onChange={(value) => setUploadedRange(value as [Dayjs, Dayjs] | null)}
      />
    </>
  ) : null}
</div>
```

- [ ] **Step 4: 运行测试验证**

```bash
cd frontend && npx vitest run src/pages/FileManagement/index.test.tsx --reporter=verbose
```

Expected: 全部 PASS，包括新增测试。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/FileManagement/index.tsx frontend/src/pages/FileManagement/index.test.tsx
git commit -m "refactor(frontend): FileManagement 筛选器分层，默认收起高级筛选"
```

---

### Task 4: FileManagement 表格操作列收纳

当前操作列最多 6-7 个按钮，宽度 220px。改为保留 1-2 个主操作按钮 + "更多"下拉菜单。

**Files:**
- Modify: `frontend/src/pages/FileManagement/index.tsx` (重构 columns 中 actions render)
- Modify: `frontend/src/pages/FileManagement/index.test.tsx`

**Interfaces:**
- Consumes: 所有现有 mutation（approveMutation、rejectMutation、syncMutation 等）保持不变
- Produces: 操作列宽度从 220px 降到 140px，低频操作收入 Dropdown

- [ ] **Step 1: 写测试——"更多操作"下拉菜单**

在 `frontend/src/pages/FileManagement/index.test.tsx` 中新增测试：

```tsx
it("shows secondary actions in a dropdown menu", async () => {
  // 假设文件处于 approved 状态（可同步+可归档+可删除）
  render(<FileManagementPage />);
  // 等待数据加载
  // 找到"更多"按钮并点击
  const moreButtons = screen.getAllByRole("button", { name: "更多操作" });
  expect(moreButtons.length).toBeGreaterThan(0);
});
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd frontend && npx vitest run src/pages/FileManagement/index.test.tsx -t "shows secondary actions" --reporter=verbose
```

Expected: FAIL — 无"更多操作"按钮。

- [ ] **Step 3: 重构操作列**

在 `frontend/src/pages/FileManagement/index.tsx` 的 `columns` 定义中，找到 `title: "操作"` 的列（第 627 行起），将其 `render` 函数替换为：

```tsx
{
  title: "操作",
  key: "actions",
  width: 148,
  fixed: "right" as const,
  render: (_, record) => {
    const canSubmit = reviewableStatuses.has(record.status);
    const canDecide = record.status === "pending_review";
    const canSync = syncableStatuses.has(record.status);
    const canReanalyze = reanalyzeStatuses.has(record.status);

    const moreItems: MenuProps["items"] = [
      canSync
        ? {
            key: "sync",
            icon: <SyncOutlined />,
            label: "手动同步",
            onClick: () => syncMutation.mutate(record.id),
          }
        : null,
      canReanalyze
        ? {
            key: "reanalyze",
            label: "重新分析",
            onClick: () => reanalyzeMutation.mutate(record.id),
          }
        : null,
      {
        key: "classify",
        label: "修改分类",
        onClick: () => openClassificationModal(record),
      },
      {
        key: "archive",
        icon: <InboxOutlined />,
        label: "归档",
        onClick: () => archiveMutation.mutate(record.id),
      },
      { type: "divider" as const },
      {
        key: "delete",
        icon: <DeleteOutlined />,
        label: "删除",
        danger: true,
        onClick: () => deleteMutation.mutate(record.id),
      },
    ].filter(Boolean);

    return (
      <Space size={4}>
        {canDecide ? (
          <>
            <Button
              type="link"
              size="small"
              className="table-link-button"
              onClick={() => openApproveModal(record)}
            >
              审核
            </Button>
            <Button
              type="link"
              danger
              size="small"
              className="table-link-button"
              onClick={() => openRejectModal(record)}
            >
              驳回
            </Button>
          </>
        ) : canSubmit ? (
          <Button
            type="link"
            size="small"
            className="table-link-button"
            loading={submitMutation.isPending}
            onClick={() => submitMutation.mutate(record.id)}
          >
            送审
          </Button>
        ) : null}
        <Dropdown menu={{ items: moreItems }} trigger={["click"]}>
          <Button type="text" size="small" aria-label="更多操作">
            ···
          </Button>
        </Dropdown>
      </Space>
    );
  },
},
```

添加 `Dropdown` 到 antd import：

```tsx
import { ..., Dropdown, ... } from "antd";
```

添加 `type MenuProps` 到 antd import（如果未导入）。

删除操作列中原来的 `Popconfirm` 包裹（归档、删除、同步的 Popconfirm 改为 Dropdown menu item 的 `onClick` 直接触发——用 Modal.confirm 替代或后续添加）。

注意：为保持破坏性操作的确认交互，对"删除"和"归档"在 onClick 中使用 `Modal.confirm`：

```tsx
{
  key: "archive",
  icon: <InboxOutlined />,
  label: "归档",
  onClick: () => {
    Modal.confirm({
      title: "归档文件",
      content: "归档后文件将停止同步，确认继续？",
      onOk: () => archiveMutation.mutate(record.id),
    });
  },
},
{ type: "divider" as const },
{
  key: "delete",
  icon: <DeleteOutlined />,
  label: "删除",
  danger: true,
  onClick: () => {
    Modal.confirm({
      title: "删除文件",
      content: "此操作不可撤销，文件将被软删除并触发 RAGFlow 联动清理，确认删除？",
      okButtonProps: { danger: true },
      onOk: () => deleteMutation.mutate(record.id),
    });
  },
},
```

在组件顶部获取 `Modal`：

```tsx
const { message, modal } = AntdApp.useApp();
```

然后用 `modal.confirm` 替代 `Modal.confirm`（使用 App context 版本）。

- [ ] **Step 4: 运行测试验证**

```bash
cd frontend && npx vitest run src/pages/FileManagement/index.test.tsx --reporter=verbose
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/FileManagement/index.tsx frontend/src/pages/FileManagement/index.test.tsx
git commit -m "refactor(frontend): FileManagement 操作列收纳为主操作+下拉菜单"
```

---

### Task 5: PageContainer 添加面包屑导航

`/files/:id`（文件详情）等子路由页面缺乏层级导航。在 `PageContainer` 中根据路由自动生成面包屑。

**Files:**
- Modify: `frontend/src/layouts/PageContainer.tsx` (添加可选 breadcrumb prop)
- Modify: `frontend/src/styles.css` (面包屑样式)
- Modify: `frontend/src/pages/FileDetail/index.tsx` (传入 breadcrumb)
- Create: `frontend/src/layouts/PageContainer.test.tsx`

**Interfaces:**
- Consumes: `PageContainerProps` 现有接口
- Produces: `PageContainerProps` 新增可选 `breadcrumb` 属性，类型为 `BreadcrumbItem[]`

- [ ] **Step 1: 写测试——面包屑渲染**

创建 `frontend/src/layouts/PageContainer.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PageContainer } from "./PageContainer";

describe("PageContainer", () => {
  it("renders title and description", () => {
    render(
      <MemoryRouter>
        <PageContainer title="测试标题" description="测试描述">
          <p>内容</p>
        </PageContainer>
      </MemoryRouter>,
    );
    expect(screen.getByText("测试标题")).toBeInTheDocument();
    expect(screen.getByText("测试描述")).toBeInTheDocument();
  });

  it("renders breadcrumb when provided", () => {
    render(
      <MemoryRouter>
        <PageContainer
          title="文件详情"
          breadcrumb={[
            { label: "文件管理", path: "/files" },
            { label: "report.pdf" },
          ]}
        >
          <p>内容</p>
        </PageContainer>
      </MemoryRouter>,
    );
    expect(screen.getByText("文件管理")).toBeInTheDocument();
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
  });

  it("does not render breadcrumb when not provided", () => {
    render(
      <MemoryRouter>
        <PageContainer title="仪表盘">
          <p>内容</p>
        </PageContainer>
      </MemoryRouter>,
    );
    expect(screen.queryByRole("navigation", { name: "面包屑" })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd frontend && npx vitest run src/layouts/PageContainer.test.tsx --reporter=verbose
```

Expected: FAIL — `breadcrumb` prop 不存在。

- [ ] **Step 3: 实现 PageContainer 面包屑**

修改 `frontend/src/layouts/PageContainer.tsx`：

```tsx
import type { ReactNode } from "react";
import { Breadcrumb, Typography } from "antd";
import { Link } from "react-router-dom";

export interface BreadcrumbItem {
  label: string;
  path?: string;
}

interface PageContainerProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  breadcrumb?: BreadcrumbItem[];
  children: ReactNode;
}

export function PageContainer({
  title,
  description,
  actions,
  className,
  breadcrumb,
  children,
}: PageContainerProps) {
  return (
    <main className={["page-container", className].filter(Boolean).join(" ")}>
      {breadcrumb && breadcrumb.length > 0 ? (
        <Breadcrumb
          className="page-breadcrumb"
          aria-label="面包屑"
          items={breadcrumb.map((item) => ({
            title: item.path ? <Link to={item.path}>{item.label}</Link> : item.label,
          }))}
        />
      ) : null}
      <div className="page-header">
        <div>
          <Typography.Title level={2} className="page-title">
            {title}
          </Typography.Title>
          {description ? (
            <Typography.Paragraph className="page-description">{description}</Typography.Paragraph>
          ) : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {children}
    </main>
  );
}
```

- [ ] **Step 4: 添加面包屑 CSS**

在 `frontend/src/styles.css` 的 `.page-header` 规则之前（约第 370 行前）添加：

```css
.page-breadcrumb {
  margin-bottom: 8px;
}

.page-breadcrumb .ant-breadcrumb-link a {
  color: var(--ku-text-secondary);
}

.page-breadcrumb .ant-breadcrumb-link a:hover {
  color: var(--ku-color-primary);
}
```

- [ ] **Step 5: 在 FileDetail 页使用面包屑**

修改 `frontend/src/pages/FileDetail/index.tsx`，在 `PageContainer` 调用中添加 `breadcrumb` prop。找到该页面中的 `<PageContainer` 调用，添加：

```tsx
<PageContainer
  title="文件详情"
  breadcrumb={[
    { label: "文件管理", path: "/files" },
    { label: file?.original_name ?? "加载中" },
  ]}
  // ...其他已有 props
>
```

同时在该文件的 import 中添加 `type BreadcrumbItem`（如果 TypeScript 需要的话——实际上 PageContainer 已经导出了类型，直接传对象字面量即可）。

- [ ] **Step 6: 运行测试验证**

```bash
cd frontend && npx vitest run src/layouts/PageContainer.test.tsx src/pages/FileDetail/index.test.tsx --reporter=verbose
```

Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/layouts/PageContainer.tsx frontend/src/layouts/PageContainer.test.tsx frontend/src/styles.css frontend/src/pages/FileDetail/index.tsx
git commit -m "feat(frontend): PageContainer 添加面包屑导航，FileDetail 页使用"
```
