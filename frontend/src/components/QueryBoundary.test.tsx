import type { ReactNode } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QueryBoundary } from "./QueryBoundary";

function renderWithProviders(node: ReactNode) {
  return render(
    <ConfigProvider>
      <AntdApp>{node}</AntdApp>
    </ConfigProvider>,
  );
}

describe("QueryBoundary", () => {
  it("renders skeleton while loading", () => {
    const { container } = renderWithProviders(
      <QueryBoundary isLoading isError={false}>
        <div>内容</div>
      </QueryBoundary>,
    );

    expect(container.querySelector(".ant-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("内容")).not.toBeInTheDocument();
  });

  it("renders error alert with retry button and calls onRetry", () => {
    const onRetry = vi.fn();
    renderWithProviders(
      <QueryBoundary
        isLoading={false}
        isError
        error={new Error("接口炸了")}
        onRetry={onRetry}
      >
        <div>内容</div>
      </QueryBoundary>,
    );

    expect(screen.getByText("接口炸了")).toBeInTheDocument();
    expect(screen.queryByText("内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders empty state when isEmpty is true", () => {
    renderWithProviders(
      <QueryBoundary isLoading={false} isError={false} isEmpty emptyDescription="空空如也">
        <div>内容</div>
      </QueryBoundary>,
    );

    expect(screen.getByText("空空如也")).toBeInTheDocument();
    expect(screen.queryByText("内容")).not.toBeInTheDocument();
  });

  it("renders children when data is ready", () => {
    renderWithProviders(
      <QueryBoundary isLoading={false} isError={false}>
        <div>内容</div>
      </QueryBoundary>,
    );

    expect(screen.getByText("内容")).toBeInTheDocument();
  });
});
