import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusTag } from "./StatusTag";

describe("StatusTag", () => {
  it("renders the known file status label", () => {
    render(<StatusTag kind="file" value="pending_review" />);

    expect(screen.getByText("待审核")).toBeInTheDocument();
  });

  it("falls back to the raw value for an unknown status", () => {
    render(<StatusTag kind="file" value="unknown_status" />);

    expect(screen.getByText("unknown_status")).toBeInTheDocument();
  });

  it("labels missing risk data as not assessed", () => {
    render(<StatusTag kind="risk" value="unknown" />);

    expect(screen.getByText("未评估")).toBeInTheDocument();
  });

  it("renders dataset status labels", () => {
    render(<StatusTag kind="dataset" value="unbound" />);

    expect(screen.getByText("未绑定 Dataset")).toBeInTheDocument();
  });

  it("renders expiry status labels", () => {
    render(<StatusTag kind="expiry" value="expiring" />);

    expect(screen.getByText("即将过期")).toBeInTheDocument();
  });

  it("renders health status labels", () => {
    render(<StatusTag kind="health" value="ok" />);
    expect(screen.getByText("正常")).toBeInTheDocument();

    render(<StatusTag kind="health" value="error" />);
    expect(screen.getByText("异常")).toBeInTheDocument();
  });

  it("adds readable status semantics to tag and dot variants", () => {
    render(<StatusTag kind="sync" value="syncing" />);
    expect(screen.getByLabelText("同步状态：同步中")).toHaveAttribute("title", "同步状态：同步中");

    render(<StatusTag kind="health" value="ok" variant="dot" />);
    expect(screen.getByLabelText("健康状态：正常")).toHaveClass("status-tag-dot");
  });
});
