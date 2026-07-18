import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createSavedView,
  deleteSavedView,
  getSavedView,
  listSavedViews,
  type SavedViewItem,
  updateSavedView,
} from "../api/client";
import type * as ApiClientModule from "../api/client";
import { type CurrentUser, useAuthStore } from "../store/auth.store";
import { themeCssVariables } from "../theme/tokens";
import { SavedViewManager } from "./SavedViewManager";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../api/client");
  return {
    ...actual,
    createSavedView: vi.fn(),
    deleteSavedView: vi.fn(),
    getSavedView: vi.fn(),
    listSavedViews: vi.fn(),
    updateSavedView: vi.fn(),
  };
});

const admin: CurrentUser = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "部门管理员",
  email: "admin@example.com",
  role: "dept_admin",
  department_id: "22222222-2222-4222-8222-222222222222",
  department_name: "研发部",
};

const savedView: SavedViewItem = {
  id: "33333333-3333-4333-8333-333333333333",
  owner_id: admin.id,
  scope: "department",
  department_id: admin.department_id ?? null,
  page_key: "task_logs",
  name: "失败任务",
  stored_schema_version: 2,
  effective_schema_version: 2,
  compatibility: "current",
  effective_definition: {
    query_definition: {
      status: "failed",
      sort: "created_at",
      order: "desc",
      page_size: 20,
    },
    column_preferences: {
      hidden: ["error_message"],
      density: "compact",
    },
  },
  row_version: 3,
  created_at: "2026-07-18T00:00:00Z",
  updated_at: "2026-07-18T00:00:00Z",
};

const quota = {
  private_per_owner_page: 100,
  department_per_department_page: 100,
};

function savedViewList(view: SavedViewItem) {
  return {
    items: [view],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
    quota,
  };
}

async function selectSavedView(): Promise<void> {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "选择保存视图" }));
  fireEvent.click(await screen.findByText(/失败任务（部门共享/));
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

afterEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ accessToken: null, user: null });
});

