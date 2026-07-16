import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { listRegistrationDepartments, register } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import RegisterPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listRegistrationDepartments: vi.fn(),
    register: vi.fn(),
  };
});

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
});

beforeEach(() => {
  vi.mocked(listRegistrationDepartments).mockResolvedValue([
    { id: "dept-tech", name: "技术部", code: "tech" },
  ]);
});

function renderRegisterPage() {
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
            <MemoryRouter initialEntries={["/register"]}>
              <Routes>
                <Route path="/register" element={<RegisterPage />} />
                <Route path="/login" element={<div>login-page-marker</div>} />
              </Routes>
            </MemoryRouter>
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

function fillInput(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function selectDepartment() {
  const select = await screen.findByRole("combobox");
  fireEvent.mouseDown(select);
  fireEvent.click(await screen.findByText("技术部（tech）"));
}

describe("RegisterPage", () => {
  it("submits the form with correct payload and redirects to login on success", async () => {
    vi.mocked(register).mockResolvedValue({ accepted: true });

    renderRegisterPage();

    fillInput("姓名", "张三");
    await selectDepartment();
    fillInput("公司邮箱", "zhangsan@company.com");
    fillInput("手机号", "13800138000");
    fillInput("密码", "Secret123!");
    fillInput("确认密码", "Secret123!");
    fireEvent.click(screen.getByRole("button", { name: "提交注册" }));

    await waitFor(() => {
      expect(register).toHaveBeenCalledWith({
        name: "张三",
        email: "zhangsan@company.com",
        password: "Secret123!",
        department_id: "dept-tech",
        phone: "13800138000",
      });
    });

    expect(await screen.findByText(/完成邮箱验证/)).toBeInTheDocument();
    expect(await screen.findByText("login-page-marker")).toBeInTheDocument();
  });

  it("shows the backend error message when register fails", async () => {
    vi.mocked(register).mockRejectedValue(new Error("仅允许使用公司邮箱注册"));

    renderRegisterPage();

    fillInput("姓名", "张三");
    await selectDepartment();
    fillInput("公司邮箱", "zhangsan@other.com");
    fillInput("密码", "Secret123!");
    fillInput("确认密码", "Secret123!");
    fireEvent.click(screen.getByRole("button", { name: "提交注册" }));

    expect(await screen.findByText("仅允许使用公司邮箱注册")).toBeInTheDocument();
    expect(screen.queryByText("login-page-marker")).not.toBeInTheDocument();
  });

  it("blocks submission when the two passwords do not match", async () => {
    renderRegisterPage();

    fillInput("姓名", "张三");
    await selectDepartment();
    fillInput("公司邮箱", "zhangsan@company.com");
    fillInput("密码", "Secret123!");
    fillInput("确认密码", "Mismatch456!");
    fireEvent.click(screen.getByRole("button", { name: "提交注册" }));

    expect(await screen.findByText("两次输入的密码不一致")).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it("explains how to recover when no department is open for registration", async () => {
    vi.mocked(listRegistrationDepartments).mockResolvedValue([]);

    renderRegisterPage();

    expect(await screen.findByText("暂无可注册部门")).toBeInTheDocument();
    expect(
      screen.getByText("当前没有开放注册的部门，请联系系统管理员完成部门配置。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交注册" })).toBeDisabled();
  });
});
