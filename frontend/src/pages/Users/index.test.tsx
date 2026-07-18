import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type AdminUserItem,
  type AdminUserListResponse,
  type DepartmentListResponse,
  type ManagedDepartmentsResponse,
  changeUserRole,
  disableUser,
  getManagedDepartments,
  listAdminUsers,
  listDepartments,
  replaceManagedDepartments,
  resetUserPassword,
  setUserDepartment,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import UsersPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listAdminUsers: vi.fn(),
    changeUserRole: vi.fn(),
    resetUserPassword: vi.fn(),
    disableUser: vi.fn(),
    listDepartments: vi.fn(),
    getManagedDepartments: vi.fn(),
    replaceManagedDepartments: vi.fn(),
    setUserDepartment: vi.fn(),
  };
});

const mockUser1: AdminUserItem = {
  id: "user-001",
  name: "张维",
  email: "zhangwei@company.com",
  role: "system_admin",
  status: "active",
  department: "产品运营部",
  email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
  upload_count: 84,
  last_upload_at: "2026-06-07T10:16:00Z",
};

const mockUser2: AdminUserItem = {
  id: "user-002",
  name: "李雪",
  email: "lixue@company.com",
  role: "dept_admin",
  status: "active",
  department: "技术支持部",
  department_id: "dept-support",
  department_name: "技术支持部",
  department_code: "support",
  managed_department_ids: ["dept-support"],
  email_verified: true,
  created_at: "2026-01-02T00:00:00Z",
  upload_count: 126,
  last_upload_at: "2026-06-07T09:58:00Z",
};

const mockUser3: AdminUserItem = {
  id: "user-003",
  name: "陈晨",
  email: "chenchen@company.com",
  role: "employee",
  status: "disabled",
  department: "市场品牌部",
  email_verified: false,
  created_at: "2026-01-03T00:00:00Z",
  upload_count: 12,
  last_upload_at: null,
};

