import type { CSSProperties, ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  type Tag,
  type TagListResponse,
  createTag,
  listTags,
  mergeTag,
  updateTag,
} from "../../api/client";
import type * as ApiClientModule from "../../api/client";
import { themeCssVariables } from "../../theme/tokens";
import TagsPage from "./index";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof ApiClientModule>("../../api/client");

  return {
    ...actual,
    listTags: vi.fn(),
    createTag: vi.fn(),
    updateTag: vi.fn(),
    mergeTag: vi.fn(),
    deleteTag: vi.fn(),
  };
});

// ── Mock data ─────────────────────────────────────────────────────────────────

const mockTag1: Tag = {
  id: "tag-1",
  name: "财务报告",
  description: "财务相关文档标签",
  usage_count: 12,
  is_system_generated: false,
  enabled: true,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

const mockTag2: Tag = {
  id: "tag-2",
  name: "技术规范",
  description: null,
  usage_count: 5,
  is_system_generated: true,
  enabled: false,
  created_at: "2026-06-02T00:00:00Z",
  updated_at: "2026-06-02T00:00:00Z",
};

const mockTag3: Tag = {
  id: "tag-3",
  name: "空标签",
  description: null,
  usage_count: 0,
  is_system_generated: false,
  enabled: true,
  created_at: "2026-06-03T00:00:00Z",
  updated_at: "2026-06-03T00:00:00Z",
};

const mockTagsResponse: TagListResponse = {
  items: [mockTag1, mockTag2, mockTag3],
  total: 3,
  page: 1,
  page_size: 50,
};

// ── Test helpers ──────────────────────────────────────────────────────────────

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

// ── Helper: click OK button in the currently open modal ─────────────────────

function clickModalOk() {
  const footerButtons = document.querySelectorAll(".ant-modal-footer .ant-btn-primary");
  if (footerButtons.length > 0) {
    fireEvent.click(footerButtons[0] as HTMLElement);
    return;
  }
  const allButtons = screen.getAllByRole("button");
  const primaryBtn = allButtons.find((btn) => btn.className.includes("ant-btn-primary"));
  if (primaryBtn) fireEvent.click(primaryBtn);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("TagsPage", () => {
  it("renders tag list with name, usage_count, source and enabled columns", async () => {
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);

    renderWithProviders(<TagsPage />);

    // Page heading
    expect(await screen.findByRole("heading", { name: "标签管理" })).toBeInTheDocument();

    // Tag names appear
    expect(await screen.findByText("财务报告")).toBeInTheDocument();
    expect(screen.getByText("技术规范")).toBeInTheDocument();
    expect(screen.getByText("空标签")).toBeInTheDocument();

    const governance = screen.getByRole("region", { name: "标签治理状态" });
    expect(governance).toHaveTextContent("标签治理状态");
    expect(governance).toHaveTextContent("2 个启用标签");
    expect(governance).toHaveTextContent("1 个停用，平台共 3 个标签");
    expect(governance).toHaveTextContent("17 次文件关联");
    expect(governance).toHaveTextContent("1 个空闲标签可清理或合并");
    expect(governance).toHaveTextContent("1 个系统标签");
    expect(governance).toHaveTextContent("2 个手动维护标签");
    expect(governance).toHaveTextContent("全部标签视图");

    // Usage counts (usage_count column)
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();

    // Source column labels
    expect(screen.getByText("系统生成")).toBeInTheDocument();
    expect(screen.getAllByText("手动创建").length).toBeGreaterThan(0);

    // Table column headers present
    expect(screen.getAllByText("使用次数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("标签名称").length).toBeGreaterThan(0);
    expect(screen.getAllByText("来源").length).toBeGreaterThan(0);
    expect(screen.getAllByText("启用").length).toBeGreaterThan(0);
  });

  it("submits createTag with correct name payload when adding a new tag", async () => {
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(createTag).mockResolvedValue({
      ...mockTag3,
      id: "tag-new",
      name: "新标签",
    });

    renderWithProviders(<TagsPage />);

    // Wait for list to render
    await screen.findByText("财务报告");

    // Open create modal
    const addButton = screen.getByRole("button", { name: /新增标签/ });
    fireEvent.click(addButton);

    // Modal title should appear (button text + modal title = multiple matches)
    const modalTitles = await screen.findAllByText("新增标签");
    expect(modalTitles.length).toBeGreaterThanOrEqual(2);

    // Fill in the name field (first non-disabled textbox in the modal)
    const textboxes = screen.getAllByRole("textbox").filter((el) => !el.hasAttribute("disabled"));
    fireEvent.change(textboxes[0], { target: { value: "新标签" } });

    // Click OK
    clickModalOk();

    await waitFor(() => {
      expect(createTag).toHaveBeenCalledWith(
        expect.objectContaining({ name: "新标签" }),
      );
    });
  });

  it("calls mergeTag(sourceId, { target_tag_id }) when merge form is submitted with a selection", async () => {
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(mergeTag).mockResolvedValue({ ...mockTag2, usage_count: 17 });

    renderWithProviders(<TagsPage />);

    await screen.findByText("财务报告");

    // Click the first "合并" button (for tag-1: 财务报告)
    const mergeButtons = screen.getAllByRole("button", { name: /合并/ });
    fireEvent.click(mergeButtons[0]);

    // Merge modal title should include the source tag name
    await screen.findByText(/合并标签/);

    // Simulate selecting a target tag: open the select dropdown
    const selectSelectors = document.querySelectorAll(".ant-select-selector");
    expect(selectSelectors.length).toBeGreaterThan(0);
    // Click to open dropdown
    fireEvent.mouseDown(selectSelectors[0] as HTMLElement);

    // Wait for dropdown options
    await waitFor(() => {
      const options = document.querySelectorAll(".ant-select-item-option");
      expect(options.length).toBeGreaterThan(0);
    });

    // Click the first option (技术规范 = tag-2)
    const optionItems = document.querySelectorAll(".ant-select-item-option");
    fireEvent.click(optionItems[0] as HTMLElement);

    // Click OK button
    clickModalOk();

    await waitFor(() => {
      expect(mergeTag).toHaveBeenCalledWith(
        "tag-1",
        expect.objectContaining({ target_tag_id: "tag-2" }),
      );
    });
  });

  it("calls updateTag with enabled: false when toggling an enabled tag's switch off", async () => {
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(updateTag).mockResolvedValue({ ...mockTag1, enabled: false });

    renderWithProviders(<TagsPage />);

    await screen.findByText("财务报告");

    // All Switch components in the enabled column
    const switches = screen.getAllByRole("switch");
    // First row is tag-1 (enabled: true) — click to disable
    fireEvent.click(switches[0]);

    await waitFor(() => {
      expect(updateTag).toHaveBeenCalledWith(
        "tag-1",
        expect.objectContaining({ enabled: false }),
      );
    });
  });

  it("sends null description when clearing an existing tag description", async () => {
    vi.mocked(listTags).mockResolvedValue(mockTagsResponse);
    vi.mocked(updateTag).mockResolvedValue({ ...mockTag1, description: null });

    renderWithProviders(<TagsPage />);

    await screen.findByText("财务报告");

    const editButtons = screen.getAllByRole("button", { name: "编辑" });
    fireEvent.click(editButtons[0]);

    await screen.findByText("编辑标签");
    const descriptionInput = document.querySelector("#description") as HTMLTextAreaElement;
    expect(descriptionInput).not.toBeNull();
    fireEvent.change(descriptionInput, { target: { value: "" } });
    clickModalOk();

    await waitFor(() => {
      expect(updateTag).toHaveBeenCalledWith(
        "tag-1",
        expect.objectContaining({ description: null }),
      );
    });
  });
});
