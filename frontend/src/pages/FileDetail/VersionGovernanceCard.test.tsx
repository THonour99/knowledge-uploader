import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import dayjs from "dayjs";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type KnowledgeFile,
  type SyncTask,
  listDocumentOwnerOptions,
  reconcileVersionSwitchTask,
  retryTask,
  updateDocumentDraft,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import {
  VersionGovernanceCard,
  canEditDocumentGovernance,
  findReconcileableVersionSwitchTask,
  findRetryableVersionSwitchTask,
  isVersionSwitchFailure,
  versionSwitchReconcileErrorMessage,
} from "./VersionGovernanceCard";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return {
    ...actual,
    listDocumentOwnerOptions: vi.fn(),
    reconcileVersionSwitchTask: vi.fn(),
    retryTask: vi.fn(),
    updateDocumentDraft: vi.fn(),
  };
});

const baseFile: KnowledgeFile = {
  id: "file-v2",
  original_name: "新版制度.pdf",
  title: "新版制度",
  extension: "pdf",
  mime_type: "application/pdf",
  size: 2048,
  uploader_id: "employee-1",
  uploader_name: "张三",
  owner_id: "employee-1",
  owner_name: "张三",
  department_id: "dept-1",
  department_name: "技术部",
  department_code: "tech",
  department: "技术部",
  category_id: null,
  dataset_mapping_id: null,
  visibility: "department",
  description: null,
  tags: [],
  status: "uploaded",
  review_status: "pending",
  review_version: 2,
  ragflow_dataset_id: "dataset-1",
  ragflow_document_id: null,
  ragflow_parse_status: null,
  ai_analysis_enabled_at_upload: true,
  series_id: "file-v1",
  version_number: 2,
  replaces_file_id: "file-v1",
  is_current_version: false,
  remote_visibility: "candidate",
  version_switch_status: "pending",
  version_switch_error: null,
  version_switch_attempt_count: 0,
  predecessor_remote_deactivated_at: null,
  local_version_activated_at: null,
  remote_version_activated_at: null,
  uploaded_at: "2026-07-17T08:00:00Z",
  expires_at: null,
  expiry_status: "never",
  last_sync_at: null,
  created_at: "2026-07-17T08:00:00Z",
  updated_at: "2026-07-17T08:00:00Z",
  duplicate: false,
  duplicate_file_id: null,
  version_chain: [
    {
      id: "file-v2",
      version_number: 2,
      replaces_file_id: "file-v1",
      title: "新版制度",
      status: "uploaded",
      is_current_version: false,
      remote_visibility: "candidate",
      version_switch_status: "pending",
      version_switch_error: null,
      created_at: "2026-07-17T08:00:00Z",
    },
    {
      id: "file-v1",
      version_number: 1,
      replaces_file_id: null,
      title: "旧版制度",
      status: "parsed",
      is_current_version: true,
      remote_visibility: "current",
      version_switch_status: "not_required",
      version_switch_error: null,
      created_at: "2026-07-01T08:00:00Z",
    },
  ],
};

const failedTask: SyncTask = {
  id: "task-latest",
  file_id: "file-v2",
  task_type: "ragflow_upload",
  status: "failed",
  retry_count: 1,
  max_retry_count: 3,
  error_message: "safe failure",
  started_at: "2026-07-17T09:00:00Z",
  finished_at: "2026-07-17T09:01:00Z",
  created_at: "2026-07-17T09:00:00Z",
  updated_at: "2026-07-17T09:01:00Z",
  logs: [],
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
    value: vi.fn().mockImplementation(() => ({ getPropertyValue: () => "" })),
  });
});

