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
});