const mockUsersResponse: AdminUserListResponse = {
  items: [mockUser1, mockUser2, mockUser3],
  total: 3,
  page: 1,
  page_size: 20,
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
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

describe("UsersPage", () => {
  it("renders user list with real API data and required columns", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);

    renderWithProviders(<UsersPage />);

    // Wait for data to load
    expect(await screen.findByText("张维")).toBeInTheDocument();
    expect(screen.getByText("zhangwei@company.com")).toBeInTheDocument();
    expect(screen.getByText("李雪")).toBeInTheDocument();
    expect(screen.getByText("lixue@company.com")).toBeInTheDocument();
    expect(screen.getByText("陈晨")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "账号治理列表" })).toBeInTheDocument();
    expect(screen.getByText("当前显示 3 个账号，共 3 条记录，1 个需处理")).toBeInTheDocument();
    const roleOverview = screen.getByRole("region", { name: "角色权限概览" });
    expect(roleOverview).toHaveTextContent("当前页 3 个账号");
    expect(roleOverview).toHaveTextContent("1 个部门管理员");
    expect(screen.getAllByText("1 人")).toHaveLength(3);

    // Role labels
    expect(screen.getAllByText("系统管理员").length).toBeGreaterThan(0);
    expect(screen.getAllByText("部门管理员").length).toBeGreaterThan(0);
    expect(screen.getAllByText("普通员工").length).toBeGreaterThan(0);

    // Department column
    expect(screen.getByText("产品运营部")).toBeInTheDocument();
    expect(screen.getByText("技术支持部")).toBeInTheDocument();

    // Upload count column shows numeric count
    expect(screen.getByText("84")).toBeInTheDocument();
    expect(screen.getByText("126")).toBeInTheDocument();

    // Status tag for disabled user
    expect(screen.getAllByText("已禁用").length).toBeGreaterThan(0);
  });

  it("triggers re-query with search param when search input changes", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);

    renderWithProviders(<UsersPage />);

    await screen.findByText("张维");

    // Find search input, type a query, then blur to trigger search
    const searchInput = screen.getByPlaceholderText(/搜索/);
    fireEvent.change(searchInput, { target: { value: "张维" } });
    fireEvent.blur(searchInput);

    await waitFor(() => {
      const calls = vi.mocked(listAdminUsers).mock.calls;
      expect(calls.length).toBeGreaterThanOrEqual(2);
      const lastCall = calls[calls.length - 1][0];
      expect(lastCall).toMatchObject({ search: "张维" });
    });
  });

  it("calls disableUser and invalidates query when disable action confirmed", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
    vi.mocked(disableUser).mockResolvedValue(undefined);

    renderWithProviders(<UsersPage />);

    await screen.findByText("张维");

    // Find disable button for first active user (mockUser1 is active)
    const disableButtons = screen.getAllByRole("button", { name: "禁用" });
    fireEvent.click(disableButtons[0]);

    // Modal.confirm (from App.useApp()) renders its OK button in the document
    await waitFor(() => {
      const footerButtons = document.querySelectorAll(
        ".ant-modal-confirm-btns .ant-btn-primary, .ant-modal-footer .ant-btn-primary",
      );
      expect(footerButtons.length).toBeGreaterThan(0);
    });

    const okButton = document.querySelector(
      ".ant-modal-confirm-btns .ant-btn-primary, .ant-modal-footer .ant-btn-primary",
    ) as HTMLElement;
    fireEvent.click(okButton);

    await waitFor(() => {
      expect(disableUser).toHaveBeenCalledWith("user-001");
    });
  });
  it.each(["A→B", "ABA"] as const)(
    "destroys an imperative disable confirm and rejects its held callback on %s",
    async (switchMode) => {
      const sessionA = {
        accessToken: "token-a",
        user: {
          id: "admin-a",
          name: "管理员 A",
          email: "a@example.com",
          role: "system_admin" as const,
        },
      };
      useAuthStore.setState(sessionA);
      vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
      renderWithProviders(<UsersPage />);
      await screen.findByText("张维");

      fireEvent.click(screen.getAllByRole("button", { name: "禁用" })[0]);
      const heldOkButton = await waitFor(() => {
        const button = document.querySelector(
          ".ant-modal-confirm-btns .ant-btn-primary",
        ) as HTMLElement | null;
        expect(button).not.toBeNull();
        return button!;
      });

      act(() => {
        useAuthStore.setState({
          accessToken: "token-b",
          user: {
            ...sessionA.user,
            id: "admin-b",
            email: "b@example.com",
          },
        });
        if (switchMode === "ABA") {
          useAuthStore.setState(sessionA);
        }
      });

      await waitFor(() => {
        expect(screen.queryByText("禁用用户")).not.toBeInTheDocument();
      });
      fireEvent.click(heldOkButton);
      await Promise.resolve();
      expect(disableUser).not.toHaveBeenCalled();
    },
  );

  it.each(["A→B", "ABA"] as const)(
    "closes a role intent opened by A before confirm on %s",
    async (switchMode) => {
      const sessionA = {
        accessToken: "token-a",
        user: {
          id: "admin-a",
          name: "管理员 A",
          email: "a@example.com",
          role: "system_admin" as const,
        },
      };
      useAuthStore.setState(sessionA);
      vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
      renderWithProviders(<UsersPage />);
      await screen.findByText("李雪");
      fireEvent.click(screen.getAllByRole("button", { name: "改角色" })[1]);
      expect(await screen.findByRole("dialog", { name: "变更用户角色" })).toBeInTheDocument();

      act(() => {
        useAuthStore.setState({
          accessToken: "token-b",
          user: {
            ...sessionA.user,
            id: "admin-b",
            email: "b@example.com",
          },
        });
        if (switchMode === "ABA") {
          useAuthStore.setState(sessionA);
        }
      });

      await waitFor(() => {
        expect(screen.queryByRole("dialog", { name: "变更用户角色" })).not.toBeInTheDocument();
      });
      expect(changeUserRole).not.toHaveBeenCalled();
    },
  );

  it("calls changeUserRole with correct role when role modal submitted", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
    vi.mocked(changeUserRole).mockResolvedValue({
      id: mockUser2.id,
      name: mockUser2.name,
      email: mockUser2.email,
      role: "employee",
      status: mockUser2.status,
      email_verified: mockUser2.email_verified,
      department_id: mockUser2.department_id,
      department_name: mockUser2.department_name,
      department_code: mockUser2.department_code,
      department: mockUser2.department,
      phone: null,
    });

    renderWithProviders(<UsersPage />);

    await screen.findByText("李雪");

    // Click change-role button for second user
    const roleButtons = screen.getAllByRole("button", { name: "改角色" });
    fireEvent.click(roleButtons[1]);

    // Wait for modal to open
    await screen.findByText("变更用户角色");
    const roleSummary = screen.getByRole("region", { name: "角色变更摘要" });
    expect(roleSummary).toHaveTextContent("李雪");
    expect(roleSummary).toHaveTextContent("部门管理员 至 部门管理员");

    // Select new role via Ant Design Select in the modal
    const roleSelect = document.querySelector(
      ".users-role-modal-select .ant-select-selector",
    ) as HTMLElement;
    expect(roleSelect).not.toBeNull();
    fireEvent.mouseDown(roleSelect);

    const employeeOption = await screen.findByTitle("普通员工");
    fireEvent.click(employeeOption);
    expect(roleSummary).toHaveTextContent("部门管理员 至 普通员工");

    // Click OK button in modal footer
    const footerButtons = document.querySelectorAll(
      ".ant-modal-footer .ant-btn-primary, .ant-modal-confirm-btns .ant-btn-primary",
    );
    if (footerButtons.length > 0) {
      fireEvent.click(footerButtons[0] as HTMLElement);
    } else {
      const allButtons = screen.getAllByRole("button");
      const okBtn = allButtons.find((btn) => btn.textContent === "确定");
      if (okBtn) fireEvent.click(okBtn);
    }

    await waitFor(() => {
      expect(changeUserRole).toHaveBeenCalledWith("user-002", "employee");
    });
  });

  it("keeps B state untouched when an A role change finishes late", async () => {
    useAuthStore.setState({
      accessToken: "token-a",
      user: {
        id: "admin-a",
        name: "管理员 A",
        email: "admin-a@company.com",
        role: "system_admin",
        email_verified: true,
        department_assigned: true,
      },
    });
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
    let resolveRoleChange!: (profile: Awaited<ReturnType<typeof changeUserRole>>) => void;
    vi.mocked(changeUserRole).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRoleChange = resolve;
        }),
    );

    renderWithProviders(<UsersPage />);
    await screen.findByText("李雪");
    fireEvent.click(screen.getAllByRole("button", { name: "改角色" })[1]);
    const roleSelect = document.querySelector(
      ".users-role-modal-select .ant-select-selector",
    ) as HTMLElement;
    fireEvent.mouseDown(roleSelect);
    fireEvent.click(await screen.findByTitle("普通员工"));
    fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
    await waitFor(() => expect(changeUserRole).toHaveBeenCalledWith("user-002", "employee"));
    const listCallCount = vi.mocked(listAdminUsers).mock.calls.length;

    act(() => {
      useAuthStore.setState({
        accessToken: "token-b",
        user: {
          id: "admin-b",
          name: "管理员 B",
          email: "admin-b@company.com",
          role: "system_admin",
          email_verified: true,
          department_assigned: true,
        },
      });
    });
    await act(async () => {
      resolveRoleChange({
        id: mockUser2.id,
        name: mockUser2.name,
        email: mockUser2.email,
        role: "employee",
        status: mockUser2.status,
        email_verified: mockUser2.email_verified,
        department_id: mockUser2.department_id,
        department_name: mockUser2.department_name,
        department_code: mockUser2.department_code,
        department: mockUser2.department,
        phone: null,
      });
      await Promise.resolve();
    });

    expect(vi.mocked(listAdminUsers).mock.calls).toHaveLength(listCallCount);
    expect(screen.queryByText("角色已变更")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "变更用户角色" })).not.toBeInTheDocument();
  });

  it("opens managed department modal for dept admins and saves current scope", async () => {
    const departments: DepartmentListResponse = {
      items: [
        {
          id: "dept-support",
          name: "技术支持部",
          code: "support",
          status: "active",
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
        },
        {
          id: "dept-hr",
          name: "人事行政部",
          code: "hr",
          status: "active",
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
        },
      ],
      total: 2,
    };
    const managed: ManagedDepartmentsResponse = {
      user_id: "user-002",
      managed_department_ids: ["dept-support"],
    };

    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
    vi.mocked(listDepartments).mockResolvedValue(departments);
    vi.mocked(getManagedDepartments).mockResolvedValue(managed);
    vi.mocked(replaceManagedDepartments).mockResolvedValue(managed);

    renderWithProviders(<UsersPage />);

    await screen.findByText("李雪");
    const managedButtons = screen.getAllByRole("button", { name: "管辖部门" });
    expect(managedButtons).toHaveLength(1);
    fireEvent.click(managedButtons[0]);

    expect(await screen.findByText("配置管辖部门")).toBeInTheDocument();
    const managedSummary = screen.getByRole("region", { name: "部门管辖摘要" });
    expect(managedSummary).toHaveTextContent("李雪");
    expect(managedSummary).toHaveTextContent("1管辖部门");
    await waitFor(() => {
      expect(listDepartments).toHaveBeenCalled();
      expect(getManagedDepartments).toHaveBeenCalledWith("user-002");
    });
    expect(await screen.findByText("技术支持部 (support)")).toBeInTheDocument();

    const okButton = document.querySelector(".ant-modal-footer .ant-btn-primary") as HTMLElement;
    fireEvent.click(okButton);

    await waitFor(() => {
      expect(replaceManagedDepartments).toHaveBeenCalledWith("user-002", ["dept-support"]);
    });
  });

  it("updates a user's assigned department", async () => {
    const departments: DepartmentListResponse = {
      items: [
        {
          id: "dept-support",
          name: "技术支持部",
          code: "support",
          status: "active",
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
        },
        {
          id: "dept-hr",
          name: "人事行政部",
          code: "hr",
          status: "active",
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
        },
      ],
      total: 2,
    };
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
    vi.mocked(listDepartments).mockResolvedValue(departments);
    vi.mocked(setUserDepartment).mockResolvedValue({
      id: mockUser1.id,
      name: mockUser1.name,
      email: mockUser1.email,
      role: mockUser1.role,
      status: mockUser1.status,
      email_verified: mockUser1.email_verified,
      department_id: "dept-hr",
      department_name: "人事行政部",
      department_code: "hr",
      department: "人事行政部",
      phone: null,
    });

    renderWithProviders(<UsersPage />);

    await screen.findByText("张维");
    const departmentButtons = screen.getAllByRole("button", { name: "所属部门" });
    fireEvent.click(departmentButtons[0]);

    expect(await screen.findByText("编辑所属部门")).toBeInTheDocument();
    await waitFor(() => {
      expect(listDepartments).toHaveBeenCalledWith({ page_size: 200 });
    });

    const selector = document.querySelector(".ant-modal .ant-select-selector") as HTMLElement;
    fireEvent.mouseDown(selector);
    const hrOption = await screen.findByTitle("人事行政部 (hr)");
    fireEvent.click(hrOption);

    const okButton = document.querySelector(".ant-modal-footer .ant-btn-primary") as HTMLElement;
    fireEvent.click(okButton);

    await waitFor(() => {
      expect(setUserDepartment).toHaveBeenCalledWith("user-001", "dept-hr");
    });
  });

  it("calls resetUserPassword and shows success message when reset confirmed", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);
    vi.mocked(resetUserPassword).mockResolvedValue(undefined);

    renderWithProviders(<UsersPage />);

    await screen.findByText("张维");

    // Click reset password button for first user
    const resetButtons = screen.getAllByRole("button", { name: "重置密码" });
    fireEvent.click(resetButtons[0]);

    // Wait for the modal footer OK button to appear (modal is now open)
    await waitFor(() => {
      const footerButtons = document.querySelectorAll(".ant-modal-footer .ant-btn-primary");
      expect(footerButtons.length).toBeGreaterThan(0);
    });

    // The modal should show the user name (appears in both table and modal)
    expect(screen.getAllByText("张维").length).toBeGreaterThanOrEqual(2);
    const resetSummary = screen.getByRole("region", { name: "密码重置摘要" });
    expect(resetSummary).toHaveTextContent("张维");
    expect(resetSummary).toHaveTextContent("发送一次性密码重置邮件");

    const okButton = document.querySelector(".ant-modal-footer .ant-btn-primary") as HTMLElement;
    fireEvent.click(okButton);

    await waitFor(() => {
      expect(resetUserPassword).toHaveBeenCalledWith("user-001");
    });
  });

  it("filters user list by role when role select changes", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue(mockUsersResponse);

    renderWithProviders(<UsersPage />);

    await screen.findByText("张维");

    // Find role filter Select combobox — look for the one with role options
    const comboboxes = screen.getAllByRole("combobox");
    // The role filter combobox should be the first one after search
    const roleCombobox =
      comboboxes.find(
        (el) =>
          el.getAttribute("aria-label") === "角色筛选" || el.closest(".users-role-filter") !== null,
      ) ?? comboboxes[0];

    fireEvent.mouseDown(roleCombobox);

    // Find the employee option in dropdown
    const employeeOption = await screen.findByTitle("普通员工");
    fireEvent.click(employeeOption);

    await waitFor(() => {
      const calls = vi.mocked(listAdminUsers).mock.calls;
      expect(calls.length).toBeGreaterThanOrEqual(2);
      const lastCall = calls[calls.length - 1][0];
      expect(lastCall).toMatchObject({ role: "employee" });
    });
  });
});
