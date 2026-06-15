import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  exportStatistics,
  getStatisticsCategories,
  getStatisticsDepartments,
  getStatisticsExpiry,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  type StatisticsCategoryListResponse,
  type StatisticsDepartmentListResponse,
  type StatisticsExpiryResponse,
  type StatisticsFailureListResponse,
  type StatisticsOverviewResponse,
  type StatisticsTrendResponse,
  type StatisticsUserListResponse,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import StatisticsPage from "./index";

vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: { title?: { text?: string } } }) => (
    <div data-testid="statistics-chart">{option.title?.text ?? "chart"}</div>
  ),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    exportStatistics: vi.fn(),
    getStatisticsCategories: vi.fn(),
    getStatisticsDepartments: vi.fn(),
    getStatisticsExpiry: vi.fn(),
    getStatisticsFailures: vi.fn(),
    getStatisticsOverview: vi.fn(),
    getStatisticsTrends: vi.fn(),
    getStatisticsUsers: vi.fn(),
  };
});

const overview: StatisticsOverviewResponse = {
  total_files: 18560,
  active_uploaders: 236,
  synced_files: 17156,
  pending_review_files: 1240,
  failed_files: 162,
  failed_tasks: 162,
  rejected_files: 84,
  sensitive_files: 37,
  total_file_size: 512_000_000_000,
  sync_success_rate: 0.924,
};

const users: StatisticsUserListResponse = {
  total: 2,
  page: 1,
  page_size: 20,
  items: [
    {
      rank: 1,
      user_id: "user-1",
      user_name: "李明",
      department: "研发中心",
      total_files: 1248,
      approved_files: 1200,
      synced_files: 1176,
      failed_files: 48,
      pending_review_files: 24,
      rejected_files: 0,
      sensitive_files: 2,
      total_file_size: 306_016_419_840,
      last_upload_at: "2026-06-12T14:32:00Z",
      last_success_sync_at: "2026-06-12T14:20:00Z",
    },
    {
      rank: 2,
      user_id: "user-2",
      user_name: "王芳",
      department: "产品部",
      total_files: 1076,
      approved_files: 1044,
      synced_files: 1002,
      failed_files: 42,
      pending_review_files: 32,
      rejected_files: 0,
      sensitive_files: 1,
      total_file_size: 213_352_513_536,
      last_upload_at: "2026-06-12T13:48:00Z",
      last_success_sync_at: "2026-06-12T13:20:00Z",
    },
  ],
};

const departments: StatisticsDepartmentListResponse = {
  total: 2,
  items: [
    {
      department: "研发中心",
      total_files: 3842,
      active_uploaders: 48,
      synced_files: 3600,
      failed_files: 82,
      pending_review_files: 160,
      total_file_size: 900_000_000_000,
    },
    {
      department: "产品部",
      total_files: 2756,
      active_uploaders: 31,
      synced_files: 2600,
      failed_files: 56,
      pending_review_files: 100,
      total_file_size: 700_000_000_000,
    },
  ],
};

const categories: StatisticsCategoryListResponse = {
  total: 2,
  items: [
    {
      category_id: "category-1",
      category_name: "技术文档",
      total_files: 6245,
      synced_files: 5800,
      failed_files: 120,
      pending_review_files: 325,
      total_file_size: 1_000_000_000,
    },
    {
      category_id: "category-2",
      category_name: "产品文档",
      total_files: 4326,
      synced_files: 4100,
      failed_files: 80,
      pending_review_files: 146,
      total_file_size: 800_000_000,
    },
  ],
};

const trends: StatisticsTrendResponse = {
  group_by: "day",
  items: [
    { period: "2026-06-01", total_files: 1024, synced_files: 860, failed_files: 20, pending_review_files: 144 },
    { period: "2026-06-02", total_files: 890, synced_files: 790, failed_files: 18, pending_review_files: 82 },
  ],
};

const failures: StatisticsFailureListResponse = {
  total: 2,
  items: [
    { reason: "RuntimeError", failed_tasks: 42, failed_files: 42 },
    { reason: "权限不足", failed_tasks: 28, failed_files: 28 },
  ],
};

