import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  getStatisticsCategories,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  type StatisticsCategoryListResponse,
  type StatisticsFailureListResponse,
  type StatisticsOverviewResponse,
  type StatisticsTrendResponse,
  type StatisticsUserListResponse,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
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
    getStatisticsOverview: vi.fn(),
    getStatisticsUsers: vi.fn(),
    getStatisticsCategories: vi.fn(),
    getStatisticsTrends: vi.fn(),
    getStatisticsFailures: vi.fn(),
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
    { period: "2026-06-09", total_files: 120, synced_files: 100, failed_files: 5, pending_review_files: 15 },
    { period: "2026-06-10", total_files: 150, synced_files: 130, failed_files: 3, pending_review_files: 17 },
  ],
};

const mockFailures: StatisticsFailureListResponse = {
  total: 2,
  items: [
    { reason: "同步超时", failed_tasks: 20, failed_files: 18 },
    { reason: "解析失败", failed_tasks: 13, failed_files: 13 },
  ],
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
          <div style={themeCssVariables as CSSProperties}>{node}</div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

function mockAllApis() {
  vi.mocked(getStatisticsOverview).mockResolvedValue(mockOverview);
  vi.mocked(getStatisticsUsers).mockResolvedValue(mockUsers);
  vi.mocked(getStatisticsCategories).mockResolvedValue(mockCategories);
  vi.mocked(getStatisticsTrends).mockResolvedValue(mockTrends);
  vi.mocked(getStatisticsFailures).mockResolvedValue(mockFailures);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("DashboardPage", () => {
  it("renders metric cards from overview API — not hardcoded values", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    // 页面标题
    expect(await screen.findByRole("heading", { name: "知识库运营总览" })).toBeInTheDocument();

    // 指标卡应显示 mock 数值，而非旧硬编码值（如 "1,248" / "1,102" / "976" / "64" / "8"）
    const ninetyNine = await screen.findAllByText("99,999");
    expect(ninetyNine.length).toBeGreaterThan(0);
    // failed_tasks=33 和 sensitive_files=22
    expect(screen.getAllByText("33").length).toBeGreaterThan(0);
    expect(screen.getAllByText("22").length).toBeGreaterThan(0);

    // 旧硬编码值不应出现
    expect(screen.queryByText("1,248")).not.toBeInTheDocument();
    expect(screen.queryByText("1,102")).not.toBeInTheDocument();
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

  it("shows category pie chart data from API", async () => {
    mockAllApis();

    renderWithProviders(<DashboardPage />);

    await waitFor(() => {
      const charts = screen.getAllByTestId("dashboard-chart");
      const hasCategoryData = charts.some((chart) => {
        const optionStr = chart.getAttribute("data-option") ?? "";
        return optionStr.includes("技术文档") || optionStr.includes("产品规范");
      });
      expect(hasCategoryData).toBe(true);
    });
  });

  it("shows Empty placeholder when failures list is empty", async () => {
    vi.mocked(getStatisticsOverview).mockResolvedValue(mockOverview);
    vi.mocked(getStatisticsUsers).mockResolvedValue(mockUsers);
    vi.mocked(getStatisticsCategories).mockResolvedValue(mockCategories);
    vi.mocked(getStatisticsTrends).mockResolvedValue(mockTrends);
    vi.mocked(getStatisticsFailures).mockResolvedValue({ total: 0, items: [] });

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("暂无失败任务")).toBeInTheDocument();
  });
});
