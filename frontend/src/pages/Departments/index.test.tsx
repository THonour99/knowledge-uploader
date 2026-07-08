import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type DepartmentListResponse,
  createDepartment,
  disableDepartment,
  listDepartments,
  updateDepartment,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import DepartmentsPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listDepartments: vi.fn(),
    createDepartment: vi.fn(),
    updateDepartment: vi.fn(),
    disableDepartment: vi.fn(),
  };
});

const departments: DepartmentListResponse = {
  items: [
    {
      id: "dept-engineering",
      name: "技术部",
      code: "engineering",
      status: "active",
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-08T08:00:00Z",
    },
    {
      id: "dept-hr",
      name: "人事行政部",
      code: "hr",
      status: "disabled",
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-08T08:00:00Z",
    },
    {
      id: "00000000-0000-0000-0000-000000000001",
      name: "未分配",
      code: "unassigned",
      status: "active",
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-08T08:00:00Z",
    },
  ],
  total: 3,
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
});

function renderWithProviders(node: ReactNode) {
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
          <div style={themeCssVariables as CSSProperties}>{node}</div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("DepartmentsPage", () => {
  it("renders department list and protects the unassigned department", async () => {
    vi.mocked(listDepartments).mockResolvedValue(departments);

    renderWithProviders(<DepartmentsPage />);

    expect(await screen.findByText("技术部")).toBeInTheDocument();
    expect(screen.getByText("engineering")).toBeInTheDocument();
    expect(screen.getByText("未分配保护")).toBeInTheDocument();
    expect(screen.getByText("部门总数")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /编辑/ })[2]).toBeDisabled();
  });

  it("creates a department from the modal", async () => {
    vi.mocked(listDepartments).mockResolvedValue(departments);
    vi.mocked(createDepartment).mockResolvedValue({
      id: "dept-legal",
      name: "法务部",
      code: "legal",
      status: "active",
      created_at: "2026-06-09T00:00:00Z",
      updated_at: "2026-06-09T00:00:00Z",
    });

    renderWithProviders(<DepartmentsPage />);

    await screen.findByText("技术部");
    fireEvent.click(screen.getByRole("button", { name: /新建部门/ }));
    fireEvent.change(screen.getByLabelText("部门名称"), { target: { value: "法务部" } });
    fireEvent.change(screen.getByLabelText("部门编码"), { target: { value: "legal" } });

    const okButton = document.querySelector(".ant-modal-footer .ant-btn-primary") as HTMLElement;
    fireEvent.click(okButton);

    await waitFor(() => {
      expect(createDepartment).toHaveBeenCalledWith({ name: "法务部", code: "legal" });
    });
  });

  it("edits, disables, and restores departments", async () => {
    vi.mocked(listDepartments).mockResolvedValue(departments);
    vi.mocked(updateDepartment).mockResolvedValue(departments.items[0]);
    vi.mocked(disableDepartment).mockResolvedValue(undefined);

    renderWithProviders(<DepartmentsPage />);

    await screen.findByText("技术部");
    fireEvent.click(screen.getAllByRole("button", { name: /编辑/ })[0]);
    fireEvent.change(screen.getByLabelText("部门名称"), { target: { value: "研发中心" } });
    fireEvent.click(document.querySelector(".ant-modal-footer .ant-btn-primary") as HTMLElement);

    await waitFor(() => {
      expect(updateDepartment).toHaveBeenCalledWith("dept-engineering", {
        name: "研发中心",
        status: "active",
      });
    });

    fireEvent.click(screen.getAllByRole("button", { name: /停用/ })[0]);
    await waitFor(() => {
      expect(document.querySelector(".ant-popconfirm-buttons .ant-btn-primary")).not.toBeNull();
    });
    fireEvent.click(
      document.querySelector(".ant-popconfirm-buttons .ant-btn-primary") as HTMLElement,
    );

    await waitFor(() => {
      expect(disableDepartment).toHaveBeenCalledWith("dept-engineering");
    });

    fireEvent.click(screen.getByRole("button", { name: /恢复/ }));

    await waitFor(() => {
      expect(updateDepartment).toHaveBeenCalledWith("dept-hr", { status: "active" });
    });
  });
});
