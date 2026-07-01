import type { ReactNode } from "react";
import { DatabaseOutlined } from "@ant-design/icons";
import { Typography } from "antd";

interface AuthLayoutProps {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function AuthLayout({ title, description, children, footer }: AuthLayoutProps) {
  return (
    <main className="auth-page">
      <section className="auth-hero" aria-label="品牌展示">
        <div className="auth-hero__grid" aria-hidden="true" />
        <div className="auth-orb auth-orb--1" aria-hidden="true" />
        <div className="auth-orb auth-orb--2" aria-hidden="true" />
        <div className="auth-orb auth-orb--3" aria-hidden="true" />
        <div className="auth-ring" aria-hidden="true" />

        <div className="auth-brand">
          <span className="auth-brand__mark">
            <DatabaseOutlined />
          </span>
          <Typography.Title level={2} className="auth-brand__title">
            知识库贡献平台
          </Typography.Title>
          <Typography.Paragraph className="auth-brand__tagline">
            让企业知识持续沉淀与同步
          </Typography.Paragraph>
          <div className="auth-brand__badges">
            <span className="auth-badge">AI 智能分析</span>
            <span className="auth-badge">多级审核</span>
            <span className="auth-badge">RAGFlow 同步</span>
          </div>
        </div>
      </section>

      <section className="auth-panel" aria-label={title}>
        <div className="auth-mobile-brand">
          <span className="auth-brand__mark">
            <DatabaseOutlined />
          </span>
          <span className="auth-brand__name">知识库贡献平台</span>
        </div>
        <div className="auth-card">
          <div className="auth-card__icon">
            <DatabaseOutlined />
          </div>
          <Typography.Title level={3} className="auth-card__title">
            {title}
          </Typography.Title>
          <Typography.Paragraph className="auth-card__desc">{description}</Typography.Paragraph>
          {children}
          {footer ? <div className="auth-card__footer">{footer}</div> : null}
        </div>
      </section>
    </main>
  );
}
