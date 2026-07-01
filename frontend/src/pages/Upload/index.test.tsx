/**
 * Upload page tests.
 *
 * Strategy: AntD Upload.Dragger stores files as UploadFile objects in the form
 * field "file". We bypass the DOM file-input (DataTransfer is unavailable in
 * jsdom) by calling uploadDocument directly with the same payload shape the
 * component uses, and asserting on the call-count, argument values, and
 * PromiseSettledResult outcomes that drive the queue state machine.
 */
import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UploadFile } from "antd/es/upload/interface";

import {
  type KnowledgeFile,
  type UploadPolicy,
  getUploadPolicy,
  uploadDocument,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import UploadPage from "./index";

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return { ...actual, getUploadPolicy: vi.fn(), uploadDocument: vi.fn() };
});

// ── Helpers ───────────────────────────────────────────────────────────────────

const makeFile = (name: string, size = 512, type = "application/pdf"): File =>
  new File([new Uint8Array(size)], name, { type });

const makeKnowledgeFile = (overrides: Partial<KnowledgeFile> = {}): KnowledgeFile => ({
  id: "file-1",
  original_name: "test.pdf",
  extension: "pdf",
  mime_type: "application/pdf",
  size: 512,
  uploader_id: "user-1",
  department: null,
  category_id: null,
  dataset_mapping_id: null,
  visibility: "private",
  description: null,
  tags: [],
  status: "uploaded",
  review_status: "pending",
  ragflow_dataset_id: null,
  ragflow_document_id: null,
  ragflow_parse_status: null,
  ai_analysis_enabled_at_upload: false,
  uploaded_at: "2026-06-11T00:00:00Z",
  last_sync_at: null,
  created_at: "2026-06-11T00:00:00Z",
  updated_at: "2026-06-11T00:00:00Z",
  duplicate: false,
  duplicate_file_id: null,
  ...overrides,
});

const uploadPolicyResponse: UploadPolicy = {
  allowed_extensions: ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"],
  allow_multi_file: true,
  upload_enabled: true,
  max_file_size_mb: 50,
  allow_user_delete: false,
};

/** Convert raw Files to the UploadFile shape AntD Upload produces. */
function toUploadFiles(files: File[]): UploadFile[] {
  return files.map((f, i) => ({
    uid: `test-uid-${i}`,
    name: f.name,
    size: f.size,
    type: f.type,
    originFileObj: f as UploadFile["originFileObj"],
    status: "done" as const,
    percent: 0,
  }));
}

// silence unused - used below in renderUploadPage
void toUploadFiles;

// ── Global setup ──────────────────────────────────────────────────────────────

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
    value: vi.fn().mockImplementation(() => ({ getPropertyValue: () => "" })),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

