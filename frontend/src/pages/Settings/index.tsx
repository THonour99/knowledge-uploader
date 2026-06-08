import {
  ApiOutlined,
  BellOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";

import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

interface SettingsStatusCard {
  title: string;
  value: string;
  description: string;
  icon: ReactNode;
  tone: "primary" | "success" | "warning" | "purple";
}

interface PolicyRow {
  key: string;
  name: string;
  value: string;
  enabled: boolean;
  owner: string;
}

interface ServiceRow {
  key: string;
  service: string;
  endpoint: string;
  status: "enabled" | "pending" | "disabled";
  latency: string;
  uptime: number;
}

const statusCards: SettingsStatusCard[] = [
  {
    title: "系统版本",
    value: "v0.9.0",
    description: "阶段 9 集成验证",
    icon: <SettingOutlined />,
    tone: "primary",
  },
  {
    title: "服务健康",
    value: "12/12",
    description: "核心容器运行正常",
    icon: <CheckCircleOutlined />,
    tone: "success",
  },
  {
    title: "安全策略",
    value: "8 项",
    description: "上传、登录、审计",
    icon: <SafetyCertificateOutlined />,
    tone: "purple",
  },
  {
    title: "待处理配置",
    value: "3",
    description: "需要管理员确认",
    icon: <BellOutlined />,
    tone: "warning",
  },
];

const policyRows: PolicyRow[] = [
  { key: "upload-rate", name: "上传限流", value: "10 次 / 分钟 / 用户", enabled: true, owner: "document" },
  { key: "login-lock", name: "登录失败锁定", value: "5 次失败锁定 15 分钟", enabled: true, owner: "auth" },
  { key: "critical-risk", name: "严重敏感内容阻断", value: "critical 默认禁止同步", enabled: true, owner: "review" },
  { key: "audit", name: "管理员审计日志", value: "所有管理员操作强制记录", enabled: true, owner: "audit" },
];

const serviceRows: ServiceRow[] = [
  {
    key: "backend-api",
    service: "backend-api",
    endpoint: "/api/system/health",
    status: "enabled",
    latency: "38ms",
    uptime: 99,
  },
  {
    key: "postgres",
    service: "PostgreSQL 16",
    endpoint: "postgres:5432",
    status: "enabled",
    latency: "12ms",
    uptime: 99,
  },
  {
    key: "minio",
    service: "MinIO",
    endpoint: "minio:9000",
    status: "enabled",
    latency: "24ms",
    uptime: 98,
  },
  {
    key: "ragflow",
    service: "RAGFlow API",
    endpoint: "192.168.4.46:8092",
    status: "pending",
    latency: "82ms",
    uptime: 93,
  },
];

const policyColumns: ColumnsType<PolicyRow> = [
  {
    title: "策略",
    dataIndex: "name",
    key: "name",
    render: (value: string, record) => (
      <Space direction="vertical" size={2}>
        <Typography.Text strong>{value}</Typography.Text>
        <Typography.Text type="secondary">模块：{record.owner}</Typography.Text>
      </Space>
    ),
  },
  {
    title: "规则",
    dataIndex: "value",
    key: "value",
  },
  {
    title: "状态",
    dataIndex: "enabled",
    key: "enabled",
    width: 120,
    render: (enabled: boolean) => <StatusTag kind="dataset" value={enabled ? "enabled" : "disabled"} />,
  },
];

const serviceColumns: ColumnsType<ServiceRow> = [
  {
    title: "服务",
    dataIndex: "service",
    key: "service",
    render: (value: string, record) => (
      <Space direction="vertical" size={2}>
        <Typography.Text strong>{value}</Typography.Text>
        <Typography.Text type="secondary">{record.endpoint}</Typography.Text>
      </Space>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 120,
    render: (value: ServiceRow["status"]) => <StatusTag kind="dataset" value={value} variant="dot" />,
  },
  {
    title: "延迟",
    dataIndex: "latency",
    key: "latency",
    width: 100,
  },
  {
    title: "可用性",
    dataIndex: "uptime",
    key: "uptime",
    width: 170,
    render: (value: number) => <Progress percent={value} size="small" />,
  },
];

function SettingsStatusCardView({ card }: { card: SettingsStatusCard }) {
  return (
    <Card className="settings-status-card">
      <div className="settings-status-card__body">
        <span className={`settings-status-card__icon settings-status-card__icon--${card.tone}`}>
          {card.icon}
        </span>
        <span className="settings-status-card__copy">
          <Typography.Text type="secondary">{card.title}</Typography.Text>
          <Typography.Title level={3}>{card.value}</Typography.Title>
          <Typography.Text type="secondary">{card.description}</Typography.Text>
        </span>
      </div>
    </Card>
  );
}

function GeneralSettingsPanel() {
  return (
    <div className="settings-panel-stack">
      <Alert
        type="info"
        showIcon
        message="配置变更会写入审计日志，影响上传、审核与同步链路。"
      />
      <Card className="settings-panel" title="基础配置">
        <Form
          layout="vertical"
          requiredMark={false}
          initialValues={{
            platformName: "知识库贡献平台",
            companyDomain: "company.com",
            defaultVisibility: "department",
            maxFileSize: 200,
            retentionDays: 365,
            enableRegistration: true,
            requireEmailVerification: true,
          }}
        >
          <div className="settings-form-grid">
            <Form.Item label="平台名称" name="platformName">
              <Input />
            </Form.Item>
            <Form.Item label="公司邮箱域名" name="companyDomain">
              <Input addonBefore="@" />
            </Form.Item>
            <Form.Item label="默认可见范围" name="defaultVisibility">
              <Select
                options={[
                  { label: "仅自己", value: "private" },
                  { label: "同部门", value: "department" },
                  { label: "全公司", value: "company" },
                ]}
              />
            </Form.Item>
            <Form.Item label="单文件大小上限" name="maxFileSize">
              <InputNumber min={1} max={1024} addonAfter="MB" className="settings-number-input" />
            </Form.Item>
            <Form.Item label="对象保留周期" name="retentionDays">
              <InputNumber min={30} max={3650} addonAfter="天" className="settings-number-input" />
            </Form.Item>
          </div>

          <div className="settings-switch-grid">
            <Form.Item name="enableRegistration" valuePropName="checked">
              <Switch checkedChildren="开放注册" unCheckedChildren="关闭注册" />
            </Form.Item>
            <Form.Item name="requireEmailVerification" valuePropName="checked">
              <Switch checkedChildren="邮箱验证" unCheckedChildren="不验证" />
            </Form.Item>
          </div>

          <Button type="primary" icon={<SaveOutlined />}>
            保存基础配置
          </Button>
        </Form>
      </Card>
    </div>
  );
}

function SecuritySettingsPanel() {
  return (
    <div className="settings-panel-stack">
      <Card className="settings-panel" title="上传与安全策略">
        <Form
          layout="vertical"
          requiredMark={false}
          initialValues={{
            allowedExtensions: ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv",
            loginFailures: 5,
            uploadRate: 10,
            encryption: true,
          }}
        >
          <Form.Item label="允许的文件扩展名" name="allowedExtensions">
            <Input.TextArea rows={3} />
          </Form.Item>
          <div className="settings-form-grid">
            <Form.Item label="登录失败锁定阈值" name="loginFailures">
              <InputNumber min={1} max={20} addonAfter="次" className="settings-number-input" />
            </Form.Item>
            <Form.Item label="用户上传限流" name="uploadRate">
              <InputNumber min={1} max={100} addonAfter="次/分钟" className="settings-number-input" />
            </Form.Item>
          </div>
          <Form.Item name="encryption" valuePropName="checked">
            <Switch checkedChildren="字段加密开启" unCheckedChildren="字段加密关闭" />
          </Form.Item>
          <Button type="primary" icon={<SaveOutlined />}>
            保存安全策略
          </Button>
        </Form>
      </Card>

      <Card className="settings-panel table-card" title="生效策略">
        <Table<PolicyRow> rowKey="key" columns={policyColumns} dataSource={policyRows} pagination={false} />
      </Card>
    </div>
  );
}

function NotificationSettingsPanel() {
  return (
    <Card className="settings-panel" title="通知与审核">
      <Form
        layout="vertical"
        requiredMark={false}
        initialValues={{
          reviewMode: "manual",
          reviewSla: 24,
          notifyEmail: true,
          notifyDingTalk: true,
          rejectedTemplate: "文件未通过审核，请根据审核意见调整后重新提交。",
        }}
      >
        <div className="settings-form-grid">
          <Form.Item label="审核模式" name="reviewMode">
            <Select
              options={[
                { label: "管理员人工审核", value: "manual" },
                { label: "低风险自动通过", value: "hybrid" },
              ]}
            />
          </Form.Item>
          <Form.Item label="审核 SLA" name="reviewSla">
            <InputNumber min={1} max={168} addonAfter="小时" className="settings-number-input" />
          </Form.Item>
        </div>
        <div className="settings-switch-grid">
          <Form.Item name="notifyEmail" valuePropName="checked">
            <Switch checkedChildren="邮件通知" unCheckedChildren="邮件关闭" />
          </Form.Item>
          <Form.Item name="notifyDingTalk" valuePropName="checked">
            <Switch checkedChildren="钉钉通知" unCheckedChildren="钉钉关闭" />
          </Form.Item>
        </div>
        <Form.Item label="拒绝通知模板" name="rejectedTemplate">
          <Input.TextArea rows={4} />
        </Form.Item>
        <Button type="primary" icon={<SaveOutlined />}>
          保存通知配置
        </Button>
      </Form>
    </Card>
  );
}

function ServiceSettingsPanel() {
  return (
    <div className="settings-panel-stack">
      <Card className="settings-panel table-card" title="服务连接状态">
        <Table<ServiceRow> rowKey="key" columns={serviceColumns} dataSource={serviceRows} pagination={false} />
      </Card>
      <div className="settings-service-grid">
        <Card className="settings-panel" title="核心依赖">
          <div className="settings-dependency-list">
            <Space>
              <DatabaseOutlined className="settings-text--primary" />
              <Typography.Text strong>PostgreSQL 16</Typography.Text>
            </Space>
            <Space>
              <CloudServerOutlined className="settings-text--success" />
              <Typography.Text strong>MinIO 对象存储</Typography.Text>
            </Space>
            <Space>
              <ApiOutlined className="settings-text--purple" />
              <Typography.Text strong>RAGFlow API</Typography.Text>
            </Space>
            <Space>
              <LockOutlined className="settings-text--warning" />
              <Typography.Text strong>Fernet 字段加密</Typography.Text>
            </Space>
          </div>
        </Card>
        <Card className="settings-panel" title="后台任务">
          <div className="settings-health-bars">
            <div>
              <Typography.Text>Celery Worker</Typography.Text>
              <Progress percent={94} />
            </div>
            <div>
              <Typography.Text>Outbox Dispatcher</Typography.Text>
              <Progress percent={98} />
            </div>
            <div>
              <Typography.Text>RAGFlow Sync Queue</Typography.Text>
              <Progress percent={76} status="active" />
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <PageContainer
      title="系统设置"
      description="管理平台基础参数、安全策略、通知审核和服务连接状态。"
      actions={
        <Space className="settings-page-actions" wrap>
          <Button icon={<ReloadOutlined />}>重新加载</Button>
          <Button type="primary" icon={<SaveOutlined />}>
            保存全部
          </Button>
        </Space>
      }
    >
      <div className="settings-status-grid">
        {statusCards.map((card) => (
          <SettingsStatusCardView key={card.title} card={card} />
        ))}
      </div>

      <Card className="settings-tabs-card">
        <Tabs
          items={[
            { key: "general", label: "基础设置", children: <GeneralSettingsPanel /> },
            { key: "security", label: "上传与安全", children: <SecuritySettingsPanel /> },
            { key: "notification", label: "通知与审核", children: <NotificationSettingsPanel /> },
            { key: "services", label: "服务状态", children: <ServiceSettingsPanel /> },
          ]}
        />
      </Card>
    </PageContainer>
  );
}