const expiry: StatisticsExpiryResponse = {
  total: 184,
  active: 128,
  expiring: 9,
  expired: 3,
  never: 44,
  remind_days: 7,
  as_of: "2026-06-15T00:00:00Z",
  window_end: "2026-06-22T00:00:00Z",
  items: [
    { status: "expired", count: 3 },
    { status: "expiring", count: 9 },
    { status: "active", count: 128 },
    { status: "never", count: 44 },
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

  Object.defineProperty(window.URL, "createObjectURL", {
    writable: true,
    value: vi.fn(() => "blob:statistics"),
  });

  Object.defineProperty(window.URL, "revokeObjectURL", {
    writable: true,
    value: vi.fn(),
  });

  Object.defineProperty(HTMLAnchorElement.prototype, "click", {
    writable: true,
    value: vi.fn(),
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

function mockStatisticsApi() {
  vi.mocked(getStatisticsOverview).mockResolvedValue(overview);
  vi.mocked(getStatisticsUsers).mockResolvedValue(users);
  vi.mocked(getStatisticsDepartments).mockResolvedValue(departments);
  vi.mocked(getStatisticsCategories).mockResolvedValue(categories);
  vi.mocked(getStatisticsTrends).mockResolvedValue(trends);
  vi.mocked(getStatisticsFailures).mockResolvedValue(failures);
  vi.mocked(getStatisticsExpiry).mockResolvedValue(expiry);
  vi.mocked(exportStatistics).mockResolvedValue(new Blob(["用户,部门,上传文件总数"]));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("StatisticsPage", () => {
  it("renders the statistics dashboard from API data", async () => {
    mockStatisticsApi();

    renderWithProviders(<StatisticsPage />);

    expect(await screen.findByRole("heading", { name: "统计分析" })).toBeInTheDocument();
    expect(await screen.findByText("18,560")).toBeInTheDocument();
    expect(screen.getByText("236")).toBeInTheDocument();
    expect(screen.getByText("92.4%")).toBeInTheDocument();
    expect(screen.getByText("上传趋势")).toBeInTheDocument();
    expect(screen.getByText("部门贡献排行")).toBeInTheDocument();
    expect(screen.getByText("分类分布")).toBeInTheDocument();
    expect(screen.getByText("活跃贡献用户排行")).toBeInTheDocument();
    expect(screen.getByText("过期提醒")).toBeInTheDocument();
    expect(screen.getByText("状态分布")).toBeInTheDocument();
    expect(screen.getByText(/2026-06-15 至 2026-06-22/)).toBeInTheDocument();
    expect(screen.getAllByText("已过期").length).toBeGreaterThan(0);
    expect(screen.getAllByText("李明").length).toBeGreaterThan(0);
    expect(screen.getByText("RuntimeError")).toBeInTheDocument();
  });

  it("filters the user table locally and exports with current query filters", async () => {
    mockStatisticsApi();

    renderWithProviders(<StatisticsPage />);

    await screen.findAllByText("李明");
    fireEvent.change(screen.getByPlaceholderText("搜索用户姓名、部门"), {
      target: { value: "王芳" },
    });

    expect(screen.queryByText("李明")).not.toBeInTheDocument();
    expect(screen.getAllByText("王芳").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /导出报表/ }));

    await waitFor(() => {
      expect(exportStatistics).toHaveBeenCalledWith(expect.objectContaining({ group_by: "day" }));
      expect(getStatisticsExpiry).toHaveBeenCalledWith(expect.objectContaining({ group_by: "day" }));
    });
  });

  it("keeps the main dashboard usable when the expiry endpoint is unavailable", async () => {
    mockStatisticsApi();
    vi.mocked(getStatisticsExpiry).mockRejectedValue(new Error("Not Found"));

    renderWithProviders(<StatisticsPage />);

    expect(await screen.findByRole("heading", { name: "统计分析" })).toBeInTheDocument();
    expect(await screen.findByText("18,560")).toBeInTheDocument();
    expect(await screen.findByText("过期统计接口暂不可用")).toBeInTheDocument();
  });
});
