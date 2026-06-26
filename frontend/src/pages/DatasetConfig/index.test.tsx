import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type CategoryListResponse,
  type DatasetMappingListResponse,
  listCategories,
  listDatasetMappings,
  testRagflowConnection,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import DatasetConfigPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listCategories: vi.fn(),
    listDatasetMappings: vi.fn(),
    createCategory: vi.fn(),
    updateCategory: vi.fn(),
    createDatasetMapping: vi.fn(),
    updateDatasetMapping: vi.fn(),
    disableDatasetMapping: vi.fn(),
    testRagflowConnection: vi.fn(),
  };
});

const categories: CategoryListResponse = {
  items: [
    {
      id: "cat-1",
      name: "制度文档",
      code: "policy",
      description: null,
      parent_id: null,
      require_review: true,
      default_dataset_id: null,
      allow_employee_select: true,
      allow_ai_recommend: true,
      default_visibility: "company",
      keywords: [],
      classification_prompt: null,
      ai_analysis_enabled: true,
      sensitive_detection_enabled: true,
      auto_sync_enabled: false,
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
  ],
  total: 1,
};

const mappings: DatasetMappingListResponse = {
  items: [],
  total: 0,
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

describe("DatasetConfigPage", () => {
  it("does not render default visibility in the table or category modal", async () => {
    vi.mocked(listCategories).mockResolvedValue(categories);
    vi.mocked(listDatasetMappings).mockResolvedValue(mappings);

    renderWithProviders(<DatasetConfigPage />);

    expect(await screen.findByText("制度文档")).toBeInTheDocument();
    expect(screen.queryByText("默认可见范围")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /新增分类/ }));
    await screen.findAllByText("新增分类");

    expect(screen.queryByText("默认可见范围")).not.toBeInTheDocument();
  });

  it("tests the RAGFlow connection from the Dataset panel", async () => {
    vi.mocked(listCategories).mockResolvedValue(categories);
    vi.mocked(listDatasetMappings).mockResolvedValue(mappings);
    vi.mocked(testRagflowConnection).mockResolvedValue({
      ok: true,
      latency_ms: 42,
      error: null,
    });

    renderWithProviders(<DatasetConfigPage />);

    expect(await screen.findByText("RAGFlow 连接状态")).toBeInTheDocument();
    expect(screen.getByText("待测试")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /测试连接/ }));

    expect(await screen.findByText("连接正常")).toBeInTheDocument();
    expect(screen.getByText("服务响应 42 ms")).toBeInTheDocument();
    expect(testRagflowConnection).toHaveBeenCalledTimes(1);
  });
});
