import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type AiConfigResponse,
  getAiConfig,
  testAiProvider,
  updateAiFeature,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import AiConfigPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getAiConfig: vi.fn(),
    testAiProvider: vi.fn(),
    updateAiFeature: vi.fn(),
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
      provider_type: "openai-compatible",
      base_url: "https://api.openai.com/v1",
      chat_model: "gpt-4o-mini",
      embedding_model: "text-embedding-3-small",
      enabled: true,
      priority: 1,
      api_key_masked: "sk-****abcd",
      last_test_status: "success",
      last_test_latency_ms: 268,
      last_tested_at: "2026-06-06T10:00:00Z",
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
    expect(screen.getByText("AI 总开关")).toBeInTheDocument();
    expect(screen.getByText("文档摘要")).toBeInTheDocument();
    expect(screen.getByText("敏感检测")).toBeInTheDocument();
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

    const title = await screen.findByText("AI 总开关");
    const globalCard = title.closest(".ai-config-switch-card");
    expect(globalCard).not.toBeNull();

    const switchControl = within(globalCard as HTMLElement).getByRole("switch");
    fireEvent.click(switchControl);

    await waitFor(() => {
      expect(updateAiFeature).toHaveBeenCalledWith("ai_analysis", { enabled: false });
    });
  });
});
