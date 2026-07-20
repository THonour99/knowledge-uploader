import { render, screen } from "@testing-library/react";

import { MarkdownContent } from ".";

describe("MarkdownContent", () => {
  it("renders GFM content and safe external links", () => {
    const { container } = render(
      <MarkdownContent>{`# 标题

- [x] 已完成

| 字段 | 值 |
| --- | --- |
| A | B |

[官网](https://example.com)`}</MarkdownContent>,
    );

    expect(screen.getByRole("heading", { name: "标题" })).toBeInTheDocument();
    expect(container.querySelector("table")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "官网" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not render HTML, images, scripts, or unsafe links", () => {
    const { container } = render(
      <MarkdownContent>{`<script>alert(1)</script>

<b>raw html</b>

![tracking](https://evil.example/pixel.png)

[unsafe](javascript:alert(1))

[protocol-relative](//evil.example/path)`}</MarkdownContent>,
    );

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(container.querySelector("b")).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(screen.getByText("unsafe").closest("a")).not.toHaveAttribute("href");
    expect(screen.getByText("protocol-relative").closest("a")).not.toHaveAttribute("href");
    expect(screen.getByText("protocol-relative").closest("a")).not.toHaveAttribute("target");
  });
});
