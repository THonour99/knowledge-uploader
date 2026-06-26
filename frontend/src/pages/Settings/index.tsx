import { useEffect, useState } from "react";
import {
  ApiOutlined,
  BellOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App as AntdApp,
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  type ConfigGroup,
  type ConfigItem,
  type RagflowConnectionTestResult,
  getConfigs,
  testRagflowConnection,
  updateConfigs,
} from "../../api/client";
import { KpiCard, type KpiTone } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

// ── Label map ────────────────────────────────────────────────────────────────

const labelMap: Record<string, string> = {
  // basic
  "basic.system_name": "系统名称",
  "basic.system_logo_url": "系统 Logo URL",
  "basic.default_language": "默认语言",
  "basic.default_timezone": "默认时区",
  "basic.notification_channels": "通知渠道",
  "basic.admin_contact_email": "管理员联系邮箱",
  // upload
  "upload.allowed_extensions": "允许的扩展名",
  "upload.max_file_size_mb": "单文件最大大小（MB）",
  "upload.user_quota_mb": "用户配额（MB）",
  "upload.allow_multi_file": "允许批量上传",
  "upload.allow_user_delete": "允许用户删除",
  "upload.enable_duplicate_check": "启用重复检测",
  // processing
  "processing.auto_parse_on_upload": "上传后自动解析",
  "processing.auto_sync_after_parse": "解析后自动同步",
  "processing.sync_after_ai_analysis": "AI 分析后同步",
  "processing.task_max_retries": "任务最大重试次数",
  "processing.task_timeout_seconds": "任务超时时间（秒）",
  "processing.parse_max_pages": "解析最大页数",
  "processing.parse_max_chars": "解析最大字符数",
  // security
  "security.allowed_email_domains": "允许的邮箱域名",
  "security.password_min_length": "密码最小长度",
  "security.login_max_failed_attempts": "登录失败锁定阈值",
  "security.login_lock_minutes": "锁定时长（分钟）",
  "security.require_email_verification": "要求邮箱验证",
  "security.require_review_before_sync": "同步前需审核",
  "security.block_critical_sensitive_sync": "阻断严重敏感内容同步",
  // ragflow
  "ragflow.base_url": "RAGFlow 服务地址",
  "ragflow.api_key": "RAGFlow API Key",
  "ragflow.default_dataset_id": "默认数据集 ID",
  "ragflow.auto_sync_enabled": "自动同步",
  "ragflow.sync_max_retries": "同步最大重试次数",
  "ragflow.sync_timeout_seconds": "同步超时时间（秒）",
  "ragflow.allow_high_risk_sync": "允许高风险文档同步",
  "ragflow.delete_remote_on_file_delete": "删除文件时删除远端",
  "ragflow.keep_remote_on_archive": "归档时保留远端",
};

function getLabel(item: ConfigItem): string {
  return labelMap[item.key] ?? item.description ?? item.key;
}

// ── Static overview data ──────────────────────────────────────────────────────

interface SettingsStatusCard {
  title: string;
  value: string;
  description: string;
  icon: ReactNode;
  tone: KpiTone;
}

type SettingsTabKey = "basic" | "upload" | "processing" | "security" | "ragflow" | "services";

