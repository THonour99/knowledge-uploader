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
});
