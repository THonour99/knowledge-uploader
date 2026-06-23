import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type KnowledgeFile,
  type SyncTaskListResponse,
  getDocument,
  listTasks,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import FileDetailPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getDocument: vi.fn(),
    listTasks: vi.fn(),
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

function renderFileDetail(node: ReactNode = <FileDetailPage />) {
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
            <MemoryRouter initialEntries={["/files/file-1"]}>
              <Routes>
                <Route path="/files/:id" element={node} />
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

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

describe("FileDetailPage", () => {
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
    expect(screen.getByText("优秀")).toBeInTheDocument();
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
    expect(await screen.findByText("制度文档")).toBeInTheDocument();
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
    await waitFor(() => {
      expect(listTasks).toHaveBeenCalledWith({ file_id: "file-1" });
    });
    expect(await screen.findByText("上传 RAGFlow 超时")).toBeInTheDocument();
    expect(screen.queryByText("其他文件的任务不应展示")).toBeNull();
  });

  it("does not request tasks for employees", async () => {
    vi.mocked(getDocument).mockResolvedValue(baseFile);

    renderFileDetail();

    expect(await screen.findByText("同步信息")).toBeInTheDocument();
    expect(listTasks).not.toHaveBeenCalled();
    expect(screen.queryByText("处理日志")).toBeNull();
  });
});
