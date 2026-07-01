import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { resetPassword } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import ResetPasswordPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    resetPassword: vi.fn(),
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

function renderResetPasswordPage(initialEntry = "/reset-password/test-token") {
  return renderWithProviders(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/reset-password/:token" element={<ResetPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/login" element={<div>login-page-marker</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function fillInput(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("ResetPasswordPage", () => {
  it("submits the token and new password then redirects to login", async () => {
    vi.mocked(resetPassword).mockResolvedValue({
      id: "user-1",
      name: "张三",
      email: "zhangsan@company.com",
      role: "employee",
      status: "active",
      email_verified: true,
      department: null,
      phone: null,
    });

    renderResetPasswordPage();

    fillInput("新密码", "NewSecret123!");
    fillInput("确认密码", "NewSecret123!");
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    await waitFor(() => {
      expect(resetPassword).toHaveBeenCalledWith({
        token: "test-token",
        new_password: "NewSecret123!",
      });
    });

    expect(await screen.findByText(/密码重置成功/)).toBeInTheDocument();
    expect(await screen.findByText("login-page-marker")).toBeInTheDocument();
  });

  it("shows the backend error message when reset fails", async () => {
    vi.mocked(resetPassword).mockRejectedValue(new Error("重置链接已失效，请重新发起找回密码"));

    renderResetPasswordPage();

    fillInput("新密码", "NewSecret123!");
    fillInput("确认密码", "NewSecret123!");
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    expect(await screen.findByText("重置链接已失效，请重新发起找回密码")).toBeInTheDocument();
    expect(screen.queryByText("login-page-marker")).not.toBeInTheDocument();
  });

  it("blocks submission when the two passwords do not match", async () => {
    renderResetPasswordPage();

    fillInput("新密码", "NewSecret123!");
    fillInput("确认密码", "Mismatch456!");
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    expect(await screen.findByText("两次输入的密码不一致")).toBeInTheDocument();
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("keeps the submit button disabled when the token is missing", () => {
    renderResetPasswordPage("/reset-password");

    expect(screen.getByRole("button", { name: "重置密码" })).toBeDisabled();
    expect(screen.getByText("当前链接缺少重置令牌，请重新发起找回密码。")).toBeInTheDocument();
  });
});
