import React from "react";
import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import type * as AntdModule from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type FileListResponse,
  type TagListResponse,
  type UploadPolicy,
  deleteFile,
  getUploadPolicy,
  listDocuments,
  listResponsibleDocuments,
  listTags,
  submitFileForReview,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { type EmployeeDashboard, getEmployeeDashboard } from "../../api/dashboard";
import type * as DashboardApiModule from "../../api/dashboard";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import MyFilesPage, { versionSummaryStatus } from "./index";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

// ── API mocks ────────────────────────────────────────────────────────────────
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getUploadPolicy: vi.fn(),
    listDocuments: vi.fn(),
    listResponsibleDocuments: vi.fn(),
    listTags: vi.fn(),
    deleteFile: vi.fn(),
    submitFileForReview: vi.fn(),
  };
});

vi.mock("../../api/dashboard", async () => {
  const actual = await vi.importActual<typeof DashboardApiModule>("../../api/dashboard");
  return { ...actual, getEmployeeDashboard: vi.fn() };
});
vi.mock("../../components/SavedViewManager", () => ({
  SavedViewManager: ({
    pageKey,
    onApply,
  }: {
    pageKey: string;
    onApply: (definition: Record<string, unknown>) => void;
  }) => (
    <button
      type="button"
      data-testid={"saved-view-" + pageKey}
      onClick={() =>
        onApply({
          relationship: "responsible",
          queue: "mine",
          task_type: "ragflow_upload",
          group_by: "month",
          order: "desc",
          page_size: 50,
        })
      }
    >
      应用测试保存视图
    </button>
  ),
}));

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
function governanceFields(seriesId: string) {
  return {
    owner_id: "user-1",
    owner_name: "张三",
    series_id: seriesId,
    version_number: 1,
    replaces_file_id: null,
    is_current_version: true,
    remote_visibility: "candidate" as const,
    version_switch_status: "not_required" as const,
    version_switch_error: null,
    version_switch_attempt_count: 0,
    predecessor_remote_deactivated_at: null,
    local_version_activated_at: null,
    remote_version_activated_at: null,
  };
}

const mockFile1 = {
  id: "file-1",
  original_name: "产品规划.pdf",
  extension: "pdf",
  mime_type: "application/pdf",
  size: 204800,
  uploader_id: "user-1",
  ...governanceFields("file-1"),
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
  ...governanceFields("file-2"),
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

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-search">{location.search}</output>;
}

function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" aria-label="浏览器后退" onClick={() => navigate(-1)}>
        后退
      </button>
      <button type="button" aria-label="浏览器前进" onClick={() => navigate(1)}>
        前进
      </button>
    </>
  );
}

function renderWithProviders(
  node: ReactNode,
  initialEntries = ["/my-files"],
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  }),
) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <LocationProbe />
      <HistoryControls />
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
  vi.mocked(listResponsibleDocuments).mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
    total_pages: 0,
  });
  vi.mocked(submitFileForReview).mockResolvedValue(mockFile2);
  vi.mocked(getEmployeeDashboard).mockResolvedValue(employeeDashboard());
});

