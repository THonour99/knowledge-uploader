import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("renders an svg with a line path for a valid series", () => {
    const { container } = render(<Sparkline data={[1, 5, 3, 8, 6]} />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
    const paths = container.querySelectorAll("path");
    // 至少一条折线 path(fill 默认开启时还会多一条面积 path)
    expect(paths.length).toBeGreaterThanOrEqual(1);
    expect(paths[paths.length - 1].getAttribute("d")).toMatch(/^M/);
  });

  it("omits the area path when fill is false", () => {
    const { container } = render(<Sparkline data={[1, 2, 3]} fill={false} />);
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });

  it("renders nothing when there are fewer than two points", () => {
    const { container } = render(<Sparkline data={[42]} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
