import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type Category,
  type CategoryListResponse,
  createCategory,
  listCategories,
  updateCategory,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import CategoriesPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listCategories: vi.fn(),
    createCategory: vi.fn(),
    updateCategory: vi.fn(),
  };
});

const mockCategory1: Category = {
  id: "cat-1",
  name: "技术文档",
  code: "tech-docs",
  description: "技术相关文档",
  parent_id: null,
  allow_ai_recommend: true,
  keywords: ["技术", "文档"],
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

const mockCategory2: Category = {
  id: "cat-2",
  name: "人事档案",
  code: "hr-files",
  description: null,
  parent_id: null,
  allow_ai_recommend: false,
  keywords: [],
  created_at: "2026-06-02T00:00:00Z",
  updated_at: "2026-06-02T00:00:00Z",
};

const mockCategoriesResponse: CategoryListResponse = {
  items: [mockCategory1, mockCategory2],
  total: 2,
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
  vi.clearAllMocks();
});

describe("CategoriesPage", () => {
  it("renders category list with all required columns", async () => {
    vi.mocked(listCategories).mockResolvedValue(mockCategoriesResponse);

    renderWithProviders(<CategoriesPage />);

    // Page title
    expect(await screen.findByRole("heading", { name: "分类管理" })).toBeInTheDocument();

    // Category names
    expect(await screen.findByText("技术文档")).toBeInTheDocument();
    expect(screen.getByText("人事档案")).toBeInTheDocument();

    expect(screen.getByText("分类策略列表")).toBeInTheDocument();
    expect(screen.getByText("当前维护 2 个分类，1 个已配置关键词")).toBeInTheDocument();

    // Category codes (appear in both name cell and code cell, use getAllBy)
    expect(screen.getAllByText("tech-docs").length).toBeGreaterThan(0);
    expect(screen.getAllByText("hr-files").length).toBeGreaterThan(0);

    // Verify table column headers are rendered (getAllByText handles duplicates safely)
    expect(screen.getAllByText("分类名称").length).toBeGreaterThan(0);
    expect(screen.getAllByText("分类编码").length).toBeGreaterThan(0);
    expect(screen.getAllByText("描述").length).toBeGreaterThan(0);
    expect(screen.getAllByText("关键词").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AI 可推荐").length).toBeGreaterThan(0);
  });

  it("submits createCategory with correct parameters when adding a new category", async () => {
    vi.mocked(listCategories).mockResolvedValue(mockCategoriesResponse);
    vi.mocked(createCategory).mockResolvedValue({
      ...mockCategory1,
      id: "cat-new",
      name: "新分类",
      code: "new-cat",
    });

    renderWithProviders(<CategoriesPage />);

    // Wait for data to load
    await screen.findByText("技术文档");

    // Open create modal
    const addButton = screen.getByRole("button", { name: /新增分类/ });
    fireEvent.click(addButton);

    // Wait for modal to render — look for the form inputs that only appear in the modal
    // The modal title "新增分类" appears as text in the page after clicking
    const modalTitles = await screen.findAllByText("新增分类");
    // There should be at least 2: the button text and the modal title
    expect(modalTitles.length).toBeGreaterThanOrEqual(2);
    const formSummary = await screen.findByRole("region", { name: "分类配置摘要" });
    expect(formSummary).toHaveTextContent("新建分类策略");
    expect(formSummary).toHaveTextContent("待配置编码");
    expect(screen.getByText("推荐策略")).toBeInTheDocument();

    // Get all visible non-disabled textboxes; they now include modal form inputs
    const textboxes = screen.getAllByRole("textbox").filter((el) => !el.hasAttribute("disabled"));
    // The modal form has name, code, description and keywords inputs.
    // First input is name
    fireEvent.change(textboxes[0], { target: { value: "新分类" } });
    // Second input is code
    fireEvent.change(textboxes[1], { target: { value: "new-cat" } });

    // Submit form: find button with "确定" or "OK" text content
    // Find the modal footer OK button by its primary button class
    // Ant Design renders okText in a primary button in the modal footer
    const footerButtons = document.querySelectorAll(
      ".ant-modal-footer .ant-btn-primary, .ant-modal-confirm-btns .ant-btn-primary",
    );
    if (footerButtons.length === 0) {
      // Fallback: find by role and check if it's a primary button
      const allButtons = screen.getAllByRole("button");
      const primaryBtn = allButtons.find((btn) => btn.className.includes("ant-btn-primary"));
      expect(primaryBtn).toBeDefined();
      fireEvent.click(primaryBtn!);
    } else {
      fireEvent.click(footerButtons[0] as HTMLElement);
    }

    await waitFor(() => {
      expect(createCategory).toHaveBeenCalledWith({
        name: "新分类",
        code: "new-cat",
        description: null,
        parent_id: null,
        allow_ai_recommend: true,
        keywords: [],
      });
    });
  });

  it("renders the fixed review policy and no removed category controls", async () => {
    vi.mocked(listCategories).mockResolvedValue(mockCategoriesResponse);

    renderWithProviders(<CategoriesPage />);

    await screen.findByText("技术文档");
    fireEvent.click(screen.getByRole("button", { name: /新增分类/ }));

    await screen.findAllByText("新增分类");
    expect(
      screen.getByText("所有文档必须审核，禁止自动同步；Dataset 仅在审批时明确选择。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("默认可见范围")).not.toBeInTheDocument();
    expect(screen.queryByText("关联知识库")).not.toBeInTheDocument();
    expect(screen.queryByText("分类 Prompt")).not.toBeInTheDocument();
    expect(screen.queryByText("需要审核")).not.toBeInTheDocument();
    expect(screen.queryByText("员工可选")).not.toBeInTheDocument();
    expect(screen.queryByText("AI 分析")).not.toBeInTheDocument();
    expect(screen.queryByText("敏感检测")).not.toBeInTheDocument();
    expect(screen.queryByText("自动同步")).not.toBeInTheDocument();
  });

  it("submits updateCategory with correct parameters when editing a category", async () => {
    vi.mocked(listCategories).mockResolvedValue(mockCategoriesResponse);
    vi.mocked(updateCategory).mockResolvedValue({
      ...mockCategory1,
      name: "技术文档（已修改）",
    });

    renderWithProviders(<CategoriesPage />);

    await screen.findByText("技术文档");

    // Click edit button for first category
    const editButtons = screen.getAllByRole("button", { name: "编辑" });
    fireEvent.click(editButtons[0]);

    // Wait for modal to open — "编辑分类" appears as modal title
    await screen.findByText("编辑分类");

    // Get all visible non-disabled textboxes; first one in the form is name
    const textboxes = screen.getAllByRole("textbox").filter((el) => !el.hasAttribute("disabled"));
    fireEvent.change(textboxes[0], { target: { value: "技术文档（已修改）" } });

    // Find the modal footer OK button by its primary button class
    // Ant Design renders okText in a primary button in the modal footer
    const footerButtons = document.querySelectorAll(
      ".ant-modal-footer .ant-btn-primary, .ant-modal-confirm-btns .ant-btn-primary",
    );
    if (footerButtons.length === 0) {
      // Fallback: find by role and check if it's a primary button
      const allButtons = screen.getAllByRole("button");
      const primaryBtn = allButtons.find((btn) => btn.className.includes("ant-btn-primary"));
      expect(primaryBtn).toBeDefined();
      fireEvent.click(primaryBtn!);
    } else {
      fireEvent.click(footerButtons[0] as HTMLElement);
    }

    await waitFor(() => {
      expect(updateCategory).toHaveBeenCalledWith(
        "cat-1",
        expect.objectContaining({
          name: "技术文档（已修改）",
        }),
      );
    });
  });

  it("updates only the AI recommendation flag from the table switch", async () => {
    vi.mocked(listCategories).mockResolvedValue(mockCategoriesResponse);
    vi.mocked(updateCategory).mockResolvedValue({
      ...mockCategory1,
      allow_ai_recommend: false,
    });

    renderWithProviders(<CategoriesPage />);

    await screen.findByText("技术文档");

    fireEvent.click(screen.getByRole("switch", { name: "技术文档 AI 可推荐" }));

    await waitFor(() => {
      expect(updateCategory).toHaveBeenCalledWith("cat-1", { allow_ai_recommend: false });
    });
  });
});
