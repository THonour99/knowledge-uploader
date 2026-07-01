import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type AiConfigResponse,
  createAiProvider,
  getAiConfig,
  testAiProvider,
  updateAiFeature,
  updateAiProvider,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import AiConfigPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    createAiProvider: vi.fn(),
    getAiConfig: vi.fn(),
    testAiProvider: vi.fn(),
    updateAiFeature: vi.fn(),
    updateAiProvider: vi.fn(),
  };
});

const mockConfig: AiConfigResponse = {
  global: {
    ai_analysis_enabled: true,
    allow_external_llm: true,
    allow_sync_when_analysis_failed: false,
  },
  features: [
    {
      key: "summary",
      name: "文档摘要",
      description: "使用 AI 生成文档摘要",
      enabled: true,
    },
    {
      key: "sensitive_detection",
      name: "敏感检测",
      description: "检测文档中的敏感内容与合规风险",
      enabled: false,
    },
  ],
  providers: [
    {
      id: "provider-1",
      name: "OpenAI 兼容供应商",
      provider_type: "openai_compatible",
      base_url: "https://api.openai.com/v1",
      chat_model: "gpt-4o-mini",
      embedding_model: null,
      vision_model: null,
      is_internal: false,
      enabled: true,
      priority: 1,
      timeout_seconds: 60,
      max_retry_count: 2,
      max_input_tokens: 128000,
      max_output_tokens: 4096,
      temperature: 0.2,
      top_p: null,
      has_api_key: true,
      api_key_masked: "sk-****abcd",
      last_test_status: "success",
      last_test_latency_ms: 268,
      last_tested_at: "2026-06-06T10:00:00Z",
      created_at: "2026-06-06T09:00:00Z",
      updated_at: "2026-06-06T10:00:00Z",
    },
  ],
  prompt_templates: [
    {
      id: "template-1",
      template_key: "document_summary",
      name: "文档摘要模板",
      description: "提炼主要内容",
      enabled: true,
      is_default: true,
      version: 3,
      updated_at: "2026-06-06T10:00:00Z",
    },
  ],
  sensitive_rules: [
    {
      id: "rule-1",
      name: "个人信息",
      rule_type: "regex",
      risk_level: "high",
      action: "desensitize",
      enabled: true,
      hit_count: 356,
      updated_at: "2026-06-06T10:00:00Z",
    },
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

describe("AiConfigPage", () => {
  it("renders the configured AI sections", async () => {
    vi.mocked(getAiConfig).mockResolvedValue(mockConfig);

    renderWithProviders(<AiConfigPage />);

    expect(await screen.findByRole("heading", { name: "AI 文档分析配置" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "功能开关" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "模型供应商" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Prompt 模板" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "敏感规则" })).toBeInTheDocument();
    expect(screen.getByText("启用功能")).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();
    expect(screen.getByText("可用供应商")).toBeInTheDocument();
    expect(screen.getByText("敏感治理")).toBeInTheDocument();
    expect(screen.getAllByText("356 次累计命中").length).toBeGreaterThan(0);
    const governance = screen.getByRole("region", { name: "AI 治理总览" });
    expect(governance).toHaveTextContent("AI 治理总览");
    expect(governance).toHaveTextContent("1/2 项已开启");
    expect(governance).toHaveTextContent("覆盖率 50%");
    expect(governance).toHaveTextContent("1/1 个通过测试");
    expect(governance).toHaveTextContent("1 个默认模板");
    expect(governance).toHaveTextContent("1 条已启用");
    expect(governance).toHaveTextContent("供应商就绪度100%");
    expect(screen.getAllByText("AI 总开关").length).toBeGreaterThan(0);
    expect(screen.getByText("文档摘要")).toBeInTheDocument();
    expect(screen.getByText("敏感检测")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "模型供应商" }));
    expect(screen.getByRole("heading", { name: "模型供应商" })).toBeInTheDocument();
    expect(screen.getByText("当前维护 1 个供应商，1 个已通过测试")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Prompt 模板" }));
    expect(screen.getByRole("heading", { name: "Prompt 模板" })).toBeInTheDocument();
    expect(screen.getByText("当前维护 1 个模板，1 个默认模板，1 个启用")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "敏感规则" }));
    expect(screen.getByRole("heading", { name: "敏感规则" })).toBeInTheDocument();
    expect(screen.getByText("当前维护 1 条规则，1 条启用，356 次累计命中")).toBeInTheDocument();
  });

  it("tests a provider connection from the provider tab", async () => {
    vi.mocked(getAiConfig).mockResolvedValue(mockConfig);
    vi.mocked(testAiProvider).mockResolvedValue({
      provider_id: "provider-1",
      status: "success",
      latency_ms: 128,
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));
    fireEvent.click(await screen.findByRole("button", { name: /测试连接/ }));

    await waitFor(() => {
      expect(testAiProvider).toHaveBeenCalledWith("provider-1");
    });
  });

  it("does not warn for private provider URLs when external models are disabled", async () => {
    vi.mocked(getAiConfig).mockResolvedValue({
      ...mockConfig,
      global: {
        ...mockConfig.global,
        allow_external_llm: false,
      },
      providers: [
        {
          ...mockConfig.providers[0],
          base_url: "http://192.168.4.94:8317/v1",
        },
      ],
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));

    expect(
      screen.queryByText("存在公网 Base URL，外部模型关闭时测试会被阻止。"),
    ).not.toBeInTheDocument();
  });

  it("warns for public provider URLs when external models are disabled", async () => {
    vi.mocked(getAiConfig).mockResolvedValue({
      ...mockConfig,
      global: {
        ...mockConfig.global,
        allow_external_llm: false,
      },
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));

    expect(screen.getByText("存在公网 Base URL，外部模型关闭时测试会被阻止。")).toBeInTheDocument();
  });

  it("creates an OpenAI-compatible provider from the modal", async () => {
    vi.mocked(getAiConfig).mockResolvedValue(mockConfig);
    vi.mocked(createAiProvider).mockResolvedValue({
      ...mockConfig.providers[0],
      id: "provider-2",
      name: "DeepSeek",
      base_url: "https://api.deepseek.com/v1",
      chat_model: "deepseek-chat",
      embedding_model: null,
      vision_model: null,
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));
    fireEvent.click(screen.getByRole("button", { name: /新增模型配置/ }));

    expect(await screen.findByRole("dialog", { name: "新增模型配置" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("供应商名称"), { target: { value: "DeepSeek" } });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://api.deepseek.com/v1" },
    });
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-deepseek" } });
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "deepseek-chat" } });
    fireEvent.click(screen.getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => {
      expect(createAiProvider).toHaveBeenCalled();
    });
    expect(vi.mocked(createAiProvider).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        name: "DeepSeek",
        provider_type: "openai_compatible",
        base_url: "https://api.deepseek.com/v1",
        api_key: "sk-deepseek",
        chat_model: "deepseek-chat",
        embedding_model: null,
        vision_model: null,
        enabled: true,
      }),
    );
  });

  it("updates a provider without sending an empty API key", async () => {
    vi.mocked(getAiConfig).mockResolvedValue(mockConfig);
    vi.mocked(updateAiProvider).mockResolvedValue({
      ...mockConfig.providers[0],
      priority: 5,
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));

    expect(await screen.findByRole("dialog", { name: "编辑模型配置" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("优先级"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(updateAiProvider).toHaveBeenCalledWith(
        "provider-1",
        expect.objectContaining({
          name: "OpenAI 兼容供应商",
          provider_type: "openai_compatible",
          priority: 5,
        }),
      );
    });
    expect(vi.mocked(updateAiProvider).mock.calls[0][1]).not.toHaveProperty("api_key");
  });

  it("updates feature switches through the feature mutation", async () => {
    vi.mocked(getAiConfig).mockResolvedValue(mockConfig);
    vi.mocked(updateAiFeature).mockResolvedValue({
      key: "sensitive_detection",
      name: "敏感检测",
      description: "检测文档中的敏感内容与合规风险",
      enabled: true,
    });

    renderWithProviders(<AiConfigPage />);

    const row = await screen.findByText("敏感检测");
    const featureRow = row.closest(".ai-config-feature-row");
    expect(featureRow).not.toBeNull();

    const switchControl = within(featureRow as HTMLElement).getByRole("switch");
    fireEvent.click(switchControl);

    await waitFor(() => {
      expect(updateAiFeature).toHaveBeenCalledWith("sensitive_detection", { enabled: true });
    });
  });

  it("updates global switches through the feature mutation", async () => {
    vi.mocked(getAiConfig).mockResolvedValue(mockConfig);
    vi.mocked(updateAiFeature).mockResolvedValue({
      key: "ai_analysis",
      name: "AI总开关",
      description: "控制上传后是否创建 AI 分析任务",
      enabled: false,
    });

    renderWithProviders(<AiConfigPage />);

    await screen.findAllByText("AI 总开关");
    const globalCard = screen
      .getAllByText("AI 总开关")
      .map((element) => element.closest(".ai-config-switch-card"))
      .find((card): card is HTMLElement => card instanceof HTMLElement);
    expect(globalCard).toBeDefined();

    const switchControl = within(globalCard as HTMLElement).getByRole("switch");
    fireEvent.click(switchControl);

    await waitFor(() => {
      expect(updateAiFeature).toHaveBeenCalledWith("ai_analysis", { enabled: false });
    });
  });
});
