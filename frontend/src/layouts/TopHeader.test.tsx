import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  getSystemHealth,
  getSystemReadiness,
  listNotifications,
  logout,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationListResponse,
} from "../api/client";
import type * as ApiClientModule from "../api/client";
import { SessionSupersededError } from "../sessionIdentity";
import { useAuthStore } from "../store/auth.store";
import { themeCssVariables } from "../theme/tokens";
import { notificationDeepLink, TopHeader } from "./TopHeader";

const FILE_ID = "11111111-1111-4111-8111-111111111111";
const TASK_ID = "22222222-2222-4222-8222-222222222222";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../api/client");

  return {
    ...actual,
    getSystemHealth: vi.fn(),
    getSystemReadiness: vi.fn(),
    listNotifications: vi.fn(),
    markAllNotificationsRead: vi.fn(),
    markNotificationRead: vi.fn(),
    logout: vi.fn(),
  };
});

const mockNotifications: NotificationListResponse = {
  items: [
    {
      id: "notice-1",
      type: "review",
      title: "文件审核待处理",
      body: "技术部有 3 个文件等待审核",
      metadata: {
        resource_type: "file",
        resource_id: FILE_ID,
        url: "https://evil.example/files/file-1",
      },
      read_at: null,
      created_at: "2026-06-26T09:30:00+08:00",
    },
    {
      id: "notice-2",
      type: "ragflow",
      title: "知识库同步失败",
      body: "RAGFlow 返回解析失败状态",
      metadata: { url: "https://evil.example/task" },
      read_at: null,
      created_at: "2026-06-26T08:15:00+08:00",
    },
  ],
  total: 2,
  unread_count: 2,
  page: 1,
  page_size: 5,
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

function renderWithProviders(
  node: ReactNode,
  initialEntry = "/dashboard",
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  }),
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ConfigProvider>
        <AntdApp>
          <QueryClientProvider client={queryClient}>
            <div style={themeCssVariables as CSSProperties}>{node}</div>
          </QueryClientProvider>
        </AntdApp>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="current-path">{location.pathname}</span>;
}
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("notificationDeepLink", () => {
  const notification = (metadata: Record<string, unknown>) => ({
    ...mockNotifications.items[0],
    metadata,
  });

  it("builds links only from the structured resource contract", () => {
    expect(
      notificationDeepLink(notification({ resource_type: "file", resource_id: FILE_ID })),
    ).toBe(`/files/${FILE_ID}`);
    expect(
      notificationDeepLink(notification({ resource_type: "sync_task", resource_id: TASK_ID })),
    ).toBe(`/task-logs?task_id=${TASK_ID}`);
  });

  it("rejects malformed resource IDs and unknown resource types", () => {
    expect(
      notificationDeepLink(
        notification({ resource_type: "file", resource_id: "../../settings", file_id: FILE_ID }),
      ),
    ).toBeNull();
    expect(
      notificationDeepLink(
        notification({ resource_type: "user", resource_id: FILE_ID, file_id: FILE_ID }),
      ),
    ).toBeNull();
  });

  it("keeps strict legacy IDs temporarily and ignores URL or path fields", () => {
    expect(notificationDeepLink(notification({ file_id: FILE_ID }))).toBe(`/files/${FILE_ID}`);
    expect(notificationDeepLink(notification({ sync_task_id: TASK_ID }))).toBe(
      `/task-logs?task_id=${TASK_ID}`,
    );
    expect(
      notificationDeepLink(
        notification({ url: `https://evil.example/files/${FILE_ID}`, path: `/files/${FILE_ID}` }),
      ),
    ).toBeNull();
  });

  it("falls back to an allowlisted file for users without task-log access", () => {
    expect(
      notificationDeepLink(
        notification({
          resource_type: "sync_task",
          resource_id: TASK_ID,
          file_id: FILE_ID,
        }),
        { canAccessTaskLogs: false },
      ),
    ).toBe(`/files/${FILE_ID}`);
    expect(
      notificationDeepLink(notification({ resource_type: "sync_task", resource_id: TASK_ID }), {
        canAccessTaskLogs: false,
      }),
    ).toBeNull();
  });
});

