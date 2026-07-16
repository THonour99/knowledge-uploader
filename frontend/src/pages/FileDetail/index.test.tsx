import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type DatasetMapping,
  type KnowledgeFile,
  type SyncTaskListResponse,
  approveFile,
  claimReviewFile,
  getDocument,
  getDocumentContent,
  listDatasetMappings,
  listTasks,
  rejectFile,
  releaseReviewClaim,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import FileDetailPage, {
  INLINE_PREVIEW_MAX_BYTES,
  buildDetailReviewDecisionPayload,
  canPreviewInline,
  fileLoadErrorPresentation,
  taskFailureMessage,
  validateInlinePreviewContent,
} from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    approveFile: vi.fn(),
    claimReviewFile: vi.fn(),
    getDocument: vi.fn(),
    getDocumentContent: vi.fn(),
    listDatasetMappings: vi.fn(),
    listTasks: vi.fn(),
    rejectFile: vi.fn(),
    releaseReviewClaim: vi.fn(),
  };
});

const baseFile: KnowledgeFile = {
  id: "file-1",
  original_name: "员工手册.pdf",
  extension: "pdf",
  mime_type: "application/pdf",
  size: 2048,
  uploader_id: "user-1",
  department: null,
  category_id: "cat-1",
  dataset_mapping_id: null,
  visibility: "private",
  description: "测试文件",
  tags: ["制度", "人事"],
  status: "analyzed",
  review_status: "pending",
  ragflow_dataset_id: null,
  ragflow_document_id: null,
  ragflow_parse_status: null,
  ai_analysis_enabled_at_upload: true,
  uploaded_at: "2026-06-10T08:00:00Z",
  last_sync_at: null,
  created_at: "2026-06-10T08:00:00Z",
  updated_at: "2026-06-10T08:00:00Z",
  duplicate: false,
  duplicate_file_id: null,
  category_name: "制度文档",
  analysis: {
    status: "succeeded",
    summary: "这是一份员工手册的摘要。",
    sensitive_risk_level: "medium",
    quality_score: null,
    extracted_text_preview: "员工手册提取文本前五百字……",
    error_message: null,
    finished_at: "2026-06-10T08:05:00Z",
  },
  sync_error: null,
};

const taskListResponse: SyncTaskListResponse = {
  items: [
    {
      id: "task-1",
      file_id: "file-1",
      task_type: "ragflow_upload",
      status: "failed",
      retry_count: 1,
      max_retry_count: 3,
      error_message: "上传 RAGFlow 超时",
      started_at: "2026-06-10T09:00:00Z",
      finished_at: "2026-06-10T09:01:00Z",
      created_at: "2026-06-10T09:00:00Z",
      updated_at: "2026-06-10T09:01:00Z",
      logs: [],
    },
    {
      id: "task-2",
      file_id: "file-other",
      task_type: "ragflow_parse",
      status: "succeeded",
      retry_count: 0,
      max_retry_count: 3,
      error_message: "其他文件的任务不应展示",
      started_at: null,
      finished_at: null,
      created_at: "2026-06-10T10:00:00Z",
      updated_at: "2026-06-10T10:00:00Z",
      logs: [],
    },
  ],
  total: 2,
};

const datasetMapping: DatasetMapping = {
  id: "mapping-1",
  name: "制度知识库",
  category_id: "cat-ragflow",
  ragflow_dataset_id: "dataset-1",
  ragflow_dataset_name: "制度 Dataset",
  enabled: true,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
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
  Object.defineProperty(URL, "createObjectURL", {
    writable: true,
    value: vi.fn(),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    writable: true,
    value: vi.fn(),
  });
});

