import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ApiError, login, resendVerification } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { themeCssVariables } from "../../theme/tokens";
import LoginPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return { ...actual, login: vi.fn(), resendVerification: vi.fn() };
});

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
});

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.clearAllMocks();
});

function renderLogin() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <div style={themeCssVariables as CSSProperties}>
            <MemoryRouter initialEntries={["/login"]}>
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route path="/dashboard" element={<span>dashboard-entry</span>} />
              </Routes>
            </MemoryRouter>
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

function fillCredentials() {
  fireEvent.change(screen.getByLabelText("公司邮箱"), {
    target: { value: "employee@company.com" },
  });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "Secret123!" } });
}

describe("LoginPage", () => {
  it("sends every role through the stable dashboard entry", async () => {
    vi.mocked(login).mockResolvedValue({
      access_token: "token",
      token_type: "bearer",
      user: {
        id: "employee-1",
        name: "员工",
        email: "employee@company.com",
        role: "employee",
      },
    });
    renderLogin();
    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    expect(await screen.findByText("dashboard-entry")).toBeInTheDocument();
  });

  it("handles EMAIL_NOT_VERIFIED separately and resends verification", async () => {
    vi.mocked(login).mockRejectedValue(
      new ApiError("forbidden", { status: 403, code: "EMAIL_NOT_VERIFIED" }),
    );
    vi.mocked(resendVerification).mockResolvedValue(undefined);
    renderLogin();
    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    expect(await screen.findByText("邮箱尚未验证")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新发送" }));
    await waitFor(() => {
      expect(resendVerification).toHaveBeenCalledWith({ email: "employee@company.com" });
    });
    expect(useAuthStore.getState().accessToken).toBeNull();
  });
});