function renderWithProviders(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  useAuthStore.setState({ accessToken: "token", user: admin });

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

describe("SavedViewManager", () => {
  it("ignores stored column preferences and applies only the effective query definition", async () => {
    vi.mocked(listSavedViews).mockResolvedValue(savedViewList(savedView));
    const onApply = vi.fn();

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={{ status: "running" }}
        onApply={onApply}
      />,
    );

    await waitFor(() =>
      expect(listSavedViews).toHaveBeenCalledWith({
        page_key: "task_logs",
        page: 1,
        page_size: 20,
      }),
    );
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "选择保存视图" }));
    fireEvent.click(await screen.findByText(/失败任务（部门共享/));
    fireEvent.click(screen.getByRole("button", { name: "应用保存视图" }));

    expect(onApply).toHaveBeenCalledWith(savedView.effective_definition?.query_definition);
    expect(onApply).not.toHaveBeenCalledWith(
      expect.objectContaining(savedView.effective_definition?.column_preferences ?? {}),
    );
    expect(screen.queryByText(/已应用.*列偏好|列偏好.*已应用/)).not.toBeInTheDocument();
  });

  it("pages and remotely searches more than one hundred views before applying an old view", async () => {
    const pageTwoView = {
      ...savedView,
      id: "44444444-4444-4444-8444-444444444444",
      name: "第二页视图",
    };
    const historicalView = {
      ...savedView,
      id: "55555555-5555-4555-8555-555555555555",
      name: "历史视图-001",
      effective_definition: {
        query_definition: {
          status: "failed",
          sort: "created_at",
          order: "asc",
          page_size: 20,
        },
        column_preferences: {},
      },
    };
    vi.mocked(listSavedViews).mockImplementation(async (params) => {
      if (params.q === "历史视图-001") {
        return savedViewList(historicalView);
      }
      if (params.page === 2) {
        return {
          items: [pageTwoView],
          total: 121,
          page: 2,
          page_size: 20,
          total_pages: 7,
          quota,
        };
      }
      return {
        items: [savedView],
        total: 121,
        page: 1,
        page_size: 20,
        total_pages: 7,
        quota,
      };
    });
    const onApply = vi.fn();

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={{ status: "running" }}
        onApply={onApply}
      />,
    );

    expect(await screen.findByText("共 121 个保存视图")).toBeInTheDocument();
    const pageTwoButton = await waitFor(() => {
      const button = document.querySelector(".ant-pagination-item-2");
      expect(button).not.toBeNull();
      return button;
    });
    fireEvent.click(pageTwoButton as HTMLElement);
    await waitFor(() =>
      expect(listSavedViews).toHaveBeenCalledWith({
        page_key: "task_logs",
        page: 2,
        page_size: 20,
      }),
    );

    const combobox = screen.getByRole("combobox", { name: "选择保存视图" });
    fireEvent.mouseDown(combobox);
    fireEvent.change(combobox, { target: { value: "历史视图-001" } });
    await waitFor(() =>
      expect(listSavedViews).toHaveBeenCalledWith({
        page_key: "task_logs",
        q: "历史视图-001",
        page: 1,
        page_size: 20,
      }),
    );
    fireEvent.click(await screen.findByText(/历史视图-001（部门共享/));
    fireEvent.click(screen.getByRole("button", { name: "应用保存视图" }));

    expect(onApply).toHaveBeenCalledWith(historicalView.effective_definition.query_definition);
  });

  it("shows the explicit quota and maps quota rejection to a recovery action", async () => {
    vi.mocked(listSavedViews).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      total_pages: 0,
      quota,
    });
    vi.mocked(createSavedView).mockRejectedValue(
      new ApiError("quota reached", {
        status: 409,
        code: "SAVED_VIEW_QUOTA_EXCEEDED",
      }),
    );

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={{ status: "failed" }}
        onApply={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /保存当前筛选/ }));
    expect(screen.getByText(/每个页面最多保存/)).toHaveTextContent(
      "每个页面最多保存 100 个私人视图；部门共享按部门和页面最多 100 个。",
    );
    fireEvent.change(screen.getByRole("textbox", { name: "视图名称" }), {
      target: { value: "超过上限" },
    });
    const okButton = document.querySelector(".ant-modal-footer .ant-btn-primary");
    expect(okButton).not.toBeNull();
    fireEvent.click(okButton as HTMLElement);

    expect(
      await screen.findByText("已达到当前页面和共享范围的保存上限，请删除不再使用的视图后重试"),
    ).toBeInTheDocument();
  });

  it("creates a department-shared view from filters without persisting result rows", async () => {
    vi.mocked(listSavedViews).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      total_pages: 0,
      quota,
    });
    vi.mocked(createSavedView).mockResolvedValue(savedView);
    const queryDefinition = {
      task_type: "ragflow_upload",
      status: "failed",
      sort: "created_at",
      order: "desc",
      page_size: 50,
    };

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={queryDefinition}
        departmentOptions={[{ label: "研发部", value: admin.department_id ?? "" }]}
        onApply={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /保存当前筛选/ }));
    expect(
      screen.getByText(
        "本页面不应用或修改列偏好；只保存筛选和排序，不保存结果行、文件内容或权限范围。",
      ),
    ).toBeInTheDocument();
    fireEvent.change(await screen.findByRole("textbox", { name: "视图名称" }), {
      target: { value: "上传失败" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "部门共享" }));

    const okButton = document.querySelector(".ant-modal-footer .ant-btn-primary");
    expect(okButton).not.toBeNull();
    fireEvent.click(okButton as HTMLElement);

    await waitFor(() =>
      expect(createSavedView).toHaveBeenCalledWith({
        page_key: "task_logs",
        name: "上传失败",
        scope: "department",
        department_id: admin.department_id,
        definition_schema_version: 2,
        query_definition: queryDefinition,
        column_preferences: {},
      }),
    );
    const payload = vi.mocked(createSavedView).mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("items");
    expect(payload).not.toHaveProperty("permissions");
  });

  it("refetches after an update conflict, preserves selection, and retries with the latest row version", async () => {
    const refreshedView = { ...savedView, row_version: 4 };
    vi.mocked(listSavedViews)
      .mockResolvedValueOnce(savedViewList(savedView))
      .mockResolvedValue(savedViewList(refreshedView));
    vi.mocked(getSavedView).mockResolvedValue(refreshedView);
    vi.mocked(updateSavedView)
      .mockRejectedValueOnce(new ApiError("row version conflict", { status: 409 }))
      .mockResolvedValueOnce({ ...refreshedView, row_version: 5 });

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={{ status: "running" }}
        onApply={vi.fn()}
      />,
    );

    await selectSavedView();
    fireEvent.click(screen.getByRole("button", { name: /更新$/ }));

    await waitFor(() =>
      expect(updateSavedView).toHaveBeenNthCalledWith(1, savedView.id, {
        row_version: 3,
        definition_schema_version: 2,
        query_definition: { status: "running" },
        column_preferences: savedView.effective_definition?.column_preferences,
      }),
    );
    expect(
      await screen.findByText("视图已被其他人更新，已刷新为最新版本，请确认后重试"),
    ).toBeInTheDocument();
    await waitFor(() => expect(listSavedViews).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: /更新$/ }));

    await waitFor(() =>
      expect(updateSavedView).toHaveBeenNthCalledWith(2, savedView.id, {
        row_version: 4,
        definition_schema_version: 2,
        query_definition: { status: "running" },
        column_preferences: refreshedView.effective_definition?.column_preferences,
      }),
    );
  });

  it("refetches and keeps an existing selection after a non-conflict update failure", async () => {
    const refreshedView = { ...savedView, row_version: 4 };
    vi.mocked(listSavedViews)
      .mockResolvedValueOnce(savedViewList(savedView))
      .mockResolvedValue(savedViewList(refreshedView));
    vi.mocked(updateSavedView).mockRejectedValueOnce(
      new ApiError("服务暂时不可用", { status: 503 }),
    );
    const onApply = vi.fn();

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={{ status: "running" }}
        onApply={onApply}
      />,
    );

    await selectSavedView();
    fireEvent.click(screen.getByRole("button", { name: /更新$/ }));

    expect(await screen.findByText("服务暂时不可用；已刷新保存视图列表")).toBeInTheDocument();
    await waitFor(() => expect(listSavedViews).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "应用保存视图" }));
    expect(onApply).toHaveBeenCalledWith(refreshedView.effective_definition?.query_definition);
  });

  it("refetches and preserves a server-retained selection after delete fails", async () => {
    vi.mocked(listSavedViews).mockResolvedValue(savedViewList(savedView));
    vi.mocked(deleteSavedView).mockRejectedValueOnce(
      new ApiError("无权删除该视图", { status: 403 }),
    );

    renderWithProviders(
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={{ status: "running" }}
        onApply={vi.fn()}
      />,
    );

    await selectSavedView();
    fireEvent.click(screen.getByRole("button", { name: /删除$/ }));
    await screen.findByText("删除保存视图");
    const confirmButton = await waitFor(() => {
      const button = document.querySelector(".ant-popconfirm-buttons .ant-btn-primary");
      expect(button).not.toBeNull();
      return button;
    });
    fireEvent.click(confirmButton as HTMLElement);

    expect(await screen.findByText("无权删除该视图；已刷新保存视图列表")).toBeInTheDocument();
    await waitFor(() => expect(listSavedViews).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "应用保存视图" })).toBeEnabled();
  });
});