function renderFileDetail(node: ReactNode = <FileDetailPage />, initialEntry = "/files/file-1") {
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
              <Routes>
                <Route path="/files/:id" element={node} />
                <Route path="/files" element={<span>审核工作台落点</span>} />
                <Route path="/my-files" element={<span>我的文件落点</span>} />
              </Routes>
            </MemoryRouter>
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

function setAdminSession() {
  useAuthStore.setState({
    accessToken: "test-token",
    user: {
      id: "admin-1",
      name: "部门管理员",
      email: "admin@company.com",
      role: "dept_admin",
    },
  });
}

function FileRouteSwitcher() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/files/file-2")}>
        切换到下一份文件
      </button>
      <FileDetailPage />
    </>
  );
}

beforeEach(() => {
  vi.mocked(approveFile).mockReset().mockResolvedValue(baseFile);
  vi.mocked(claimReviewFile).mockReset().mockResolvedValue(baseFile);
  vi.mocked(getDocument).mockReset().mockResolvedValue(baseFile);
  vi.mocked(getDocumentContent).mockReset();
  vi.mocked(listDatasetMappings)
    .mockReset()
    .mockResolvedValue({ items: [datasetMapping], total: 1 });
  vi.mocked(listTasks).mockReset().mockResolvedValue({ items: [], total: 0 });
  vi.mocked(rejectFile).mockReset().mockResolvedValue(baseFile);
  vi.mocked(releaseReviewClaim).mockReset().mockResolvedValue(baseFile);
});
afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

