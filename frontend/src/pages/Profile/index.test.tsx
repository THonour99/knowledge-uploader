import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { type UserProfile, changePassword, getMe } from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import ProfilePage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    getMe: vi.fn(),
    changePassword: vi.fn(),
  };
});

const mockProfile: UserProfile = {
  id: "user-1",
  name: "张三",
  email: "zhangsan@example.com",
  role: "dept_admin",
  status: "active",
  email_verified: true,
  department: "技术部",
  phone: "13800138000",
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
    <MemoryRouter>
      <ConfigProvider>
        <AntdApp>
          <QueryClientProvider client={queryClient}>
            <div style={themeCssVariables as CSSProperties}>{node}</div>
          </QueryClientProvider>
        </AntdApp>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("ProfilePage", () => {
  it("renders user profile information from getMe", async () => {
    vi.mocked(getMe).mockResolvedValue(mockProfile);

    renderWithProviders(<ProfilePage />);

    expect(await screen.findByText("张三")).toBeInTheDocument();
    expect(screen.getAllByText("zhangsan@example.com").length).toBeGreaterThan(0);
    expect(screen.getByText("技术部")).toBeInTheDocument();
    // Role should be displayed in Chinese
    expect(screen.getAllByText("部门管理员").length).toBeGreaterThan(0);

  });

  it("calls changePassword with correct params and resets form on success", async () => {
    vi.mocked(getMe).mockResolvedValue(mockProfile);
    vi.mocked(changePassword).mockResolvedValue(undefined);

    renderWithProviders(<ProfilePage />);

    // Wait for the page to load
    await screen.findByText("张三");

    // Fill in the password form
    const currentPasswordInput = screen.getByLabelText("当前密码");
    const newPasswordInput = screen.getByLabelText("新密码");
    const confirmPasswordInput = screen.getByLabelText("确认新密码");

    fireEvent.change(currentPasswordInput, { target: { value: "OldPass123" } });
    fireEvent.change(newPasswordInput, { target: { value: "NewPass456" } });
    fireEvent.change(confirmPasswordInput, { target: { value: "NewPass456" } });

    const submitButton = screen.getByRole("button", { name: "修改密码" });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(changePassword).toHaveBeenCalledWith({
        current_password: "OldPass123",
        new_password: "NewPass456",
      });
    });

    // Form should be reset after success — Ant Design resets React state,
    // which removes the defaultValue attribute from the DOM input
    await waitFor(() => {
      const input = screen.getByLabelText("当前密码") as HTMLInputElement;
      // After resetFields(), Ant Design clears the form field value
      expect(input.getAttribute("value")).toBeNull();
    });
  });

  it("blocks submission when new password and confirm password do not match", async () => {
    vi.mocked(getMe).mockResolvedValue(mockProfile);

    renderWithProviders(<ProfilePage />);

    await screen.findByText("张三");

    const currentPasswordInput = screen.getByLabelText("当前密码");
    const newPasswordInput = screen.getByLabelText("新密码");
    const confirmPasswordInput = screen.getByLabelText("确认新密码");

    fireEvent.change(currentPasswordInput, { target: { value: "OldPass123" } });
    fireEvent.change(newPasswordInput, { target: { value: "NewPass456" } });
    fireEvent.change(confirmPasswordInput, { target: { value: "DifferentPass789" } });

    const submitButton = screen.getByRole("button", { name: "修改密码" });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText("两次密码不一致")).toBeInTheDocument();
    });

    expect(changePassword).not.toHaveBeenCalled();
  });

  it("displays error message when backend returns error", async () => {
    vi.mocked(getMe).mockResolvedValue(mockProfile);
    vi.mocked(changePassword).mockRejectedValue({
      response: { data: { message: "当前密码错误" } },
    });

    renderWithProviders(<ProfilePage />);

    await screen.findByText("张三");

    const currentPasswordInput = screen.getByLabelText("当前密码");
    const newPasswordInput = screen.getByLabelText("新密码");
    const confirmPasswordInput = screen.getByLabelText("确认新密码");

    fireEvent.change(currentPasswordInput, { target: { value: "WrongPass" } });
    fireEvent.change(newPasswordInput, { target: { value: "NewPass456" } });
    fireEvent.change(confirmPasswordInput, { target: { value: "NewPass456" } });

    const submitButton = screen.getByRole("button", { name: "修改密码" });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(changePassword).toHaveBeenCalled();
    });
  });
});
