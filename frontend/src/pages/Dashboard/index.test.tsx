import type { CSSProperties, ReactNode } from "react";
import dayjs from "dayjs";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  exportStatistics,
  getGovernanceCapacity,
  getGovernanceLlmUsage,
  getGovernanceRagflowUsage,
  getStatisticsCategories,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  getSystemReadiness,
  type GovernanceCapacityResponse,
  type GovernanceLlmUsageResponse,
  type GovernanceRagflowUsageResponse,
  type StatisticsCategoryListResponse,
  type StatisticsFailureListResponse,
  type StatisticsOverviewResponse,
  type StatisticsTrendResponse,
  type StatisticsUserListResponse,
  type SystemReadiness,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { Roles, useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import { getDefaultGovernanceDateRange, resolveGovernanceWindow } from "./GovernanceMetricsPanel";
import DashboardPage from "./index";

vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => (
    <div data-testid="dashboard-chart" data-option={JSON.stringify(option)} />
  ),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getGovernanceCapacity: vi.fn(),
    getGovernanceLlmUsage: vi.fn(),
    getGovernanceRagflowUsage: vi.fn(),
    getStatisticsOverview: vi.fn(),
    getStatisticsUsers: vi.fn(),
    getStatisticsCategories: vi.fn(),
    getStatisticsTrends: vi.fn(),
    getStatisticsFailures: vi.fn(),
    getSystemReadiness: vi.fn(),
    exportStatistics: vi.fn(),
  };
});

const mockOverview: StatisticsOverviewResponse = {
  total_files: 99999,
  active_uploaders: 777,
  synced_files: 88888,
  pending_review_files: 555,
  failed_files: 44,
  failed_tasks: 33,
  rejected_files: 11,
  sensitive_files: 22,
  total_file_size: 1_234_567_890,
  sync_success_rate: 0.876,
};

const mockUsers: StatisticsUserListResponse = {
  total: 2,
  page: 1,
  page_size: 10,
  items: [
    {
      rank: 1,
      user_id: "uid-1",
      user_name: "测试用户甲",
      department: "测试部门A",
      total_files: 500,
      approved_files: 480,
      synced_files: 460,
      failed_files: 10,
      pending_review_files: 10,
      rejected_files: 0,
      sensitive_files: 1,
      total_file_size: 100_000_000,
      last_upload_at: "2026-06-10T10:00:00Z",
      last_success_sync_at: "2026-06-10T09:50:00Z",
    },
    {
      rank: 2,
      user_id: "uid-2",
      user_name: "测试用户乙",
      department: "测试部门B",
      total_files: 300,
      approved_files: 290,
      synced_files: 280,
      failed_files: 5,
      pending_review_files: 5,
      rejected_files: 0,
      sensitive_files: 0,
      total_file_size: 50_000_000,
      last_upload_at: "2026-06-10T09:00:00Z",
      last_success_sync_at: "2026-06-10T08:55:00Z",
    },
  ],
};

const mockCategories: StatisticsCategoryListResponse = {
  total: 2,
  items: [
    {
      category_id: "cat-1",
      category_name: "技术文档",
      total_files: 400,
      synced_files: 380,
      failed_files: 5,
      pending_review_files: 15,
      total_file_size: 200_000_000,
    },
    {
      category_id: "cat-2",
      category_name: "产品规范",
      total_files: 250,
      synced_files: 230,
      failed_files: 3,
      pending_review_files: 17,
      total_file_size: 150_000_000,
    },
  ],
};

const mockTrends: StatisticsTrendResponse = {
  group_by: "day",
  items: [
    {
      period: "2026-06-09",
      total_files: 120,
      synced_files: 100,
      failed_files: 5,
      pending_review_files: 15,
    },
    {
      period: "2026-06-10",
      total_files: 150,
      synced_files: 130,
      failed_files: 3,
      pending_review_files: 17,
    },
  ],
};

const mockFailures: StatisticsFailureListResponse = {
  total: 2,
  items: [
    { reason: "同步超时", failed_tasks: 20, failed_files: 18 },
    { reason: "解析失败", failed_tasks: 13, failed_files: 13 },
  ],
};