interface SettingsSummaryItem {
  key: SettingsTabKey;
  title: string;
  meta: string;
  description: string;
  icon: ReactNode;
  tone: KpiTone;
  status: {
    kind: "dataset" | "health";
    value: string;
  };
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

const settingsSummaryItems: SettingsSummaryItem[] = [
  {
    key: "basic",
    title: "基础参数",
    meta: "6 项配置",
    description: "平台名称、语言、时区和通知渠道",
    icon: <SettingOutlined />,
    tone: "primary",
    status: { kind: "dataset", value: "enabled" },
  },
  {
    key: "upload",
    title: "上传策略",
    meta: "容量与类型",
    description: "文件白名单、配额和去重规则",
    icon: <CloudServerOutlined />,
    tone: "success",
    status: { kind: "dataset", value: "enabled" },
  },
  {
    key: "processing",
    title: "处理链路",
    meta: "Celery 队列",
    description: "解析、AI 分析和同步触发策略",
    icon: <ExperimentOutlined />,
    tone: "warning",
    status: { kind: "health", value: "ok" },
  },
  {
    key: "security",
    title: "安全审计",
    meta: "强制审计",
    description: "登录锁定、邮箱验证和敏感阻断",
    icon: <SafetyCertificateOutlined />,
    tone: "purple",
    status: { kind: "dataset", value: "enabled" },
  },
  {
    key: "ragflow",
    title: "RAGFlow 同步",
    meta: "密钥加密",
    description: "连接地址、API Key 和同步重试",
    icon: <ApiOutlined />,
    tone: "primary",
    status: { kind: "dataset", value: "pending" },
  },
  {
    key: "services",
    title: "服务状态",
    meta: "4 个依赖",
    description: "API、PostgreSQL、MinIO 与 RAGFlow",
    icon: <CheckCircleOutlined />,
    tone: "success",
    status: { kind: "health", value: "ok" },
  },
];

const policyRows: PolicyRow[] = [
  {
    key: "upload-rate",
    name: "上传限流",
    value: "10 次 / 分钟 / 用户",
    enabled: true,
    owner: "document",
  },
  {
    key: "login-lock",
    name: "登录失败锁定",
    value: "5 次失败锁定 15 分钟",
    enabled: true,
    owner: "auth",
  },
  {
    key: "critical-risk",
    name: "严重敏感内容阻断",
    value: "critical 默认禁止同步",
    enabled: true,
    owner: "review",
  },
  {
    key: "audit",
    name: "管理员审计日志",
    value: "所有管理员操作强制记录",
    enabled: true,
    owner: "audit",
  },
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
  { title: "规则", dataIndex: "value", key: "value" },
  {
    title: "状态",
    dataIndex: "enabled",
    key: "enabled",
    width: 120,
    render: (enabled: boolean) => (
      <StatusTag kind="dataset" value={enabled ? "enabled" : "disabled"} />
    ),
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
    render: (value: ServiceRow["status"]) => (
      <StatusTag kind="dataset" value={value} variant="dot" />
    ),
  },
  { title: "延迟", dataIndex: "latency", key: "latency", width: 100 },
  {
    title: "可用性",
    dataIndex: "uptime",
    key: "uptime",
    width: 170,
    render: (value: number) => <Progress percent={value} size="small" />,
  },
];

// ── Configuration command strip ───────────────────────────────────────────────

function SettingsCommandStrip({
  activeTab,
  onSelectTab,
}: {
  activeTab: SettingsTabKey;
  onSelectTab: (key: SettingsTabKey) => void;
}) {
  return (
    <section className="settings-command-strip" aria-label="配置运行摘要">
      <div className="settings-command-strip__main">
        <span className="settings-command-strip__icon">
          <SettingOutlined />
        </span>
        <span className="settings-command-strip__copy">
          <Typography.Text strong className="settings-command-strip__title">
            配置中心
          </Typography.Text>
          <Typography.Text type="secondary">
            汇总平台基础参数、上传策略、安全审计和 RAGFlow 连接状态，点击卡片快速切换配置域。
          </Typography.Text>
        </span>
      </div>
      <div className="settings-command-strip__cards" aria-label="配置域快捷入口">
        {settingsSummaryItems.map((item) => (
          <button
            aria-pressed={activeTab === item.key}
            className={`settings-command-card settings-command-card--${item.tone} ${
              activeTab === item.key ? "settings-command-card--active" : ""
            }`}
            key={item.key}
            onClick={() => onSelectTab(item.key)}
            type="button"
          >
            <span className="settings-command-card__icon">{item.icon}</span>
            <span className="settings-command-card__body">
              <span className="settings-command-card__topline">
                <Typography.Text strong>{item.title}</Typography.Text>
                <StatusTag kind={item.status.kind} value={item.status.value} variant="dot" />
              </span>
              <Typography.Text className="settings-command-card__meta">{item.meta}</Typography.Text>
              <Typography.Text type="secondary" className="settings-command-card__description">
                {item.description}
              </Typography.Text>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

// ── Generic config form field renderer ───────────────────────────────────────

function ConfigField({ item }: { item: ConfigItem }) {
  const label = getLabel(item);

  if (item.is_secret || item.value_type === "secret") {
    const placeholder = item.masked_value ?? "未设置";

    return (
      <Form.Item label={label} name={item.key} help="留空表示不修改">
        <Input.Password placeholder={placeholder} autoComplete="new-password" />
      </Form.Item>
    );
  }

  if (item.value_type === "bool") {
    return (
      <Form.Item label={label} name={item.key} valuePropName="checked">
        <Switch />
      </Form.Item>
    );
  }

  if (item.value_type === "int") {
    return (
      <Form.Item label={label} name={item.key}>
        <InputNumber className="settings-number-input" />
      </Form.Item>
    );
  }

  if (item.value_type === "list") {
    return (
      <Form.Item label={label} name={item.key}>
        <Select mode="tags" />
      </Form.Item>
    );
  }

  // string (default)
  return (
    <Form.Item label={label} name={item.key}>
      <Input />
    </Form.Item>
  );
}

// ── Helper: build initial values from config items ───────────────────────────

type FormValues = Record<string, string | number | boolean | string[] | null | undefined>;

function buildInitialValues(items: ConfigItem[]): FormValues {
  const values: FormValues = {};
  for (const item of items) {
    if (!item.is_secret && item.value_type !== "secret") {
      // Cast to supported form field types
      if (
        typeof item.value === "string" ||
        typeof item.value === "number" ||
        typeof item.value === "boolean" ||
        Array.isArray(item.value) ||
        item.value === null
      ) {
        values[item.key] = item.value as FormValues[string];
      }
    }
  }

  return values;
}

// ── Helper: filter payload (strip empty secrets) ──────────────────────────────

function buildPayload(formValues: FormValues, items: ConfigItem[]): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const item of items) {
    const val = formValues[item.key];

    if (item.is_secret || item.value_type === "secret") {
      if (typeof val === "string" && val.trim() !== "") {
        payload[item.key] = val;
      }
    } else {
      payload[item.key] = val;
    }
  }

  return payload;
}

// ── Generic config panel ──────────────────────────────────────────────────────

interface ConfigPanelProps {
  group: ConfigGroup;
  cardTitle: string;
  /** When true, wrap save action in a Modal.confirm */
  dangerConfirm?: boolean;
}

function ConfigPanel({ group, cardTitle, dangerConfirm = false }: ConfigPanelProps) {
  const { message, modal } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FormValues>();

  const queryKey = ["configs", group] as const;

  const query = useQuery({
    queryKey,
    queryFn: () => getConfigs(group),
  });

  // Populate form when data arrives — use useEffect to avoid calling setFieldsValue during render
  useEffect(() => {
    if (query.data) {
      form.setFieldsValue(buildInitialValues(query.data.items));
    }
  }, [form, query.data]);

  const saveMutation = useMutation({
    mutationFn: (items: Record<string, unknown>) => updateConfigs(group, items),
    onSuccess: async () => {
      void message.success("配置已保存");
      await queryClient.invalidateQueries({ queryKey });
    },
    onError: (err: Error) => {
      void message.error(err.message);
    },
  });

  function handleSave() {
    void form.validateFields().then((values: FormValues) => {
      const configItems = query.data?.items ?? [];
      const payload = buildPayload(values, configItems);

      if (dangerConfirm) {
        void modal.confirm({
          title: "确认修改安全配置",
          content: "安全配置变更将立即生效并影响所有用户，确认保存？",
          okText: "确认保存",
          cancelText: "取消",
          onOk: () => {
            saveMutation.mutate(payload);
          },
        });
      } else {
        saveMutation.mutate(payload);
      }
    });
  }

  if (query.isLoading) {
    return <Card className="settings-panel" title={cardTitle} loading />;
  }

  if (query.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message={`加载 ${cardTitle} 失败`}
        description={query.error.message}
        action={<Button onClick={() => void query.refetch()}>重试</Button>}
      />
    );
  }

  const items = query.data?.items ?? [];

  return (
    <Card className="settings-panel" title={cardTitle}>
      <Alert
        type="info"
        showIcon
        message="配置变更会写入审计日志，影响上传、审核与同步链路。"
        style={{ marginBottom: 16 }}
      />
      <Form form={form} layout="vertical" requiredMark={false}>
        <div className="settings-form-grid">
          {items.map((item) => (
            <ConfigField key={item.key} item={item} />
          ))}
        </div>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saveMutation.isPending}
          onClick={handleSave}
        >
          保存
        </Button>
      </Form>
    </Card>
  );
}

// ── RAGFlow panel with test-connection ────────────────────────────────────────

function RagflowPanel() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const [testResult, setTestResult] = useState<RagflowConnectionTestResult | null>(null);

  const group: ConfigGroup = "ragflow";
  const queryKey = ["configs", group] as const;

  const query = useQuery({
    queryKey,
    queryFn: () => getConfigs(group),
  });

  useEffect(() => {
    if (query.data) {
      form.setFieldsValue(buildInitialValues(query.data.items));
    }
  }, [form, query.data]);

  const saveMutation = useMutation({
    mutationFn: (items: Record<string, unknown>) => updateConfigs(group, items),
    onSuccess: async () => {
      void message.success("RAGFlow 配置已保存");
      await queryClient.invalidateQueries({ queryKey });
    },
    onError: (err: Error) => {
      void message.error(err.message);
    },
  });

  const testMutation = useMutation({
    mutationFn: testRagflowConnection,
    onSuccess: (result: RagflowConnectionTestResult) => {
      setTestResult(result);
    },
    onError: (err: Error) => {
      setTestResult({ ok: false, latency_ms: null, error: err.message });
    },
  });

  function handleSave() {
    void form.validateFields().then((values: FormValues) => {
      const configItems = query.data?.items ?? [];
      saveMutation.mutate(buildPayload(values, configItems));
    });
  }

  if (query.isLoading) {
    return <Card className="settings-panel" title="RAGFlow 配置" loading />;
  }

  if (query.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="加载 RAGFlow 配置失败"
        description={query.error.message}
        action={<Button onClick={() => void query.refetch()}>重试</Button>}
      />
    );
  }