describe("FileDetailPage", () => {
  it("classifies missing, forbidden, server and network failures without collapsing them to 404", () => {
    expect(fileLoadErrorPresentation(new ApiError("missing", { status: 404 }))).toMatchObject({
      status: "404",
      title: "文件不存在",
    });
    expect(fileLoadErrorPresentation(new ApiError("forbidden", { status: 403 }))).toMatchObject({
      status: "403",
      title: "无权访问此文件",
    });
    expect(fileLoadErrorPresentation(new ApiError("server", { status: 503 }))).toMatchObject({
      status: "500",
      title: "文件服务暂时不可用",
    });
    expect(fileLoadErrorPresentation(new TypeError("network down"))).toMatchObject({
      status: "warning",
      title: "无法连接文件服务",
    });
  });

  it("requires an explicit RAGFlow decision and derives the sync category from the mapping", () => {
    expect(() => buildDetailReviewDecisionPayload({}, baseFile, [datasetMapping])).toThrow(
      "必须明确选择是否进入 RAGFlow",
    );
    expect(
      buildDetailReviewDecisionPayload(
        { sync_decision: "approve_only", dataset_mapping_id: "must-not-leak" },
        baseFile,
        [datasetMapping],
      ),
    ).toEqual({
      sync_decision: "approve_only",
      category_id: "cat-1",
      reason: null,
    });
    expect(
      buildDetailReviewDecisionPayload(
        { sync_decision: "sync", dataset_mapping_id: datasetMapping.id, reason: " 已确认 " },
        baseFile,
        [datasetMapping],
      ),
    ).toEqual({
      sync_decision: "sync",
      category_id: datasetMapping.category_id,
      dataset_mapping_id: datasetMapping.id,
      reason: "已确认",
    });
  });

  it("never leaves a failed task without an actionable error message", () => {
    const failedTask = {
      ...taskListResponse.items[0],
      error_message: null,
      logs: [],
    };
    expect(taskFailureMessage(failedTask)).toContain("服务端未提供错误详情");
    expect(
      taskFailureMessage({
        ...failedTask,
        logs: [
          {
            id: 1,
            task_id: failedTask.id,
            status: "failed",
            message: "  消费队列连接中断  ",
            created_at: failedTask.updated_at,
          },
        ],
      }),
    ).toBe("消费队列连接中断");
  });

  it("uses the product title for navigation while preserving the original filename", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...baseFile,
      title: "员工制度与入职指南",
    });

    renderFileDetail();

    expect((await screen.findAllByText("员工制度与入职指南")).length).toBeGreaterThan(0);
    expect(screen.getByText("原始文件名")).toBeInTheDocument();
    expect(screen.getAllByText("员工手册.pdf").length).toBeGreaterThan(0);
  });

  it("rejects SVG and HTML from inline preview metadata and response headers", () => {
    expect(canPreviewInline({ ...baseFile, mime_type: "image/svg+xml" })).toBe(false);
    expect(canPreviewInline({ ...baseFile, mime_type: "text/html" })).toBe(false);

    expect(() =>
      validateInlinePreviewContent({
        blob: new Blob(["<svg><script>alert(1)</script></svg>"], {
          type: "image/svg+xml",
        }),
        contentType: "image/svg+xml",
        contentDisposition: "inline",
        contentLength: 38,
        etag: null,
      }),
    ).toThrow("不在安全预览白名单");
    expect(() =>
      validateInlinePreviewContent({
        blob: new Blob(["<html></html>"], { type: "text/html" }),
        contentType: "text/html; charset=utf-8",
        contentDisposition: "attachment; filename=document.html",
        contentLength: 13,
        etag: null,
      }),
    ).toThrow("附件方式下载");
    expect(canPreviewInline({ ...baseFile, size: 20 * 1024 * 1024 + 1 })).toBe(false);
    expect(() =>
      validateInlinePreviewContent({
        blob: new Blob(["safe"], { type: "application/pdf" }),
        contentType: "application/pdf",
        contentDisposition: "inline",
        contentLength: 20 * 1024 * 1024 + 1,
        etag: null,
      }),
    ).toThrow("超过 20 MiB");
  });

  it("blocks an oversized preview before making a content request", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...baseFile,
      size: INLINE_PREVIEW_MAX_BYTES + 1,
    });

    renderFileDetail();

    expect(await screen.findByText(/请流式下载后查看/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载预览" })).not.toBeInTheDocument();
    expect(getDocumentContent).not.toHaveBeenCalled();
  });

  it("rejects a response Blob over the limit even when headers claim it is small", async () => {
    const safeBlob = new Blob(["%PDF"], { type: "application/pdf" });
    const oversizedBlob = new Blob(["oversized"], { type: "application/pdf" });
    Object.defineProperty(oversizedBlob, "size", {
      configurable: true,
      value: INLINE_PREVIEW_MAX_BYTES + 1,
    });
    vi.mocked(getDocumentContent)
      .mockResolvedValueOnce({
        blob: safeBlob,
        contentType: "application/pdf",
        contentDisposition: "inline",
        contentLength: safeBlob.size,
        etag: null,
      })
      .mockResolvedValueOnce({
        blob: oversizedBlob,
        contentType: "application/pdf",
        contentDisposition: "inline",
        contentLength: 10,
        etag: null,
      });
    vi.mocked(URL.createObjectURL).mockReturnValue("blob:preview-before-oversize");

    renderFileDetail();
    fireEvent.click(await screen.findByRole("button", { name: /加载预览/ }));
    expect(await screen.findByTitle("员工手册.pdf 原件预览")).toHaveAttribute(
      "src",
      "blob:preview-before-oversize",
    );

    fireEvent.click(screen.getByRole("button", { name: /重新加载预览/ }));

    expect(await screen.findByText(/超过 20 MiB.*请流式下载后查看/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTitle("员工手册.pdf 原件预览")).not.toBeInTheDocument();
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-before-oversize");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("sandboxes an allowlisted preview and revokes its object URL on unmount", async () => {
    vi.mocked(getDocument).mockResolvedValue(baseFile);
    vi.mocked(getDocumentContent).mockResolvedValue({
      blob: new Blob(["%PDF"], { type: "application/pdf" }),
      contentType: "application/pdf",
      contentDisposition: "inline",
      contentLength: 4,
      etag: '"preview-etag"',
    });
    vi.mocked(URL.createObjectURL).mockReturnValue("blob:preview-file-1");

    const view = renderFileDetail();
    fireEvent.click(await screen.findByRole("button", { name: /加载预览/ }));

    const frame = await screen.findByTitle("员工手册.pdf 原件预览");
    expect(frame).toHaveAttribute("src", "blob:preview-file-1");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    view.unmount();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-file-1");
  });

  it("revokes the previous document preview when the route id changes", async () => {
    vi.mocked(getDocument).mockImplementation(async (id) => ({
      ...baseFile,
      id,
      original_name: id === "file-1" ? "员工手册.pdf" : "安全规范.pdf",
    }));
    vi.mocked(getDocumentContent).mockResolvedValue({
      blob: new Blob(["%PDF"], { type: "application/pdf" }),
      contentType: "application/pdf",
      contentDisposition: "inline",
      contentLength: 4,
      etag: '"preview-etag"',
    });
    vi.mocked(URL.createObjectURL).mockReturnValue("blob:preview-file-1");

    renderFileDetail(<FileRouteSwitcher />);
    fireEvent.click(await screen.findByRole("button", { name: /加载预览/ }));
    expect(await screen.findByTitle("员工手册.pdf 原件预览")).toHaveAttribute(
      "src",
      "blob:preview-file-1",
    );

    fireEvent.click(screen.getByRole("button", { name: "切换到下一份文件" }));

    expect(await screen.findByRole("heading", { name: "安全规范.pdf" })).toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-file-1");
    expect(screen.queryByTitle("安全规范.pdf 原件预览")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /加载预览/ })).toBeInTheDocument();
  });

  it("revokes the previous object URL when a successful preview is reloaded", async () => {
    vi.mocked(getDocument).mockResolvedValue(baseFile);
    vi.mocked(getDocumentContent).mockResolvedValue({
      blob: new Blob(["%PDF"], { type: "application/pdf" }),
      contentType: "application/pdf",
      contentDisposition: "inline",
      contentLength: 4,
      etag: '"preview-etag"',
    });
    vi.mocked(URL.createObjectURL)
      .mockReturnValueOnce("blob:preview-old")
      .mockReturnValueOnce("blob:preview-latest");

    const view = renderFileDetail();
    fireEvent.click(await screen.findByRole("button", { name: /加载预览/ }));
    expect(await screen.findByTitle("员工手册.pdf 原件预览")).toHaveAttribute(
      "src",
      "blob:preview-old",
    );
    fireEvent.click(screen.getByRole("button", { name: /重新加载预览/ }));

    await waitFor(() => {
      expect(screen.getByTitle("员工手册.pdf 原件预览")).toHaveAttribute(
        "src",
        "blob:preview-latest",
      );
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-old");
    });
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:preview-latest");
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-latest");
  });

  it("renders analysis card with summary, risk tag and extracted text preview", async () => {
    vi.mocked(getDocument).mockResolvedValue(baseFile);

    renderFileDetail();

    expect((await screen.findAllByText("AI 分析")).length).toBeGreaterThan(0);
    expect(screen.getByText("这是一份员工手册的摘要。")).toBeInTheDocument();
    expect(screen.getByText("中风险")).toBeInTheDocument();

    fireEvent.click(screen.getByText("提取文本预览"));
    expect(await screen.findByText("员工手册提取文本前五百字……")).toBeInTheDocument();
  });

  it("renders advanced analysis quality, similarity, table and expiry fields when present", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...baseFile,
      expires_at: "2026-06-20T00:00:00Z",
      expiry_status: "expiring",
      analysis: {
        ...baseFile.analysis!,
        quality_score: 88,
        table_count: 1,
        tables_json: [
          {
            title: "费用明细",
            markdown: "| 项目 | 金额 |\n|---|---:|\n| 培训 | 1000 |",
          },
        ],
        similar_file_ids: ["similar-file-1"],
      },
    });

    renderFileDetail();

    expect(await screen.findByText("88 分")).toBeInTheDocument();
    expect(screen.getAllByText("优秀").length).toBeGreaterThan(0);
    expect(screen.getAllByText("即将过期").length).toBeGreaterThan(0);
    expect(screen.getByText("检测到 1 个相似文档")).toBeInTheDocument();
    expect(screen.getByText("similar-file-1")).toBeInTheDocument();

    fireEvent.click(screen.getByText("费用明细"));
    expect(await screen.findByText(/培训/)).toBeInTheDocument();
  });

  it("renders category and tags card without visibility metadata", async () => {
    vi.mocked(getDocument).mockResolvedValue(baseFile);

    renderFileDetail();

    expect(await screen.findByText("分类与标签")).toBeInTheDocument();
    expect((await screen.findAllByText("制度文档")).length).toBeGreaterThan(0);
    expect(screen.getByText("制度")).toBeInTheDocument();
    expect(screen.getByText("人事")).toBeInTheDocument();
    expect(screen.queryByText("可见范围")).not.toBeInTheDocument();
  });

  it("hides analysis card when there is no analysis record", async () => {
    vi.mocked(getDocument).mockResolvedValue({ ...baseFile, analysis: null });

    renderFileDetail();

    expect(await screen.findByText("同步信息")).toBeInTheDocument();
    expect(screen.queryByText("AI 分析")).toBeNull();
  });

  it("shows analysis failure alert with error message", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...baseFile,
      analysis: {
        status: "failed",
        summary: null,
        sensitive_risk_level: "none",
        quality_score: null,
        extracted_text_preview: null,
        error_message: "模型调用超时",
        finished_at: "2026-06-10T08:05:00Z",
      },
    });

    renderFileDetail();

    expect(await screen.findByText("AI 分析失败")).toBeInTheDocument();
    expect(screen.getByText("模型调用超时")).toBeInTheDocument();
  });

  it("shows sync error alert inside the sync card", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...baseFile,
      sync_error: "RAGFlow 连接失败",
    });

    renderFileDetail();

    expect(await screen.findByText("同步失败原因")).toBeInTheDocument();
    expect(screen.getByText("RAGFlow 连接失败")).toBeInTheDocument();
  });

  it("renders the task timeline for admins filtered by file id", async () => {
    setAdminSession();
    vi.mocked(getDocument).mockResolvedValue(baseFile);
    vi.mocked(listTasks).mockResolvedValue(taskListResponse);

    renderFileDetail();

    expect(await screen.findByText("处理日志")).toBeInTheDocument();
    const processingSidebar = screen.getByRole("complementary", { name: "文件处理侧栏" });
    expect(processingSidebar).toHaveTextContent("分类与标签");
    expect(processingSidebar).toHaveTextContent("同步信息");
    expect(processingSidebar).toHaveTextContent("处理日志");
    await waitFor(() => {
      expect(listTasks).toHaveBeenCalledWith({ file_id: "file-1" });
    });
    expect(await screen.findByText("上传 RAGFlow 超时")).toBeInTheDocument();
    expect(screen.queryByText("其他文件的任务不应展示")).toBeNull();
  });

  it("shows a server failure distinctly and retries the file query in place", async () => {
    vi.mocked(getDocument)
      .mockRejectedValueOnce(new ApiError("temporary", { status: 503 }))
      .mockResolvedValue(baseFile);

    renderFileDetail();

    expect(await screen.findByText("文件服务暂时不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(await screen.findByText("同步信息")).toBeInTheDocument();
    expect(getDocument).toHaveBeenCalledTimes(2);
  });

  it("returns an administrator to the review workbench", async () => {
    setAdminSession();
    renderFileDetail();

    await screen.findByText("同步信息");
    fireEvent.click(screen.getByRole("button", { name: /返回$/ }));
    expect(await screen.findByText("审核工作台落点")).toBeInTheDocument();
  });

  it("supports preview then claim then an explicit approve-only decision", async () => {
    setAdminSession();
    let currentFile: KnowledgeFile = {
      ...baseFile,
      status: "pending_review",
      review_status: "pending",
      claimed_by: null,
      claimed_by_name: null,
      claimed_at: null,
      claim_expires_at: null,
    };
    vi.mocked(getDocument).mockImplementation(async () => currentFile);
    vi.mocked(claimReviewFile).mockImplementation(async () => {
      currentFile = {
        ...currentFile,
        claimed_by: "admin-1",
        claimed_by_name: "部门管理员",
        claimed_at: new Date(Date.now() - 1_000).toISOString(),
        claim_expires_at: new Date(Date.now() + 60_000).toISOString(),
      };
      return currentFile;
    });
    vi.mocked(approveFile).mockImplementation(async (_id, payload) => {
      currentFile = {
        ...currentFile,
        status: "approved",
        review_status: "approved",
        claimed_by: null,
        claimed_by_name: null,
        claimed_at: null,
        claim_expires_at: null,
      };
      expect(payload).toEqual({
        sync_decision: "approve_only",
        category_id: "cat-1",
        reason: null,
      });
      return currentFile;
    });

    renderFileDetail();

    expect(await screen.findByRole("button", { name: /1\. 查看原件与分析/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /领取审核任务$/ }));
    expect(await screen.findByRole("button", { name: /审核通过$/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /审核通过$/ }));

    fireEvent.click(screen.getByRole("button", { name: "确认批准" }));
    expect(await screen.findByText("请明确选择是否进入 RAGFlow")).toBeInTheDocument();
    expect(approveFile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("radio", { name: "仅批准，本次不进入 RAGFlow" }));
    fireEvent.click(screen.getByRole("button", { name: "确认批准" }));
    await waitFor(() => expect(approveFile).toHaveBeenCalledWith("file-1", expect.any(Object)));
    expect(claimReviewFile).toHaveBeenCalledWith("file-1");
  });

  it("allows only the active claimant to reject with a reason", async () => {
    setAdminSession();
    const activeFile: KnowledgeFile = {
      ...baseFile,
      status: "pending_review",
      review_status: "pending",
      claimed_by: "admin-1",
      claimed_by_name: "部门管理员",
      claimed_at: new Date(Date.now() - 1_000).toISOString(),
      claim_expires_at: new Date(Date.now() + 60_000).toISOString(),
    };
    vi.mocked(getDocument).mockResolvedValue(activeFile);
    vi.mocked(rejectFile).mockResolvedValue({
      ...activeFile,
      status: "rejected",
      review_status: "rejected",
    });

    renderFileDetail();

    fireEvent.click(await screen.findByRole("button", { name: /驳回文件$/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "驳回原因" }), {
      target: { value: "内容不符合部门知识规范" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认驳回" }));

    await waitFor(() =>
      expect(rejectFile).toHaveBeenCalledWith("file-1", "内容不符合部门知识规范"),
    );
  });

  it("renders a fallback for blank failed tasks and retries task-query failures", async () => {
    setAdminSession();
    const blankFailure = {
      ...taskListResponse.items[0],
      error_message: null,
      logs: [],
    };
    vi.mocked(listTasks).mockResolvedValueOnce({ items: [blankFailure], total: 1 });

    const firstView = renderFileDetail();
    expect(await screen.findByText(/任务失败（task-1）/)).toBeInTheDocument();
    firstView.unmount();

    vi.mocked(listTasks)
      .mockRejectedValueOnce(new Error("队列服务不可用"))
      .mockResolvedValue({ items: [], total: 0 });
    renderFileDetail();
    const taskErrorTitle = await screen.findByText("处理日志加载失败");
    expect(taskErrorTitle).toBeInTheDocument();
    expect(screen.getByText("队列服务不可用")).toBeInTheDocument();
    const retryButton = taskErrorTitle.closest("[role='alert']")?.querySelector("button");
    if (!retryButton) {
      throw new Error("处理日志错误未提供重试按钮");
    }
    fireEvent.click(retryButton);
    expect(await screen.findByText("暂无任务记录")).toBeInTheDocument();
  });
  it("does not request tasks for employees", async () => {
    vi.mocked(getDocument).mockResolvedValue(baseFile);

    renderFileDetail();

    expect(await screen.findByText("同步信息")).toBeInTheDocument();
    expect(listTasks).not.toHaveBeenCalled();
    expect(screen.queryByText("处理日志")).toBeNull();
  });
});
