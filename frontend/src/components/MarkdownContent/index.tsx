import type { ComponentPropsWithoutRef } from "react";
import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import "./styles.css";

const SAFE_LINK_PATTERN = /^(https?:|mailto:|\/(?!\/)|#)/i;
const EXTERNAL_LINK_PATTERN = /^https?:/i;

interface MarkdownContentProps {
  children: string;
}

function safeLink({ href, children, ...props }: ComponentPropsWithoutRef<"a">) {
  const safeHref = href && SAFE_LINK_PATTERN.test(href) ? href : undefined;
  const external = Boolean(safeHref && EXTERNAL_LINK_PATTERN.test(safeHref));
  return (
    <a
      {...props}
      href={safeHref}
      target={external ? "_blank" : undefined}
      rel={external ? "noopener noreferrer" : undefined}
    >
      {children}
    </a>
  );
}

export function MarkdownContent({ children }: MarkdownContentProps) {
  return (
    <div className="markdown-content">
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{ a: safeLink, img: () => null }}
      >
        {children}
      </Markdown>
    </div>
  );
}
