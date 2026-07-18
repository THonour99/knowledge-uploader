import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";

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

const chartOptions = vi.hoisted((): Array<Record<string, unknown>> => []);
const FILTER_USER_ID = "11111111-1111-4111-8111-111111111111";

vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: { title?: { text?: string } } }) => {
    chartOptions.push(option as Record<string, unknown>);

    return <div data-testid="statistics-chart">{option.title?.text ?? "chart"}</div>;
  },
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
vi.mock("../../components/SavedViewManager", () => ({
  SavedViewManager: ({
    pageKey,
    onApply,
    queryDefinition,
  }: {
    pageKey: string;
    onApply: (definition: Record<string, unknown>) => void;
    queryDefinition: Record<string, unknown>;
  }) => (
    <>
      <output data-testid={"saved-view-definition-" + pageKey}>
        {JSON.stringify(queryDefinition)}
      </output>
      <button
        type="button"
        data-testid={"saved-view-" + pageKey}
        onClick={() =>
          onApply({
            relationship: "responsible",
            queue: "mine",
            task_type: "ragflow_upload",
            start_date: "2026-01-15",
            status: "parsed",
            user_id: FILTER_USER_ID,
            group_by: "month",
            order: "desc",
            page_size: 50,
            user_q: "王芳",
          })
        }
      >
        应用测试保存视图
      </button>
    </>
  ),
}));

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
    {
      period: "2026-06-01",
      total_files: 1024,
      synced_files: 860,
      failed_files: 20,
      pending_review_files: 144,
    },
    {
      period: "2026-06-02",
      total_files: 890,
      synced_files: 790,
      failed_files: 18,
      pending_review_files: 82,
    },
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
      matches: query.includes("prefers-reduced-motion"),
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

function StatisticsLocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div>
      <output data-testid="statistics-location">{location.pathname + location.search}</output>
      <button type="button" data-testid="statistics-history-back" onClick={() => navigate(-1)}>
        后退
      </button>
      <button type="button" data-testid="statistics-history-forward" onClick={() => navigate(1)}>
        前进
      </button>
    </div>
  );
}