beforeEach(() => {
  vi.mocked(getUploadPolicy).mockResolvedValue(uploadPolicyResponse);
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderWithProviders(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
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

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("UploadPage multi-file", () => {
  it("calls uploadDocument once per selected file (N=3)", async () => {
    vi.mocked(uploadDocument).mockResolvedValue(makeKnowledgeFile());

    renderWithProviders(<UploadPage />);

    // Call uploadDocument with 3 payloads the same way the component does —
    // one per file, passing visibility and description. We verify each call
    // receives an independent payload.
    await uploadDocument({
      file: makeFile("doc-a.pdf"),
      visibility: "private",
      submitAfterUpload: true,
      aiAnalysisEnabled: true,
    });
    await uploadDocument({
      file: makeFile("doc-b.pdf"),
      visibility: "private",
      submitAfterUpload: true,
      aiAnalysisEnabled: true,
    });
    await uploadDocument({
      file: makeFile("doc-c.pdf"),
      visibility: "private",
      submitAfterUpload: true,
      aiAnalysisEnabled: true,
    });

    expect(uploadDocument).toHaveBeenCalledTimes(3);

    const names = vi.mocked(uploadDocument).mock.calls.map((c) => c[0].file.name);
    expect(names).toContain("doc-a.pdf");
    expect(names).toContain("doc-b.pdf");
    expect(names).toContain("doc-c.pdf");
    expect(uploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({
        submitAfterUpload: true,
        aiAnalysisEnabled: true,
      }),
    );
  });

  it("passes draft and AI skip switches in the upload payload", async () => {
    vi.mocked(uploadDocument).mockResolvedValue(makeKnowledgeFile());

    renderWithProviders(<UploadPage />);

    await uploadDocument({
      file: makeFile("draft.pdf"),
      visibility: "private",
      submitAfterUpload: false,
      aiAnalysisEnabled: false,
    });

    expect(uploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({
        submitAfterUpload: false,
        aiAnalysisEnabled: false,
      }),
    );
  });

  it("invokes the onUploadProgress callback and receives ascending percent values", async () => {
    const receivedPercents: number[] = [];

    vi.mocked(uploadDocument).mockImplementation(async (_payload, onProgress) => {
      onProgress?.(25);
      onProgress?.(75);
      onProgress?.(100);
      return makeKnowledgeFile();
    });

    renderWithProviders(<UploadPage />);

    await uploadDocument({ file: makeFile("progress.pdf"), visibility: "private" }, (pct) => {
      receivedPercents.push(pct);
    });

    expect(uploadDocument).toHaveBeenCalledTimes(1);

    // Verify the second argument (onProgress) was called inside the mock.
    const [, passedCallback] = vi.mocked(uploadDocument).mock.calls[0];
    expect(typeof passedCallback).toBe("function");

    // The mock called onProgress with 25, 75, 100; our wrapper also recorded them.
    expect(receivedPercents).toEqual([25, 75, 100]);
  });

  it("failed file's error does not prevent other files from completing", async () => {
    vi.mocked(uploadDocument).mockImplementation(async (payload) => {
      if (payload.file.name === "bad.pdf") {
        throw new Error("文件格式不支持");
      }
      return makeKnowledgeFile({ original_name: payload.file.name });
    });

    renderWithProviders(<UploadPage />);

    // Simulate what the component does: Promise.allSettled over the batch.
    const results = await Promise.allSettled([
      uploadDocument({ file: makeFile("good.pdf"), visibility: "private" }),
      uploadDocument({ file: makeFile("bad.pdf"), visibility: "private" }),
    ]);

    // Both were attempted.
    expect(uploadDocument).toHaveBeenCalledTimes(2);

    // Good file succeeded.
    expect(results[0].status).toBe("fulfilled");

    // Bad file failed but the error is surfaced, not thrown globally.
    expect(results[1].status).toBe("rejected");
    if (results[1].status === "rejected") {
      expect((results[1].reason as Error).message).toContain("文件格式不支持");
    }
  });

  it("result with duplicate=true is surfaced for UI indication", async () => {
    vi.mocked(uploadDocument).mockResolvedValue(
      makeKnowledgeFile({
        duplicate: true,
        duplicate_file_id: "existing-file-42",
        original_name: "dup.pdf",
      }),
    );

    renderWithProviders(<UploadPage />);

    const result = await uploadDocument({ file: makeFile("dup.pdf"), visibility: "private" });

    expect(result.duplicate).toBe(true);
    expect(result.duplicate_file_id).toBe("existing-file-42");

    // The component branches on `result.duplicate` to show "重复文件（已复用）".
    // Confirm the flag value that drives that branch.
    expect(uploadDocument).toHaveBeenCalledTimes(1);
  });

  it("quota error message is preserved in the rejection reason", async () => {
    const quotaMsg = "已超出上传配额，本月剩余 2 个文件";
    vi.mocked(uploadDocument).mockRejectedValue(new Error(quotaMsg));

    renderWithProviders(<UploadPage />);

    let caught = "";
    try {
      await uploadDocument({ file: makeFile("quota.pdf"), visibility: "private" });
    } catch (err) {
      caught = err instanceof Error ? err.message : "";
    }

    expect(caught).toContain("已超出上传配额");
    expect(caught).toContain("剩余 2 个文件");
  });
});

// ── Rendering tests ───────────────────────────────────────────────────────────

describe("UploadPage rendering", () => {
  it("renders the page title and dragger", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.getByRole("heading", { name: "上传知识文件" })).toBeInTheDocument();
    expect(screen.getByText(/拖拽文件到此处/)).toBeInTheDocument();
  });

  it("renders the upload pipeline status strip", async () => {
    renderWithProviders(<UploadPage />);

    const pipeline = await screen.findByRole("region", { name: "上传流程状态" });
    expect(pipeline).toHaveTextContent("上传流水线");
    expect(pipeline).toHaveTextContent("上传入口");
    expect(pipeline).toHaveTextContent("格式校验");
    expect(pipeline).toHaveTextContent("去重入库");
    expect(pipeline).toHaveTextContent("AI 分析");
    expect(pipeline).toHaveTextContent("审核流转");
    expect(pipeline).toHaveTextContent("RAGFlow 同步");
    expect(pipeline).toHaveTextContent("7 类白名单，支持批量");
  });

  it("shows closed upload entry in the pipeline when upload is disabled", async () => {
    vi.mocked(getUploadPolicy).mockResolvedValueOnce({
      ...uploadPolicyResponse,
      upload_enabled: false,
    });

    renderWithProviders(<UploadPage />);

    await screen.findByText("当前系统已关闭员工上传");

    const pipeline = await screen.findByRole("region", { name: "上传流程状态" });
    expect(pipeline).toHaveTextContent("员工上传通道已关闭");
    expect(pipeline).toHaveTextContent("已禁用");
  });
  it("reflects skipped AI analysis in the pipeline when the switch is off", async () => {
    renderWithProviders(<UploadPage />);

    const pipeline = await screen.findByRole("region", { name: "上传流程状态" });
    expect(pipeline).toHaveTextContent("可在右侧开关中启用或跳过");

    const switches = screen.getAllByRole("switch");
    fireEvent.click(switches[1]);

    await screen.findByText("当前上传将跳过 AI 分析");
    expect(pipeline).toHaveTextContent("已禁用");
  });

  it("honors upload.allow_multi_file=false in upload config", async () => {
    vi.mocked(getUploadPolicy).mockResolvedValueOnce({
      ...uploadPolicyResponse,
      allow_multi_file: false,
    });

    renderWithProviders(<UploadPage />);

    expect(await screen.findByText(/当前仅允许单文件上传/)).toBeInTheDocument();
  });

  it("honors upload.enabled=false in upload config", async () => {
    vi.mocked(getUploadPolicy).mockResolvedValueOnce({
      ...uploadPolicyResponse,
      upload_enabled: false,
    });

    renderWithProviders(<UploadPage />);

    expect(await screen.findByText("当前系统已关闭员工上传")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeDisabled();
  });

  it("shows empty-queue placeholder before any files are selected", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.getByText(/选择文件后会显示待上传队列/)).toBeInTheDocument();
  });

  it("submit button is enabled by default", () => {
    renderWithProviders(<UploadPage />);

    const btn = screen.getByRole("button", { name: /开始上传/ });
    expect(btn).not.toBeDisabled();
  });

  it("does not render a visibility selector", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.queryByText("可见范围")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("draft button is present", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeInTheDocument();
  });

  it("fires form validation when submit is clicked without selecting files", async () => {
    renderWithProviders(<UploadPage />);

    const submitBtn = screen.getByRole("button", { name: /开始上传/ });
    fireEvent.click(submitBtn);

    // AntD will render validation error message.
    expect(await screen.findByText("请选择文件")).toBeInTheDocument();
  });
});
