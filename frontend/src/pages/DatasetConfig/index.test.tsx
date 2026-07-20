import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type CategoryListResponse,
  type ConfigGroupResponse,
  type DatasetMappingListResponse,
  type RagflowDatasetDiscoveryResult,
  createDatasetMapping,
  discoverRagflowDatasets,
  getConfigs,
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
    discoverRagflowDatasets: vi.fn(),
    getConfigs: vi.fn(),
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
      allow_ai_recommend: true,
      keywords: [],
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
    {
      id: "cat-2",
      name: "产品资料",
      code: "product",
      description: null,
      parent_id: null,
      allow_ai_recommend: true,
      keywords: [],
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
  ],
  total: 2,
};

const mappings: DatasetMappingListResponse = {
  items: [
    {
      id: "mapping-1",
      name: "制度文档 Dataset",
      category_id: "cat-1",
      ragflow_dataset_id: "ds-policy",
      ragflow_dataset_name: "policy-dataset",
      enabled: true,
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
  ],
  total: 1,
};

const ragflowConfig: ConfigGroupResponse = {
  group: "ragflow",
  items: [
    {
      key: "ragflow.allowed_dataset_ids",
      value: ["ds-policy"],
      value_type: "list",
      is_secret: false,
      masked_value: null,
      description: "允许同步的 RAGFlow Dataset",
      updated_at: "2026-06-01T00:00:00Z",
    },
  ],
};

const discoveredRagflowDatasets: RagflowDatasetDiscoveryResult = {
  ok: true,
  items: [
    { dataset_id: "ds-policy", name: "制度知识库" },
    { dataset_id: "ds-hidden", name: "未允许知识库" },
  ],
  error: null,
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

beforeEach(() => {
  vi.mocked(listCategories).mockResolvedValue(categories);
  vi.mocked(listDatasetMappings).mockResolvedValue(mappings);
  vi.mocked(getConfigs).mockResolvedValue(ragflowConfig);
  vi.mocked(discoverRagflowDatasets).mockResolvedValue(discoveredRagflowDatasets);
  vi.mocked(createDatasetMapping).mockResolvedValue(mappings.items[0]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("DatasetConfigPage", () => {
  it("shows explicit approval mapping policy and no removed category controls", async () => {
    vi.mocked(listCategories).mockResolvedValue(categories);
    vi.mocked(listDatasetMappings).mockResolvedValue(mappings);

    renderWithProviders(<DatasetConfigPage />);

    expect(await screen.findByText("制度文档")).toBeInTheDocument();
    const mappingWorkbench = screen.getByRole("region", { name: "Dataset 映射工作台" });
    expect(mappingWorkbench).toHaveTextContent("Dataset 映射工作台");
    expect(mappingWorkbench).toHaveTextContent(
      "当前筛选 2 类；同步目标在审核时从启用映射中明确选择",
    );
    expect(mappingWorkbench).toHaveTextContent("已启用1类");
    expect(mappingWorkbench).toHaveTextContent("待绑定1类");
    expect(mappingWorkbench).toHaveTextContent("已禁用0类");
    expect(mappingWorkbench).toHaveTextContent("绑定覆盖率50%");
    expect(screen.queryByText("默认可见范围")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /只看待绑定/ }));

    expect(mappingWorkbench).toHaveTextContent(
      "当前筛选 1 类；同步目标在审核时从启用映射中明确选择",
    );
    expect(mappingWorkbench).toHaveTextContent("已启用0类");
    expect(mappingWorkbench).toHaveTextContent("待绑定1类");

    fireEvent.click(screen.getByRole("button", { name: /新增分类/ }));
    await screen.findAllByText("新增分类");
    const categorySummary = screen.getByRole("region", { name: "分类配置摘要" });
    expect(categorySummary).toHaveTextContent("新分类策略");
    expect(categorySummary).toHaveTextContent("新增分类");
    expect(categorySummary).toHaveTextContent(
      "所有文档必须审核，禁止自动同步；Dataset 在审批时明确选择",
    );

    expect(screen.queryByText("默认可见范围")).not.toBeInTheDocument();
    expect(screen.queryByText("默认 Dataset ID")).not.toBeInTheDocument();
    expect(screen.queryByText("分类 Prompt")).not.toBeInTheDocument();
    expect(screen.queryByText("需要审核")).not.toBeInTheDocument();
    expect(screen.queryByText("员工可选")).not.toBeInTheDocument();
    expect(screen.queryByText("AI 分析")).not.toBeInTheDocument();
    expect(screen.queryByText("敏感检测")).not.toBeInTheDocument();
    expect(screen.queryByText("自动同步")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /批量操作/ })).not.toBeInTheDocument();
  });

  it("selects an allowed RAGFlow Dataset and fills its ID and name", async () => {
    renderWithProviders(<DatasetConfigPage />);

    expect(await screen.findByText("制度文档")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "绑定" }));

    expect(await screen.findByText("新增 Dataset 映射")).toBeInTheDocument();
    const mappingSummary = screen.getByRole("region", { name: "Dataset 映射摘要" });
    expect(mappingSummary).toHaveTextContent("新 Dataset 映射");
    expect(mappingSummary).toHaveTextContent("启用映射");
    expect(screen.queryByLabelText("RAGFlow Dataset ID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("RAGFlow Dataset 名称")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("映射名称"), {
      target: { value: "产品资料知识库" },
    });
    const datasetSelect = await screen.findByRole("combobox", { name: "RAGFlow Dataset" });
    fireEvent.mouseDown(datasetSelect);

    fireEvent.click(await screen.findByText("制度知识库（ds-policy）"));
    expect(screen.queryByText("未允许知识库（ds-hidden）")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => {
      expect(createDatasetMapping).toHaveBeenCalledWith({
        name: "产品资料知识库",
        category_id: "cat-2",
        ragflow_dataset_id: "ds-policy",
        ragflow_dataset_name: "制度知识库",
        enabled: true,
      });
    });
    expect(discoverRagflowDatasets).toHaveBeenCalledWith({});
  });

  it("shows a recoverable error when RAGFlow Dataset discovery fails", async () => {
    vi.mocked(discoverRagflowDatasets).mockResolvedValue({
      ok: false,
      items: [],
      error: "RAGFlow 服务暂时不可用",
    });

    renderWithProviders(<DatasetConfigPage />);

    expect(await screen.findByText("制度文档")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "绑定" }));

    expect(await screen.findByText("RAGFlow Dataset 加载失败")).toBeInTheDocument();
    expect(screen.getByText("RAGFlow 服务暂时不可用")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /刷新 Dataset/ }));
    await waitFor(() => {
      expect(discoverRagflowDatasets).toHaveBeenCalledTimes(2);
    });
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
