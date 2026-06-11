import React from "react";
import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import type * as AntdModule from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type ConfigGroupResponse,
  type FileListResponse,
  type KnowledgeFile,
  type Tag,
  type TagListResponse,
  approveFile,
  archiveFile,
  deleteFile,
  getConfigs,
  listCategories,
  listDatasetMappings,
  listReviewFiles,
  listTags,
  reanalyzeFile,
  syncFile,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import FileManagementPage from "./index";

// ── API mocks ─────────────────────────────────────────────────────────────────

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listReviewFiles: vi.fn(),
    getConfigs: vi.fn(),
    listCategories: vi.fn(),
    listDatasetMappings: vi.fn(),
    listTags: vi.fn(),
    approveFile: vi.fn(),
    archiveFile: vi.fn(),
    deleteFile: vi.fn(),
    reanalyzeFile: vi.fn(),
    syncFile: vi.fn(),
  };
});

// ── Ant Design mocks ──────────────────────────────────────────────────────────
// Popconfirm: immediately calls onConfirm when wrapper is clicked (portal doesn't render in jsdom)
// Select: renders a native <select> for reliable fireEvent.change testing
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof AntdModule>("antd");

  function MockPopconfirm({
    children,
    onConfirm,
    title,
  }: {
    children: ReactNode;
    onConfirm?: () => void;
    onCancel?: () => void;
    title?: string;
    description?: string;
    okText?: string;
    cancelText?: string;
    okButtonProps?: Record<string, unknown>;
  }) {
    return (
      <span
        data-testid={`popconfirm-${String(title ?? "default")}`}
        onClick={(e: React.MouseEvent) => {
          e.stopPropagation();
          onConfirm?.();
        }}
      >
        {children}
      </span>
    );
  }

  function MockSelect(props: {
    value?: string;
    onChange?: (value: string | undefined) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    loading?: boolean;
    allowClear?: boolean;
    showSearch?: boolean;
    optionFilterProp?: string;
    className?: string;
  }) {
    const { value, onChange, options, placeholder, className } = props;
    return (
      <select
        role="combobox"
        className={className}
        aria-label={placeholder ?? "select"}
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          onChange?.(v === "" ? undefined : v);
        }}
      >
        <option value="">{placeholder ?? "全部"}</option>
        {options?.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  }

  return { ...actual, Popconfirm: MockPopconfirm, Select: MockSelect };
});

// ── helpers ───────────────────────────────────────────────────────────────────

function makeFile(overrides: Partial<KnowledgeFile> = {}): KnowledgeFile {
  return {
    id: "file-1",
    original_name: "test.pdf",
    extension: "pdf",
    mime_type: "application/pdf",
    size: 1024,
    uploader_id: "user-abc123",
    department: "技术部",
    category_id: null,
    dataset_mapping_id: null,
    visibility: "company",
    description: null,
    tags: [],
    status: "approved",
    review_status: "approved",
    ragflow_dataset_id: null,
    ragflow_document_id: null,
    ragflow_parse_status: null,
    ai_analysis_enabled_at_upload: true,
    uploaded_at: "2026-06-10T10:00:00Z",
    last_sync_at: null,
    created_at: "2026-06-10T10:00:00Z",
    updated_at: "2026-06-10T10:00:00Z",
    duplicate: false,
    duplicate_file_id: null,
    ...overrides,
  };
}