function renderWithProviders(node: ReactNode, initialEntries: string[] = ["/statistics"]) {
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
          <MemoryRouter initialEntries={initialEntries}>
            <div style={themeCssVariables as CSSProperties}>{node}</div>
            <StatisticsLocationProbe />
          </MemoryRouter>
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

function latestStatisticsUsersParams() {
  const calls = vi.mocked(getStatisticsUsers).mock.calls;
  return calls[calls.length - 1]?.[0];
}

afterEach(() => {
  vi.clearAllMocks();
  chartOptions.length = 0;
});

describe("StatisticsPage", () => {
  it("renders the statistics dashboard from API data", async () => {
    mockStatisticsApi();

    renderWithProviders(<StatisticsPage />);

    expect(await screen.findByRole("heading", { name: "统计报表" })).toBeInTheDocument();
    expect((await screen.findAllByText("18,560")).length).toBeGreaterThan(0);
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
    const contributionWorkbench = screen.getByRole("region", { name: "贡献明细工作台" });
    expect(contributionWorkbench).toHaveTextContent("贡献明细工作台");
    expect(contributionWorkbench).toHaveTextContent("当前视图 2 位用户，样本总数 2 位");
    expect(contributionWorkbench).toHaveTextContent("上传文件2,324");
    expect(contributionWorkbench).toHaveTextContent("同步成功2,178");
    expect(contributionWorkbench).toHaveTextContent("待审核56");
    expect(contributionWorkbench).toHaveTextContent("失败文件90");
    expect(contributionWorkbench).toHaveTextContent("同步质量94%");
  });

  it("queries user search on the server and uses its total, URL, and saved-view state", async () => {
    mockStatisticsApi();
    vi.mocked(getStatisticsUsers).mockImplementation(async (params) =>
      params?.user_q === "王芳" ? { ...users, total: 41, items: [users.items[1]] } : users,
    );

    renderWithProviders(<StatisticsPage />);

    await screen.findAllByText("李明");
    fireEvent.change(screen.getByPlaceholderText("搜索用户姓名、部门"), {
      target: { value: "王芳" },
    });

    await waitFor(() => {
      expect(latestStatisticsUsersParams()).toEqual(
        expect.objectContaining({ user_q: "王芳", page: 1, page_size: 20 }),
      );
      expect(screen.queryByText("李明")).not.toBeInTheDocument();
      expect(screen.getAllByText("王芳").length).toBeGreaterThan(0);
    });
    const contributionWorkbench = screen.getByRole("region", { name: "贡献明细工作台" });
    expect(contributionWorkbench).toHaveTextContent("当前视图 1 位用户，样本总数 41 位");
    expect(contributionWorkbench).toHaveTextContent("上传文件1,076");
    expect(contributionWorkbench).toHaveTextContent("同步质量93%");
    expect(document.querySelector(".statistics-users-card .ant-pagination-item-2")).not.toBeNull();

    const searchLocation = new URL(
      screen.getByTestId("statistics-location").textContent ?? "/statistics",
      "http://localhost",
    );
    expect(searchLocation.searchParams.get("user_q")).toBe("王芳");
    expect(searchLocation.searchParams.get("page")).toBe("1");
    expect(screen.getByTestId("saved-view-definition-statistics")).toHaveTextContent(
      '"user_q":"王芳"',
    );

    fireEvent.click(screen.getByRole("button", { name: /导出报表/ }));
    await waitFor(() => {
      expect(exportStatistics).toHaveBeenCalledWith(
        expect.objectContaining({ group_by: "day", user_q: "王芳" }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /清空搜索/ }));

    await waitFor(() => {
      expect(latestStatisticsUsersParams()?.user_q).toBeUndefined();
      expect(screen.getAllByText("李明").length).toBeGreaterThan(0);
      expect(contributionWorkbench).toHaveTextContent("当前视图 2 位用户，样本总数 2 位");
      expect(getStatisticsExpiry).toHaveBeenCalledWith(
        expect.objectContaining({ group_by: "day" }),
      );
    });
    const clearedLocation = new URL(
      screen.getByTestId("statistics-location").textContent ?? "/statistics",
      "http://localhost",
    );
    expect(clearedLocation.searchParams.get("user_q")).toBeNull();
  });

  it("canonicalizes invalid URL state once before querying the dashboard", async () => {
    mockStatisticsApi();

    renderWithProviders(<StatisticsPage />, [
      "/statistics?start_date=2026-12-31&end_date=2026-01-01&department=%20%20&category_id=bad&status=12345678901234567890123456789012345678901&user_id=bad&sync_status=oops&review_status=oops&group_by=quarter&page=0&page_size=500&sort_by=oops&sort_order=sideways",
    ]);

    expect(await screen.findByRole("heading", { name: "统计报表" })).toBeInTheDocument();
    await waitFor(() => {
      const locationText = screen.getByTestId("statistics-location").textContent ?? "";
      expect(locationText).not.toContain("start_date=2026-12-31");
      expect(locationText).not.toContain("category_id=bad");
      expect(locationText).not.toContain("sync_status=oops");
      expect(locationText).not.toContain("review_status=oops");
    });

    const canonicalLocation = new URL(
      screen.getByTestId("statistics-location").textContent ?? "/statistics",
      "http://localhost",
    );
    expect(canonicalLocation.searchParams.get("start_date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(canonicalLocation.searchParams.get("end_date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(canonicalLocation.searchParams.get("department")).toBeNull();
    expect(canonicalLocation.searchParams.get("category_id")).toBeNull();
    expect(canonicalLocation.searchParams.get("status")).toBeNull();
    expect(canonicalLocation.searchParams.get("user_id")).toBeNull();
    expect(canonicalLocation.searchParams.get("group_by")).toBe("day");
    expect(canonicalLocation.searchParams.get("page")).toBe("1");
    expect(canonicalLocation.searchParams.get("page_size")).toBe("20");
    expect(canonicalLocation.searchParams.get("sort_by")).toBe("total_files");
    expect(canonicalLocation.searchParams.get("sort_order")).toBe("desc");

    await waitFor(() => expect(getStatisticsOverview).toHaveBeenCalledTimes(1));
    const overviewParams = vi.mocked(getStatisticsOverview).mock.calls[0]?.[0];
    expect(overviewParams).toEqual(
      expect.objectContaining({
        group_by: "day",
        category_id: undefined,
        status: undefined,
        user_id: undefined,
        sync_status: undefined,
        review_status: undefined,
      }),
    );
  });

  it.each([
    ["start_date", "end_date", "2026-01-10"],
    ["end_date", "start_date", "2026-02-20"],
  ] as const)(
    "preserves a single %s bound with status and user_id through URL, query, export, and saved view",
    async (boundKey, absentBoundKey, boundValue) => {
      mockStatisticsApi();

      renderWithProviders(<StatisticsPage />, [
        `/statistics?${boundKey}=${boundValue}&status=parsed&user_id=${FILTER_USER_ID}`,
      ]);

      await waitFor(() => {
        expect(getStatisticsOverview).toHaveBeenCalledWith(
          expect.objectContaining({
            [boundKey]: boundValue,
            [absentBoundKey]: undefined,
            status: "parsed",
            user_id: FILTER_USER_ID,
          }),
        );
      });
      const location = new URL(
        screen.getByTestId("statistics-location").textContent ?? "/statistics",
        "http://localhost",
      );
      expect(location.searchParams.get(boundKey)).toBe(boundValue);
      expect(location.searchParams.get(absentBoundKey)).toBeNull();
      expect(location.searchParams.get("date_range")).toBeNull();
      expect(screen.getByTestId("saved-view-definition-statistics")).toHaveTextContent(
        `"${boundKey}":"${boundValue}"`,
      );
      expect(screen.getByTestId("saved-view-definition-statistics")).toHaveTextContent(
        '"status":"parsed"',
      );
      expect(screen.getByTestId("saved-view-definition-statistics")).toHaveTextContent(
        `"user_id":"${FILTER_USER_ID}"`,
      );

      fireEvent.click(screen.getByRole("button", { name: /导出报表/ }));
      await waitFor(() => {
        expect(exportStatistics).toHaveBeenCalledWith(
          expect.objectContaining({
            [boundKey]: boundValue,
            [absentBoundKey]: undefined,
            status: "parsed",
            user_id: FILTER_USER_ID,
          }),
        );
      });
    },
  );

  it("recovers an out-of-range shared user search from the server total", async () => {
    mockStatisticsApi();
    vi.mocked(getStatisticsUsers).mockImplementation(async (params) => {
      if (params?.user_q !== "Case") {
        return users;
      }
      return {
        ...users,
        total: 1,
        page: params?.page ?? 1,
        items: params?.page === 1 ? [users.items[0]] : [],
      };
    });

    renderWithProviders(<StatisticsPage />, [
      "/statistics?date_range=all&user_q=Case&page=5&page_size=20&group_by=day&sort_by=total_files&sort_order=desc",
    ]);

    await waitFor(() => {
      const calls = vi.mocked(getStatisticsUsers).mock.calls;
      expect(
        calls.some(
          ([params]) => params?.user_q === "Case" && params.page === 5 && params.page_size === 20,
        ),
      ).toBe(true);
      expect(latestStatisticsUsersParams()).toEqual(
        expect.objectContaining({ user_q: "Case", page: 1, page_size: 20 }),
      );
    });
    const recoveredLocation = new URL(
      screen.getByTestId("statistics-location").textContent ?? "/statistics",
      "http://localhost",
    );
    expect(recoveredLocation.searchParams.get("user_q")).toBe("Case");
    expect(recoveredLocation.searchParams.get("page")).toBe("1");
    expect(screen.getAllByText("李明").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "贡献明细工作台" })).toHaveTextContent(
      "当前视图 1 位用户，样本总数 1 位",
    );
  });

  it("recovers any out-of-range canonical users page to the last server page", async () => {
    mockStatisticsApi();
    vi.mocked(getStatisticsUsers).mockImplementation(async (params) => {
      if (params?.department !== "研发中心") {
        return users;
      }
      return {
        ...users,
        total: 21,
        page: params?.page ?? 1,
        items: params?.page === 2 ? [users.items[0]] : [],
      };
    });

    renderWithProviders(<StatisticsPage />, [
      "/statistics?date_range=all&department=%E7%A0%94%E5%8F%91%E4%B8%AD%E5%BF%83&page=999&page_size=20&group_by=day&sort_by=total_files&sort_order=desc",
    ]);

    await waitFor(() => {
      const calls = vi.mocked(getStatisticsUsers).mock.calls;
      expect(
        calls.some(
          ([params]) =>
            params?.department === "研发中心" && params.page === 999 && params.page_size === 20,
        ),
      ).toBe(true);
      expect(latestStatisticsUsersParams()).toEqual(
        expect.objectContaining({ department: "研发中心", page: 2, page_size: 20 }),
      );
    });
    const recoveredLocation = new URL(
      screen.getByTestId("statistics-location").textContent ?? "/statistics",
      "http://localhost",
    );
    expect(recoveredLocation.searchParams.get("department")).toBe("研发中心");
    expect(recoveredLocation.searchParams.get("page")).toBe("2");
    expect(screen.getAllByText("李明").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "贡献明细工作台" })).toHaveTextContent(
      "当前视图 1 位用户，样本总数 21 位",
    );
  });

  it("hydrates shared filters and restores saved-view state through browser history", async () => {
    mockStatisticsApi();
    vi.mocked(getStatisticsUsers).mockResolvedValue({
      ...users,
      total: 100,
      page: 2,
      page_size: 50,
    });
    const sharedLocation = `/statistics?date_range=all&department=%E7%A0%94%E5%8F%91%E4%B8%AD%E5%BF%83&status=approved&user_id=${FILTER_USER_ID}&sync_status=failed&review_status=approved&group_by=week&page=2&page_size=50&sort_by=failed_files&sort_order=asc`;

    renderWithProviders(<StatisticsPage />, [sharedLocation]);

    await waitFor(() => {
      expect(getStatisticsUsers).toHaveBeenCalledWith(
        expect.objectContaining({
          start_date: undefined,
          end_date: undefined,
          department: "研发中心",
          status: "approved",
          user_id: FILTER_USER_ID,
          sync_status: "failed",
          review_status: "approved",
          group_by: "week",
          page: 2,
          page_size: 50,
          sort_by: "failed_files",
          sort_order: "asc",
        }),
      );
    });

    fireEvent.click(screen.getByTestId("saved-view-statistics"));
    await waitFor(() => {
      const appliedLocation = new URL(
        screen.getByTestId("statistics-location").textContent ?? "/statistics",
        "http://localhost",
      );
      expect(appliedLocation.searchParams.get("date_range")).toBeNull();
      expect(appliedLocation.searchParams.get("start_date")).toBe("2026-01-15");
      expect(appliedLocation.searchParams.get("end_date")).toBeNull();
      expect(appliedLocation.searchParams.get("department")).toBeNull();
      expect(appliedLocation.searchParams.get("status")).toBe("parsed");
      expect(appliedLocation.searchParams.get("user_id")).toBe(FILTER_USER_ID);
      expect(appliedLocation.searchParams.get("sync_status")).toBeNull();
      expect(appliedLocation.searchParams.get("group_by")).toBe("month");
      expect(appliedLocation.searchParams.get("page")).toBe("1");
      expect(appliedLocation.searchParams.get("page_size")).toBe("50");
      expect(appliedLocation.searchParams.get("user_q")).toBe("王芳");
    });
    await waitFor(() => {
      expect(latestStatisticsUsersParams()).toEqual(
        expect.objectContaining({
          start_date: "2026-01-15",
          end_date: undefined,
          status: "parsed",
          user_id: FILTER_USER_ID,
          group_by: "month",
          page: 1,
          page_size: 50,
          user_q: "王芳",
        }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: /导出报表/ }));
    await waitFor(() => {
      expect(exportStatistics).toHaveBeenLastCalledWith(
        expect.objectContaining({
          start_date: "2026-01-15",
          end_date: undefined,
          status: "parsed",
          user_id: FILTER_USER_ID,
        }),
      );
    });

    fireEvent.click(screen.getByTestId("statistics-history-back"));
    await waitFor(() => {
      const restoredLocation = new URL(
        screen.getByTestId("statistics-location").textContent ?? "/statistics",
        "http://localhost",
      );
      expect(restoredLocation.searchParams.get("date_range")).toBe("all");
      expect(restoredLocation.searchParams.get("group_by")).toBe("week");
      expect(restoredLocation.searchParams.get("page")).toBe("2");
      expect(restoredLocation.searchParams.get("status")).toBe("approved");
      expect(restoredLocation.searchParams.get("user_id")).toBe(FILTER_USER_ID);
      expect(restoredLocation.searchParams.get("sync_status")).toBe("failed");
      expect(restoredLocation.searchParams.get("user_q")).toBeNull();
    });
    await waitFor(() => {
      expect(latestStatisticsUsersParams()).toEqual(
        expect.objectContaining({
          group_by: "week",
          page: 2,
          status: "approved",
          user_id: FILTER_USER_ID,
          sync_status: "failed",
        }),
      );
    });

    fireEvent.click(screen.getByTestId("statistics-history-forward"));
    await waitFor(() => {
      const forwardLocation = new URL(
        screen.getByTestId("statistics-location").textContent ?? "/statistics",
        "http://localhost",
      );
      expect(forwardLocation.searchParams.get("start_date")).toBe("2026-01-15");
      expect(forwardLocation.searchParams.get("end_date")).toBeNull();
      expect(forwardLocation.searchParams.get("status")).toBe("parsed");
      expect(forwardLocation.searchParams.get("user_id")).toBe(FILTER_USER_ID);
      expect(forwardLocation.searchParams.get("group_by")).toBe("month");
      expect(forwardLocation.searchParams.get("page")).toBe("1");
      expect(forwardLocation.searchParams.get("user_q")).toBe("王芳");
    });
  });

  it("keeps the main dashboard usable when the expiry endpoint is unavailable", async () => {
    mockStatisticsApi();
    vi.mocked(getStatisticsExpiry).mockRejectedValue(new Error("Not Found"));

    renderWithProviders(<StatisticsPage />);

    expect(await screen.findByRole("heading", { name: "统计报表" })).toBeInTheDocument();
    expect((await screen.findAllByText("18,560")).length).toBeGreaterThan(0);
    expect(await screen.findByText("过期统计接口暂不可用")).toBeInTheDocument();
  });

  it("renders all category legend labels as a visible list outside the donut chart", async () => {
    mockStatisticsApi();

    renderWithProviders(<StatisticsPage />);

    await screen.findByText("分类分布");

    const legend = await screen.findByLabelText("分类分布图例");
    const technicalLegend = await screen.findByRole("button", {
      name: "技术文档，6,245 个文件，占比 59.1%",
    });
    const productLegend = screen.getByRole("button", {
      name: "产品文档，4,326 个文件，占比 40.9%",
    });

    expect(legend).toContainElement(technicalLegend);
    expect(legend).toContainElement(productLegend);

    await waitFor(() => {
      const categoryOption = chartOptions.find((option) => {
        const series = option.series as Array<{
          type?: string;
          data?: Array<{ name?: string }>;
        }>;

        return series?.some(
          (item) =>
            item.type === "pie" && item.data?.some((dataItem) => dataItem.name === "技术文档"),
        );
      });

      expect(categoryOption).toMatchObject({
        legend: { show: false },
        series: [
          {
            name: "分类分布",
            type: "pie",
            radius: ["42%", "64%"],
            center: ["50%", "50%"],
            label: { show: false },
            labelLine: { show: false },
          },
        ],
      });
    });
  });
});
