import type { CSSProperties } from "react";
import { ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { getSystemHealth } from "../api/client";
import type * as ApiClientModule from "../api/client";
import { Roles, useAuthStore } from "../store/auth.store";
import { useUiStore } from "../store/ui.store";
import { themeCssVariables } from "../theme/tokens";
import { Sidebar } from "./Sidebar";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../api/client");

  return {
    ...actual,
    getSystemHealth: vi.fn(),
  };
});

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
});

function renderSidebar(initialEntry = "/dashboard") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ConfigProvider>
        <QueryClientProvider client={queryClient}>
          <div className="app-shell__sider" style={themeCssVariables as CSSProperties}>
            <Sidebar />
          </div>
        </QueryClientProvider>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useAuthStore.setState({ accessToken: null, user: null });
  useUiStore.setState({ sidebarCollapsed: false });
});

describe("Sidebar", () => {
  it("renders admin navigation and the platform health card", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: Roles.SYSTEM_ADMIN,
      },
    });

    renderSidebar();

    expect(screen.getByText("知识库贡献平台")).toBeInTheDocument();
    expect(screen.getByText("运营总览")).toBeInTheDocument();
    expect(screen.getByText("统计报表")).toBeInTheDocument();
    const healthCard = screen.getByLabelText("平台运行状态");
    expect(healthCard).toHaveTextContent("运行状态");
    expect(await within(healthCard).findByText("正常")).toBeInTheDocument();
    expect(healthCard).toHaveTextContent("服务链路正常");
    expect(getSystemHealth).toHaveBeenCalledTimes(1);
  });

  it("collapses health card text when the sidebar is collapsed", async () => {
    vi.mocked(getSystemHealth).mockResolvedValue({ status: "ok" });
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "user-1",
        name: "王明",
        email: "wangming@example.com",
        role: Roles.SYSTEM_ADMIN,
      },
    });

    renderSidebar();
    expect(await screen.findByText("正常")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "收起菜单" }));

    const healthCard = screen.getByLabelText("平台运行状态");
    expect(screen.queryByText("知识库贡献平台")).not.toBeInTheDocument();
    expect(healthCard).not.toHaveTextContent("运行状态");
    expect(screen.getByRole("button", { name: "展开菜单" })).toBeInTheDocument();
  });
});