  const items = query.data?.items ?? [];

  return (
    <div className="settings-panel-stack">
      <Card className="settings-panel" title="RAGFlow 配置">
        <Alert
          type="info"
          showIcon
          message="配置变更会写入审计日志，影响上传、审核与同步链路。"
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical" requiredMark={false}>
          <div className="settings-form-grid">
            {items.map((item) => (
              <ConfigField key={item.key} item={item} />
            ))}
          </div>
          <Space wrap>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saveMutation.isPending}
              onClick={handleSave}
            >
              保存
            </Button>
            <Button
              icon={<ExperimentOutlined />}
              loading={testMutation.isPending}
              onClick={() => {
                setTestResult(null);
                testMutation.mutate();
              }}
            >
              测试连接
            </Button>
          </Space>
        </Form>
      </Card>

      {testResult !== null ? (
        testResult.ok ? (
          <Alert
            type="success"
            showIcon
            message={`连接成功${typeof testResult.latency_ms === "number" ? `，延迟 ${testResult.latency_ms}ms` : ""}`}
          />
        ) : (
          <Alert
            type="error"
            showIcon
            message="连接失败"
            description={testResult.error ?? "未知错误"}
          />
        )
      ) : null}
    </div>
  );
}

// ── Service status panel (static) ─────────────────────────────────────────────