const mockReadiness: SystemReadiness = {
  status: "ok",
  dependencies: {
    database: { status: "ok" },
    redis: { status: "ok" },
    rabbitmq: { status: "ok" },
    minio: { status: "error", detail: "TimeoutError" },
  },
};

const mockGovernanceCapacity: GovernanceCapacityResponse = {
  basis: "database_file_rows_uploaded_in_window",
  group_by: "processing_stage",
  window: {
    start_at: "2026-06-01T00:00:00Z",
    end_before: "2026-07-01T00:00:00Z",
    timezone: "UTC",
  },
  physical: {
    status: "stale",
    requested_dimension: "cluster",
    scope: "cluster",
    measurement_basis: "minio_raw_cluster_capacity",
    source_kind: "minio_cluster_metrics",
    total_bytes: "1099511627776",
    used_bytes: "549755813888",
    free_bytes: "549755813888",
    captured_at: "2026-06-30T08:00:00Z",
    collected_at: "2026-06-30T08:00:01Z",
  },
  items: [
    {
      dimension_key: "parsed",
      dimension_label: "已入库",
      file_count: "9007199254740993",
      active_logical_bytes: "1073741824",
      retained_inactive_bytes: "536870912",
      total_referenced_bytes: "1610612736",
    },
  ],
  pagination: { page: 1, page_size: 5, total: 1, total_pages: 1 },
};

const mockGovernanceLlm: GovernanceLlmUsageResponse = {
  basis: "ai_usage_logs_created_in_window",
  group_by: "provider",
  window: mockGovernanceCapacity.window,
  items: [
    {
      dimension_key: "openai",
      dimension_label: "OpenAI",
      total_calls: "6",
      known_costs: [
        {
          currency: "CNY",
          calls: "2",
          prompt_tokens: "1000",
          completion_tokens: "500",
          estimated_cost_microunits: "1250000",
        },
        {
          currency: "USD",
          calls: "1",
          prompt_tokens: "0",
          completion_tokens: "0",
          estimated_cost_microunits: "0",
        },
      ],
      unknown_costs: [
        {
          status: "unknown_pricing",
          calls: "1",
          known_prompt_tokens: "100",
          known_completion_tokens: "50",
          calls_with_unknown_tokens: "0",
        },
        {
          status: "unknown_usage",
          calls: "1",
          known_prompt_tokens: "0",
          known_completion_tokens: "0",
          calls_with_unknown_tokens: "1",
        },
        {
          status: "legacy_unverifiable",
          calls: "1",
          known_prompt_tokens: "0",
          known_completion_tokens: "0",
          calls_with_unknown_tokens: "1",
        },
      ],
    },
  ],
  pagination: { page: 1, page_size: 5, total: 1, total_pages: 1 },
};

const mockGovernanceRagflow: GovernanceRagflowUsageResponse = {
  basis: "ragflow_api_calls_started_in_window",
  group_by: "result",
  window: mockGovernanceCapacity.window,
  items: [
    {
      dimension_key: "failure",
      dimension_label: "失败",
      calls: "4",
      completed_calls: "3",
      failure_calls: "1",
      in_progress_calls: "1",
      total_latency_ms: "1200",
    },
  ],
  pagination: { page: 1, page_size: 5, total: 1, total_pages: 1 },
};

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  Object.defineProperty(window, "getComputedStyle", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      getPropertyValue: () => "",
    })),
  });
});

