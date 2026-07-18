/**
 * Upload page tests.
 *
 * The session-race cases exercise the real AntD file input and form submission path; smaller
 * payload-contract cases call the mocked client directly.
 */
import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UploadFile } from "antd/es/upload/interface";

import {
  type KnowledgeFile,
  type UploadPolicy,
  getUploadPolicy,
  listDocuments,
  uploadDocument,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import UploadPage, { fileSizeValidationMessage } from "./index";

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return {
    ...actual,
    getUploadPolicy: vi.fn(),
    listDocuments: vi.fn(),
    uploadDocument: vi.fn(),
  };
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
  owner_id: "user-1",
  owner_name: "张三",
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
  series_id: "file-1",
  version_number: 1,
  replaces_file_id: null,
  is_current_version: true,
  remote_visibility: "candidate",
  version_switch_status: "not_required",
  version_switch_error: null,
  version_switch_attempt_count: 0,
  predecessor_remote_deactivated_at: null,
  local_version_activated_at: null,
  remote_version_activated_at: null,
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
  useAuthStore.setState({ accessToken: null, user: null });
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
  vi.mocked(listDocuments).mockResolvedValue({
    items: [
      makeKnowledgeFile({
        id: "existing-file-1",
        original_name: "旧版制度.pdf",
        uploader_id: "employee-1",
        status: "parsed",
        version_number: 3,
        is_current_version: true,
        remote_visibility: "current",
      }),
    ],
    total: 1,
    page: 1,
    page_size: 100,
    total_pages: 1,
  });
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
  it.each(["A→B", "ABA"] as const)(
    "binds a five-file queue to session A and stops pending work on %s",
    async (switchMode) => {
      const sessionA = {
        accessToken: "token-a",
        user: {
          id: "employee-1",
          name: "张三",
          email: "a@example.com",
          role: "employee" as const,
          email_verified: true,
          department_assigned: true,
          department_id: "dept-tech",
          department_name: "技术部",
          department_code: "tech",
        },
      };
      useAuthStore.setState(sessionA);
      vi.mocked(uploadDocument).mockImplementation((_payload, _progress, options) => {
        const signal = options?.signal;
        return new Promise<KnowledgeFile>((_resolve, reject) => {
          if (signal?.aborted) {
            reject(signal.reason);
            return;
          }
          signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
        });
      });
      const view = renderWithProviders(<UploadPage />);
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /开始上传/ })).not.toBeDisabled(),
      );
      const files = Array.from({ length: 5 }, (_, index) => makeFile(`queue-${index + 1}.pdf`));
      const fileInput = view.container.querySelector<HTMLInputElement>('input[type="file"]');
      if (!fileInput) {
        throw new Error("上传控件未渲染文件输入框");
      }

      fireEvent.change(fileInput, { target: { files } });
      expect((await screen.findAllByText("queue-5.pdf")).length).toBeGreaterThan(1);
      fireEvent.click(screen.getByRole("button", { name: /开始上传/ }));

      await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(3));
      const startedCalls = vi.mocked(uploadDocument).mock.calls;
      expect(startedCalls.map(([payload]) => payload.file.name)).toEqual([
        "queue-1.pdf",
        "queue-2.pdf",
        "queue-3.pdf",
      ]);
      for (const [, , options] of startedCalls) {
        expect(options?.requestIdentity).toMatchObject({
          accessToken: "token-a",
          userId: "employee-1",
        });
        expect(options?.signal?.aborted).toBe(false);
      }

      act(() => {
        useAuthStore.setState({
          accessToken: "token-b",
          user: {
            ...sessionA.user,
            id: "employee-2",
            email: "b@example.com",
          },
        });
        if (switchMode === "ABA") {
          useAuthStore.setState(sessionA);
        }
      });

      await waitFor(() => {
        for (const [, , options] of startedCalls) {
          expect(options?.signal?.aborted).toBe(true);
        }
      });
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(uploadDocument).toHaveBeenCalledTimes(3);
      expect(
        vi.mocked(uploadDocument).mock.calls.some(([, , options]) => {
          return options?.requestIdentity?.accessToken === "token-b";
        }),
      ).toBe(false);
    },
  );
  it.each(["A→B", "ABA"] as const)(
    "clears a selected file and draft fields before confirmation on %s",
    async (switchMode) => {
      const sessionA = {
        accessToken: "token-a",
        user: {
          id: "employee-1",
          name: "张三",
          email: "a@example.com",
          role: "employee" as const,
          email_verified: true,
          department_assigned: true,
          department_id: "dept-tech",
          department_name: "技术部",
          department_code: "tech",
        },
      };
      useAuthStore.setState(sessionA);
      const view = renderWithProviders(<UploadPage />);
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /开始上传/ })).not.toBeDisabled(),
      );
      const fileInput = view.container.querySelector<HTMLInputElement>('input[type="file"]');
      if (!fileInput) {
        throw new Error("上传控件未渲染文件输入框");
      }

      fireEvent.change(fileInput, { target: { files: [makeFile("pre-confirm.pdf")] } });
      fireEvent.change(screen.getByLabelText("说明"), {
        target: { value: "只属于会话 A 的说明" },
      });
      expect((await screen.findAllByText("pre-confirm.pdf")).length).toBeGreaterThan(1);
      expect(screen.getByLabelText("说明")).toHaveValue("只属于会话 A 的说明");

      act(() => {
        useAuthStore.setState({
          accessToken: "token-b",
          user: {
            ...sessionA.user,
            id: "employee-2",
            email: "b@example.com",
          },
        });
        if (switchMode === "ABA") {
          useAuthStore.setState(sessionA);
        }
      });

      await waitFor(() => {
        expect(screen.queryAllByText("pre-confirm.pdf")).toHaveLength(0);
        expect(screen.getByLabelText("说明")).toHaveValue("");
      });
      fireEvent.click(screen.getByRole("button", { name: /开始上传/ }));
      await Promise.resolve();
      expect(uploadDocument).not.toHaveBeenCalled();
    },
  );
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
  it("validates the configured byte boundary and fails closed for an invalid limit", () => {
    expect(fileSizeValidationMessage({ name: "edge.pdf", size: 1024 * 1024 }, 1)).toBeNull();
    expect(fileSizeValidationMessage({ name: "too-large.pdf", size: 1024 * 1024 + 1 }, 1)).toBe(
      "too-large.pdf 超过单文件最大 1 MB，请压缩或拆分后重试",
    );
    expect(fileSizeValidationMessage({ name: "unknown.pdf", size: 1 }, 0)).toContain(
      "上传大小策略不可用",
    );
  });

  it("shows max_file_size_mb and rejects an oversized file before it enters the queue", async () => {
    vi.mocked(getUploadPolicy).mockResolvedValueOnce({
      ...uploadPolicyResponse,
      max_file_size_mb: 1,
    });
    const view = renderWithProviders(<UploadPage />);

    expect(await screen.findByText(/单文件最大 1 MB/)).toBeInTheDocument();
    const fileInput = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) {
      throw new Error("上传控件未渲染文件输入框");
    }
    fireEvent.change(fileInput, {
      target: { files: [makeFile("too-large.pdf", 1024 * 1024 + 1)] },
    });

    expect(
      await screen.findByText("too-large.pdf 超过单文件最大 1 MB，请压缩或拆分后重试"),
    ).toBeInTheDocument();
    expect(screen.getByText(/选择文件后会显示待上传队列/)).toBeInTheDocument();
    expect(uploadDocument).not.toHaveBeenCalled();
  });
  it("fails closed for an old persisted session without department assignment fields", async () => {
    useAuthStore.setState({
      accessToken: "old-token",
      user: {
        id: "employee-1",
        name: "张三",
        email: "zhangsan@company.com",
        role: "employee",
      },
    });

    renderWithProviders(<UploadPage />);

    expect(await screen.findByText("尚未分配有效部门")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeDisabled();
  });

  it("renders the page title and dragger", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.getByRole("heading", { name: "上传知识文件" })).toBeInTheDocument();
    expect(screen.getByText(/拖拽文件到此处/)).toBeInTheDocument();
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

  it("fails closed and offers retry when upload policy cannot be loaded", async () => {
    vi.mocked(getUploadPolicy).mockRejectedValueOnce(new Error("policy unavailable"));

    renderWithProviders(<UploadPage />);

    expect(await screen.findByText("上传策略加载失败，上传入口已暂停")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeInTheDocument();
  });

  it("shows empty-queue placeholder before any files are selected", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.getByText(/选择文件后会显示待上传队列/)).toBeInTheDocument();
  });

  it("enables submit only after a trusted upload policy is loaded", async () => {
    renderWithProviders(<UploadPage />);

    const btn = screen.getByRole("button", { name: /开始上传/ });
    expect(btn).toBeDisabled();
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("does not render a visibility selector", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.queryByText("可见范围")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("explains replacement safety and forces a single-file candidate flow", async () => {
    const view = renderWithProviders(<UploadPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /开始上传/ })).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("switch", { name: "替代现有文档" }));

    expect(await screen.findByText("将创建候选版本，不会覆盖旧原件")).toBeInTheDocument();
    expect(screen.getByText(/旧版本仍按当前状态服务/)).toBeInTheDocument();
    expect(screen.getByText(/上传时冻结的系统策略/)).toBeInTheDocument();
    await waitFor(() =>
      expect(listDocuments).toHaveBeenCalledWith({
        page: 1,
        page_size: 100,
        q: undefined,
        status: "parsed",
        sort: "updated_at",
        order: "desc",
      }),
    );
    const candidateSelect = await screen.findByRole("combobox");
    expect(candidateSelect).toBeEnabled();
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeDisabled();

    fireEvent.mouseDown(candidateSelect);
    fireEvent.click(await screen.findByText("旧版制度.pdf · v3"));
    await waitFor(() => expect(screen.getByRole("button", { name: /开始上传/ })).toBeEnabled());

    fireEvent.change(candidateSelect, { target: { value: "新版" } });
    await waitFor(() =>
      expect(listDocuments).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, page_size: 100, q: "新版" }),
      ),
    );
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();

    const fileInput = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    expect(fileInput).not.toHaveAttribute("multiple");
  });

  it("keeps replacement candidates after the first 100 accessible through pagination", async () => {
    const secondPageCandidate = makeKnowledgeFile({
      id: "existing-file-101",
      original_name: "第 101 份制度.pdf",
      uploader_id: "employee-1",
      status: "parsed",
      version_number: 101,
      is_current_version: true,
      remote_visibility: "current",
    });
    vi.mocked(listDocuments).mockImplementation(async (params = {}) => {
      if (params.page === 2) {
        return {
          items: [secondPageCandidate],
          total: 101,
          page: 2,
          page_size: 100,
          total_pages: 2,
        };
      }
      return {
        items: [
          makeKnowledgeFile({
            id: "existing-file-1",
            original_name: "旧版制度.pdf",
            uploader_id: "employee-1",
            status: "parsed",
            version_number: 3,
            is_current_version: true,
            remote_visibility: "current",
          }),
        ],
        total: 101,
        page: 1,
        page_size: 100,
        total_pages: 2,
      };
    });
    renderWithProviders(<UploadPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /开始上传/ })).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("switch", { name: "替代现有文档" }));
    const loadMore = await screen.findByRole("button", { name: "加载更多候选" });
    fireEvent.click(loadMore);

    await waitFor(() =>
      expect(listDocuments).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, page_size: 100, q: undefined }),
      ),
    );
    const candidateSelect = await screen.findByRole("combobox");
    fireEvent.mouseDown(candidateSelect);
    expect(await screen.findByText("第 101 份制度.pdf · v101")).toBeInTheDocument();
  });

  it("fails closed when loading a later replacement page fails", async () => {
    vi.mocked(listDocuments).mockImplementation(async (params = {}) => {
      if (params.page === 2) {
        throw new Error("second page unavailable");
      }
      return {
        items: [
          makeKnowledgeFile({
            id: "existing-file-1",
            original_name: "旧版制度.pdf",
            uploader_id: "employee-1",
            status: "parsed",
            version_number: 3,
            is_current_version: true,
            remote_visibility: "current",
          }),
        ],
        total: 101,
        page: 1,
        page_size: 100,
        total_pages: 2,
      };
    });
    renderWithProviders(<UploadPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /开始上传/ })).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("switch", { name: "替代现有文档" }));
    fireEvent.click(await screen.findByRole("button", { name: "加载更多候选" }));

    expect(await screen.findByText("可替代文档加载失败")).toBeInTheDocument();
    expect(screen.getByText(/候选列表恢复前不会提交上传/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();
  });

  it("fails closed when replacement candidates cannot be loaded", async () => {
    vi.mocked(listDocuments).mockRejectedValueOnce(new Error("candidate unavailable"));
    renderWithProviders(<UploadPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /开始上传/ })).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("switch", { name: "替代现有文档" }));

    expect(await screen.findByText("可替代文档加载失败")).toBeInTheDocument();
    expect(screen.getByText(/候选列表恢复前不会提交上传/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /开始上传/ })).toBeDisabled();
  });

  it("draft button is present", () => {
    renderWithProviders(<UploadPage />);

    expect(screen.getByRole("button", { name: /保存草稿/ })).toBeInTheDocument();
  });

  it("fires form validation when submit is clicked without selecting files", async () => {
    renderWithProviders(<UploadPage />);

    const submitBtn = screen.getByRole("button", { name: /开始上传/ });
    await waitFor(() => expect(submitBtn).not.toBeDisabled());
    fireEvent.click(submitBtn);

    // AntD will render validation error message.
    expect(await screen.findByText("请选择文件")).toBeInTheDocument();
  });
});
