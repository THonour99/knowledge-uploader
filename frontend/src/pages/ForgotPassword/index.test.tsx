import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { forgotPassword } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import ForgotPasswordPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    forgotPassword: vi.fn(),
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

function renderForgotPasswordPage() {
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
            <MemoryRouter initialEntries={["/forgot-password"]}>
              <ForgotPasswordPage />
            </MemoryRouter>
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>,
  );
}

describe("ForgotPasswordPage", () => {
  it("submits the email and shows a success notice", async () => {
    vi.mocked(forgotPassword).mockResolvedValue(undefined);

    renderForgotPasswordPage();

    fireEvent.change(screen.getByLabelText("公司邮箱"), {
      target: { value: "zhangsan@company.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送重置邮件" }));

    await waitFor(() => {
      expect(forgotPassword).toHaveBeenCalledWith({ email: "zhangsan@company.com" });
    });

    expect(await screen.findByText(/重置邮件已发送/)).toBeInTheDocument();
  });

  it("shows the backend error message when the request fails", async () => {
    vi.mocked(forgotPassword).mockRejectedValue(new Error("请求过于频繁，请稍后再试"));

    renderForgotPasswordPage();

    fireEvent.change(screen.getByLabelText("公司邮箱"), {
      target: { value: "zhangsan@company.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送重置邮件" }));

    expect(await screen.findByText("请求过于频繁，请稍后再试")).toBeInTheDocument();
  });

  it("blocks submission when the email is invalid", async () => {
    renderForgotPasswordPage();

    fireEvent.change(screen.getByLabelText("公司邮箱"), {
      target: { value: "not-an-email" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送重置邮件" }));

    expect(await screen.findByText("请输入有效邮箱")).toBeInTheDocument();
    expect(forgotPassword).not.toHaveBeenCalled();
  });
});
