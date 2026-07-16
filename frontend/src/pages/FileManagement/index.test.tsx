import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type KnowledgeFile,
  type UploadPolicy,
  approveFile,
  claimReviewFile,
  getUploadPolicy,
  listCategories,
  listDatasetMappings,
  listReviewFiles,
  listTags,
  rejectFile,
  releaseReviewClaim,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import FileManagementPage, {
  buildBulkApproveOnlyPayload,
  eligibleReviewTargets,
  hasActiveReviewClaim,
  hasValidReviewClaim,
} from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return {
    ...actual,
    approveFile: vi.fn(),
    claimReviewFile: vi.fn(),
    getUploadPolicy: vi.fn(),
    listCategories: vi.fn(),
    listDatasetMappings: vi.fn(),
    listReviewFiles: vi.fn(),
    listTags: vi.fn(),
    rejectFile: vi.fn(),
    releaseReviewClaim: vi.fn(),
  };
});

const uploadPolicy: UploadPolicy = {
  allowed_extensions: ["pdf", "docx"],
  allow_multi_file: true,
  upload_enabled: true,
  max_file_size_mb: 50,
  allow_user_delete: false,
};

function makeReviewFile(overrides: Partial<KnowledgeFile> = {}): KnowledgeFile {
  return {
    id: "review-file-1",
    original_name: "待审核制度.pdf",
    extension: "pdf",
    mime_type: "application/pdf",
    size: 1024,
    uploader_id: "employee-1",
    uploader_name: "张三",
    department: "技术部",
    category_id: null,
    dataset_mapping_id: null,
    visibility: "department",
    description: "制度文件",
    tags: [],
    status: "pending_review",
    review_status: "pending",
    ragflow_dataset_id: null,
    ragflow_document_id: null,
    ragflow_parse_status: null,
    ai_analysis_enabled_at_upload: true,
    uploaded_at: "2026-07-15T08:00:00Z",
    submitted_at: "2026-07-15T09:00:00Z",
    review_due_at: "2026-07-15T10:00:00Z",
    claimed_by: null,
    claimed_by_name: null,
    claimed_at: overrides.claimed_by ? new Date(Date.now() - 60_000).toISOString() : null,
    claim_expires_at: overrides.claimed_by ? new Date(Date.now() + 60_000).toISOString() : null,
    review_version: 1,
    sensitive_risk_level: "medium",
    last_sync_at: null,
    created_at: "2026-07-15T08:00:00Z",
    updated_at: "2026-07-15T09:00:00Z",
    duplicate: false,
    duplicate_file_id: null,
    ...overrides,
  };
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

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "token",
    user: {
      id: "admin-1",
      name: "部门管理员",
      email: "admin@company.com",
      role: "dept_admin",
      department_assigned: true,
    },
  });
  vi.mocked(getUploadPolicy).mockResolvedValue(uploadPolicy);
  vi.mocked(listCategories).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(listDatasetMappings).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(listTags).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
});

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

function renderWorkbench(initialEntry = "/admin/files") {
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
          <div style={themeCssVariables as CSSProperties}>
            <MemoryRouter initialEntries={[initialEntry]}>
              <FileManagementPage />
              <RouteProbe />
            </MemoryRouter>
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

function RouteProbe() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <>
      <span data-testid="location">
        {location.pathname}
        {decodeURIComponent(location.search)}
      </span>
      <button type="button" onClick={() => navigate("/admin/files?q=外部更新&page=3")}>
        模拟外部地址变化
      </button>
    </>
  );
}

