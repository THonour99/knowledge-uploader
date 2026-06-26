import type { ReactNode } from "react";
import { CloudUploadOutlined } from "@ant-design/icons";
import { App as AntdApp, ConfigProvider } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KpiCard } from "./KpiCard";

function stubReducedMotion(): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

function renderCard(node: ReactNode) {
  return render(
    <ConfigProvider>
      <AntdApp>{node}</AntdApp>
    </ConfigProvider>,
  );
}

describe("KpiCard", () => {
  beforeEach(() => stubReducedMotion());
  afterEach(() => vi.unstubAllGlobals());

  it("renders title and thousands-formatted value", () => {
    renderCard(
      <KpiCard icon={<CloudUploadOutlined />} title="文件总数" value={12345} tone="primary" />,
    );
    expect(screen.getByText("文件总数")).toBeInTheDocument();
    expect(screen.getByText("12,345")).toBeInTheDocument();
  });

  it("uses a custom formatter when provided", () => {
    renderCard(
      <KpiCard
        icon={null}
        title="成功率"
        value={87.6}
        tone="purple"
        formatter={(n) => `${n.toFixed(1)}%`}
      />,
    );
    expect(screen.getByText("87.6%")).toBeInTheDocument();
  });

  it("renders a non-numeric display value as-is", () => {
    renderCard(
      <KpiCard icon={null} title="系统版本" value="v0.9.0" tone="info" />,
    );
    expect(screen.getByText("系统版本")).toBeInTheDocument();
    expect(screen.getByText("v0.9.0")).toBeInTheDocument();
  });
  it("shows an upward delta as good (green) when rising is positive", () => {
    const { container } = renderCard(
      <KpiCard icon={null} title="同步" value={100} tone="success" deltaPct={12.3} />,
    );
    expect(screen.getByText("12.3%")).toBeInTheDocument();
    expect(container.querySelector(".kpi-card__delta--good")).toBeInTheDocument();
  });

  it("treats an increase as bad when deltaPositiveIsGood is false", () => {
    const { container } = renderCard(
      <KpiCard
        icon={null}
        title="失败任务"
        value={5}
        tone="danger"
        deltaPct={8}
        deltaPositiveIsGood={false}
      />,
    );
    expect(container.querySelector(".kpi-card__delta--bad")).toBeInTheDocument();
  });

  it("renders a sparkline when trend has at least two points", () => {
    const { container } = renderCard(
      <KpiCard icon={null} title="趋势" value={1} tone="primary" trend={[1, 2, 3, 4]} />,
    );
    expect(container.querySelector(".kpi-card__spark")).toBeInTheDocument();
  });

  it("fires onClick when clickable", () => {
    const onClick = vi.fn();
    renderCard(<KpiCard icon={null} title="可点卡" value={1} tone="primary" onClick={onClick} />);
    fireEvent.click(screen.getByText("可点卡"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
