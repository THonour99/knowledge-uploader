import { App as AntdApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { createAnnouncement, listAdminAnnouncements } from "../../api/announcements";
import type * as AnnouncementApiModule from "../../api/announcements";
import { listDepartments } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import AnnouncementManagementPage from ".";

vi.mock("../../api/announcements", async () => {
  const actual = await vi.importActual<typeof AnnouncementApiModule>("../../api/announcements");
  return {
    ...actual,
    listAdminAnnouncements: vi.fn(),
    createAnnouncement: vi.fn(),
    getAnnouncementStats: vi.fn(),
  };
});

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return { ...actual, listDepartments: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AntdApp>
          <AnnouncementManagementPage />
        </AntdApp>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      media: "",
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
    value: vi.fn().mockImplementation(() => ({ getPropertyValue: () => "" })),
  });
});

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "token",
    user: {
      id: "admin-1",
      name: "管理员",
      email: "admin@company.com",
      role: "system_admin",
    },
  });
  vi.mocked(listAdminAnnouncements).mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
  });
  vi.mocked(listDepartments).mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 100,
  });
});

describe("AnnouncementManagementPage", () => {
  it("creates a plain in-app Markdown draft with safe defaults", async () => {
    vi.mocked(createAnnouncement).mockResolvedValue({
      id: "announcement-1",
      title: "维护通知",
      body_markdown: "# 维护窗口",
      audience_type: "all",
      department_ids: [],
      roles: [],
      lifecycle_state: "draft",
      state: "draft",
      visible_from: null,
      expires_at: null,
      is_pinned: false,
      is_read: false,
      row_version: 1,
      published_at: null,
      withdrawn_at: null,
      withdraw_reason: null,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /新建公告/ }));
    fireEvent.change(screen.getByLabelText("公告标题"), { target: { value: "维护通知" } });
    fireEvent.change(screen.getByPlaceholderText(/支持标题/), {
      target: { value: "# 维护窗口" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存草稿/ }));

    await waitFor(() =>
      expect(createAnnouncement).toHaveBeenCalledWith({
        title: "维护通知",
        body_markdown: "# 维护窗口",
        audience_type: "all",
        department_ids: [],
        roles: [],
        visible_from: null,
        expires_at: null,
        is_pinned: false,
      }),
    );
  });

  it("searches the complete department catalog through the server", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /新建公告/ }));
    fireEvent.mouseDown(screen.getByLabelText("受众范围"));
    fireEvent.click(await screen.findByText("指定部门"));
    fireEvent.change(screen.getByLabelText("目标部门"), { target: { value: "财务" } });

    await waitFor(() =>
      expect(listDepartments).toHaveBeenLastCalledWith({
        status: "active",
        page: 1,
        page_size: 100,
        search: "财务",
      }),
    );
  });

  it("shows an actionable error instead of an empty table", async () => {
    vi.mocked(listAdminAnnouncements).mockRejectedValueOnce(new Error("network unavailable"));
    renderPage();

    expect(await screen.findByText("公告列表加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });
});
