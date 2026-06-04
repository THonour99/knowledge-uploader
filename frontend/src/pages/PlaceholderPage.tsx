import { Button, Card, Col, Row, Space, Typography } from "antd";

import { StatusTag, type StatusKind } from "../components/StatusTag";
import { PageContainer } from "../layouts/PageContainer";

interface StatusSample {
  kind: StatusKind;
  value: string;
}

interface PlaceholderPageProps {
  title: string;
  description: string;
  primaryAction?: string;
  samples?: StatusSample[];
}

export function PlaceholderPage({
  title,
  description,
  primaryAction = "待实现",
  samples = [],
}: PlaceholderPageProps) {
  return (
    <PageContainer
      title={title}
      description={description}
      actions={<Button type="primary">{primaryAction}</Button>}
    >
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card className="placeholder-card">
            <Space direction="vertical" size={16}>
              <Typography.Title level={4}>阶段 0 占位</Typography.Title>
              <Typography.Paragraph type="secondary">
                页面路由和全局布局已预留，业务功能会在后续阶段按模块实现。
              </Typography.Paragraph>
              {samples.length > 0 ? (
                <Space wrap>
                  {samples.map((sample) => (
                    <StatusTag
                      key={`${sample.kind}:${sample.value}`}
                      kind={sample.kind}
                      value={sample.value}
                    />
                  ))}
                </Space>
              ) : null}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card className="placeholder-card" title="页面状态">
            <Typography.Text type="secondary">待接入后端数据</Typography.Text>
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
}
