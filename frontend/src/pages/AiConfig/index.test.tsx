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
import AiConfigPage, { AI_PROVIDER_RUNTIME_LIMITS } from "./index";

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
    ai_analysis_environment_enabled: true,
    ai_analysis_db_enabled: true,
    allow_external_llm: true,
    allow_external_llm_environment_enabled: true,
    allow_external_llm_db_enabled: true,
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
      is_internal: false,
      enabled: true,
      priority: 1,
      timeout_seconds: 60,
      max_retry_count: 2,
      max_input_tokens: 128000,
      max_output_tokens: 4096,
      temperature: 0.2,
      top_p: null,
      input_price_microunits_per_million_tokens: 0,
      output_price_microunits_per_million_tokens: 0,
      pricing_currency: "USD",
      pricing_configured: true,
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
      prompt_text: "请总结文档：{text}",
      variables: ["text"],
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
      pattern: "\\d{17}[\\dXx]",
      keywords: [],
      risk_level: "high",
      action: "require_review",
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
    expect(screen.getByText("可用供应商")).toBeInTheDocument();
    expect(screen.getByText("敏感治理")).toBeInTheDocument();
    expect(screen.getAllByText("356 次累计命中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AI 总开关").length).toBeGreaterThan(0);
    expect(screen.getByText("文档摘要")).toBeInTheDocument();
    expect(screen.getByText("敏感检测")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "模型供应商" }));
    expect(screen.getByRole("heading", { name: "模型供应商" })).toBeInTheDocument();
    expect(screen.getByText("当前维护 1 个供应商，1 个已通过测试")).toBeInTheDocument();
    expect(screen.getByText("当前估算单价为 0")).toBeInTheDocument();
    expect(screen.queryByText(/免费/)).not.toBeInTheDocument();

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
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));
    fireEvent.click(screen.getByRole("button", { name: /新增模型配置/ }));

    expect(await screen.findByRole("dialog", { name: "新增模型配置" })).toBeInTheDocument();
    expect(AI_PROVIDER_RUNTIME_LIMITS).toEqual({
      priority: 2_147_483_647,
      timeout_seconds: 240,
      max_retry_count: 10,
      max_input_tokens: 1_000_000_000,
      max_output_tokens: 4_096,
      temperature: 2,
      top_p: 1,
    });

    fireEvent.change(screen.getByLabelText("供应商名称"), { target: { value: "DeepSeek" } });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://api.deepseek.com/v1" },
    });
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-deepseek" } });
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "deepseek-chat" } });
    fireEvent.change(screen.getByLabelText("输入价格（货币/百万 Token）"), {
      target: { value: "5.25" },
    });
    fireEvent.change(screen.getByLabelText("输出价格（货币/百万 Token）"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("计价币种"), { target: { value: "cny" } });
    const pricingConfirmation = screen.getByLabelText("价格口径已确认");
    expect(pricingConfirmation).not.toBeChecked();
    fireEvent.click(pricingConfirmation);
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
        enabled: true,
        input_price_microunits_per_million_tokens: 5_250_000,
        output_price_microunits_per_million_tokens: 10_000_000,
        pricing_currency: "CNY",
        pricing_configured: true,
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
    const pricingConfirmation = screen.getByLabelText("价格口径已确认");
    expect(pricingConfirmation).toBeChecked();
    fireEvent.click(pricingConfirmation);
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(updateAiProvider).toHaveBeenCalledWith(
        "provider-1",
        expect.objectContaining({
          name: "OpenAI 兼容供应商",
          provider_type: "openai_compatible",
          priority: 5,
          pricing_configured: false,
        }),
      );
    });
    expect(vi.mocked(updateAiProvider).mock.calls[0][1]).not.toHaveProperty("api_key");
  });

  it("preserves pricing status when an old response omits the compatibility field", async () => {
    const legacyProvider = { ...mockConfig.providers[0] };
    delete legacyProvider.pricing_configured;
    vi.mocked(getAiConfig).mockResolvedValue({
      ...mockConfig,
      providers: [legacyProvider],
    });
    vi.mocked(updateAiProvider).mockResolvedValue({
      ...legacyProvider,
      priority: 5,
    });

    renderWithProviders(<AiConfigPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "模型供应商" }));
    expect(await screen.findByText("价格口径状态未知（服务版本兼容）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));

    expect(await screen.findByRole("dialog", { name: "编辑模型配置" })).toBeInTheDocument();
    expect(screen.getByText("当前服务版本未返回价格口径状态")).toBeInTheDocument();
    const pricingConfirmation = screen.getByLabelText("价格口径已确认");
    expect(pricingConfirmation).toBeDisabled();
    fireEvent.change(screen.getByLabelText("优先级"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(updateAiProvider).toHaveBeenCalledWith("provider-1", expect.any(Object));
    });
    const payload = vi.mocked(updateAiProvider).mock.calls[0][1];
    expect(payload).toEqual(expect.objectContaining({ priority: 5 }));
    expect(payload).not.toHaveProperty("pricing_configured");
    expect(payload).not.toHaveProperty("api_key");
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

  it("shows the effective AI gate and disables DB control when environment blocks it", async () => {
    vi.mocked(getAiConfig).mockResolvedValue({
      ...mockConfig,
      global: {
        ...mockConfig.global,
        ai_analysis_enabled: false,
        ai_analysis_environment_enabled: false,
        ai_analysis_db_enabled: true,
      },
    });

    renderWithProviders(<AiConfigPage />);

    const aiTitles = await screen.findAllByText("AI 总开关");
    const kpiCard = aiTitles
      .map((element) => element.closest(".kpi-card"))
      .find((card): card is HTMLElement => card instanceof HTMLElement);
    expect(kpiCard).toBeDefined();
    expect(within(kpiCard as HTMLElement).getByText("已关闭")).toBeInTheDocument();

    const globalCard = aiTitles
      .map((element) => element.closest(".ai-config-switch-card"))
      .find((card): card is HTMLElement => card instanceof HTMLElement);
    expect(globalCard).toBeDefined();
    const switchControl = within(globalCard as HTMLElement).getByRole("switch");
    expect(switchControl).toBeChecked();
    expect(switchControl).toBeDisabled();
    expect(
      within(globalCard as HTMLElement).getByText(/环境硬门禁已关闭，需先由运维启用/),
    ).toBeInTheDocument();
    expect(within(globalCard as HTMLElement).getByText("已关闭")).toBeInTheDocument();
  });

  it("shows the effective external gate and disables DB control when environment blocks it", async () => {
    vi.mocked(getAiConfig).mockResolvedValue({
      ...mockConfig,
      global: {
        ...mockConfig.global,
        allow_external_llm: false,
        allow_external_llm_environment_enabled: false,
        allow_external_llm_db_enabled: true,
      },
    });

    renderWithProviders(<AiConfigPage />);

    const title = await screen.findByText("是否允许外部模型");
    const globalCard = title.closest(".ai-config-switch-card");
    expect(globalCard).not.toBeNull();
    expect(within(globalCard as HTMLElement).getByRole("switch")).toBeDisabled();
    expect(
      within(globalCard as HTMLElement).getByText(/环境硬门禁已关闭，需先由运维启用/),
    ).toBeInTheDocument();
  });
});