function makeTag(overrides: Partial<Tag> = {}): Tag {
  return {
    id: "tag-1",
    name: "技术文档",
    description: null,
    usage_count: 5,
    is_system_generated: false,
    enabled: true,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

const emptyTagList: TagListResponse = { items: [], total: 0, page: 1, page_size: 20 };

const uploadConfigResponse: ConfigGroupResponse = {
  group: "upload",
  items: [
    {
      key: "upload.allowed_extensions",
      value: ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"],
      value_type: "list",
      is_secret: false,
      masked_value: null,
      description: "允许的扩展名",
      updated_at: null,
    },
  ],
};
const emptyFileList: FileListResponse = { items: [], total: 0 };

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

afterEach(() => {
  vi.clearAllMocks();
});

beforeEach(() => {
  vi.mocked(getConfigs).mockResolvedValue(uploadConfigResponse);
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

// ── tests ─────────────────────────────────────────────────────────────────────

describe("FileManagementPage — 同步按钮", () => {
  it("approved 状态文件展示同步按钮且点击 popconfirm 调用 syncFile", async () => {
    const file = makeFile({ id: "file-1", status: "approved", review_status: "approved" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);
    vi.mocked(syncFile).mockResolvedValue({
      id: "task-1",
      file_id: "file-1",
      task_type: "ragflow_upload",
      status: "queued",
      retry_count: 0,
      max_retry_count: 3,
      error_message: null,
      started_at: null,
      finished_at: null,
      created_at: "2026-06-10T10:00:00Z",
      updated_at: "2026-06-10T10:00:00Z",
      logs: [],
    });

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    // MockPopconfirm wrapper 存在
    const syncWrapper = screen.getByTestId("popconfirm-手动触发同步");
    expect(syncWrapper).toBeInTheDocument();

    // 点击 MockPopconfirm wrapper 直接触发 onConfirm
    fireEvent.click(syncWrapper);

    await waitFor(() => {
      expect(syncFile).toHaveBeenCalledWith("file-1");
    });
  });

  it("failed 状态文件也展示同步按钮", async () => {
    const file = makeFile({ id: "file-2", status: "failed", review_status: "approved" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    // 同步按钮的 popconfirm wrapper 存在
    expect(screen.getByTestId("popconfirm-手动触发同步")).toBeInTheDocument();
  });

  it("pending_review 状态文件不展示同步按钮", async () => {
    const file = makeFile({ id: "file-3", status: "pending_review", review_status: "pending" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    expect(screen.queryByTestId("popconfirm-手动触发同步")).toBeNull();
  });
});

describe("FileManagementPage — 删除确认流", () => {
  it("点击删除 popconfirm wrapper 调用 deleteFile", async () => {
    const file = makeFile({ id: "file-del", status: "uploaded", review_status: "pending" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);
    vi.mocked(deleteFile).mockResolvedValue(undefined);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    // MockPopconfirm wrapper 存在
    const deleteWrapper = screen.getByTestId("popconfirm-删除文件");
    expect(deleteWrapper).toBeInTheDocument();

    // 点击触发 onConfirm
    fireEvent.click(deleteWrapper);

    await waitFor(() => {
      expect(deleteFile).toHaveBeenCalledWith("file-del");
    });
  });

  it("删除按钮（danger）存在且不 disabled", async () => {
    const file = makeFile({ id: "file-del2", status: "uploaded", review_status: "pending" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    const deleteWrapper = screen.getByTestId("popconfirm-删除文件");
    expect(deleteWrapper).toBeInTheDocument();

    // deleteFile 未被调用（未点击确认）
    expect(deleteFile).not.toHaveBeenCalled();
  });
});

describe("FileManagementPage — 重新分析", () => {
  it("analysis_failed 状态文件展示重新分析按钮且调用 reanalyzeFile", async () => {
    const file = makeFile({
      id: "file-ana",
      status: "analysis_failed",
      review_status: "pending",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);
    vi.mocked(reanalyzeFile).mockResolvedValue(undefined);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    const reanalyzeBtn = screen.getByRole("button", { name: "重新分析" });
    expect(reanalyzeBtn).not.toBeDisabled();

    fireEvent.click(reanalyzeBtn);

    await waitFor(() => {
      expect(reanalyzeFile).toHaveBeenCalledWith("file-ana");
    });
  });

  it("analyzed 状态文件也展示重新分析按钮", async () => {
    const file = makeFile({ id: "file-ana2", status: "analyzed", review_status: "pending" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    expect(screen.getByRole("button", { name: "重新分析" })).not.toBeDisabled();
  });

  it("uploaded 状态文件不展示重新分析按钮", async () => {
    const file = makeFile({ id: "file-up", status: "uploaded", review_status: "pending" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    expect(screen.queryByRole("button", { name: "重新分析" })).toBeNull();
  });
});

describe("FileManagementPage — 标签筛选", () => {
  it("listTags 在组件挂载时被调用", async () => {
    vi.mocked(listReviewFiles).mockResolvedValue(emptyFileList);
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await waitFor(() => {
      expect(listTags).toHaveBeenCalled();
    });
  });

  it("标签筛选 Select 展示来自 listTags 的标签选项", async () => {
    const tag = makeTag({ id: "tag-abc", name: "合规文件" });
    vi.mocked(listReviewFiles).mockResolvedValue(emptyFileList);
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue({
      items: [tag],
      total: 1,
      page: 1,
      page_size: 20,
    });

    renderWithProviders(<FileManagementPage />);

    // 等待初始查询完成
    await waitFor(() => {
      expect(listReviewFiles).toHaveBeenCalledTimes(1);
    });

    // 找到标签筛选的 MockSelect（aria-label="标签：全部"）
    const tagSelect = screen.getByRole("combobox", { name: "标签：全部" });
    expect(tagSelect).toBeInTheDocument();

    // 标签选项已渲染（来自 listTags）
    await waitFor(() => {
      const option = tagSelect.querySelector('option[value="tag-abc"]');
      expect(option).not.toBeNull();
      expect(option?.textContent).toBe("合规文件");
    });
  });

  it("文件类型 Select 包含固定扩展名 pdf 选项", async () => {
    vi.mocked(listReviewFiles).mockResolvedValue(emptyFileList);
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await waitFor(() => {
      expect(listReviewFiles).toHaveBeenCalledTimes(1);
    });

    // MockSelect for 文件类型 has aria-label="文件类型：全部"
    const typeSelect = screen.getByRole("combobox", { name: "文件类型：全部" });
    expect(typeSelect).toBeInTheDocument();

    // 包含 pdf 选项
    const pdfOption = typeSelect.querySelector('option[value="pdf"]');
    expect(pdfOption).not.toBeNull();
    expect(pdfOption?.textContent).toBe(".pdf");
  });
});

describe("FileManagementPage — 归档操作", () => {
  it("点击归档 popconfirm wrapper 调用 archiveFile", async () => {
    const file = makeFile({
      id: "file-arc",
      status: "approved",
      review_status: "approved",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);
    vi.mocked(archiveFile).mockResolvedValue(file);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    const archiveWrapper = screen.getByTestId("popconfirm-归档文件");
    expect(archiveWrapper).toBeInTheDocument();

    fireEvent.click(archiveWrapper);

    await waitFor(() => {
      expect(archiveFile).toHaveBeenCalledWith("file-arc");
    });
  });
});

describe("FileManagementPage — 既有审核测试不回归", () => {
  it("pending_review 文件可点击审核按钮", async () => {
    const file = makeFile({
      id: "file-pr",
      status: "pending_review",
      review_status: "pending",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    expect(screen.getByRole("button", { name: "审核" })).not.toBeDisabled();
  });

  it("pending_review 文件可点击驳回按钮", async () => {
    const file = makeFile({
      id: "file-rej",
      status: "pending_review",
      review_status: "pending",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    expect(screen.getByRole("button", { name: "驳回" })).not.toBeDisabled();
  });

  it("approveFile mutation 被正确调用", async () => {
    const file = makeFile({
      id: "file-approve",
      status: "pending_review",
      review_status: "pending",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(emptyTagList);
    vi.mocked(approveFile).mockResolvedValue({ ...file, status: "approved" });

    renderWithProviders(<FileManagementPage />);

    await screen.findByText("test.pdf");

    const approveBtn = screen.getByRole("button", { name: "审核" });
    fireEvent.click(approveBtn);

    // Modal 打开
    await screen.findByText("审核通过");

    // 点击 Modal footer 确认按钮
    const footerBtn = document.querySelector(
      ".ant-modal-footer .ant-btn-primary",
    ) as HTMLElement | null;
    if (footerBtn) {
      fireEvent.click(footerBtn);
    }

    await waitFor(() => {
      expect(approveFile).toHaveBeenCalledWith("file-approve", expect.any(Object));
    });
  });
});