function renderWithProviders(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <ConfigProvider>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <div style={themeCssVariables as CSSProperties}>{node}</div>
          </MemoryRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

function mockAllApis() {
  useAuthStore.setState({
    accessToken: "system-admin-token",
    user: {
      id: "system-admin",
      name: "System Administrator",
      email: "system-admin@company.com",
      role: Roles.SYSTEM_ADMIN,
      email_verified: true,
      department_assigned: true,
    },
  });
  vi.mocked(getGovernanceCapacity).mockResolvedValue(mockGovernanceCapacity);
  vi.mocked(getGovernanceLlmUsage).mockResolvedValue(mockGovernanceLlm);
  vi.mocked(getGovernanceRagflowUsage).mockResolvedValue(mockGovernanceRagflow);
  vi.mocked(getStatisticsOverview).mockResolvedValue(mockOverview);
  vi.mocked(getStatisticsUsers).mockResolvedValue(mockUsers);
  vi.mocked(getStatisticsCategories).mockResolvedValue(mockCategories);
  vi.mocked(getStatisticsTrends).mockResolvedValue(mockTrends);
  vi.mocked(getStatisticsFailures).mockResolvedValue(mockFailures);
  vi.mocked(getSystemReadiness).mockResolvedValue(mockReadiness);
}

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("DashboardPage", () => {
  it("uses UTC calendar days and enforces the inclusive 366-day limit", () => {
    const defaultRange = getDefaultGovernanceDateRange(new Date("2026-07-17T16:30:00.000Z"));
    expect(defaultRange.map((value) => value.format("YYYY-MM-DD"))).toEqual([
      "2026-06-18",
      "2026-07-17",
    ]);
    expect(resolveGovernanceWindow(defaultRange)).toEqual({
      params: {
        start_at: "2026-06-18T00:00:00.000Z",
        end_before: "2026-07-18T00:00:00.000Z",
      },
      error: null,
    });

    expect(resolveGovernanceWindow([dayjs("2025-07-18"), dayjs("2026-07-18")]).error).toBeNull();
    expect(resolveGovernanceWindow([dayjs("2025-07-17"), dayjs("2026-07-18")]).error).toContain(
      "366",
    );
  });

  it("renders metric cards from overview API — not hardcoded values", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    // 页面标题
    expect(await screen.findByRole("heading", { name: "知识库运营总览" })).toBeInTheDocument();

    // 指标卡应显示 mock 数值，而非旧硬编码值（如 "1,248" / "1,102" / "976" / "64" / "8"）
    const ninetyNine = await screen.findAllByText("99,999");
    expect(ninetyNine.length).toBeGreaterThan(0);
    // failed_tasks=33
    expect(screen.getAllByText("33").length).toBeGreaterThan(0);

    // 旧硬编码值不应出现
    expect(screen.queryByText("1,248")).not.toBeInTheDocument();
    expect(screen.queryByText("1,102")).not.toBeInTheDocument();
  });

  it.each([
    { label: "employee", role: Roles.EMPLOYEE },
    { label: "dept_admin", role: Roles.DEPT_ADMIN },
  ])("does not expose or query system governance metrics for $label", async ({ role }) => {
    mockAllApis();
    useAuthStore.setState({
      accessToken: `${role}-token`,
      user: {
        id: `${role}-user`,
        name: role,
        email: `${role}@company.com`,
        role,
        email_verified: true,
        department_assigned: true,
      },
    });

    const { container } = renderWithProviders(<DashboardPage />);

    await waitFor(() => expect(getStatisticsOverview).toHaveBeenCalledOnce());
    expect(container.querySelector(".dashboard-governance-shell")).toBeNull();
    expect(getGovernanceCapacity).not.toHaveBeenCalled();
    expect(getGovernanceLlmUsage).not.toHaveBeenCalled();
    expect(getGovernanceRagflowUsage).not.toHaveBeenCalled();
  });

  it("keeps the global refresh state active while governance data is still fetching", async () => {
    mockAllApis();
    vi.mocked(getGovernanceCapacity).mockImplementation(
      () => new Promise<GovernanceCapacityResponse>(() => undefined),
    );

    renderWithProviders(<DashboardPage />);

    await screen.findByText("测试用户甲");
    await screen.findByText("解析失败");
    await screen.findByText("数据库");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /刷新/ })).toHaveClass("ant-btn-loading");
    });
  });

  it("passes trend data to ECharts component", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    await waitFor(() => {
      const charts = screen.getAllByTestId("dashboard-chart");
      expect(charts.length).toBeGreaterThan(0);
      // 至少一个图表的 option 应包含趋势点的 period 数据
      const hasChartWithPeriod = charts.some((chart) => {
        const optionStr = chart.getAttribute("data-option") ?? "";
        return optionStr.includes("2026-06-09") || optionStr.includes("2026-06-10");
      });
      expect(hasChartWithPeriod).toBe(true);
    });
  });

  it("renders health timeline matrix from trend data", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    const timeline = await screen.findByRole("region", { name: "运行健康时间线" });
    expect(timeline).toHaveTextContent("近周期健康矩阵");
    expect(timeline).toHaveTextContent("上传活跃");
    expect(timeline).toHaveTextContent("同步成功");
    expect(timeline).toHaveTextContent("待审积压");
    expect(timeline).toHaveTextContent("失败文件");

    expect(screen.getByLabelText("2026-06-10 同步成功 130")).toHaveClass(
      "dashboard-health-timeline__cell--success",
    );
    expect(screen.getByLabelText("2026-06-10 待审积压 17")).toHaveClass(
      "dashboard-health-timeline__cell--warning",
    );
    expect(screen.getByLabelText("2026-06-09 失败文件 5")).toHaveClass(
      "dashboard-health-timeline__cell--danger",
    );
    expect(screen.getByTitle("2026-06-10 同步成功: 130")).toBeInTheDocument();
  });

  it("renders user upload ranking from API data", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("测试用户甲")).toBeInTheDocument();
    expect(screen.getByText("测试用户乙")).toBeInTheDocument();
    // 旧硬编码排行名不应出现
    expect(screen.queryByText("产品运营部")).not.toBeInTheDocument();
  });

  it("renders failure list from API data", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("同步超时")).toBeInTheDocument();
    expect(screen.getByText("解析失败")).toBeInTheDocument();
    // 旧硬编码 activity 文字不应出现
    expect(screen.queryByText("AI 分析完成")).not.toBeInTheDocument();
  });

  it("renders category card with data from API", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("知识分类占比")).toBeInTheDocument();
  });

  it("shows Empty placeholder when failures list is empty", async () => {
    vi.mocked(getStatisticsOverview).mockResolvedValue(mockOverview);
    vi.mocked(getStatisticsUsers).mockResolvedValue(mockUsers);
    vi.mocked(getStatisticsCategories).mockResolvedValue(mockCategories);
    vi.mocked(getStatisticsTrends).mockResolvedValue(mockTrends);
    vi.mocked(getStatisticsFailures).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getSystemReadiness).mockResolvedValue(mockReadiness);

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("暂无失败任务")).toBeInTheDocument();
  });

  it("renders real dependency health from the readiness API", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    // 真实依赖名称（不再是伪造的 "RAGFlow 连接"）
    expect(await screen.findByText("数据库")).toBeInTheDocument();
    expect(screen.getByText("缓存 Redis")).toBeInTheDocument();
    expect(screen.getByText("消息队列")).toBeInTheDocument();
    expect(screen.getByText("对象存储")).toBeInTheDocument();
    // minio 探针返回 error，应渲染“异常”
    expect(screen.getAllByText("异常").length).toBeGreaterThan(0);
  });

  it("renders auditable capacity, known and unknown LLM cost, and RAGFlow failures", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("容量与成本治理")).toBeInTheDocument();
    expect(await screen.findByLabelText("物理容量快照状态：快照已过期")).toBeInTheDocument();
    expect(screen.getByText("9,007,199,254,740,993")).toBeInTheDocument();
    expect(screen.getByText("CNY 1.25")).toBeInTheDocument();
    expect(screen.getByText("USD 0")).toBeInTheDocument();
    expect(screen.getByText(/估算金额为 0/)).toBeInTheDocument();
    expect(screen.queryByText(/已确认免费/)).not.toBeInTheDocument();
    expect(screen.getByText("定价未确认 1 次")).toBeInTheDocument();
    expect(screen.getByText("Token 用量未知 1 次")).toBeInTheDocument();
    expect(screen.getByText("历史记录不可核验 1 次")).toBeInTheDocument();
    expect(screen.queryByText("parsed")).not.toBeInTheDocument();

    const ragflowTable = await screen.findByRole("table", { name: "RAGFlow 调用明细" });
    const failedRow = ragflowTable.querySelector('tr[data-row-key="failure"]');
    expect(failedRow).toHaveTextContent("4");
    expect(failedRow).toHaveTextContent("1");
  });

  it("does not invent department-level MinIO physical capacity", async () => {
    mockAllApis();
    vi.mocked(getGovernanceCapacity).mockImplementation(async (params = {}) => ({
      ...mockGovernanceCapacity,
      physical:
        params.physical_dimension === "department"
          ? {
              status: "unsupported_dimension",
              requested_dimension: "department",
              scope: "cluster",
              measurement_basis: null,
              source_kind: null,
              total_bytes: null,
              used_bytes: null,
              free_bytes: null,
              captured_at: null,
              collected_at: null,
            }
          : mockGovernanceCapacity.physical,
    }));

    renderWithProviders(<DashboardPage />);
    await screen.findByLabelText("物理容量快照状态：快照已过期");

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "物理容量维度" }));
    const dimensionOptions = await screen.findAllByTitle("部门物理容量");
    const departmentDimension = dimensionOptions.find(
      (element) => element.closest(".ant-select-dropdown") !== null,
    );
    fireEvent.click(departmentDimension ?? dimensionOptions[dimensionOptions.length - 1]);

    expect(await screen.findByLabelText("物理容量快照状态：维度不支持")).toBeInTheDocument();
    expect(
      screen.getByText("MinIO 仅提供集群级原始物理容量，无法按部门或文件类型可靠拆分。"),
    ).toBeInTheDocument();
    const calls = vi.mocked(getGovernanceCapacity).mock.calls;
    expect(calls[calls.length - 1][0]).toMatchObject({
      physical_dimension: "department",
      page: 1,
    });
  });

  it("queries governance capacity with server pagination and resets the page on regrouping", async () => {
    mockAllApis();
    vi.mocked(getGovernanceCapacity).mockImplementation(async (params = {}) => ({
      ...mockGovernanceCapacity,
      group_by: params.group_by ?? "none",
      pagination: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 5,
        total: 6,
        total_pages: 2,
      },
    }));

    renderWithProviders(<DashboardPage />);
    await screen.findByLabelText("物理容量快照状态：快照已过期");

    fireEvent.click(await screen.findByRole("listitem", { name: "2" }));
    await waitFor(() => {
      const calls = vi.mocked(getGovernanceCapacity).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ page: 2, page_size: 5 });
    });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "容量分组" }));
    const departmentOptions = await screen.findAllByTitle("部门");
    const departmentOption = departmentOptions.find(
      (element) => element.closest(".ant-select-dropdown") !== null,
    );
    fireEvent.click(departmentOption ?? departmentOptions[departmentOptions.length - 1]);

    await waitFor(() => {
      const calls = vi.mocked(getGovernanceCapacity).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({
        group_by: "department",
        page: 1,
        page_size: 5,
      });
    });
  });

  it("calls exportStatistics when the export button is clicked", async () => {
    mockAllApis();
    vi.mocked(exportStatistics).mockResolvedValue(new Blob(["csv"], { type: "text/csv" }));

    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    renderWithProviders(<DashboardPage />);

    fireEvent.click(await screen.findByRole("button", { name: /导出报表/ }));

    await waitFor(() => expect(exportStatistics).toHaveBeenCalledTimes(1));
    expect(clickSpy).toHaveBeenCalled();

    vi.unstubAllGlobals();
  });
  it("does not download, toast, or clear the loading state for B when A export resolves late", async () => {
    mockAllApis();
    useAuthStore.setState({
      accessToken: "token-a",
      user: {
        id: "admin-a",
        name: "管理员 A",
        email: "admin-a@company.com",
        role: "system_admin",
        email_verified: true,
        department_assigned: true,
      },
    });
    let resolveExport!: (blob: Blob) => void;
    vi.mocked(exportStatistics).mockImplementation(
      () =>
        new Promise<Blob>((resolve) => {
          resolveExport = resolve;
        }),
    );
    const createObjectURL = vi.fn(() => "blob:late-export");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    renderWithProviders(<DashboardPage />);
    const exportButton = await screen.findByRole("button", { name: /导出报表/ });
    fireEvent.click(exportButton);
    await waitFor(() => expect(exportStatistics).toHaveBeenCalledOnce());
    expect(exportButton).toHaveClass("ant-btn-loading");

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "admin-b",
          name: "管理员 B",
          email: "admin-b@company.com",
          role: "system_admin",
          email_verified: true,
          department_assigned: true,
        },
      });
    });
    await act(async () => {
      resolveExport(new Blob(["late"], { type: "text/csv" }));
      await Promise.resolve();
    });

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
    expect(screen.queryByText("报表已开始下载")).not.toBeInTheDocument();
    expect(exportButton).toHaveClass("ant-btn-loading");
  });
});