function renderCard(
  node: ReactNode,
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
) {
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
  useAuthStore.setState({
    accessToken: "token",
    user: {
      id: "employee-1",
      name: "张三",
      email: "employee@example.com",
      role: "employee",
      department_assigned: true,
      department_id: "dept-1",
      department_name: "技术部",
      department_code: "tech",
    },
  });
  vi.mocked(listDocumentOwnerOptions)
    .mockReset()
    .mockResolvedValue({
      items: [
        { id: "employee-1", name: "张三" },
        { id: "employee-2", name: "李四" },
      ],
      total: 2,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
  vi.mocked(updateDocumentDraft).mockReset().mockResolvedValue(baseFile);
  vi.mocked(retryTask).mockReset().mockResolvedValue(failedTask);
  vi.mocked(reconcileVersionSwitchTask).mockReset().mockResolvedValue(failedTask);
});

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

describe("VersionGovernanceCard", () => {
  it("retries only one unambiguous failure for the exact file", () => {
    const secondRetryable = {
      ...failedTask,
      id: "task-second",
      updated_at: "2026-07-17T08:00:00Z",
    };
    const exhausted = {
      ...failedTask,
      id: "task-exhausted",
      retry_count: 3,
      max_retry_count: 3,
      updated_at: "2026-07-17T10:00:00Z",
    };
    const parseFailure = { ...failedTask, id: "task-parse", task_type: "ragflow_parse" };
    const otherFileFailure = { ...failedTask, id: "task-other", file_id: "file-other" };

    expect(
      findRetryableVersionSwitchTask(
        [exhausted, parseFailure, otherFileFailure, failedTask],
        "file-v2",
      )?.id,
    ).toBe("task-latest");
    expect(findRetryableVersionSwitchTask([secondRetryable, failedTask], "file-v2")).toBeNull();
    expect(isVersionSwitchFailure("failed_new_activate")).toBe(true);
    expect(isVersionSwitchFailure("completed")).toBe(false);
    expect(canEditDocumentGovernance(baseFile, "employee-1")).toBe(true);
    expect(
      canEditDocumentGovernance({ ...baseFile, status: "sensitive_review_required" }, "employee-1"),
    ).toBe(true);
    expect(canEditDocumentGovernance(baseFile, "employee-2")).toBe(false);
  });

  it("allows manual reconciliation only for one exhausted or canceled incomplete switch task", () => {
    const exhausted = {
      ...failedTask,
      id: "task-exhausted",
      retry_count: 3,
      max_retry_count: 3,
    };
    const canceled = { ...failedTask, id: "task-canceled", status: "canceled" };

    expect(findReconcileableVersionSwitchTask([exhausted], baseFile)?.id).toBe("task-exhausted");
    expect(findReconcileableVersionSwitchTask([canceled], baseFile)?.id).toBe("task-canceled");
    expect(
      findReconcileableVersionSwitchTask([exhausted], {
        ...baseFile,
        version_switch_status: "completed",
      }),
    ).toBeNull();
    expect(findReconcileableVersionSwitchTask([exhausted, canceled], baseFile)).toBeNull();
    expect(findReconcileableVersionSwitchTask([failedTask], baseFile)).toBeNull();
  });

  it("maps manual reconciliation 404 and 409 responses to actionable messages", () => {
    expect(versionSwitchReconcileErrorMessage(new ApiError("missing", { status: 404 }))).toContain(
      "不存在",
    );
    expect(versionSwitchReconcileErrorMessage(new ApiError("conflict", { status: 409 }))).toContain(
      "状态已变化",
    );
  });

  it("hides exhausted-task reconciliation from an employee even when the prop is misconfigured", async () => {
    renderCard(
      <VersionGovernanceCard
        file={{ ...baseFile, version_switch_status: "failed_new_activate" }}
        tasks={[{ ...failedTask, retry_count: 3, max_retry_count: 3 }]}
        isAdmin
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(await screen.findByText("新版本远端激活失败")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "人工协调版本切换" })).not.toBeInTheDocument();
  });

  it("confirms an administrator reconciliation with a trimmed reason and exact cache invalidations", async () => {
    useAuthStore.setState({
      accessToken: "admin-token",
      user: {
        id: "admin-1",
        name: "部门管理员",
        email: "admin@example.com",
        role: "dept_admin",
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue(undefined);
    renderCard(
      <VersionGovernanceCard
        file={{ ...baseFile, version_switch_status: "failed_new_activate" }}
        tasks={[{ ...failedTask, retry_count: 3, max_retry_count: 3 }]}
        isAdmin
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
      queryClient,
    );

    fireEvent.click(await screen.findByRole("button", { name: "人工协调版本切换" }));
    const confirm = screen.getByRole("button", { name: "确认按远端事实协调" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "人工协调原因" }), {
      target: { value: "  已核对远端仅新版本可见  " },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => {
      expect(reconcileVersionSwitchTask).toHaveBeenCalledWith("task-latest", {
        reason: "已核对远端仅新版本可见",
      });
    });
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(4));
    expect(invalidate.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: ["tasks", { file_id: "file-v2" }] },
      { queryKey: ["documents", { file_id: "file-v2" }] },
      { queryKey: ["documents", "uploaded"] },
      { queryKey: ["documents", "responsible"] },
    ]);
    expect(await screen.findByText("版本切换已按当前远端事实完成人工协调")).toBeInTheDocument();
  });

  it("treats an unconfirmed old-version deactivation as potentially still visible", async () => {
    renderCard(
      <VersionGovernanceCard
        file={{ ...baseFile, version_switch_status: "failed_old_deactivate" }}
        tasks={[failedTask]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(await screen.findByText("旧版本停用结果未确认")).toBeInTheDocument();
    expect(screen.getByText("旧版本停用未确认")).toBeInTheDocument();
    expect(
      screen.getByText(
        "系统未确认旧远端版本已停用，应按仍可能可见处理。请先核对处理日志，再决定是否重试。",
      ),
    ).toBeInTheDocument();
  });

  it.each([
    ["delete", "旧远端删除"],
    ["archive", "旧远端保留并标记非当前"],
  ] as const)(
    "displays the frozen %s replacement policy from the document contract",
    async (action, label) => {
      const versionChain = baseFile.version_chain?.map((item, index) =>
        index === 0 ? { ...item, replacement_remote_action: action } : item,
      );

      renderCard(
        <VersionGovernanceCard
          file={{ ...baseFile, replacement_remote_action: action, version_chain: versionChain }}
          tasks={[]}
          isAdmin={false}
          onOpenVersion={vi.fn()}
          onRefresh={vi.fn().mockResolvedValue(undefined)}
        />,
      );

      expect(await screen.findByText(label)).toBeInTheDocument();
      expect(
        screen.getByText(
          (_content, element) =>
            element?.tagName === "SPAN" && element.textContent === `冻结策略：${label}`,
        ),
      ).toBeInTheDocument();
    },
  );

  it("surfaces unavailable frozen policy data instead of hiding a replacement decision", async () => {
    const versionChain = baseFile.version_chain?.map((item, index) =>
      index === 0 ? { ...item, replacement_remote_action: null } : item,
    );

    renderCard(
      <VersionGovernanceCard
        file={{ ...baseFile, replacement_remote_action: null, version_chain: versionChain }}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(await screen.findByText("策略数据不可用")).toBeInTheDocument();
    expect(
      screen.getByText(
        (_content, element) =>
          element?.tagName === "SPAN" && element.textContent === "冻结策略：策略数据不可用",
      ),
    ).toBeInTheDocument();
  });

  it("shows the recoverable switch phase and retries the exact failed task for admins", async () => {
    useAuthStore.setState({
      accessToken: "admin-token",
      user: {
        id: "admin-1",
        name: "部门管理员",
        email: "admin@example.com",
        role: "dept_admin",
      },
    });
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderCard(
      <VersionGovernanceCard
        file={{
          ...baseFile,
          version_switch_status: "failed_new_activate",
          version_switch_error: "remote_activation_failed",
          version_switch_attempt_count: 2,
          predecessor_remote_deactivated_at: "2026-07-17T09:00:00Z",
          local_version_activated_at: "2026-07-17T09:00:10Z",
        }}
        tasks={[failedTask]}
        isAdmin
        onOpenVersion={vi.fn()}
        onRefresh={refresh}
      />,
    );

    expect(await screen.findByText("新版本远端激活失败")).toBeInTheDocument();
    expect(screen.getByText("异常类型：remote_activation_failed")).toBeInTheDocument();
    expect(
      screen.getByText(dayjs("2026-07-17T09:00:00Z").format("YYYY-MM-DD HH:mm:ss")),
    ).toBeInTheDocument();
    expect(screen.getByText("v2 · 新版制度")).toHaveAttribute("aria-current", "page");

    fireEvent.click(screen.getByRole("button", { name: /重试切换任务/ }));
    await waitFor(() => expect(retryTask).toHaveBeenCalledWith("task-latest"));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  });

  it("updates the owner with optimistic versioning and explicitly clears expiry", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderCard(
      <VersionGovernanceCard
        file={baseFile}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={refresh}
      />,
    );

    expect(
      screen.getByText("被指定负责人可查看此文件详情与原件，不获得修改/删除权限"),
    ).toBeInTheDocument();
    const save = await screen.findByRole("button", { name: /保存治理信息/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() =>
      expect(updateDocumentDraft).toHaveBeenCalledWith("file-v2", {
        expected_version: 2,
        owner_id: "employee-1",
        expires_at: null,
      }),
    );
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  });

  it("preserves the exact selected expiry time instead of forcing end of day", async () => {
    const expiresAt = "2026-07-25T06:30:00.000Z";
    renderCard(
      <VersionGovernanceCard
        file={{ ...baseFile, expires_at: expiresAt }}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const save = await screen.findByRole("button", { name: /保存治理信息/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() =>
      expect(updateDocumentDraft).toHaveBeenCalledWith("file-v2", {
        expected_version: 2,
        owner_id: "employee-1",
        expires_at: dayjs(expiresAt).toISOString(),
      }),
    );
  });

  it("loads owner page 101 and performs server-side search without keeping an unverified selection", async () => {
    vi.mocked(listDocumentOwnerOptions).mockImplementation((params = {}) => {
      const { q, page = 1, page_size = 50 } = params;
      if (q === "王") {
        return Promise.resolve({
          items: [{ id: "employee-101", name: "第101位成员" }],
          total: 1,
          page: 1,
          page_size,
          total_pages: 1,
        });
      }
      const items =
        page === 1
          ? [{ id: "employee-1", name: "张三" }]
          : page === 2
            ? [{ id: "employee-51", name: "第51位成员" }]
            : [{ id: "employee-101", name: "第101位成员" }];
      return Promise.resolve({
        items,
        total: 101,
        page,
        page_size,
        total_pages: 3,
      });
    });
    renderCard(
      <VersionGovernanceCard
        file={baseFile}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const loadMore = await screen.findByRole("button", { name: "加载更多负责人" });
    fireEvent.click(loadMore);
    await waitFor(() =>
      expect(listDocumentOwnerOptions).toHaveBeenCalledWith({
        q: undefined,
        page: 2,
        page_size: 50,
      }),
    );
    await waitFor(() => expect(loadMore).toBeEnabled());
    fireEvent.click(loadMore);
    await waitFor(() =>
      expect(listDocumentOwnerOptions).toHaveBeenCalledWith({
        q: undefined,
        page: 3,
        page_size: 50,
      }),
    );

    const ownerSelect = screen.getByRole("combobox", { name: "到期负责人" });
    fireEvent.mouseDown(ownerSelect);
    expect(await screen.findByText("第101位成员")).toBeInTheDocument();

    fireEvent.change(ownerSelect, { target: { value: "王" } });
    await waitFor(() =>
      expect(listDocumentOwnerOptions).toHaveBeenCalledWith({
        q: "王",
        page: 1,
        page_size: 50,
      }),
    );
    expect(screen.getByRole("button", { name: /保存治理信息/ })).toBeDisabled();
  });

  it("does not offer a blind retry when more than one task is eligible", async () => {
    useAuthStore.setState({
      accessToken: "admin-token",
      user: {
        id: "admin-1",
        name: "部门管理员",
        email: "admin@example.com",
        role: "dept_admin",
      },
    });
    renderCard(
      <VersionGovernanceCard
        file={{ ...baseFile, version_switch_status: "failed_new_activate" }}
        tasks={[failedTask, { ...failedTask, id: "task-second" }]}
        isAdmin
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(await screen.findByText("新版本远端激活失败")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重试切换任务/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看处理日志" })).toBeInTheDocument();
  });

  it("describes an already-active first version in the present tense", async () => {
    renderCard(
      <VersionGovernanceCard
        file={{
          ...baseFile,
          is_current_version: true,
          remote_visibility: "current",
          version_switch_status: "not_required",
        }}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(await screen.findByText("首个版本已生效")).toBeInTheDocument();
    expect(screen.getByText("该文档已成为本地与 RAGFlow 当前版本。")).toBeInTheDocument();
  });

  it("describes a historical first version as replaced by a later version", async () => {
    renderCard(
      <VersionGovernanceCard
        file={{
          ...baseFile,
          version_number: 1,
          replaces_file_id: null,
          is_current_version: false,
          remote_visibility: "not_current",
          version_switch_status: "not_required",
        }}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(await screen.findByText("已被后续版本替代")).toBeInTheDocument();
    expect(
      screen.getByText("该首版本已成为可追溯的历史版本，当前版本由后续版本承接。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("首个版本无需执行替代切换")).not.toBeInTheDocument();
  });

  it("fails closed when a later owner page cannot be verified", async () => {
    vi.mocked(listDocumentOwnerOptions).mockImplementation((params = {}) => {
      if (params.page === 2) {
        return Promise.reject(new Error("next owner page unavailable"));
      }
      return Promise.resolve({
        items: [{ id: "employee-1", name: "张三" }],
        total: 51,
        page: 1,
        page_size: 50,
        total_pages: 2,
      });
    });
    renderCard(
      <VersionGovernanceCard
        file={baseFile}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const save = await screen.findByRole("button", { name: /保存治理信息/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "加载更多负责人" }));

    expect(await screen.findByText("负责人候选加载失败")).toBeInTheDocument();
    expect(save).toBeDisabled();
  });

  it("does not fetch owner options for a non-uploader", async () => {
    useAuthStore.setState({
      accessToken: "other-token",
      user: {
        id: "employee-2",
        name: "李四",
        email: "other@example.com",
        role: "employee",
      },
    });
    renderCard(
      <VersionGovernanceCard
        file={baseFile}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("仅上传者可在可编辑状态调整负责人和到期时间。"),
    ).toBeInTheDocument();
    expect(listDocumentOwnerOptions).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /保存治理信息/ })).not.toBeInTheDocument();
  });

  it("does not trust an existing owner that is absent from verified candidates", async () => {
    vi.mocked(listDocumentOwnerOptions).mockResolvedValueOnce({
      items: [{ id: "employee-2", name: "李四" }],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
    renderCard(
      <VersionGovernanceCard
        file={baseFile}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const save = await screen.findByRole("button", { name: /保存治理信息/ });
    await waitFor(() => expect(listDocumentOwnerOptions).toHaveBeenCalled());
    expect(await screen.findByText("当前负责人不在有效候选中")).toBeInTheDocument();
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(updateDocumentDraft).not.toHaveBeenCalled();
  });

  it("keeps the form closed when owner candidates fail", async () => {
    vi.mocked(listDocumentOwnerOptions).mockRejectedValueOnce(new Error("owner unavailable"));
    renderCard(
      <VersionGovernanceCard
        file={baseFile}
        tasks={[]}
        isAdmin={false}
        onOpenVersion={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(await screen.findByText("负责人候选加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存治理信息/ })).toBeDisabled();
  });
});
