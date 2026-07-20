import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { getMe, getSystemHealth, getSystemReadiness, listNotifications } from "../api/client";
import type * as ApiClientModule from "../api/client";
import type * as AnnouncementApiModule from "../api/announcements";
import { hasAssignedDepartment, useAuthStore } from "../store/auth.store";
import { useUiStore } from "../store/ui.store";
import { themeCssVariables } from "../theme/tokens";
import { AppShell } from "./AppShell";

vi.mock("../api/announcements", async () => {
  const actual = await vi.importActual<typeof AnnouncementApiModule>("../api/announcements");
  return {
    ...actual,
    listAnnouncements: vi.fn().mockResolvedValue({
      items: [],
      total: 0,
      unread_count: 0,
      page: 1,
      page_size: 20,
    }),
    markAnnouncementRead: vi.fn().mockResolvedValue(undefined),
  };
});

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../api/client");
  return {
    ...actual,
    getMe: vi.fn(),
    getSystemHealth: vi.fn(),
    getSystemReadiness: vi.fn(),
    listNotifications: vi.fn(),
  };
});

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("max-width"),
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

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useAuthStore.setState({ accessToken: null, user: null });
  useUiStore.setState({ sidebarCollapsed: false, mobileNavigationOpen: false });
});

describe("AppShell mobile navigation", () => {
  it("opens the drawer from the header and closes it after navigation", async () => {
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
    vi.mocked(listNotifications).mockResolvedValue({
      items: [],
      total: 0,
      unread_count: 0,
      page: 1,
      page_size: 5,
    });
    vi.mocked(getMe).mockResolvedValue({
      id: "employee-1",
      name: "张三",
      email: "employee@company.com",
      role: "employee",
      status: "active",
      email_verified: true,
      department_assigned: true,
      department_id: "dept-tech",
      department_name: "技术部",
      department_code: "tech",
      department: "技术部",
      phone: null,
    });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "employee-1",
        name: "张三",
        email: "employee@company.com",
        role: "employee",
      },
    });
    expect(hasAssignedDepartment(useAuthStore.getState().user)).toBe(false);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <MemoryRouter initialEntries={["/upload"]}>
        <ConfigProvider>
          <AntdApp>
            <QueryClientProvider client={queryClient}>
              <div style={themeCssVariables as CSSProperties}>
                <Routes>
                  <Route element={<AppShell />}>
                    <Route path="/upload" element={<span>上传页面</span>} />
                    <Route path="/my-files" element={<span>我的文件页面</span>} />
                  </Route>
                </Routes>
              </div>
            </QueryClientProvider>
          </AntdApp>
        </ConfigProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(getMe).toHaveBeenCalledTimes(1);
      expect(hasAssignedDepartment(useAuthStore.getState().user)).toBe(true);
    });

    expect(screen.getByText("上传页面")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));

    const drawer = await screen.findByLabelText("移动导航");
    expect(useUiStore.getState().mobileNavigationOpen).toBe(true);
    fireEvent.click(within(drawer).getByText("我的文件"));

    await waitFor(() => {
      expect(screen.getByText("我的文件页面")).toBeInTheDocument();
      expect(useUiStore.getState().mobileNavigationOpen).toBe(false);
    });
  });
});
