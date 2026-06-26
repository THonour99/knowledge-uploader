import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { listNotifications, type NotificationListResponse } from "../api/client";
import type * as ApiClientModule from "../api/client";
import { useAuthStore } from "../store/auth.store";
import { themeCssVariables } from "../theme/tokens";
import { TopHeader } from "./TopHeader";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../api/client");

  return {
    ...actual,
    listNotifications: vi.fn(),
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
      metadata: {},
      read_at: null,
      created_at: "2026-06-26T09:30:00+08:00",
    },
    {
      id: "notice-2",
      type: "ragflow",
      title: "知识库同步失败",
      body: "RAGFlow 返回解析失败状态",
      metadata: {},
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

function renderWithProviders(node: ReactNode, initialEntry = "/dashboard") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

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

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("TopHeader", () => {
  it("renders notification status and unread notification preview", async () => {
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

    expect(await screen.findByText("通知正常")).toBeInTheDocument();
    expect(screen.getByText("仪表盘")).toBeInTheDocument();
    expect(screen.getByText("王明")).toBeInTheDocument();
    expect(screen.getByText("系统管理员")).toBeInTheDocument();
    expect(document.querySelector(".ant-badge-count")).toHaveTextContent("2");

    fireEvent.click(screen.getByRole("button", { name: "通知中心" }));

    expect(await screen.findByText("文件审核待处理")).toBeInTheDocument();
    expect(screen.getByText("知识库同步失败")).toBeInTheDocument();
  });
});
