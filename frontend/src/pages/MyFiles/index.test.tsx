import React from "react";
import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import type * as AntdModule from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type * as RouterModule from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type ConfigGroupResponse,
  type FileListResponse,
  type TagListResponse,
  deleteFile,
  getConfigs,
  listDocuments,
  listTags,
  submitFileForReview,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import MyFilesPage from "./index";

// ── API mocks ────────────────────────────────────────────────────────────────
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getConfigs: vi.fn(),
    listDocuments: vi.fn(),
    listTags: vi.fn(),
    deleteFile: vi.fn(),
    submitFileForReview: vi.fn(),
  };
});

// ── react-router-dom mock ────────────────────────────────────────────────────
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof RouterModule>("react-router-dom");

  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

// ── Ant Design mocks ─────────────────────────────────────────────────────────
// Popconfirm: immediately calls onConfirm when wrapper is clicked (portal doesn't render in jsdom)
// Select: renders a native <select> for reliable fireEvent.change testing
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof AntdModule>("antd");

  function MockPopconfirm({
    children,
    onConfirm,
  }: {
    children: ReactNode;
    onConfirm?: () => void;
    title?: string;
    description?: string;
    okText?: string;
    cancelText?: string;
  }) {
    return (
      <span
        data-testid="popconfirm-wrapper"
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
    className?: string;
  }) {
    const { value, onChange, options, placeholder, className } = props;
    return (
      <select
        role="combobox"
        className={className}
        aria-label={placeholder}
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

// ── Fixtures ─────────────────────────────────────────────────────────────────
const mockFile1 = {
  id: "file-1",
  original_name: "产品规划.pdf",
  extension: "pdf",
  mime_type: "application/pdf",
  size: 204800,
  uploader_id: "user-1",
  department: "产品部",
  category_id: null,
  dataset_mapping_id: null,
  visibility: "company" as const,
  description: "年度产品规划",
  tags: ["规划"],
  status: "approved",
  review_status: "approved",
  ragflow_dataset_id: null,
  ragflow_document_id: null,
  ragflow_parse_status: null,
  ai_analysis_enabled_at_upload: true,
  uploaded_at: "2026-06-01T10:00:00Z",
  last_sync_at: null,
  created_at: "2026-06-01T10:00:00Z",
  updated_at: "2026-06-01T10:00:00Z",
  duplicate: false,
  duplicate_file_id: null,
};

const mockFile2 = {
  id: "file-2",
  original_name: "技术架构.docx",
  extension: "docx",
  mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  size: 512000,
  uploader_id: "user-1",
  department: "技术部",
  category_id: null,
  dataset_mapping_id: null,
  visibility: "department" as const,
  description: null,
  tags: [],
  status: "pending_review",
  review_status: "pending",
  ragflow_dataset_id: null,
  ragflow_document_id: null,
  ragflow_parse_status: null,
  ai_analysis_enabled_at_upload: false,
  uploaded_at: "2026-06-05T14:30:00Z",
  last_sync_at: null,
  created_at: "2026-06-05T14:30:00Z",
  updated_at: "2026-06-05T14:30:00Z",
  duplicate: false,
  duplicate_file_id: null,
};

const mockDraftFile = {
  ...mockFile2,
  id: "file-draft",
  original_name: "草稿文件.pdf",
  status: "uploaded",
  review_status: "pending",
};

const mockRejectedFile = {
  ...mockFile2,
  id: "file-rejected",
  original_name: "被拒文件.pdf",
  status: "rejected",
  review_status: "rejected",
};

const mockFilesResponse: FileListResponse = {
  items: [mockFile1, mockFile2],
  total: 2,
};

const mockTagsResponse: TagListResponse = {
  items: [
    {
      id: "tag-1",
      name: "规划",
      description: null,
      usage_count: 3,
      is_system_generated: true,
      enabled: true,
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
    {
      id: "tag-2",
      name: "架构",
      description: null,
      usage_count: 1,
      is_system_generated: false,
      enabled: true,
      created_at: "2026-06-02T00:00:00Z",
      updated_at: "2026-06-02T00:00:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 100,
};

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

// ── Setup ────────────────────────────────────────────────────────────────────
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
    <MemoryRouter>
      <ConfigProvider>
        <AntdApp>
          <QueryClientProvider client={queryClient}>
            <div style={themeCssVariables as CSSProperties}>{node}</div>
          </QueryClientProvider>
        </AntdApp>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

beforeEach(() => {
  vi.mocked(getConfigs).mockResolvedValue(uploadConfigResponse);
  vi.mocked(submitFileForReview).mockResolvedValue(mockFile2);
});

// ── Tests ────────────────────────────────────────────────────────────────────
describe("MyFilesPage", () => {
  it("renders file list with file names", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    expect(await screen.findByText("产品规划.pdf")).toBeInTheDocument();
    expect(await screen.findByText("技术架构.docx")).toBeInTheDocument();

    const statusRegion = screen.getByRole("region", { name: "我的知识库状态" });
    expect(statusRegion).toHaveTextContent("个人知识库");
    expect(statusRegion).toHaveTextContent("7 类格式");
    expect(statusRegion).toHaveTextContent("1 个待审核");
  });

  it("calls deleteFile when delete button is clicked and Popconfirm confirms", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockResolvedValue(undefined);

    renderWithProviders(<MyFilesPage />);

    // Wait for files to load
    await screen.findByText("产品规划.pdf");

    // Popconfirm is mocked: clicking the delete button immediately triggers onConfirm (deleteFile)
    const deleteButtons = screen.getAllByRole("button", { name: /删除/ });
    expect(deleteButtons.length).toBeGreaterThanOrEqual(1);

    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(deleteFile).toHaveBeenCalledWith("file-1");
    });
  });

  it("re-fetches file list after successful delete", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockResolvedValue(undefined);

    renderWithProviders(<MyFilesPage />);

    await screen.findByText("产品规划.pdf");

    const deleteButtons = screen.getAllByRole("button", { name: /删除/ });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      // listDocuments should be called at least twice: initial load + after delete invalidation
      expect(listDocuments).toHaveBeenCalledTimes(2);
    });
  });

  it("submits an uploaded draft file for review and refreshes the list", async () => {
    vi.mocked(listDocuments).mockResolvedValue({
      items: [mockDraftFile],
      total: 1,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(submitFileForReview).mockResolvedValue({
      ...mockDraftFile,
      status: "pending_review",
    });

    renderWithProviders(<MyFilesPage />);

    await screen.findByText("草稿文件.pdf");
    fireEvent.click(screen.getByRole("button", { name: /提交审核 草稿文件/ }));

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-draft");
    });
    await waitFor(() => {
      expect(listDocuments).toHaveBeenCalledTimes(2);
    });
  });

  it("submits a rejected file for review", async () => {
    vi.mocked(listDocuments).mockResolvedValue({
      items: [mockRejectedFile],
      total: 1,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(submitFileForReview).mockResolvedValue({
      ...mockRejectedFile,
      status: "pending_review",
      review_status: "pending",
    });

    renderWithProviders(<MyFilesPage />);

    await screen.findByText("被拒文件.pdf");
    fireEvent.click(screen.getByRole("button", { name: /提交审核 被拒文件/ }));

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-rejected");
    });
  });

  it("handles 403-like error without crashing and does not re-fetch", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockRejectedValue(new Error("管理员未开放删除权限"));

    renderWithProviders(<MyFilesPage />);

    await screen.findByText("产品规划.pdf");

    const deleteButtons = screen.getAllByRole("button", { name: /删除/ });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(deleteFile).toHaveBeenCalledWith("file-1");
    });
    // After error, list should NOT be re-fetched (only 1 initial call)
    expect(listDocuments).toHaveBeenCalledTimes(1);
  });

  it("filters by extension and passes the param to listDocuments", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findByText("产品规划.pdf");

    // Find the extension select (placeholder "文件类型（扩展名）")
    const extensionSelect = screen.getByRole("combobox", { name: "文件类型（扩展名）" });
    fireEvent.change(extensionSelect, { target: { value: "pdf" } });

    await waitFor(() => {
      expect(listDocuments).toHaveBeenCalledWith(expect.objectContaining({ extension: "pdf" }));
    });
  });

  it("filters by tag and passes tag_id to listDocuments", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findByText("产品规划.pdf");

    // Find the tag select (placeholder "标签筛选")
    const tagSelect = screen.getByRole("combobox", { name: "标签筛选" });
    fireEvent.change(tagSelect, { target: { value: "tag-1" } });

    await waitFor(() => {
      expect(listDocuments).toHaveBeenCalledWith(expect.objectContaining({ tag_id: "tag-1" }));
    });
  });
});