describe("TopHeader", () => {
  it("renders notification status and unread notification preview", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(<TopHeader />);

    await waitFor(() => {
      expect(listNotifications).toHaveBeenCalledWith({ page: 1, page_size: 5 });
    });

    const statusBar = screen.getByLabelText("顶部状态栏");
    expect(await within(statusBar).findByText("API")).toBeInTheDocument();
    expect(statusBar).toHaveTextContent("队列");
    expect(statusBar).toHaveTextContent("存储");
    expect(within(statusBar).getAllByText("正常")).toHaveLength(3);
    expect(screen.getByText("API /api")).toBeInTheDocument();
    expect(screen.getByText("工作台首页")).toBeInTheDocument();
    expect(screen.getByText("王明")).toBeInTheDocument();
    expect(screen.getByText("系统管理员")).toBeInTheDocument();
    expect(document.querySelector(".ant-badge-count")).toHaveTextContent("2");

    fireEvent.click(screen.getByRole("button", { name: "通知中心" }));

    expect(await screen.findByText("文件审核待处理")).toBeInTheDocument();
    expect(screen.getByText("知识库同步失败")).toBeInTheDocument();
  });

  it("marks a notification read and only follows an allowlisted metadata deep link", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    vi.mocked(markNotificationRead).mockResolvedValue({
      ...mockNotifications.items[0],
      read_at: "2026-06-26T10:00:00+08:00",
    });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("文件审核待处理"));

    await waitFor(() => {
      expect(vi.mocked(markNotificationRead).mock.calls[0]?.[0]).toBe("notice-1");
      expect(screen.getByTestId("current-path")).toHaveTextContent(`/files/${FILE_ID}`);
    });
  });

  it("ignores a superseded notification mutation without navigating or showing an error", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    const readDeferred = createDeferred<NotificationListResponse["items"][number]>();
    vi.mocked(markNotificationRead).mockReturnValue(readDeferred.promise);
    useAuthStore.setState({
      accessToken: "token-a",
      user: {
        id: "user-a",
        name: "甲用户",
        email: "a@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("文件审核待处理"));
    await waitFor(() => {
      expect(vi.mocked(markNotificationRead).mock.calls[0]?.[0]).toBe("notice-1");
    });

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "user-b",
          name: "乙用户",
          email: "b@example.com",
          role: "employee",
        },
      });
    });
    await act(async () => {
      readDeferred.reject(new SessionSupersededError());
      await readDeferred.promise.catch(() => undefined);
      await Promise.resolve();
    });

    expect(screen.getByTestId("current-path")).toHaveTextContent("/dashboard");
    expect(screen.queryByText("请求所属登录会话已变更")).not.toBeInTheDocument();
  });

  it("blocks notification navigation when an ABA switch occurs during cache invalidation", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    vi.mocked(markNotificationRead).mockResolvedValue({
      ...mockNotifications.items[0],
      read_at: "2026-06-26T10:00:00+08:00",
    });
    const invalidationDeferred = createDeferred<void>();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    vi.spyOn(queryClient, "invalidateQueries").mockReturnValue(invalidationDeferred.promise);
    const sessionA = {
      accessToken: "token-a",
      user: {
        id: "user-a",
        name: "甲用户",
        email: "a@example.com",
        role: "system_admin" as const,
      },
    };
    useAuthStore.setState(sessionA);

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
      "/dashboard",
      queryClient,
    );
    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("文件审核待处理"));
    await waitFor(() => expect(queryClient.invalidateQueries).toHaveBeenCalled());

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "user-b",
          name: "乙用户",
          email: "b@example.com",
          role: "employee",
        },
      });
      useAuthStore.setState(sessionA);
    });
    await act(async () => {
      invalidationDeferred.resolve(undefined);
      await invalidationDeferred.promise;
      await Promise.resolve();
    });

    expect(screen.getByTestId("current-path")).toHaveTextContent("/dashboard");
    expect(screen.queryByText("请求所属登录会话已变更")).not.toBeInTheDocument();
  });

  it("blocks notification continuation after a same-token role downgrade", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    vi.mocked(markNotificationRead).mockResolvedValue({
      ...mockNotifications.items[0],
      read_at: "2026-06-26T10:00:00+08:00",
    });
    const invalidationDeferred = createDeferred<void>();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    vi.spyOn(queryClient, "invalidateQueries").mockReturnValue(invalidationDeferred.promise);
    useAuthStore.setState({
      accessToken: "same-token",
      user: {
        id: "user-a",
        name: "甲用户",
        email: "a@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
      "/dashboard",
      queryClient,
    );
    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("文件审核待处理"));
    await waitFor(() => expect(queryClient.invalidateQueries).toHaveBeenCalled());

    act(() => {
      useAuthStore.setState({
        accessToken: "same-token",
        user: {
          id: "user-a",
          name: "甲用户",
          email: "a@example.com",
          role: "employee",
        },
      });
    });
    await act(async () => {
      invalidationDeferred.resolve(undefined);
      await invalidationDeferred.promise;
      await Promise.resolve();
    });

    expect(screen.getByTestId("current-path")).toHaveTextContent("/dashboard");
    expect(screen.queryByText("请求所属登录会话已变更")).not.toBeInTheDocument();
  });
  it("suppresses a read-all success toast when session A is replaced during invalidation", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    vi.mocked(markAllNotificationsRead).mockResolvedValue({ updated_count: 2 });
    const invalidationDeferred = createDeferred<void>();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    vi.spyOn(queryClient, "invalidateQueries").mockReturnValue(invalidationDeferred.promise);
    useAuthStore.setState({
      accessToken: "token-a",
      user: {
        id: "user-a",
        name: "甲用户",
        email: "a@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(<TopHeader />, "/dashboard", queryClient);
    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("全部标为已读"));
    await waitFor(() => expect(queryClient.invalidateQueries).toHaveBeenCalled());

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "user-b",
          name: "乙用户",
          email: "b@example.com",
          role: "employee",
        },
      });
    });
    await act(async () => {
      invalidationDeferred.resolve(undefined);
      await invalidationDeferred.promise;
      await Promise.resolve();
    });

    expect(screen.queryByText("已将 2 条通知标为已读")).not.toBeInTheDocument();
  });

  it("does not let a late logout clear a direct ABA return to session A", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue({ ...mockNotifications, items: [] });
    const logoutDeferred = createDeferred<void>();
    vi.mocked(logout).mockReturnValueOnce(logoutDeferred.promise);
    const sessionA = {
      accessToken: "token-a",
      user: {
        id: "user-a",
        name: "甲用户",
        email: "a@example.com",
        role: "system_admin" as const,
      },
    };
    useAuthStore.setState(sessionA);

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
    );
    fireEvent.click((await screen.findByText("甲用户")).closest("button")!);
    fireEvent.click(await screen.findByText("退出登录"));
    await waitFor(() => expect(logout).toHaveBeenCalledOnce());

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "user-b",
          name: "乙用户",
          email: "b@example.com",
          role: "employee",
        },
      });
      useAuthStore.setState(sessionA);
    });
    await act(async () => {
      logoutDeferred.resolve(undefined);
      await logoutDeferred.promise;
      await Promise.resolve();
    });

    expect(useAuthStore.getState()).toMatchObject({
      accessToken: "token-a",
      user: { id: "user-a" },
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/dashboard");
  });

  it("keeps session B when session A logout completes late", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue({ ...mockNotifications, items: [] });
    const logoutDeferred = createDeferred<void>();
    vi.mocked(logout).mockReturnValueOnce(logoutDeferred.promise);
    useAuthStore.setState({
      accessToken: "token-a",
      user: {
        id: "user-a",
        name: "甲用户",
        email: "a@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
    );

    fireEvent.click((await screen.findByText("甲用户")).closest("button")!);
    fireEvent.click(await screen.findByText("退出登录"));
    await waitFor(() => expect(logout).toHaveBeenCalledOnce());

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "user-b",
          name: "乙用户",
          email: "b@example.com",
          role: "employee",
        },
      });
    });
    await act(async () => {
      logoutDeferred.resolve(undefined);
      await logoutDeferred.promise;
      await Promise.resolve();
    });

    expect(useAuthStore.getState()).toMatchObject({
      accessToken: "token-b",
      user: { id: "user-b" },
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/dashboard");
  });

  it("uses the read-all response contract and ignores arbitrary metadata URLs", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue(mockNotifications);
    vi.mocked(markAllNotificationsRead).mockResolvedValue({ updated_count: 2 });
    vi.mocked(markNotificationRead).mockResolvedValue({
      ...mockNotifications.items[1],
      read_at: "2026-06-26T10:00:00+08:00",
    });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("全部标为已读"));
    await waitFor(() => {
      expect(markAllNotificationsRead).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "通知中心" }));
    fireEvent.click(await screen.findByText("知识库同步失败"));
    await waitFor(() => {
      expect(vi.mocked(markNotificationRead).mock.calls[0]?.[0]).toBe("notice-2");
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/dashboard");
  });

  it("opens a paginated notification center and filters unread items", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockImplementation(async (params = {}) => {
      if (params.page_size === 10) {
        return {
          ...mockNotifications,
          total: 12,
          page: params.page ?? 1,
          page_size: 10,
          items: params.unread_only ? [mockNotifications.items[0]] : mockNotifications.items,
        };
      }
      return mockNotifications;
    });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(<TopHeader />);

    fireEvent.click(await screen.findByRole("button", { name: "通知中心" }));

    const drawerTitle = await screen.findByText("通知中心", {
      selector: ".ant-drawer-title",
    });
    const drawer = drawerTitle.closest<HTMLElement>(".ant-drawer-content");
    expect(drawer).not.toBeNull();
    if (!drawer) {
      throw new Error("notification center drawer did not render");
    }
    await waitFor(() => {
      expect(listNotifications).toHaveBeenCalledWith({
        page: 1,
        page_size: 10,
        unread_only: false,
      });
    });
    expect(within(drawer).getByText("2 条未读")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "全部标为已读" })).toBeInTheDocument();

    fireEvent.click(within(drawer).getByText("未读"));
    await waitFor(() => {
      expect(listNotifications).toHaveBeenCalledWith({
        page: 1,
        page_size: 10,
        unread_only: true,
      });
    });

    fireEvent.click(await within(drawer).findByTitle("2"));
    await waitFor(() => {
      expect(listNotifications).toHaveBeenCalledWith({
        page: 2,
        page_size: 10,
        unread_only: true,
      });
    });
  });

  it("navigates to matched admin page from global search", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(getSystemReadiness).mockResolvedValue({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        rabbitmq: { status: "ok" },
        redis: { status: "ok" },
        minio: { status: "ok" },
      },
    });
    vi.mocked(listNotifications).mockResolvedValue({ ...mockNotifications, items: [] });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: "system_admin",
      },
    });

    renderWithProviders(
      <>
        <TopHeader />
        <LocationProbe />
      </>,
    );

    const searchInput = await screen.findByRole("searchbox", { name: "全局搜索" });
    fireEvent.change(searchInput, { target: { value: "文件管理" } });
    fireEvent.keyDown(searchInput, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      expect(screen.getByTestId("current-path")).toHaveTextContent("/files");
    });
  });
});
