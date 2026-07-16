import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { resendVerification, verifyEmail } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import VerifyEmailPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");
  return {
    ...actual,
    resendVerification: vi.fn(),
    verifyEmail: vi.fn(),
  };
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

afterEach(() => vi.clearAllMocks());

function renderPage(entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <div style={themeCssVariables as CSSProperties}>
            <MemoryRouter initialEntries={[entry]}>
              <VerifyEmailPage />
            </MemoryRouter>
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

describe("VerifyEmailPage", () => {
  it("verifies the token from the public URL", async () => {
    vi.mocked(verifyEmail).mockResolvedValue({
      id: "employee-1",
      name: "员工",
      email: "employee@company.com",
      role: "employee",
      status: "active",
      email_verified: true,
      department: "技术部",
      phone: null,
    });

    renderPage("/verify-email?token=verify-token");

    expect(await screen.findByText("邮箱验证成功")).toBeInTheDocument();
    expect(verifyEmail).toHaveBeenCalledWith({ token: "verify-token" });
  });

  it("does not call verify without a token and offers resend recovery", async () => {
    vi.mocked(resendVerification).mockResolvedValue(undefined);

    renderPage("/verify-email");

    expect(await screen.findByText("验证链接缺少令牌")).toBeInTheDocument();
    expect(verifyEmail).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("公司邮箱"), {
      target: { value: "employee@company.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "重新发送验证邮件" }));

    await waitFor(() => {
      expect(resendVerification).toHaveBeenCalledWith({ email: "employee@company.com" });
    });
    expect(await screen.findByRole("button", { name: "验证邮件已发送" })).toBeDisabled();
  });

  it("shows an invalid-token state without leaking backend details", async () => {
    vi.mocked(verifyEmail).mockRejectedValue(new Error("raw token lookup failed"));

    renderPage("/verify-email?token=expired-token");

    expect(await screen.findByText("验证链接无效或已失效")).toBeInTheDocument();
    expect(screen.queryByText("raw token lookup failed")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新发送验证邮件" })).toBeInTheDocument();
  });
});
