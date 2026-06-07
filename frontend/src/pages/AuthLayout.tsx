import type { ReactNode } from "react";
import {
  BarChartOutlined,
  CheckCircleOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Typography } from "antd";

interface AuthLayoutProps {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
}

const authFeatures = [
  {
    icon: <CloudUploadOutlined />,
    title: "文件上传",
    description: "多格式文件快速上传，安全存储",
  },
  {
    icon: <RobotOutlined />,
    title: "AI 整理",
    description: "智能提取与标签化，结构化知识",
  },
  {
    icon: <SafetyCertificateOutlined />,
    title: "审核同步",
    description: "多级审核与同步，保证内容质量",
  },
  {
    icon: <BarChartOutlined />,
    title: "统计分析",
    description: "多维数据看板，洞察知识价值",
  },
];

export function AuthLayout({ title, description, children, footer }: AuthLayoutProps) {
  return (
    <main className="auth-page">
      <section className="auth-hero" aria-label="知识库贡献平台能力">
        <div className="auth-brand">
          <span className="auth-brand__mark">
            <DatabaseOutlined />
          </span>
          <span className="auth-brand__name">知识库贡献平台</span>
        </div>

        <div className="auth-hero__copy">
          <Typography.Title level={1} className="auth-hero__title">
            知识库贡献平台
          </Typography.Title>
          <Typography.Paragraph className="auth-hero__subtitle">
            让企业知识持续沉淀与同步
          </Typography.Paragraph>
        </div>

        <div className="auth-feature-list">
          {authFeatures.map((feature) => (
            <div className="auth-feature" key={feature.title}>
              <span className="auth-feature__icon">{feature.icon}</span>
              <span className="auth-feature__copy">
                <Typography.Text strong>{feature.title}</Typography.Text>
                <Typography.Text type="secondary">{feature.description}</Typography.Text>
              </span>
            </div>
          ))}
        </div>

        <div className="auth-preview" aria-hidden="true">
          <div className="auth-preview__header">
            <span className="auth-preview__dot auth-preview__dot--red" />
            <span className="auth-preview__dot auth-preview__dot--yellow" />
            <span className="auth-preview__dot auth-preview__dot--green" />
          </div>
          <div className="auth-preview__body">
            <div className="auth-preview__side">
              <span />
              <span />
              <span />
            </div>
            <div className="auth-preview__content">
              <div className="auth-preview__stat-row">
                <span>
                  <FileTextOutlined />
                  1,248
                </span>
                <span>
                  <CheckCircleOutlined />
                  96%
                </span>
                <span>
                  <RobotOutlined />
                  128
                </span>
              </div>
              <div className="auth-preview__chart">
                <i style={{ height: "38%" }} />
                <i style={{ height: "58%" }} />
                <i style={{ height: "44%" }} />
                <i style={{ height: "74%" }} />
                <i style={{ height: "66%" }} />
                <i style={{ height: "86%" }} />
              </div>
              <div className="auth-preview__rows">
                <span />
                <span />
                <span />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="auth-panel" aria-label={title}>
        <div className="auth-panel__heading">
          <Typography.Title level={2} className="auth-title">
            {title}
          </Typography.Title>
          <Typography.Paragraph className="auth-description">{description}</Typography.Paragraph>
        </div>
        {children}
        {footer ? <div className="auth-panel__footer">{footer}</div> : null}
      </section>
    </main>
  );
}
