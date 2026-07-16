import React from "react";
import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import type * as AntdModule from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type * as RouterModule from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type FileListResponse,
  type TagListResponse,
  type UploadPolicy,
  deleteFile,
  getUploadPolicy,
  listDocuments,
  listTags,
  submitFileForReview,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { type EmployeeDashboard, getEmployeeDashboard } from "../../api/dashboard";
import type * as DashboardApiModule from "../../api/dashboard";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import MyFilesPage from "./index";

// ── API mocks ────────────────────────────────────────────────────────────────
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getUploadPolicy: vi.fn(),
    listDocuments: vi.fn(),
    listTags: vi.fn(),
    deleteFile: vi.fn(),
    submitFileForReview: vi.fn(),
  };
});

vi.mock("../../api/dashboard", async () => {
  const actual = await vi.importActual<typeof DashboardApiModule>("../../api/dashboard");
  return { ...actual, getEmployeeDashboard: vi.fn() };
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

const mockAnalysisFailedFile = {
  ...mockFile2,
  id: "file-analysis-failed",
  original_name: "分析失败文档.pdf",
  status: "analysis_failed",
  review_status: "pending",
};

const mockSensitiveFile = {
  ...mockFile2,
  id: "file-sensitive",
  original_name: "涉敏客户资料.pdf",
  status: "sensitive_review_required",
  review_status: "pending",
  sensitive_risk_level: "high" as const,
};

const mockSensitiveRejectedFile = {
  ...mockRejectedFile,
  id: "file-sensitive-rejected",
  original_name: "涉敏驳回材料.pdf",
  sensitive_risk_level: "medium" as const,
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

const uploadPolicyResponse: UploadPolicy = {
  allowed_extensions: ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"],
  allow_multi_file: true,
  upload_enabled: true,
  max_file_size_mb: 50,
  allow_user_delete: true,
};

function employeeDashboard(
  recentDocuments: EmployeeDashboard["employee"] extends infer Workbench
    ? Workbench extends { recent_documents: infer Documents }
      ? Documents
      : never
    : never = [],
): EmployeeDashboard {
  return {
    role: "employee",
    generated_at: "2026-06-10T00:00:00Z",
    access: { scope: "self", ready: true, department_ids: ["dept-tech"] },
    employee: {
      status_counts: {
        total: recentDocuments.length,
        draft: 0,
        ai_processing: 0,
        analysis_failed: 0,
        sensitive_review: 0,
        pending_review: 0,
        approved: 0,
        rejected: 0,
        sync_processing: 0,
        parsed: 0,
        sync_failed: 0,
        archived: 0,
      },
      action_counts: {
        total: recentDocuments.length,
        submit_draft: 0,
        revise_rejected: 0,
        confirm_sensitive: 0,
        analysis_failed: 0,
      },
      recent_documents: recentDocuments,
      recent_notifications: [],
      unread_notification_count: 0,
    },
    admin: null,
    system: null,
  };
}

function dashboardDocument(file: typeof mockFile2 & { title?: string | null }) {
  return {
    id: file.id,
    original_name: file.original_name,
    title: file.title,
    extension: file.extension,
    status: file.status,
    review_status: file.review_status,
    updated_at: file.updated_at,
    next_action:
      file.status === "rejected"
        ? ("revise_rejected" as const)
        : file.status === "sensitive_review_required"
          ? ("confirm_sensitive" as const)
          : ("submit_review" as const),
  };
}

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
  Object.defineProperty(URL, "createObjectURL", {
    writable: true,
    value: vi.fn(),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    writable: true,
    value: vi.fn(),
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
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "token",
    user: {
      id: "employee-1",
      name: "张三",
      email: "zhangsan@company.com",
      role: "employee",
      department_assigned: true,
      department_id: "dept-tech",
      department_name: "技术部",
      department_code: "tech",
    },
  });
  vi.mocked(getUploadPolicy).mockResolvedValue(uploadPolicyResponse);
  vi.mocked(submitFileForReview).mockResolvedValue(mockFile2);
  vi.mocked(getEmployeeDashboard).mockResolvedValue(employeeDashboard());
});

// ── Tests ────────────────────────────────────────────────────────────────────
describe("MyFilesPage", () => {
  it("blocks upload and submit with a department-assignment recovery action", async () => {
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "employee-1",
        name: "张三",
        email: "zhangsan@company.com",
        role: "employee",
        department_assigned: false,
      },
    });
    vi.mocked(listDocuments).mockResolvedValue({ items: [mockDraftFile], total: 1 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    expect(await screen.findByText("尚未分配有效部门")).toBeInTheDocument();
    await screen.findAllByText("草稿文件.pdf");
    const recovery = screen.getByRole("link", { name: "联系管理员分配部门" });
    expect(recovery).toHaveAttribute("href", expect.stringContaining("mailto:"));
    expect(screen.getByRole("button", { name: /上传文档/ })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: /提交审核 草稿文件/ })[0]).toBeDisabled();
  });

  it("uses the document title as primary text while retaining the original filename", async () => {
    const titledFile = { ...mockFile1, title: "年度产品规划" };
    vi.mocked(listDocuments).mockResolvedValue({ items: [titledFile], total: 1 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    expect((await screen.findAllByText("年度产品规划")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("原始文件：产品规划.pdf").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "下载原件 年度产品规划" }).length).toBeGreaterThan(
      0,
    );
  });

  it("loads one dashboard aggregation while keeping one paginated file request", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("产品规划.pdf");
    await waitFor(() => expect(getEmployeeDashboard).toHaveBeenCalledTimes(1));
    expect(listDocuments).toHaveBeenCalledTimes(1);
    expect(listDocuments).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 20 }));
  });

  it("shows a retryable error instead of a false empty state when actionable summaries fail", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(getEmployeeDashboard).mockRejectedValue(new Error("dashboard unavailable"));
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    expect(await screen.findByText("待办汇总加载失败")).toBeInTheDocument();
    expect(screen.queryByText("当前没有需要继续处理的文档")).not.toBeInTheDocument();
    expect(screen.getByText(/当前无法确认全部待处理文档/)).toBeInTheDocument();
    const callsBeforeRetry = vi.mocked(getEmployeeDashboard).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重试待办汇总" }));

    await waitFor(() => {
      expect(vi.mocked(getEmployeeDashboard).mock.calls.length).toBeGreaterThan(callsBeforeRetry);
    });
  });

  it("puts analysis failures in continue processing and allows submission", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [mockAnalysisFailedFile], total: 1 });
    vi.mocked(getEmployeeDashboard).mockResolvedValue(
      employeeDashboard([dashboardDocument(mockAnalysisFailedFile)]),
    );
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(submitFileForReview).mockResolvedValue({
      ...mockAnalysisFailedFile,
      status: "pending_review",
    });

    renderWithProviders(<MyFilesPage />);

    expect((await screen.findAllByText("分析失败文档.pdf")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("尝试提交；若策略限制请联系管理员重试分析").length).toBeGreaterThan(
      0,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "提交审核 分析失败文档.pdf" })[0]);

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-analysis-failed", undefined);
    });
  });

  it("keeps an analysis failure in recent actions even when the backend action is view_detail", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(getEmployeeDashboard).mockResolvedValue(
      employeeDashboard([
        { ...dashboardDocument(mockAnalysisFailedFile), next_action: "view_detail" },
      ]),
    );

    renderWithProviders(<MyFilesPage />);

    expect((await screen.findAllByText("分析失败文档.pdf")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "查看并处理" })).toBeInTheDocument();
    expect(screen.queryByText(/最近五条动态未包含/)).not.toBeInTheDocument();
  });

  it("uses the aggregated rail as a filter without refetching the dashboard", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);
    const railButton = await screen.findByRole("button", { name: /分析失败/ });
    await waitFor(() => expect(getEmployeeDashboard).toHaveBeenCalledTimes(1));
    vi.mocked(listDocuments).mockClear();
    fireEvent.click(railButton);

    await waitFor(() =>
      expect(listDocuments).toHaveBeenCalledWith(
        expect.objectContaining({ status: "analysis_failed" }),
      ),
    );
    expect(getEmployeeDashboard).toHaveBeenCalledTimes(1);
    expect(railButton).toHaveAttribute("aria-pressed", "true");
  });

  it("does not present draft or sync-processing aggregate counts as exact filters", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);
    const rail = screen.getByRole("region", { name: "文档状态轨道" });
    await waitFor(() => expect(getEmployeeDashboard).toHaveBeenCalledTimes(1));

    expect(within(rail).queryByRole("button", { name: /草稿/ })).not.toBeInTheDocument();
    expect(within(rail).queryByRole("button", { name: /入库处理中/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("草稿（聚合状态）")).toHaveTextContent("聚合项请用下方筛选");
    expect(screen.getByLabelText("入库处理中（聚合状态）")).toHaveTextContent("聚合项请用下方筛选");
    expect(listDocuments).toHaveBeenCalledTimes(1);
  });

  it("shows a recoverable explanation when policy blocks analysis-failed submission", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [mockAnalysisFailedFile], total: 1 });
    vi.mocked(getEmployeeDashboard).mockResolvedValue(
      employeeDashboard([dashboardDocument(mockAnalysisFailedFile)]),
    );
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(submitFileForReview).mockRejectedValue(
      new ApiError("submission disabled", {
        status: 409,
        code: "ANALYSIS_FAILED_SUBMISSION_DISABLED",
      }),
    );

    renderWithProviders(<MyFilesPage />);
    expect((await screen.findAllByText("分析失败文档.pdf")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "提交审核 分析失败文档.pdf" })[0]);

    expect(await screen.findByText(/分析失败文档.pdf.*暂不能提交/)).toBeInTheDocument();
    expect(screen.getByText(/请联系部门管理员重新发起分析或检查 AI 配置/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看文档与处理建议" })).toBeInTheDocument();
  });

  it("requires explicit acknowledgement before submitting a sensitive document", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [mockSensitiveFile], total: 1 });
    vi.mocked(getEmployeeDashboard).mockResolvedValue(
      employeeDashboard([dashboardDocument(mockSensitiveFile)]),
    );
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(submitFileForReview).mockResolvedValue({
      ...mockSensitiveFile,
      status: "pending_review",
    });

    renderWithProviders(<MyFilesPage />);
    expect((await screen.findAllByText("涉敏客户资料.pdf")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "提交审核 涉敏客户资料.pdf" })[0]);

    expect(submitFileForReview).not.toHaveBeenCalled();
    expect(await screen.findByText("确认提交敏感风险文档")).toBeInTheDocument();
    expect(screen.getByText("此文档触发了敏感内容规则")).toBeInTheDocument();
    expect(screen.getByText(/不会自动批准文档，也不会自动同步到 RAGFlow/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "我已知悉风险，提交审核" }));

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-sensitive", {
        acknowledge_sensitive_risk: true,
      });
    });
  });

  it("keeps sensitive acknowledgement on rejected documents before resubmission", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [mockSensitiveRejectedFile], total: 1 });
    vi.mocked(getEmployeeDashboard).mockResolvedValue(
      employeeDashboard([dashboardDocument(mockSensitiveRejectedFile)]),
    );
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(submitFileForReview).mockResolvedValue({
      ...mockSensitiveRejectedFile,
      status: "pending_review",
    });

    renderWithProviders(<MyFilesPage />);
    expect((await screen.findAllByText("涉敏驳回材料.pdf")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "提交审核 涉敏驳回材料.pdf" })[0]);

    expect(submitFileForReview).not.toHaveBeenCalled();
    expect(await screen.findByText("确认提交敏感风险文档")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "我已知悉风险，提交审核" }));

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-sensitive-rejected", {
        acknowledge_sensitive_risk: true,
      });
    });
  });

  it.each([
    ["入库排队", "queued"],
    ["RAGFlow 上传中", "syncing"],
    ["等待解析", "uploaded_to_ragflow"],
    ["解析中", "parsing"],
    ["已批准·未入库", "approved"],
    ["已入库", "parsed"],
    ["入库失败", "failed"],
  ])("drills down from %s with the exact server status", async (label, expectedStatus) => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText(label);
    const statusSelect = screen.getByRole("combobox", { name: "文档状态" });
    vi.mocked(listDocuments).mockClear();
    fireEvent.change(statusSelect, { target: { value: expectedStatus } });

    await waitFor(() => {
      expect(listDocuments).toHaveBeenCalledWith(
        expect.objectContaining({
          page_size: 20,
          status: expectedStatus,
        }),
      );
    });
  });

  it("calls deleteFile when delete button is clicked and Popconfirm confirms", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockResolvedValue(undefined);

    renderWithProviders(<MyFilesPage />);

    // Wait for files to load
    await screen.findAllByText("产品规划.pdf");

    // Popconfirm is mocked: clicking the delete button immediately triggers onConfirm (deleteFile)
    const deleteButtons = screen.getAllByRole("button", { name: /删除/ });
    expect(deleteButtons.length).toBeGreaterThanOrEqual(1);

    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(vi.mocked(deleteFile).mock.calls[0]?.[0]).toBe("file-1");
    });
  });

  it("hides the delete button when allow_user_delete is false", async () => {
    vi.mocked(getUploadPolicy).mockResolvedValue({
      ...uploadPolicyResponse,
      allow_user_delete: false,
    });
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("产品规划.pdf");

    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument();
  });

  it("hides delete for pending-review and pipeline-running files", async () => {
    vi.mocked(listDocuments).mockResolvedValue({
      items: [
        mockFile2,
        { ...mockFile2, id: "file-queued", original_name: "排队文档.pdf", status: "queued" },
        { ...mockFile2, id: "file-parsing", original_name: "解析文档.pdf", status: "parsing" },
      ],
      total: 3,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("技术架构.docx");
    expect(screen.queryByRole("button", { name: "删除 技术架构.docx" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除 排队文档.pdf" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除 解析文档.pdf" })).not.toBeInTheDocument();
  });

  it("fails closed for upload and delete when policy loading fails", async () => {
    vi.mocked(getUploadPolicy).mockRejectedValueOnce(new Error("policy unavailable"));
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    expect(await screen.findByText("上传与删除策略加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /上传文档/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重\s*试策略/ })).toBeInTheDocument();
  });

  it("re-fetches file list after successful delete", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockResolvedValue(undefined);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("产品规划.pdf");

    const initialCallCount = vi.mocked(listDocuments).mock.calls.length;

    const deleteButtons = screen.getAllByRole("button", { name: /删除/ });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(vi.mocked(listDocuments).mock.calls.length).toBeGreaterThan(initialCallCount);
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

    await screen.findAllByText("草稿文件.pdf");
    const initialCallCount = vi.mocked(listDocuments).mock.calls.length;
    fireEvent.click(screen.getAllByRole("button", { name: /提交审核 草稿文件/ })[0]);

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-draft", undefined);
    });
    await waitFor(() => {
      expect(vi.mocked(listDocuments).mock.calls.length).toBeGreaterThan(initialCallCount);
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

    await screen.findAllByText("被拒文件.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: /提交审核 被拒文件/ })[0]);

    await waitFor(() => {
      expect(submitFileForReview).toHaveBeenCalledWith("file-rejected", undefined);
    });
  });

  it("handles 403-like error without crashing and does not re-fetch", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockRejectedValue(new Error("管理员未开放删除权限"));

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("产品规划.pdf");

    const initialCallCount = vi.mocked(listDocuments).mock.calls.length;

    const deleteButtons = screen.getAllByRole("button", { name: /删除/ });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(vi.mocked(deleteFile).mock.calls[0]?.[0]).toBe("file-1");
    });
    expect(listDocuments).toHaveBeenCalledTimes(initialCallCount);
  });

  it("filters by extension and passes the param to listDocuments", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("产品规划.pdf");

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

    await screen.findAllByText("产品规划.pdf");

    // Find the tag select (placeholder "标签筛选")
    const tagSelect = screen.getByRole("combobox", { name: "标签筛选" });
    fireEvent.change(tagSelect, { target: { value: "tag-1" } });

    await waitFor(() => {
      expect(listDocuments).toHaveBeenCalledWith(expect.objectContaining({ tag_id: "tag-1" }));
    });
  });
});
