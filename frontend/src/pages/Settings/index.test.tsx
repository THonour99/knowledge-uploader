import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type ConfigGroupResponse,
  type RagflowConnectionTestResult,
  getConfigs,
  testRagflowConnection,
  updateConfigs,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import SettingsPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getConfigs: vi.fn(),
    updateConfigs: vi.fn(),
    testRagflowConnection: vi.fn(),
  };
});

// ── Mock data ─────────────────────────────────────────────────────────────────

const mockUploadGroup: ConfigGroupResponse = {
  group: "upload",
  items: [
    {
      key: "upload.enabled",
      value: true,
      value_type: "bool",
      is_secret: false,
      immutable: true,
      masked_value: null,
      description: "是否允许员工发起新的文件上传",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "upload.max_file_size_mb",
      value: 200,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "单文件最大大小（MB）",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "upload.allow_multi_file",
      value: true,
      value_type: "bool",
      is_secret: false,
      masked_value: null,
      description: "允许批量上传",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "upload.allowed_extensions",
      value: [".pdf", ".docx"],
      value_type: "list",
      is_secret: false,
      masked_value: null,
      description: "允许的扩展名",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const mockProcessingGroup: ConfigGroupResponse = {
  group: "processing",
  items: [
    {
      key: "processing.parse_max_pages",
      value: 200,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "解析最大页数",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const mockOutboxGroup: ConfigGroupResponse = {
  group: "outbox",
  items: [
    {
      key: "outbox.publish_max_retries",
      value: 3,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "Outbox 事件发布最大重试次数",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const mockSecurityGroup: ConfigGroupResponse = {
  group: "security",
  items: [
    {
      key: "security.login_max_failed_attempts",
      value: 5,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "登录失败锁定阈值",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "security.block_critical_sensitive_sync",
      value: true,
      value_type: "bool",
      is_secret: false,
      masked_value: null,
      description: "严重敏感文档禁止同步",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const mockReviewGroup: ConfigGroupResponse = {
  group: "review",
  items: [
    {
      key: "review.claim_timeout_minutes",
      value: 30,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "审核领取有效分钟数",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "review.sla_hours",
      value: 24,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "审核 SLA 小时数",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const mockRagflowGroup: ConfigGroupResponse = {
  group: "ragflow",
  items: [
    {
      key: "ragflow.base_url",
      value: "http://192.168.4.46:8092",
      value_type: "string",
      is_secret: false,
      masked_value: null,
      description: "RAGFlow 服务地址",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "ragflow.api_key",
      value: null,
      value_type: "secret",
      is_secret: true,
      masked_value: "sk-****abcd",
      description: "RAGFlow API Key",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "ragflow.allow_high_risk_sync",
      value: false,
      value_type: "bool",
      is_secret: false,
      masked_value: null,
      description: "允许高风险文档同步",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "ragflow.keep_replaced_remote",
      value: true,
      value_type: "bool",
      is_secret: false,
      masked_value: null,
      description: "新版本生效时是否保留旧远端文档并将其标记为非当前版本",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function setupMocks() {
  vi.mocked(getConfigs).mockImplementation((group) => {
    const map: Record<string, ConfigGroupResponse> = {
      upload: mockUploadGroup,
      processing: mockProcessingGroup,
      security: mockSecurityGroup,
      review: mockReviewGroup,
      ragflow: mockRagflowGroup,
      outbox: mockOutboxGroup,
    };

    return Promise.resolve(map[group] ?? mockUploadGroup);
  });
}

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
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("SettingsPage", () => {
  it("renders all 5 config tabs", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    expect(await screen.findByRole("tab", { name: "上传" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "处理" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "审核" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "安全" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "RAGFlow" })).toBeInTheDocument();
  });

  it("renders configuration summary strip and switches panels from shortcut cards", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    const summary = await screen.findByRole("region", { name: "配置运行摘要" });
    expect(summary).toHaveTextContent("配置中心");
    expect(summary).toHaveTextContent("上传策略");
    expect(summary).toHaveTextContent("审核时效");
    expect(summary).toHaveTextContent("RAGFlow 同步");
    expect(summary).toHaveTextContent("服务状态");

    fireEvent.click(screen.getByRole("button", { name: /服务状态/ }));

    expect(await screen.findByText("服务连接状态")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "服务状态", selected: true })).toBeInTheDocument();
  });

  it("drives overview cards and shortcut metadata from fetched config groups", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    expect(await screen.findByText("6/6")).toBeInTheDocument();
    expect(screen.getByText("配置已同步")).toBeInTheDocument();
    expect(screen.getAllByText("1 项").length).toBeGreaterThan(0);
    expect(screen.getByText("无待处理项")).toBeInTheDocument();

    const summary = screen.getByRole("region", { name: "配置运行摘要" });
    expect(summary).toHaveTextContent("4 项配置");
    expect(summary).toHaveTextContent("2 项配置");
  });
  it("defaults to upload and exposes the upload enable gate", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    expect(await screen.findByRole("tab", { name: "上传", selected: true })).toBeInTheDocument();
    expect(await screen.findByText("允许员工上传")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("200")).toBeInTheDocument();
  });

  it("renders configuration panel summary metrics", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    await screen.findByDisplayValue("200");

    const uploadSummary = screen.getByRole("region", { name: "配置面板摘要" });
    expect(uploadSummary).toHaveTextContent("配置摘要");
    expect(uploadSummary).toHaveTextContent("配置项");
    expect(uploadSummary).toHaveTextContent("4 项");
    expect(uploadSummary).toHaveTextContent("最近更新");

    fireEvent.click(screen.getByRole("tab", { name: "RAGFlow" }));
    await screen.findByDisplayValue("http://192.168.4.46:8092");

    const ragflowSummary = screen.getByRole("region", { name: "配置面板摘要" });
    expect(ragflowSummary).toHaveTextContent("密钥项");
    expect(ragflowSummary).toHaveTextContent("1 项");
  });

  it("loads upload tab and shows max_file_size_mb field", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "上传" }));

    // The InputNumber for max_file_size_mb should show value 200
    expect(await screen.findByDisplayValue("200")).toBeInTheDocument();
  });

  it("saves upload group with correct payload when max_file_size_mb changes", async () => {
    setupMocks();
    vi.mocked(updateConfigs).mockResolvedValue(mockUploadGroup);

    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "上传" }));

    // wait for form to populate
    const sizeInput = await screen.findByDisplayValue("200");

    // change the value
    fireEvent.change(sizeInput, { target: { value: "512" } });

    // click save
    const saveBtn = await screen.findByRole("button", { name: /保存/ });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateConfigs).toHaveBeenCalledWith(
        "upload",
        expect.objectContaining({ "upload.max_file_size_mb": expect.anything() }),
      );
    });
  });

  it("does not submit A configuration from a delayed form continuation after switching to B", async () => {
    setupMocks();
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
    vi.mocked(updateConfigs).mockResolvedValue(mockUploadGroup);

    renderWithProviders(<SettingsPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "上传" }));
    const sizeInput = await screen.findByDisplayValue("200");
    fireEvent.change(sizeInput, { target: { value: "512" } });
    fireEvent.click(await screen.findByRole("button", { name: /保存/ }));
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
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(updateConfigs).not.toHaveBeenCalled();
  });

  it("loads and saves the review claim timeout and SLA contract", async () => {
    setupMocks();
    vi.mocked(updateConfigs).mockResolvedValue(mockReviewGroup);

    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "审核" }));

    expect(await screen.findByText("审核领取有效期（分钟）")).toBeInTheDocument();
    expect(screen.getByText("审核 SLA（小时）")).toBeInTheDocument();
    expect(screen.getByDisplayValue("30")).toBeInTheDocument();
    expect(screen.getByDisplayValue("24")).toBeInTheDocument();

    const saveBtn = await screen.findByRole("button", { name: /保存/ });
    fireEvent.click(saveBtn);
    await waitFor(() => {
      expect(document.querySelector(".ant-modal-confirm-btns .ant-btn-primary")).not.toBeNull();
    });
    fireEvent.click(
      document.querySelector(".ant-modal-confirm-btns .ant-btn-primary") as HTMLElement,
    );

    await waitFor(() => {
      expect(updateConfigs).toHaveBeenCalled();
      expect(updateConfigs).toHaveBeenCalledWith(
        "review",
        expect.objectContaining({
          "review.claim_timeout_minutes": 30,
          "review.sla_hours": 24,
        }),
      );
    });
  });

  it("keeps the high-risk RAGFlow sync decision configurable", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "RAGFlow" }));

    expect(await screen.findByText("允许高风险文档同步")).toBeInTheDocument();
  });

  it("shows a product label for the replaced-remote policy instead of its raw key", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "RAGFlow" }));

    expect(await screen.findByText("替代版本生效后保留旧远端")).toBeInTheDocument();
    expect(screen.queryByText("ragflow.keep_replaced_remote")).not.toBeInTheDocument();
  });

  it("locks the critical-risk invariant and excludes it from update payloads", async () => {
    setupMocks();
    vi.mocked(updateConfigs).mockResolvedValue(mockSecurityGroup);
    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "安全" }));

    const invariantSwitch = await screen.findByRole("switch", {
      name: "阻断严重敏感内容同步",
    });
    expect(invariantSwitch).toBeChecked();
    expect(invariantSwitch).toBeDisabled();
    expect(screen.getAllByText(/安全不变量.*不能关闭/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => {
      expect(document.querySelector(".ant-modal-confirm-btns .ant-btn-primary")).not.toBeNull();
    });
    fireEvent.click(
      document.querySelector(".ant-modal-confirm-btns .ant-btn-primary") as HTMLElement,
    );

    await waitFor(() => {
      expect(updateConfigs).toHaveBeenCalled();
      const [, items] = vi.mocked(updateConfigs).mock.calls[0];
      expect(items).not.toHaveProperty("security.block_critical_sensitive_sync");
      expect(items).toHaveProperty("security.login_max_failed_attempts", 5);
    });
  });

  it("does not include empty secret fields in the submit payload", async () => {
    setupMocks();
    vi.mocked(updateConfigs).mockResolvedValue(mockRagflowGroup);

    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "RAGFlow" }));

    // wait for form to be ready — the base_url field should be visible
    await screen.findByDisplayValue("http://192.168.4.46:8092");

    // Do NOT fill the api_key Password field — leave it empty

    const saveBtn = await screen.findByRole("button", { name: /保存/ });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateConfigs).toHaveBeenCalled();
      const [, items] = vi.mocked(updateConfigs).mock.calls[0];
      expect(items).not.toHaveProperty("ragflow.api_key");
    });
  });
  it.each(["A→B", "ABA"] as const)(
    "clears a RAGFlow secret entered before save on %s",
    async (switchMode) => {
      const sessionA = {
        accessToken: "token-a",
        user: {
          id: "admin-a",
          name: "甲管理员",
          email: "a@example.com",
          role: "system_admin" as const,
        },
      };
      useAuthStore.setState(sessionA);
      setupMocks();
      renderWithProviders(<SettingsPage />);

      fireEvent.click(await screen.findByRole("tab", { name: "RAGFlow" }));
      const secretInput = await screen.findByLabelText("RAGFlow API Key");
      fireEvent.change(secretInput, { target: { value: "sk-session-a-secret" } });
      expect(secretInput).toHaveValue("sk-session-a-secret");

      act(() => {
        useAuthStore.setState({
          accessToken: "token-b",
          user: {
            ...sessionA.user,
            id: "admin-b",
            email: "b@example.com",
          },
        });
        if (switchMode === "ABA") {
          useAuthStore.setState(sessionA);
        }
      });

      await waitFor(() => {
        expect(screen.getByLabelText("RAGFlow API Key")).toHaveValue("");
      });
      fireEvent.click(screen.getByRole("button", { name: /保存/ }));
      await Promise.resolve();
      expect(updateConfigs).not.toHaveBeenCalled();
    },
  );

  it("shows ragflow test-connection success state with latency", async () => {
    setupMocks();
    const successResult: RagflowConnectionTestResult = {
      ok: true,
      latency_ms: 85,
      error: null,
    };
    vi.mocked(testRagflowConnection).mockResolvedValue(successResult);

    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "RAGFlow" }));

    const testBtn = await screen.findByRole("button", { name: /测试连接/ });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testRagflowConnection).toHaveBeenCalled();
    });

    // Success message should mention latency
    expect(await screen.findByText(/85ms/)).toBeInTheDocument();
  });

  it("shows ragflow test-connection failure state with error message", async () => {
    setupMocks();
    const failResult: RagflowConnectionTestResult = {
      ok: false,
      latency_ms: null,
      error: "Connection refused",
    };
    vi.mocked(testRagflowConnection).mockResolvedValue(failResult);

    renderWithProviders(<SettingsPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "RAGFlow" }));

    const testBtn = await screen.findByRole("button", { name: /测试连接/ });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testRagflowConnection).toHaveBeenCalled();
    });

    // Failure message / alert should be visible
    expect(await screen.findByText(/Connection refused/)).toBeInTheDocument();
  });
});
