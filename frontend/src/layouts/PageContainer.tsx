import type { ReactNode } from "react";
import { Breadcrumb, Typography } from "antd";
import { Link } from "react-router-dom";

export interface BreadcrumbItem {
  label: string;
  path?: string;
}

interface PageContainerProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  breadcrumb?: BreadcrumbItem[];
  children: ReactNode;
}

export function PageContainer({
  title,
  description,
  actions,
  className,
  breadcrumb,
  children,
}: PageContainerProps) {
  return (
    <main className={["page-container", className].filter(Boolean).join(" ")}>
      {breadcrumb && breadcrumb.length > 0 ? (
        <Breadcrumb
          className="page-breadcrumb"
          aria-label="面包屑"
          items={breadcrumb.map((item) => ({
            title: item.path ? <Link to={item.path}>{item.label}</Link> : item.label,
          }))}
        />
      ) : null}
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