describe("FileManagementPage", () => {
  it("passes queue, search and pagination state to the server", async () => {
    vi.mocked(listReviewFiles).mockResolvedValue({
      items: [],
      total: 0,
      page: 2,
      page_size: 10,
      total_pages: 1,
    });

    renderWorkbench("/admin/files?q=制度&page=2&page_size=10&queue=overdue");

    await waitFor(() => {
      expect(listReviewFiles).toHaveBeenCalledWith(
        expect.objectContaining({
          page: 2,
          page_size: 10,
          q: "制度",
          queue: "overdue",
        }),
      );
    });
    expect(screen.getByRole("tab", { name: /已超时/ })).toHaveAttribute("aria-selected", "true");
  });

  it("normalizes invalid URL filters before querying the review queue", async () => {
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [], total: 0 });

    renderWorkbench(
      "/admin/files?queue=not-a-queue&risk=severe&tag_id=tag-1&extension=%3Cscript%3E&page=9",
    );

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/admin/files?page=1");
    });
    expect(listReviewFiles).toHaveBeenLastCalledWith(
      expect.objectContaining({
        page: 1,
        queue: undefined,
        sensitive_risk_level: undefined,
        tag_id: undefined,
        extension: undefined,
      }),
    );
  });

  it("resets pagination for advanced filters and syncs search after external URL changes", async () => {
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [], total: 0 });

    renderWorkbench("/admin/files?q=初始关键字&page=4");
    expect(screen.getByPlaceholderText("搜索文件名称、关键词")).toHaveValue("初始关键字");

    fireEvent.click(screen.getByRole("button", { name: "更多筛选" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "风险等级筛选" }));
    fireEvent.click(await screen.findByText("高风险"));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/admin/files?q=初始关键字&page=1&risk=high",
      );
    });
    expect(listReviewFiles).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, sensitive_risk_level: "high" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "模拟外部地址变化" }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("搜索文件名称、关键词")).toHaveValue("外部更新");
    });
  });

  it("keeps server q results and total even when only the uploader name matches", async () => {
    const uploaderMatch = makeReviewFile({
      original_name: "完全不同的文件名.pdf",
      uploader_name: "张三",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({
      items: [uploaderMatch],
      total: 42,
      page: 1,
      page_size: 20,
      total_pages: 3,
    });

    renderWorkbench("/admin/files?q=张三");

    expect(await screen.findByText("完全不同的文件名.pdf")).toBeInTheDocument();
    expect(screen.getByText("共 42 条")).toBeInTheDocument();
    expect(listReviewFiles).toHaveBeenCalledWith(expect.objectContaining({ q: "张三" }));
  });

  it("renders missing risk as not assessed without inventing a low or rejected risk", async () => {
    const file = makeReviewFile({
      sensitive_risk_level: null,
      review_status: "rejected",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench();

    expect(await screen.findByLabelText("风险等级：未评估")).toBeInTheDocument();
    expect(screen.queryByLabelText("风险等级：低风险")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("风险等级：中风险")).not.toBeInTheDocument();
    expect(document.querySelector(".file-title-cell__star")).toBeNull();
  });

  it("shows SLA state and lets an administrator claim an unclaimed review", async () => {
    const file = makeReviewFile();
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(claimReviewFile).mockResolvedValue({
      ...file,
      claimed_by: "admin-1",
      claimed_by_name: "部门管理员",
    });

    renderWorkbench();

    expect(await screen.findByText("待审核制度.pdf")).toBeInTheDocument();
    expect(screen.getAllByText("已超时").length).toBeGreaterThan(0);
    expect(screen.getByText("当前页已超时")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /领取$/ }));

    await waitFor(() => {
      expect(vi.mocked(claimReviewFile).mock.calls[0]?.[0]).toBe("review-file-1");
    });
  });

  it("recovers from a claim conflict by showing row feedback and refreshing", async () => {
    const file = makeReviewFile();
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(claimReviewFile).mockRejectedValue(
      new ApiError("already claimed", {
        status: 409,
        code: "REVIEW_CLAIM_CONFLICT",
      }),
    );

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    const initialCalls = vi.mocked(listReviewFiles).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /领取$/ }));

    expect(await screen.findByText("该任务刚刚被他人领取，队列已刷新")).toBeInTheDocument();
    await waitFor(() => {
      expect(vi.mocked(listReviewFiles).mock.calls.length).toBeGreaterThan(initialCalls);
    });
  });

  it("sends an explicit approve-only decision without requiring an optional reason", async () => {
    const file = makeReviewFile({
      claimed_by: "admin-1",
      claimed_by_name: "部门管理员",
      sensitive_risk_level: "high",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(approveFile).mockResolvedValue({ ...file, status: "approved" });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    fireEvent.click(screen.getByRole("button", { name: "审核" }));
    fireEvent.click(screen.getByLabelText("仅批准，不进入知识库"));
    fireEvent.click(screen.getByRole("button", { name: "确认批准" }));

    await waitFor(() => {
      expect(approveFile).toHaveBeenCalledWith("review-file-1", {
        sync_decision: "approve_only",
        category_id: null,
        dataset_mapping_id: null,
        reason: null,
      });
    });
  });

  it("clears an existing Dataset mapping when building a batch approve-only decision", () => {
    const file = makeReviewFile({
      claimed_by: "admin-1",
      dataset_mapping_id: "mapping-that-must-not-leak",
      category_id: "category-1",
    });

    expect(buildBulkApproveOnlyPayload(file)).toEqual({
      sync_decision: "approve_only",
      category_id: "category-1",
      dataset_mapping_id: null,
      reason: "批量审核通过",
    });
  });

  it("allows decisions and bulk approval only for the user's unexpired claim", () => {
    const now = Date.parse("2026-07-16T08:00:00Z");
    const mine = makeReviewFile({
      id: "mine",
      claimed_by: "admin-1",
      claimed_at: "2026-07-16T07:00:00Z",
      claim_expires_at: "2026-07-16T09:00:00Z",
    });
    const expired = makeReviewFile({
      id: "expired",
      claimed_by: "admin-1",
      claimed_at: "2026-07-16T06:00:00Z",
      claim_expires_at: "2026-07-16T07:00:00Z",
    });
    const other = makeReviewFile({
      id: "other",
      claimed_by: "admin-2",
      claimed_at: "2026-07-16T07:00:00Z",
      claim_expires_at: "2026-07-16T09:00:00Z",
    });
    const unclaimed = makeReviewFile({ id: "unclaimed", claimed_by: null });

    expect(hasActiveReviewClaim(mine, "admin-1", now)).toBe(true);
    expect(hasValidReviewClaim(mine, now)).toBe(true);
    expect(hasActiveReviewClaim(expired, "admin-1", now)).toBe(false);
    expect(hasValidReviewClaim(expired, now)).toBe(false);
    expect(hasActiveReviewClaim(other, "admin-1", now)).toBe(false);
    expect(hasValidReviewClaim(other, now)).toBe(true);
    expect(
      eligibleReviewTargets(
        [
          { ...mine, claim_expires_at: new Date(Date.now() + 60_000).toISOString() },
          { ...expired, claim_expires_at: new Date(Date.now() - 60_000).toISOString() },
          { ...other, claim_expires_at: new Date(Date.now() + 60_000).toISOString() },
          unclaimed,
        ],
        "admin-1",
      ).map((file) => file.id),
    ).toEqual(["mine"]);
  });

  it("fails closed for legacy or corrupt claims without a valid expiry", () => {
    const legacy = makeReviewFile({
      claimed_by: "admin-1",
      claimed_at: new Date(Date.now() - 60_000).toISOString(),
      claim_expires_at: null,
    });
    const corrupt = makeReviewFile({
      claimed_by: "admin-1",
      claimed_at: new Date(Date.now() - 60_000).toISOString(),
      claim_expires_at: "not-a-date",
    });

    expect(hasActiveReviewClaim(legacy, "admin-1")).toBe(false);
    expect(hasActiveReviewClaim(corrupt, "admin-1")).toBe(false);
    expect(hasValidReviewClaim(legacy)).toBe(false);
    expect(hasValidReviewClaim(corrupt)).toBe(false);
    expect(eligibleReviewTargets([legacy, corrupt], "admin-1")).toEqual([]);
  });

  it("treats expired claims as available without showing release actions", async () => {
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "system-admin-1",
        name: "系统管理员",
        email: "root@company.com",
        role: "system_admin",
        department_assigned: true,
      },
    });
    const file = makeReviewFile({
      claimed_by: "other-admin",
      claimed_by_name: "其他审核人",
      claimed_at: new Date(Date.now() - 120_000).toISOString(),
      claim_expires_at: new Date(Date.now() - 60_000).toISOString(),
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");

    expect(screen.getByText("领取已失效")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新领取/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /领取下一份/ })).not.toBeDisabled();
    expect(screen.queryByRole("button", { name: /^释放$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /强制释放/ })).not.toBeInTheDocument();
    expect(screen.getByText("当前页待领取").parentElement).toHaveTextContent("1项");
  });

  it("opens the unclaimed queue when the current page has no directly claimable task", async () => {
    const file = makeReviewFile({
      claimed_by: "other-admin",
      claimed_by_name: "其他审核人",
      claimed_at: new Date(Date.now() - 60_000).toISOString(),
      claim_expires_at: new Date(Date.now() + 60_000).toISOString(),
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench("/admin/files?queue=mine&page=3");
    await screen.findByText("待审核制度.pdf");

    const nextButton = screen.getByRole("button", { name: /领取下一份/ });
    expect(nextButton).not.toBeDisabled();
    fireEvent.click(nextButton);

    expect(
      await screen.findByText("当前页没有可直接领取项，已为你打开待领取队列"),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/admin/files?queue=unclaimed&page=1",
      );
    });
    expect(claimReviewFile).not.toHaveBeenCalled();
  });

  it("requires a system administrator to force-release with a reason before claiming", async () => {
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "system-admin-1",
        name: "系统管理员",
        email: "root@company.com",
        role: "system_admin",
        department_assigned: true,
      },
    });
    const file = makeReviewFile({
      claimed_by: "other-admin",
      claimed_by_name: "其他审核人",
      claim_expires_at: new Date(Date.now() + 60_000).toISOString(),
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(releaseReviewClaim).mockResolvedValue({ ...file, claimed_by: null });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    expect(screen.queryByRole("button", { name: "审核" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "驳回" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /强制释放/ }));
    fireEvent.change(screen.getByLabelText("强制释放原因"), {
      target: { value: "原审核人已离岗，转交当前值班管理员" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认强制释放" }));

    await waitFor(() => {
      expect(releaseReviewClaim).toHaveBeenCalledWith(
        "review-file-1",
        "原审核人已离岗，转交当前值班管理员",
      );
    });
    expect(claimReviewFile).not.toHaveBeenCalled();
  });

  it("shows classification drafting only to the owner of a complete active claim", async () => {
    const file = makeReviewFile({ claimed_by: "admin-1" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    const draftAction = await screen.findByText("编辑审核草案");
    expect(screen.queryByText("归档")).not.toBeInTheDocument();
    expect(screen.queryByText("删除")).not.toBeInTheDocument();
    fireEvent.click(draftAction);

    expect(await screen.findByText("编辑审核分类草案")).toBeInTheDocument();
    expect(screen.getByText("这里保存的是审核草案")).toBeInTheDocument();
    expect(
      screen.getByText(/最终 Dataset 必须在“审核通过”时随同步决定再次确认/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("审核草案分类")).toBeInTheDocument();
    expect(screen.getByLabelText("审核草案 Dataset")).toBeInTheDocument();
  });

  it("hides classification, archive and delete for another reviewer's active claim", async () => {
    const file = makeReviewFile({
      claimed_by: "other-admin",
      claimed_by_name: "其他审核人",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");

    expect(screen.queryByRole("button", { name: "更多操作" })).not.toBeInTheDocument();
    expect(screen.queryByText("编辑审核草案")).not.toBeInTheDocument();
    expect(screen.queryByText("归档")).not.toBeInTheDocument();
    expect(screen.queryByText("删除")).not.toBeInTheDocument();
  });

  it("does not approve a sync decision without an explicit Dataset", async () => {
    const file = makeReviewFile({ claimed_by: "admin-1" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    fireEvent.click(screen.getByRole("button", { name: "审核" }));
    fireEvent.click(screen.getByLabelText(/批准并同步到 RAGFlow/));
    fireEvent.click(screen.getByRole("button", { name: "确认批准" }));

    expect(await screen.findByText("批准并同步时必须选择 Dataset")).toBeInTheDocument();
    expect(approveFile).not.toHaveBeenCalled();
  });

  it("blocks critical-risk sync and still allows an approve-only path", async () => {
    const file = makeReviewFile({
      claimed_by: "admin-1",
      sensitive_risk_level: "critical",
    });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    fireEvent.click(screen.getByRole("button", { name: "审核" }));

    expect(screen.getByText("严重风险文档禁止同步")).toBeInTheDocument();
    expect(screen.getByLabelText(/批准并同步到 RAGFlow/)).toBeDisabled();
    expect(screen.getByLabelText("仅批准，不进入知识库")).not.toBeDisabled();
  });

  it("rejects with a required reason and releases a claimed task", async () => {
    const file = makeReviewFile({ claimed_by: "admin-1" });
    vi.mocked(listReviewFiles).mockResolvedValue({ items: [file], total: 1 });
    vi.mocked(rejectFile).mockResolvedValue({ ...file, status: "rejected" });
    vi.mocked(releaseReviewClaim).mockResolvedValue({ ...file, claimed_by: null });

    renderWorkbench();
    await screen.findByText("待审核制度.pdf");
    fireEvent.click(screen.getByRole("button", { name: "驳回" }));
    fireEvent.change(screen.getByLabelText("拒绝原因"), {
      target: { value: "缺少有效来源" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认驳回" }));

    await waitFor(() => {
      expect(rejectFile).toHaveBeenCalledWith("review-file-1", "缺少有效来源");
    });

    fireEvent.click(screen.getByRole("button", { name: /释放/ }));
    await waitFor(() => {
      expect(releaseReviewClaim).toHaveBeenCalledWith("review-file-1");
    });
  });
});
