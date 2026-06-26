import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type ConfigGroupResponse,
  type RagflowConnectionTestResult,
  getConfigs,
  testRagflowConnection,
  updateConfigs,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
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

const mockBasicGroup: ConfigGroupResponse = {
  group: "basic",
  items: [
    {
      key: "basic.system_name",
      value: "知识库平台",
      value_type: "string",
      is_secret: false,
      masked_value: null,
      description: "系统名称",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "basic.default_language",
      value: "zh-CN",
      value_type: "string",
      is_secret: false,
      masked_value: null,
      description: "默认语言",
      updated_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const mockUploadGroup: ConfigGroupResponse = {
  group: "upload",
  items: [
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
      key: "processing.auto_parse_on_upload",
      value: true,
      value_type: "bool",
      is_secret: false,
      masked_value: null,
      description: "上传后自动解析",
      updated_at: "2026-06-10T00:00:00Z",
    },
    {
      key: "processing.task_max_retries",
      value: 3,
      value_type: "int",
      is_secret: false,
      masked_value: null,
      description: "任务最大重试次数",
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
      key: "security.require_email_verification",
      value: true,
      value_type: "bool",
      is_secret: false,
      masked_value: null,
      description: "要求邮箱验证",
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
  ],
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function setupMocks() {
  vi.mocked(getConfigs).mockImplementation((group) => {
    const map: Record<string, ConfigGroupResponse> = {
      basic: mockBasicGroup,
      upload: mockUploadGroup,
      processing: mockProcessingGroup,
      security: mockSecurityGroup,
      ragflow: mockRagflowGroup,
    };

    return Promise.resolve(map[group] ?? mockBasicGroup);
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
  vi.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("SettingsPage", () => {
  it("renders all 5 config tabs", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    expect(await screen.findByRole("tab", { name: "基础" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "上传" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "处理" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "安全" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "RAGFlow" })).toBeInTheDocument();
  });

  it("renders configuration summary strip and switches panels from shortcut cards", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    const summary = await screen.findByRole("region", { name: "配置运行摘要" });
    expect(summary).toHaveTextContent("配置中心");
    expect(summary).toHaveTextContent("基础参数");
    expect(summary).toHaveTextContent("上传策略");
    expect(summary).toHaveTextContent("RAGFlow 同步");
    expect(summary).toHaveTextContent("服务状态");

    fireEvent.click(screen.getByRole("button", { name: /服务状态/ }));

    expect(await screen.findByText("服务连接状态")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "服务状态", selected: true })).toBeInTheDocument();
  });

  it("loads and fills basic tab with fetched data", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    // basic tab is active by default; wait for data to populate
    expect(await screen.findByDisplayValue("知识库平台")).toBeInTheDocument();
  });

  it("renders configuration panel summary metrics", async () => {
    setupMocks();
    renderWithProviders(<SettingsPage />);

    await screen.findByDisplayValue("知识库平台");

    const basicSummary = screen.getByRole("region", { name: "配置面板摘要" });
    expect(basicSummary).toHaveTextContent("配置摘要");
    expect(basicSummary).toHaveTextContent("配置项");
    expect(basicSummary).toHaveTextContent("2 项");
    expect(basicSummary).toHaveTextContent("最近更新");

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
