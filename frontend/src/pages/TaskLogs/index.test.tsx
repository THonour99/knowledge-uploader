import type { CSSProperties, ReactNode } from "react";
import React from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import type * as AntdModule from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  cancelTask,
  getTask,
  listTasks,
  retryTask,
  type SyncTask,
  type SyncTaskListResponse,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import TaskLogsPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listTasks: vi.fn(),
    getTask: vi.fn(),
    retryTask: vi.fn(),
    cancelTask: vi.fn(),
  };
});

// Simplify AntD components that are difficult to interact with in jsdom:
// - Popconfirm: immediately call onConfirm when children are clicked
// - Select: render a native <select> element for easy fireEvent.change testing
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof AntdModule>("antd");

  function MockPopconfirm({
    children,
    onConfirm,
  }: {
    children: React.ReactNode;
    onConfirm?: () => void;
    title?: string;
    okText?: string;
    cancelText?: string;
  }) {
    return (
      <span
        data-testid="popconfirm-wrapper"
        onClick={(e) => {
          e.stopPropagation();
          onConfirm?.();
        }}
      >
        {children}
      </span>
    );
  }

  function MockSelect({
    value,
    onChange,
    options,
    placeholder,
    style,
  }: {
    value?: string;
    onChange?: (value: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    style?: React.CSSProperties;
  }) {
    return (
      <select
        role="combobox"
        value={value ?? ""}
        style={style}
        aria-label={placeholder}
        onChange={(e) => onChange?.(e.target.value)}
      >
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

const makeMockTask = (overrides: Partial<SyncTask> = {}): SyncTask => ({
  id: "task-1",
  file_id: "file-abc",
  task_type: "ragflow_upload",
  status: "failed",
  retry_count: 2,
  max_retry_count: 3,
  error_message: "连接 RAGFlow 超时",
  started_at: "2026-06-10T08:00:00Z",
  finished_at: "2026-06-10T08:05:00Z",
  created_at: "2026-06-10T07:59:00Z",
  updated_at: "2026-06-10T08:05:00Z",
  logs: [
    {
      id: 1,
      task_id: "task-1",
      status: "running",
      message: "开始上传至 RAGFlow",
      created_at: "2026-06-10T08:00:01Z",
    },
    {
      id: 2,
      task_id: "task-1",
      status: "failed",
      message: "连接 RAGFlow 超时",
      created_at: "2026-06-10T08:05:00Z",
    },
  ],
  ...overrides,
});

const mockListResponse: SyncTaskListResponse = {
  items: [
    makeMockTask({ id: "task-1", status: "failed" }),
    makeMockTask({
      id: "task-2",
      file_id: "file-def",
      task_type: "ragflow_parse",
      status: "running",
      retry_count: 0,
      error_message: null,
      started_at: "2026-06-10T09:00:00Z",
      finished_at: null,
      logs: [],
    }),
    makeMockTask({
      id: "task-3",
      file_id: "file-ghi",
      task_type: "ragflow_upload",
      status: "queued",
      retry_count: 0,
      error_message: null,
      started_at: null,
      finished_at: null,
      logs: [],
    }),
  ],
  total: 3,
};

const LINKED_TASK_ID = "11111111-1111-4111-8111-111111111111";

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

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="task-log-location">{`${location.pathname}${location.search}`}</span>;
}

function renderWithProviders(node: ReactNode, initialEntry = "/task-logs") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ConfigProvider>
        <AntdApp>
          <QueryClientProvider client={queryClient}>
            <div style={themeCssVariables as CSSProperties}>
              <LocationProbe />
              {node}
            </div>
          </QueryClientProvider>
        </AntdApp>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("TaskLogsPage", () => {
  it("opens a UUID task deep link and removes it when the drawer closes", async () => {
    const linkedTask = makeMockTask({ id: LINKED_TASK_ID });
    vi.mocked(listTasks).mockResolvedValue(mockListResponse);
    vi.mocked(getTask).mockResolvedValue(linkedTask);

    renderWithProviders(<TaskLogsPage />, `/task-logs?task_id=${LINKED_TASK_ID}`);

    await waitFor(() => expect(getTask).toHaveBeenCalledWith(LINKED_TASK_ID));
    expect(await screen.findByRole("region", { name: "任务执行摘要" })).toHaveTextContent(
      LINKED_TASK_ID,
    );
    const closeButton = document.querySelector(".ant-drawer-close") as HTMLButtonElement;
    expect(closeButton).toBeTruthy();
    fireEvent.click(closeButton);

    await waitFor(() => {
      expect(screen.getByTestId("task-log-location")).toHaveTextContent("/task-logs");
      expect(screen.getByTestId("task-log-location")).not.toHaveTextContent("task_id=");
    });
  });

  it("rejects and normalizes a malformed task deep link", async () => {
    vi.mocked(listTasks).mockResolvedValue(mockListResponse);

    renderWithProviders(<TaskLogsPage />, "/task-logs?task_id=..%2F..%2Fsettings");

    await screen.findByRole("heading", { name: "任务日志" });
    await waitFor(() => {
      expect(screen.getByTestId("task-log-location")).toHaveTextContent("/task-logs");
      expect(screen.getByTestId("task-log-location")).not.toHaveTextContent("task_id=");
    });
    expect(getTask).not.toHaveBeenCalled();
  });

  it("renders task list with type, file_id, status tag, retry count and time columns", async () => {
    vi.mocked(listTasks).mockResolvedValue(mockListResponse);

    renderWithProviders(<TaskLogsPage />);

    // heading
    expect(await screen.findByRole("heading", { name: "任务日志" })).toBeInTheDocument();

    // task_type column values (use findAllByText as Select options may also render these values)
    const uploadCells = await screen.findAllByText("ragflow_upload");
    expect(uploadCells.length).toBeGreaterThanOrEqual(1);
    const parseCells = await screen.findAllByText("ragflow_parse");
    expect(parseCells.length).toBeGreaterThanOrEqual(1);

    // file_id column values
    expect(screen.getByText("file-abc")).toBeInTheDocument();
    expect(screen.getByText("file-def")).toBeInTheDocument();

    // retry count
    expect(screen.getByText("2")).toBeInTheDocument();

    // status tags rendered (sync kind: "failed" → "同步失败", "syncing" → "同步中", "queued" → "待同步")
    expect(screen.getByText("同步失败")).toBeInTheDocument();
    expect(screen.getByText("同步中")).toBeInTheDocument();
    expect(screen.getByText("待同步")).toBeInTheDocument();

    expect(screen.getByText("任务列表")).toBeInTheDocument();
    expect(screen.getByText("当前显示 3 条任务，共 3 条队列记录")).toBeInTheDocument();
  });

  it("re-queries when task_type filter changes", async () => {
    vi.mocked(listTasks).mockResolvedValue({ items: [], total: 0 });

    renderWithProviders(<TaskLogsPage />);

    await screen.findByRole("heading", { name: "任务日志" });

    // initial call
    await waitFor(() => {
      expect(listTasks).toHaveBeenCalledTimes(1);
    });

    // Select is mocked as native <select>; use fireEvent.change to trigger onChange
    const selects = screen.getAllByRole("combobox");
    // first combobox is task_type filter
    fireEvent.change(selects[0], { target: { value: "ragflow_upload" } });

    await waitFor(() => {
      expect(listTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({ task_type: "ragflow_upload" }),
      );
    });
  });

  it("shows retry button for failed task, calls retryTask on confirm and invalidates list", async () => {
    vi.mocked(listTasks).mockResolvedValue(mockListResponse);
    vi.mocked(retryTask).mockResolvedValue(makeMockTask({ status: "queued" }));

    renderWithProviders(<TaskLogsPage />);

    // wait for table to render
    await screen.findByText("file-abc");

    // find retry button for task-1 via data-testid
    const retryBtn = await screen.findByTestId("retry-task-1");
    expect(retryBtn).toBeInTheDocument();

    // Popconfirm is mocked: clicking the button directly triggers onConfirm
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(retryTask).toHaveBeenCalledWith("task-1");
    });

    // after mutation, list should be re-fetched
    await waitFor(() => {
      expect(listTasks).toHaveBeenCalledTimes(2);
    });
  });

  it("opens detail Drawer showing log timeline and error_message for a task", async () => {
    vi.mocked(listTasks).mockResolvedValue(mockListResponse);
    const detailTask = makeMockTask();
    vi.mocked(getTask).mockResolvedValue(detailTask);

    renderWithProviders(<TaskLogsPage />);

    await screen.findByText("file-abc");

    // click the detail button for task-1 via data-testid
    const detailBtn = await screen.findByTestId("detail-task-1");
    fireEvent.click(detailBtn);

    // getTask called with task-1 id
    await waitFor(() => {
      expect(getTask).toHaveBeenCalledWith("task-1");
    });

    // Drawer content: summary and log messages appear
    const detailSummary = await screen.findByRole("region", { name: "任务执行摘要" });
    expect(detailSummary).toHaveTextContent("ragflow_upload");
    expect(detailSummary).toHaveTextContent("任务 ID：task-1");
    expect(detailSummary).toHaveTextContent("file-abc");
    expect(detailSummary).toHaveTextContent("2 / 3");

    expect(await screen.findByText("开始上传至 RAGFlow")).toBeInTheDocument();

    // "连接 RAGFlow 超时" appears both in the Timeline log and in the Alert error_message
    const errorTexts = await screen.findAllByText("连接 RAGFlow 超时");
    expect(errorTexts.length).toBeGreaterThanOrEqual(1);
  });

  it("shows cancel button for running task and calls cancelTask on confirm", async () => {
    vi.mocked(listTasks).mockResolvedValue(mockListResponse);
    vi.mocked(cancelTask).mockResolvedValue(
      makeMockTask({ id: "task-2", status: "canceled", error_message: null }),
    );

    renderWithProviders(<TaskLogsPage />);

    await screen.findByText("file-def");

    // task-2 has status=running, so it has cancel button
    const cancelBtn = await screen.findByTestId("cancel-task-2");
    expect(cancelBtn).toBeInTheDocument();

    // Popconfirm is mocked: clicking the button directly triggers onConfirm
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(cancelTask).toHaveBeenCalledWith("task-2");
    });
  });
});
