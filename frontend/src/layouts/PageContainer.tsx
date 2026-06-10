import type { ReactNode } from "react";
import { Typography } from "antd";

interface PageContainerProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function PageContainer({
  title,
  description,
  actions,
  className,
  children,
}: PageContainerProps) {
  return (
    <main className={["page-container", className].filter(Boolean).join(" ")}>
      <div className="page-header">
        <div>
          <Typography.Title level={2} className="page-title">
            {title}
          </Typography.Title>
          {description ? (
            <Typography.Paragraph className="page-description">{description}</Typography.Paragraph>
          ) : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {children}
    </main>
  );
}