// ── Tests ────────────────────────────────────────────────────────────────────
describe("MyFilesPage", () => {
  it.each([
    [
      {
        version_switch_status: "failed_new_activate",
        remote_visibility: "current",
        is_current_version: true,
      },
      "summary_failed",
    ],
    [
      {
        version_switch_status: "local_switched",
        remote_visibility: "unknown",
        is_current_version: true,
      },
      "summary_candidate",
    ],
    [
      {
        version_switch_status: "completed",
        remote_visibility: "unknown",
        is_current_version: true,
      },
      "summary_unknown",
    ],
    [
      {
        version_switch_status: "completed",
        remote_visibility: "candidate",
        is_current_version: true,
      },
      "summary_current",
    ],
    [
      {
        version_switch_status: "completed",
        remote_visibility: "candidate",
        is_current_version: false,
      },
      "summary_candidate",
    ],
    [
      {
        version_switch_status: "completed",
        remote_visibility: "not_current",
        is_current_version: false,
      },
      "summary_history",
    ],
  ] as const)(
    "applies failure > intermediate > unknown > current > candidate > history priority",
    (file, expected) => {
      expect(versionSummaryStatus(file)).toBe(expected);
    },
  );

  it("canonicalizes a responsible deep link, persists the view, and hides uploader-only actions", async () => {
    const delegatedFile = {
      ...mockDraftFile,
      id: "file-delegated",
      title: "委派制度",
      uploader_id: "employee-uploader",
      owner_id: "employee-1",
      owner_name: "张三",
      expires_at: "2026-07-20T08:00:00Z",
      expiry_status: "expiring",
    };
    vi.mocked(listResponsibleDocuments).mockResolvedValue({
      items: [delegatedFile],
      total: 1,
      page: 2,
      page_size: 20,
      total_pages: 2,
    });
    vi.mocked(listDocuments).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      total_pages: 0,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />, [
      "/my-files?relationship=responsible&q=制度&expiry_status=expiring&page=2&tag_id=stale-tag",
    ]);

    await waitFor(() => {
      const location = screen.getByTestId("location-search").textContent ?? "";
      expect(location).toContain("relationship=responsible");
      expect(location).not.toContain("tag_id");
    });

    expect((await screen.findAllByText("委派制度")).length).toBeGreaterThan(0);
    expect(listResponsibleDocuments).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 2,
        page_size: 20,
        q: "制度",
        expiry_status: "expiring",
        sort: "updated_at",
        order: "desc",
      }),
    );
    expect(listDocuments).not.toHaveBeenCalled();
    expect(listTags).not.toHaveBeenCalled();
    expect(getEmployeeDashboard).not.toHaveBeenCalled();
    expect(vi.mocked(listResponsibleDocuments).mock.calls[0][0]).not.toHaveProperty("tag_id");
    expect(
      screen.getByText("被指定负责人可查看文件详情与原件，但不能修改、提交、替代或删除文件。"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "委派制度" })[0]).toHaveAttribute(
      "href",
      "/files/file-delegated",
    );
    expect(screen.getAllByRole("button", { name: "预览原件 委派制度" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "下载原件 委派制度" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "提交审核 委派制度" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除 委派制度" })).not.toBeInTheDocument();
    const desktopList = document.querySelector(".recent-files-table");
    expect(desktopList).not.toBeNull();
    expect(within(desktopList as HTMLElement).getByText("即将过期")).toBeInTheDocument();
    expect(within(desktopList as HTMLElement).getByText(/^2026-07-20 /)).toBeInTheDocument();
    expect(within(desktopList as HTMLElement).queryByLabelText(/文档版本/)).not.toBeInTheDocument();
    const mobileList = screen.getByLabelText("移动端文档列表");
    expect(within(mobileList).getByText("即将过期")).toBeInTheDocument();
    expect(within(mobileList).getByText(/^2026-07-20 /)).toBeInTheDocument();
    expect(within(mobileList).queryByLabelText(/文档版本/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "到期状态" }), {
      target: { value: "expired" },
    });
    await waitFor(() => {
      const location = screen.getByTestId("location-search").textContent ?? "";
      expect(location).toContain("relationship=responsible");
      expect(location).toContain("expiry_status=expired");
      expect(location).toContain("page=1");
    });

    fireEvent.click(screen.getByText("我上传的"));
    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    expect(screen.getByTestId("location-search")).toHaveTextContent("relationship=uploaded");

    fireEvent.change(await screen.findByRole("combobox", { name: "标签筛选" }), {
      target: { value: "tag-1" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("location-search")).toHaveTextContent("tag_id=tag-1"),
    );

    fireEvent.click(screen.getByText("我负责的"));
    await waitFor(() => {
      const location = screen.getByTestId("location-search").textContent ?? "";
      expect(location).toContain("relationship=responsible");
      expect(location).not.toContain("tag_id");
    });
  });

  it("canonicalizes a stale responsible tag reached through back and forward history", async () => {
    vi.mocked(listDocuments).mockResolvedValue({
      items: [mockFile1],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />, [
      "/my-files?relationship=responsible&tag_id=stale-first&q=first",
      "/my-files?relationship=responsible&tag_id=stale-second&q=second",
      "/my-files?relationship=uploaded&tag_id=tag-1",
    ]);

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    expect(screen.getByTestId("location-search")).toHaveTextContent("relationship=uploaded");
    expect(screen.getByTestId("location-search")).toHaveTextContent("tag_id=tag-1");
    vi.mocked(listDocuments).mockClear();
    vi.mocked(listResponsibleDocuments).mockClear();
    vi.mocked(listTags).mockClear();

    fireEvent.click(screen.getByRole("button", { name: "浏览器后退" }));
    await waitFor(() => {
      const location = screen.getByTestId("location-search").textContent ?? "";
      expect(location).toContain("relationship=responsible");
      expect(location).toContain("q=second");
      expect(location).not.toContain("tag_id");
    });
    await waitFor(() =>
      expect(listResponsibleDocuments).toHaveBeenCalledWith(
        expect.objectContaining({ q: "second" }),
      ),
    );
    for (const [params] of vi.mocked(listResponsibleDocuments).mock.calls) {
      expect(params).not.toHaveProperty("tag_id");
    }
    expect(listDocuments).not.toHaveBeenCalled();
    expect(listTags).not.toHaveBeenCalled();

    vi.mocked(listResponsibleDocuments).mockClear();
    fireEvent.click(screen.getByRole("button", { name: "浏览器后退" }));
    await waitFor(() => {
      const location = screen.getByTestId("location-search").textContent ?? "";
      expect(location).toContain("relationship=responsible");
      expect(location).toContain("q=first");
      expect(location).not.toContain("tag_id");
    });
    await waitFor(() =>
      expect(listResponsibleDocuments).toHaveBeenCalledWith(
        expect.objectContaining({ q: "first" }),
      ),
    );
    for (const [params] of vi.mocked(listResponsibleDocuments).mock.calls) {
      expect(params).not.toHaveProperty("tag_id");
    }
    expect(listDocuments).not.toHaveBeenCalled();
    expect(listTags).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "浏览器前进" }));
    await waitFor(() => {
      const location = screen.getByTestId("location-search").textContent ?? "";
      expect(location).toContain("relationship=responsible");
      expect(location).toContain("q=second");
      expect(location).not.toContain("tag_id");
    });
  });

  it("shows explicit expiry placeholders in responsible desktop and mobile views", async () => {
    vi.mocked(listResponsibleDocuments).mockResolvedValue({
      items: [
        {
          ...mockDraftFile,
          id: "file-no-expiry",
          title: "无到期信息制度",
          uploader_id: "employee-uploader",
          owner_id: "employee-1",
          expires_at: null,
          expiry_status: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    });

    renderWithProviders(<MyFilesPage />, ["/my-files?relationship=responsible"]);
    expect((await screen.findAllByText("无到期信息制度")).length).toBeGreaterThan(0);

    const desktopList = document.querySelector(".recent-files-table");
    expect(desktopList).not.toBeNull();
    expect(within(desktopList as HTMLElement).getByText("到期状态未知")).toBeInTheDocument();
    expect(within(desktopList as HTMLElement).getByText("未设置到期时间")).toBeInTheDocument();
    const mobileList = screen.getByLabelText("移动端文档列表");
    expect(within(mobileList).getByText("到期状态未知")).toBeInTheDocument();
    expect(within(mobileList).getByText("未设置到期时间")).toBeInTheDocument();
  });

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

  it("distinguishes retained versions in both desktop and mobile uploaded views", async () => {
    const versionedFiles = [
      {
        ...mockFile1,
        id: "version-history",
        original_name: "同名制度.pdf",
        version_number: 1,
        is_current_version: false,
        remote_visibility: "not_current" as const,
        version_switch_status: "not_required" as const,
      },
      {
        ...mockFile1,
        id: "version-current",
        original_name: "同名制度.pdf",
        version_number: 2,
        is_current_version: true,
        remote_visibility: "current" as const,
        version_switch_status: "completed" as const,
      },
      {
        ...mockFile1,
        id: "version-candidate",
        original_name: "候选制度.pdf",
        version_number: 3,
        is_current_version: false,
        remote_visibility: "candidate" as const,
        version_switch_status: "pending" as const,
      },
      {
        ...mockFile1,
        id: "version-failed",
        original_name: "切换失败制度.pdf",
        version_number: 4,
        is_current_version: false,
        remote_visibility: "candidate" as const,
        version_switch_status: "failed_new_activate" as const,
      },
      {
        ...mockFile1,
        id: "version-unknown",
        original_name: "待确认制度.pdf",
        version_number: 5,
        is_current_version: false,
        remote_visibility: "unknown" as const,
        version_switch_status: "completed" as const,
      },
    ];
    vi.mocked(listDocuments).mockResolvedValue({
      items: versionedFiles,
      total: versionedFiles.length,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    expect((await screen.findAllByText("同名制度.pdf")).length).toBeGreaterThanOrEqual(4);
    const desktopList = document.querySelector(".recent-files-table");
    expect(desktopList).not.toBeNull();
    const mobileList = screen.getByLabelText("移动端文档列表");

    for (const list of [desktopList as HTMLElement, mobileList]) {
      const scope = within(list);
      expect(scope.getByText("v1")).toBeInTheDocument();
      expect(scope.getByText("v2")).toBeInTheDocument();
      expect(scope.getByText("v3")).toBeInTheDocument();
      expect(scope.getByText("v4")).toBeInTheDocument();
      expect(scope.getByText("v5")).toBeInTheDocument();
      expect(scope.getByText("历史")).toBeInTheDocument();
      expect(scope.getByText("当前")).toBeInTheDocument();
      expect(scope.getByText("候选处理中")).toBeInTheDocument();
      expect(scope.getByText("切换失败")).toBeInTheDocument();
      expect(scope.getByText("待确认")).toBeInTheDocument();
    }
  });

  it("marks an initial v1 draft as current before remote activation", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [mockDraftFile], total: 1 });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<MyFilesPage />);

    await screen.findAllByText("草稿文件.pdf");
    const desktopList = document.querySelector(".recent-files-table");
    expect(desktopList).not.toBeNull();
    const mobileList = screen.getByLabelText("移动端文档列表");

    for (const list of [desktopList as HTMLElement, mobileList]) {
      const scope = within(list);
      expect(scope.getByText("v1")).toBeInTheDocument();
      expect(scope.getByText("当前")).toBeInTheDocument();
      expect(scope.queryByText("候选处理中")).not.toBeInTheDocument();
    }
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

  it("returns to page one when deleting the only item on the last page shrinks total", async () => {
    vi.mocked(listDocuments)
      .mockResolvedValueOnce({
        items: [mockFile1],
        total: 21,
        page: 2,
        page_size: 20,
        total_pages: 2,
      })
      .mockResolvedValueOnce({
        items: [],
        total: 20,
        page: 2,
        page_size: 20,
        total_pages: 1,
      })
      .mockResolvedValue({
        items: [mockFile2],
        total: 20,
        page: 1,
        page_size: 20,
        total_pages: 1,
      });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(deleteFile).mockResolvedValue(undefined);

    renderWithProviders(<MyFilesPage />, ["/my-files?page=2&page_size=20"]);
    await screen.findAllByText("产品规划.pdf");

    fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);

    await waitFor(() => expect(screen.getByTestId("location-search")).toHaveTextContent("page=1"));
    expect(listDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }),
    );
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

  it("suppresses delete invalidation and success UI after a direct ABA session switch", async () => {
    vi.mocked(listDocuments).mockResolvedValue(mockFilesResponse);
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    const deleteDeferred = createDeferred<void>();
    vi.mocked(deleteFile).mockReturnValue(deleteDeferred.promise);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const sessionA = {
      accessToken: useAuthStore.getState().accessToken,
      user: useAuthStore.getState().user,
    };

    renderWithProviders(<MyFilesPage />, ["/my-files"], queryClient);
    await screen.findAllByText("产品规划.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);
    await waitFor(() => expect(deleteFile).toHaveBeenCalledWith("file-1"));

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "employee-b",
          name: "李四",
          email: "b@example.com",
          role: "employee",
          department_assigned: true,
          department_id: "dept-tech",
        },
      });
      useAuthStore.setState(sessionA);
    });
    await act(async () => {
      deleteDeferred.resolve(undefined);
      await deleteDeferred.promise;
      await Promise.resolve();
    });

    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.queryByText("文件已删除")).not.toBeInTheDocument();
  });

  it("suppresses submit invalidation and success UI after session replacement", async () => {
    vi.mocked(listDocuments).mockResolvedValue({
      items: [mockDraftFile],
      total: 1,
    });
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    const submitDeferred = createDeferred<typeof mockDraftFile>();
    vi.mocked(submitFileForReview).mockReturnValue(submitDeferred.promise);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    renderWithProviders(<MyFilesPage />, ["/my-files"], queryClient);
    await screen.findAllByText("草稿文件.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: /提交审核 草稿文件/ })[0]);
    await waitFor(() => expect(submitFileForReview).toHaveBeenCalledWith("file-draft", undefined));

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "employee-b",
          name: "李四",
          email: "b@example.com",
          role: "employee",
          department_assigned: true,
          department_id: "dept-tech",
        },
      });
    });
    await act(async () => {
      submitDeferred.resolve({
        ...mockDraftFile,
        status: "pending_review",
      });
      await submitDeferred.promise;
      await Promise.resolve();
    });

    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.queryByText("已提交审核")).not.toBeInTheDocument();
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
