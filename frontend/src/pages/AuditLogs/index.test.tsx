import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { type AuditLogListResponse, listAuditLogs } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import AuditLogsPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listAuditLogs: vi.fn(),
  };
});

const mockLogs: AuditLogListResponse = {
  items: [
    {
      id: "log-001",
      actor_id: "user-001",
      actor_name: "张三",
      actor_email: "zhangsan@example.com",
      action: "config.update",
      target_type: "SystemConfig",
      target_id: "cfg-001",
      ip_address: "192.168.1.100",
      user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      reason: "更新了 RAGFlow 连接配置",
      metadata: { key: "ragflow_api_key", old_masked: "sk-****1234", changed_by: "admin" },
      created_at: "2026-06-10T08:30:00Z",
    },
    {
      id: "log-002",
      actor_id: "user-002",
      actor_name: "李四",
      actor_email: "lisi@example.com",
      action: "file.approve",
      target_type: "KnowledgeFile",
      target_id: "file-abc",
      ip_address: "10.0.0.55",
      user_agent: "Chrome/120",
      reason: "内容符合规范",
      metadata: null,
      created_at: "2026-06-10T09:15:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
};

const mockLogsPage2: AuditLogListResponse = {
  items: [
    {
      id: "log-003",
      actor_id: "user-003",
      actor_name: "王五",
      actor_email: "wangwu@example.com",
      action: "user.disable",
      target_type: "User",
      target_id: "user-999",
      ip_address: "172.16.0.1",
      user_agent: "Firefox/115",
      reason: "违规操作",
      metadata: { note: "多次违规" },
      created_at: "2026-06-09T14:00:00Z",
    },
  ],
  total: 21,
  page: 2,
  page_size: 20,
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

afterEach(() => {
  vi.clearAllMocks();
});

describe("AuditLogsPage", () => {
  it("renders table with audit log data columns", async () => {
    vi.mocked(listAuditLogs).mockResolvedValue(mockLogs);

    renderWithProviders(<AuditLogsPage />);

    // 等待数据加载
    expect(await screen.findByText("张三")).toBeInTheDocument();
    expect(screen.getByText("zhangsan@example.com")).toBeInTheDocument();
    expect(screen.getByText("config.update")).toBeInTheDocument();
    expect(screen.getByText("SystemConfig")).toBeInTheDocument();
    expect(screen.getByText("192.168.1.100")).toBeInTheDocument();
    expect(screen.getByText("更新了 RAGFlow 连接配置")).toBeInTheDocument();

    const traceStrip = screen.getByRole("region", { name: "审计运行状态" });
    expect(traceStrip).toHaveTextContent("审计运行状态");
    expect(traceStrip).toHaveTextContent("2 条当前页记录");
    expect(traceStrip).toHaveTextContent("平台匹配 2 条审计事件");
    expect(traceStrip).toHaveTextContent("2 个操作人");
    expect(traceStrip).toHaveTextContent("1 条配置变更");
    expect(traceStrip).toHaveTextContent("1 条文件操作");
    expect(traceStrip).toHaveTextContent("当前列表未应用筛选条件");

    // 第二行
    expect(screen.getByText("李四")).toBeInTheDocument();
    expect(screen.getByText("lisi@example.com")).toBeInTheDocument();
    expect(screen.getByText("file.approve")).toBeInTheDocument();
    expect(screen.getByText("KnowledgeFile")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.55")).toBeInTheDocument();
  });

  it("triggers re-query with action filter param when action select changes", async () => {
    vi.mocked(listAuditLogs).mockResolvedValue(mockLogs);

    renderWithProviders(<AuditLogsPage />);

    await screen.findByText("张三");

    // 找到操作类型 Select 并选择一个选项
    const actionSelect = screen.getByRole("combobox", { name: /操作类型/ });
    fireEvent.mouseDown(actionSelect);

    // Ant Design Select 下拉选项渲染在 document.body 末尾的 popup 容器中
    // 使用 getAllByTitle 再筛选出 dropdown 选项（role=option）
    const options = await screen.findAllByTitle("config.update");
    const dropdownOption = options.find((el) => el.classList.contains("ant-select-item-option-content") || el.getAttribute("role") === "option" || el.closest(".ant-select-dropdown") !== null);
    fireEvent.click(dropdownOption ?? options[options.length - 1]);

    await waitFor(() => {
      const calls = vi.mocked(listAuditLogs).mock.calls;
      expect(calls.length).toBeGreaterThanOrEqual(2);
      const lastCall = calls[calls.length - 1][0];
      expect(lastCall).toMatchObject({ action: "config.update" });
    });
  });

  it("triggers re-query with page param when pagination changes", async () => {
    const bigResponse: AuditLogListResponse = {
      ...mockLogsPage2,
      total: 21,
      page: 1,
      page_size: 20,
    };
    vi.mocked(listAuditLogs).mockResolvedValue(bigResponse);

    renderWithProviders(<AuditLogsPage />);

    await screen.findByText("王五");

    // 点击第 2 页
    const page2Button = await screen.findByRole("listitem", { name: "2" });
    fireEvent.click(page2Button);

    await waitFor(() => {
      const calls = vi.mocked(listAuditLogs).mock.calls;
      expect(calls.length).toBeGreaterThanOrEqual(2);
      const lastCall = calls[calls.length - 1][0];
      expect(lastCall).toMatchObject({ page: 2 });
    });
  });

  it("opens detail drawer showing metadata JSON when detail button clicked", async () => {
    vi.mocked(listAuditLogs).mockResolvedValue(mockLogs);

    renderWithProviders(<AuditLogsPage />);

    await screen.findByText("张三");

    // 点击第一行的详情按钮
    const detailButtons = screen.getAllByRole("button", { name: "详情" });
    fireEvent.click(detailButtons[0]);

    // Drawer 应该打开，显示 metadata 和 user_agent
    await waitFor(() => {
      // metadata 的 JSON 内容
      expect(screen.getByText(/ragflow_api_key/)).toBeInTheDocument();
      // user_agent
      expect(
        screen.getByText(/Mozilla\/5\.0 \(Windows NT 10\.0; Win64; x64\)/),
      ).toBeInTheDocument();
    });
  });
});
