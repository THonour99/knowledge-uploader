import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PageContainer } from "./PageContainer";

describe("PageContainer", () => {
  it("renders title and description", () => {
    render(
      <MemoryRouter>
        <PageContainer title="测试标题" description="测试描述">
          <p>内容</p>
        </PageContainer>
      </MemoryRouter>,
    );
    expect(screen.getByText("测试标题")).toBeInTheDocument();
    expect(screen.getByText("测试描述")).toBeInTheDocument();
  });

  it("renders breadcrumb when provided", () => {
    render(
      <MemoryRouter>
        <PageContainer
          title="文件详情"
          breadcrumb={[{ label: "文件审核", path: "/files" }, { label: "report.pdf" }]}
        >
          <p>内容</p>
        </PageContainer>
      </MemoryRouter>,
    );
    expect(screen.getByText("文件审核")).toBeInTheDocument();
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
  });

  it("does not render breadcrumb when not provided", () => {
    render(
      <MemoryRouter>
        <PageContainer title="仪表盘">
          <p>内容</p>
        </PageContainer>
      </MemoryRouter>,
    );
    expect(screen.queryByRole("navigation", { name: "面包屑" })).not.toBeInTheDocument();
  });
});