function ServiceSettingsPanel() {
  return (
    <div className="settings-panel-stack">
      <Card className="settings-panel table-card" title="服务连接状态">
        <Table<ServiceRow>
          rowKey="key"
          columns={serviceColumns}
          dataSource={serviceRows}
          pagination={false}
        />
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

// ── Main page ──────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<SettingsTabKey>("basic");

  function handleReload() {
    void queryClient.invalidateQueries({ queryKey: ["configs"] });
  }

  return (
    <PageContainer
      title="系统设置"
      description="管理平台基础参数、安全策略、服务连接配置，所有变更写入审计日志。"
      actions={
        <Space className="settings-page-actions" wrap>
          <Button icon={<ReloadOutlined />} onClick={handleReload}>
            重新加载
          </Button>
        </Space>
      }
    >
      <div className="settings-status-grid">
        {statusCards.map((card) => (
          <KpiCard
            key={card.title}
            icon={card.icon}
            title={card.title}
            value={card.value}
            description={card.description}
            tone={card.tone}
          />
        ))}
      </div>

      <SettingsCommandStrip activeTab={activeTab} onSelectTab={setActiveTab} />

      <Tabs
        activeKey={activeTab}
        className="settings-tabs"
        onChange={(key) => setActiveTab(key as SettingsTabKey)}
        items={[
          {
            key: "basic",
            label: "基础",
            children: <ConfigPanel group="basic" cardTitle="基础配置" />,
          },
          {
            key: "upload",
            label: "上传",
            children: <ConfigPanel group="upload" cardTitle="上传配置" />,
          },
          {
            key: "processing",
            label: "处理",
            children: <ConfigPanel group="processing" cardTitle="处理配置" />,
          },
          {
            key: "security",
            label: "安全",
            children: (
              <div className="settings-panel-stack">
                <ConfigPanel group="security" cardTitle="安全策略" dangerConfirm />
                <Card className="settings-panel table-card" title="生效策略">
                  <Table<PolicyRow>
                    rowKey="key"
                    columns={policyColumns}
                    dataSource={policyRows}
                    pagination={false}
                  />
                </Card>
              </div>
            ),
          },
          {
            key: "ragflow",
            label: "RAGFlow",
            children: <RagflowPanel />,
          },
          {
            key: "services",
            label: "服务状态",
            children: <ServiceSettingsPanel />,
          },
        ]}
      />
    </PageContainer>
  );
}
