import { useEffect, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Typography,
} from "antd";
import {
  ApiOutlined,
  ClockCircleOutlined,
  EditOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";

import {
  type AiConfigResponse,
  type AiFeatureConfig,
  type AiPromptTemplate,
  type AiProviderConfig,
  type AiProviderPayload,
  type AiProviderTestResult,
  type AiSensitiveRule,
  createAiProvider,
  getAiConfig,
  testAiProvider,
  updateAiFeature,
  updateAiProvider,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const aiConfigQueryKey = ["ai-config"] as const;

const globalSwitches = [
  {
    key: "ai_analysis_enabled",
    featureKey: "ai_analysis",
    title: "AI 总开关",
    description: "开启后，系统将使用 AI 能力进行文档分析。",
    checkedText: "已开启",
    uncheckedText: "已关闭",
  },
  {
    key: "allow_external_llm",
    featureKey: "allow_external_llm",
    title: "是否允许外部模型",
    description: "允许使用第三方 OpenAI-compatible 模型供应商。",
    checkedText: "允许",
    uncheckedText: "禁止",
  },
  {
    key: "allow_sync_when_analysis_failed",
    featureKey: "allow_sync_when_analysis_failed",
    title: "分析失败后是否允许同步",
    description: "AI 分析失败时仍允许文档继续同步到知识库。",
    checkedText: "允许同步",
    uncheckedText: "禁止同步",
  },
] as const;

const ruleActionLabels: Record<string, string> = {
  flag: "标记",
  require_review: "进入复核",
  block_sync: "阻断同步",
  block: "阻断",
  warn: "阻断并告警",
  alert: "告警",
  desensitize: "脱敏",
  allow: "放行",
};

const providerStatusValue = (status?: string | null) => {
  if (status === "success") {
    return "synced";
  }
  if (status === "failed") {
    return "failed";
  }
  return "not_synced";
};

const formatDateTime = (value?: string | null) =>
  value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "-";

const compactNumber = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });

const providerTypeOptions = [
  { label: "OpenAI-compatible", value: "openai_compatible" },
  { label: "本地 OpenAI-compatible", value: "local_openai_compatible" },
  { label: "vLLM", value: "vllm" },
  { label: "Ollama", value: "ollama" },
  { label: "LM Studio", value: "lmstudio" },
  { label: "自定义", value: "custom" },
  { label: "Mock", value: "mock" },
  { label: "禁用", value: "disabled" },
];

const providerTypesWithoutEndpoint = new Set(["mock", "disabled"]);

type ProviderFormMode = "create" | "edit";

type ProviderModalState = {
  mode: ProviderFormMode;
  provider?: AiProviderConfig;
};

type AiProviderFormValues = AiProviderPayload;

const providerDefaultValues: AiProviderFormValues = {
  name: "",
  provider_type: "openai_compatible",
  base_url: "",
  api_key: "",
  clear_api_key: false,
  chat_model: "",
  embedding_model: "",
  vision_model: "",
  is_internal: false,
  enabled: true,
  priority: 100,
  timeout_seconds: 60,
  max_retry_count: 2,
  max_input_tokens: null,
  max_output_tokens: null,
  temperature: 0.2,
  top_p: null,
};

function optionalText(value?: string | null): string | null {
  const cleaned = value?.trim();
  return cleaned ? cleaned : null;
}

function optionalNumber(value?: number | null): number | null {
  return typeof value === "number" ? value : null;
}

function providerFormValues(provider?: AiProviderConfig): AiProviderFormValues {
  if (!provider) {
    return { ...providerDefaultValues };
  }

  return {
    name: provider.name,
    provider_type: provider.provider_type,
    base_url: provider.base_url ?? "",
    api_key: "",
    clear_api_key: false,
    chat_model: provider.chat_model ?? "",
    embedding_model: provider.embedding_model ?? "",
    vision_model: provider.vision_model ?? "",
    is_internal: provider.is_internal,
    enabled: provider.enabled,
    priority: provider.priority,
    timeout_seconds: provider.timeout_seconds,
    max_retry_count: provider.max_retry_count,
    max_input_tokens: provider.max_input_tokens ?? null,
    max_output_tokens: provider.max_output_tokens ?? null,
    temperature: provider.temperature,
    top_p: provider.top_p ?? null,
  };
}

function providerPayloadFromValues(values: AiProviderFormValues): AiProviderPayload {
  const apiKey = optionalText(values.api_key);
  const payload: AiProviderPayload = {
    name: values.name.trim(),
    provider_type: values.provider_type,
    base_url: optionalText(values.base_url),
    clear_api_key: Boolean(values.clear_api_key),
    chat_model: optionalText(values.chat_model),
    embedding_model: optionalText(values.embedding_model),
    vision_model: optionalText(values.vision_model),
    is_internal: Boolean(values.is_internal),
    enabled: Boolean(values.enabled),
    priority: values.priority,
    timeout_seconds: values.timeout_seconds,
    max_retry_count: values.max_retry_count,
    max_input_tokens: optionalNumber(values.max_input_tokens),
    max_output_tokens: optionalNumber(values.max_output_tokens),
    temperature: values.temperature,
    top_p: optionalNumber(values.top_p),
  };

  if (apiKey) {
    payload.api_key = apiKey;
  }

  return payload;
}

function countEnabled(items: Array<{ enabled: boolean }>): number {
  return items.filter((item) => item.enabled).length;
}

function AiGovernanceStrip({ config }: { config: AiConfigResponse }) {
  const enabledFeatures = countEnabled(config.features);
  const enabledProviders = countEnabled(config.providers);
  const testedProviders = config.providers.filter(
    (provider) => provider.enabled && provider.last_test_status === "success",
  ).length;
  const failedProviders = config.providers.filter(
    (provider) => provider.enabled && provider.last_test_status === "failed",
  ).length;
  const enabledPrompts = countEnabled(config.prompt_templates);
  const defaultPrompts = config.prompt_templates.filter((template) => template.is_default).length;
  const enabledRules = countEnabled(config.sensitive_rules);
  const ruleHits = config.sensitive_rules.reduce((total, rule) => total + rule.hit_count, 0);
  const featureCoverage =
    config.features.length === 0 ? 0 : Math.round((enabledFeatures / config.features.length) * 100);
  const providerReadiness =
    enabledProviders === 0 ? 0 : Math.round((testedProviders / enabledProviders) * 100);
  const stripStatus =
    config.global.ai_analysis_enabled && enabledProviders > 0 && failedProviders === 0
      ? "ok"
      : "unknown";
  const lanes = [
    {
      key: "features",
      icon: <ExperimentOutlined />,
      title: "能力覆盖",
      primary: `${enabledFeatures}/${config.features.length} 项已开启`,
      secondary: `覆盖率 ${featureCoverage}%`,
      status: { kind: "dataset" as const, value: enabledFeatures > 0 ? "enabled" : "pending" },
    },
    {
      key: "providers",
      icon: <ApiOutlined />,
      title: "模型连通",
      primary: `${testedProviders}/${enabledProviders} 个通过测试`,
      secondary: `${config.providers.length} 个供应商，${failedProviders} 个异常`,
      status: {
        kind: "health" as const,
        value: failedProviders > 0 ? "error" : testedProviders > 0 ? "ok" : "unknown",
      },
    },
    {
      key: "prompts",
      icon: <FileTextOutlined />,
      title: "Prompt 默认",
      primary: `${defaultPrompts} 个默认模板`,
      secondary: `${enabledPrompts} 个模板可用`,
      status: { kind: "health" as const, value: defaultPrompts > 0 ? "ok" : "unknown" },
    },
    {
      key: "rules",
      icon: <SafetyCertificateOutlined />,
      title: "敏感规则",
      primary: `${enabledRules} 条已启用`,
      secondary: `${compactNumber.format(ruleHits)} 次累计命中`,
      status: { kind: "risk" as const, value: ruleHits > 0 ? "high" : "low" },
    },
  ];

  return (
    <section className="ai-governance-strip" role="region" aria-label="AI 治理总览">
      <div className="ai-governance-strip__main">
        <span className="ai-governance-strip__icon">
          <ThunderboltOutlined />
        </span>
        <span className="ai-governance-strip__copy">
          <span className="ai-governance-strip__title-row">
            <Typography.Text strong className="ai-governance-strip__title">
              AI 治理总览
            </Typography.Text>
            <StatusTag kind="health" value={stripStatus} variant="dot" />
          </span>
          <Typography.Text type="secondary">
            集中检查分析能力、模型连通、Prompt 模板与敏感规则覆盖。
          </Typography.Text>
        </span>
        <span className="ai-governance-strip__total">
          <strong>{enabledFeatures}</strong>
          <Typography.Text type="secondary">启用能力</Typography.Text>
        </span>
      </div>
      <div className="ai-governance-strip__lanes" aria-label="AI 治理指标">
        {lanes.map((lane) => (
          <div className="ai-governance-lane" key={lane.key}>
            <span className="ai-governance-lane__icon">{lane.icon}</span>
            <span className="ai-governance-lane__body">
              <span className="ai-governance-lane__topline">
                <Typography.Text strong>{lane.title}</Typography.Text>
                <StatusTag kind={lane.status.kind} value={lane.status.value} variant="dot" />
              </span>
              <strong>{lane.primary}</strong>
              <Typography.Text type="secondary">{lane.secondary}</Typography.Text>
            </span>
          </div>
        ))}
      </div>
      <div className="ai-governance-strip__readiness" aria-label="模型供应商就绪度">
        <span className="ai-governance-strip__readiness-copy">
          <Typography.Text type="secondary">供应商就绪度</Typography.Text>
          <strong>{providerReadiness}%</strong>
        </span>
        <Progress percent={providerReadiness} size="small" showInfo={false} />
      </div>
    </section>
  );
}

function AiOverview({ config }: { config: AiConfigResponse }) {
  const enabledFeatures = countEnabled(config.features);
  const enabledProviders = countEnabled(config.providers);
  const testedProviders = config.providers.filter(
    (provider) => provider.enabled && provider.last_test_status === "success",
  ).length;
  const enabledPrompts = countEnabled(config.prompt_templates);
  const defaultPrompts = config.prompt_templates.filter((template) => template.is_default).length;
  const enabledRules = countEnabled(config.sensitive_rules);
  const ruleHits = config.sensitive_rules.reduce((total, rule) => total + rule.hit_count, 0);

  return (
    <div className="metric-grid ai-config-kpi-grid">
      <KpiCard
        icon={<ThunderboltOutlined />}
        title="AI 总开关"
        value={config.global.ai_analysis_enabled ? "已开启" : "已关闭"}
        description={config.global.allow_external_llm ? "允许外部模型" : "仅内部模型"}
        tone={config.global.ai_analysis_enabled ? "success" : "warning"}
      />
      <KpiCard
        icon={<ExperimentOutlined />}
        title="启用功能"
        value={`${enabledFeatures}/${config.features.length}`}
        description="文档分析能力覆盖"
        tone={enabledFeatures > 0 ? "primary" : "warning"}
      />
      <KpiCard
        icon={<ApiOutlined />}
        title="可用供应商"
        value={enabledProviders}
        description={`${testedProviders} 个最近测试通过`}
        tone={enabledProviders > 0 ? "success" : "danger"}
      />
      <KpiCard
        icon={<SafetyCertificateOutlined />}
        title="敏感治理"
        value={enabledRules}
        description={`${compactNumber.format(ruleHits)} 次累计命中`}
        tone={ruleHits > 0 ? "warning" : "purple"}
      />
      <KpiCard
        icon={<FileTextOutlined />}
        title="Prompt 模板"
        value={enabledPrompts}
        description={`${defaultPrompts} 个默认模板`}
        tone="info"
      />
    </div>
  );
}

function EmptyBlock({ description }: { description: string }) {
  return (
    <div className="ai-config-empty">
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
    </div>
  );
}

function GlobalSwitchCard({
  title,
  description,
  checked,
  checkedText,
  uncheckedText,
  loading,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  checkedText: string;
  uncheckedText: string;
  loading?: boolean;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <Card className="ai-config-switch-card">
      <Space align="start" size={12} className="ai-config-switch-card__content">
        <span className="ai-config-switch-card__icon">
          <ThunderboltOutlined />
        </span>
        <span className="ai-config-switch-card__copy">
          <Typography.Text strong>{title}</Typography.Text>
          <Typography.Text type="secondary">{description}</Typography.Text>
          <StatusTag kind="dataset" value={checked ? "enabled" : "disabled"} />
        </span>
        <Switch checked={checked} loading={loading} onChange={onChange} />
      </Space>
      <Typography.Text type="secondary" className="ai-config-switch-card__state">
        {checked ? checkedText : uncheckedText}
      </Typography.Text>
    </Card>
  );
}

function FeaturesPanel({
  features,
  global,
  onFeatureToggle,
  togglingKey,
}: {
  features: AiFeatureConfig[];
  global: Record<(typeof globalSwitches)[number]["key"], boolean>;
  onFeatureToggle: (featureKey: string, enabled: boolean) => void;
  togglingKey?: string;
}) {
  return (
    <div className="ai-config-panel-stack">
      <Card className="document-panel" title="全局 AI 设置">
        <div className="ai-config-global-grid">
          {globalSwitches.map((item) => (
            <GlobalSwitchCard
              key={item.key}
              title={item.title}
              description={item.description}
              checked={global[item.key]}
              checkedText={item.checkedText}
              uncheckedText={item.uncheckedText}
              loading={togglingKey === item.featureKey}
              onChange={(enabled) => onFeatureToggle(item.featureKey, enabled)}
            />
          ))}
        </div>
      </Card>

      <Card className="document-panel" title="功能开关与配置">
        {features.length > 0 ? (
          <div className="ai-config-feature-list">
            {features.map((feature) => (
              <div className="ai-config-feature-row" key={feature.key}>
                <span className="ai-config-feature-row__icon">
                  <ExperimentOutlined />
                </span>
                <span className="ai-config-feature-row__copy">
                  <Typography.Text strong>{feature.name}</Typography.Text>
                  <Typography.Text type="secondary">
                    {feature.description || "未填写功能说明"}
                  </Typography.Text>
                </span>
                <StatusTag kind="dataset" value={feature.enabled ? "enabled" : "disabled"} />
                <Switch
                  checked={feature.enabled}
                  loading={togglingKey === feature.key}
                  onChange={(enabled) => onFeatureToggle(feature.key, enabled)}
                />
              </div>
            ))}
          </div>
        ) : (
          <EmptyBlock description="暂无 AI 功能开关" />
        )}
      </Card>
    </div>
  );
}

function ProviderFormModal({
  open,
  mode,
  provider,
  confirmLoading,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  mode: ProviderFormMode;
  provider?: AiProviderConfig;
  confirmLoading?: boolean;
  onCancel: () => void;
  onSubmit: (payload: AiProviderPayload) => void;
}) {
  const [form] = Form.useForm<AiProviderFormValues>();
  const selectedType = Form.useWatch("provider_type", form);
  const clearApiKey = Form.useWatch("clear_api_key", form);
  const requiresEndpoint = !providerTypesWithoutEndpoint.has(selectedType ?? "openai_compatible");

  useEffect(() => {
    if (open) {
      form.setFieldsValue(providerFormValues(provider));
    } else {
      form.resetFields();
    }
  }, [form, open, provider]);

  return (
    <Modal
      title={mode === "create" ? "新增模型供应商" : "编辑模型供应商"}
      open={open}
      width={760}
      okText={mode === "create" ? "创建" : "保存"}
      cancelText="取消"
      confirmLoading={confirmLoading}
      onCancel={onCancel}
      onOk={() => form.submit()}
    >
      <Alert
        className="ai-provider-form-alert"
        type="info"
        showIcon
        message="Base URL 填 OpenAI 协议根地址，系统会请求 /chat/completions。"
      />
      <Form<AiProviderFormValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => onSubmit(providerPayloadFromValues(values))}
      >
        <div className="ai-provider-form-grid">
          <Form.Item
            label="供应商名称"
            name="name"
            rules={[{ required: true, whitespace: true, message: "请输入供应商名称" }]}
          >
            <Input placeholder="OpenAI 兼容供应商" />
          </Form.Item>
          <Form.Item
            label="供应商类型"
            name="provider_type"
            rules={[{ required: true, message: "请选择供应商类型" }]}
          >
            <Select options={providerTypeOptions} />
          </Form.Item>
          <Form.Item
            className="ai-provider-form-grid__wide"
            label="Base URL"
            name="base_url"
            rules={
              requiresEndpoint
                ? [{ required: true, whitespace: true, message: "请输入 Base URL" }]
                : []
            }
            extra="示例：https://api.openai.com/v1、https://api.deepseek.com/v1、http://localhost:8000/v1"
          >
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item
            label="API Key"
            name="api_key"
            extra={
              mode === "edit"
                ? `留空则保持当前密钥：${provider?.api_key_masked ?? "未配置"}`
                : "本地服务如无需鉴权可留空"
            }
          >
            <Input.Password
              autoComplete="off"
              disabled={Boolean(clearApiKey)}
              placeholder={mode === "edit" ? "留空则不变" : "sk-..."}
            />
          </Form.Item>
          <Form.Item label="清空 API Key" name="clear_api_key" valuePropName="checked">
            <Switch disabled={mode === "create"} />
          </Form.Item>
          <Form.Item
            label="对话模型"
            name="chat_model"
            rules={
              requiresEndpoint
                ? [{ required: true, whitespace: true, message: "请输入对话模型" }]
                : []
            }
          >
            <Input placeholder="gpt-4o-mini / deepseek-chat / qwen-plus" />
          </Form.Item>
          <Form.Item label="向量模型" name="embedding_model">
            <Input placeholder="text-embedding-3-small" />
          </Form.Item>
          <Form.Item label="视觉模型" name="vision_model">
            <Input placeholder="gpt-4o-mini" />
          </Form.Item>
          <Form.Item label="优先级" name="priority" rules={[{ required: true, message: "请输入优先级" }]}>
            <InputNumber min={0} precision={0} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item
            label="超时秒数"
            name="timeout_seconds"
            rules={[{ required: true, message: "请输入超时秒数" }]}
          >
            <InputNumber min={1} precision={0} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item
            label="最大重试"
            name="max_retry_count"
            rules={[{ required: true, message: "请输入最大重试次数" }]}
          >
            <InputNumber min={0} precision={0} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item label="输入 Token 上限" name="max_input_tokens">
            <InputNumber min={1} precision={0} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item label="输出 Token 上限" name="max_output_tokens">
            <InputNumber min={1} precision={0} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item
            label="Temperature"
            name="temperature"
            rules={[{ required: true, message: "请输入 Temperature" }]}
          >
            <InputNumber min={0} max={2} step={0.1} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item label="Top P" name="top_p">
            <InputNumber min={0} max={1} step={0.05} className="ai-provider-form-number" />
          </Form.Item>
          <Form.Item label="内部服务" name="is_internal" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="启用供应商" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  );
}

function ProvidersPanel({
  providers,
  allowExternalLlm,
  onCreate,
  onEdit,
  onTest,
  testingProviderId,
}: {
  providers: AiProviderConfig[];
  allowExternalLlm: boolean;
  onCreate: () => void;
  onEdit: (provider: AiProviderConfig) => void;
  onTest: (provider: AiProviderConfig) => void;
  testingProviderId?: string;
}) {
  const columns: ColumnsType<AiProviderConfig> = [
    {
      title: "供应商",
      dataIndex: "name",
      key: "name",
      width: 180,
      render: (value: string, record) => (
        <span className="ai-config-provider-cell">
          <span className="ai-config-provider-cell__icon">
            <ApiOutlined />
          </span>
          <span>
            <Typography.Text strong className="single-line-text" title={value}>
              {value}
            </Typography.Text>
            <Typography.Text
              type="secondary"
              className="single-line-text"
              title={record.provider_type}
            >
              {record.provider_type}
            </Typography.Text>
          </span>
        </span>
      ),
    },
    {
      title: "Base URL",
      dataIndex: "base_url",
      key: "base_url",
      width: 220,
      render: (value?: string | null) => (
        <Typography.Text className="single-line-text" title={value ?? "-"}>
          {value ?? "-"}
        </Typography.Text>
      ),
    },
    {
      title: "API Key",
      dataIndex: "api_key_masked",
      key: "api_key_masked",
      width: 180,
      render: (value?: string | null) => (
        <Typography.Text code className="single-line-text" title={value ?? "未配置"}>
          {value ?? "未配置"}
        </Typography.Text>
      ),
    },
    {
      title: "模型",
      key: "models",
      width: 220,
      render: (_, record) => (
        <span className="ai-config-models-cell">
          <Typography.Text className="single-line-text" title={record.chat_model ?? "-"}>
            对话：{record.chat_model ?? "-"}
          </Typography.Text>
          <Typography.Text
            type="secondary"
            className="single-line-text"
            title={record.embedding_model ?? "-"}
          >
            向量：{record.embedding_model ?? "-"}
          </Typography.Text>
        </span>
      ),
    },
    {
      title: "优先级",
      dataIndex: "priority",
      key: "priority",
      width: 90,
      align: "right",
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (enabled: boolean) => (
        <StatusTag kind="dataset" value={enabled ? "enabled" : "disabled"} />
      ),
    },
    {
      title: "测试状态",
      dataIndex: "last_test_status",
      key: "last_test_status",
      width: 130,
      render: (status?: string | null) => (
        <StatusTag kind="sync" value={providerStatusValue(status)} variant="dot" />
      ),
    },
    {
      title: "延迟",
      dataIndex: "last_test_latency_ms",
      key: "last_test_latency_ms",
      width: 100,
      render: (value?: number | null) => (typeof value === "number" ? `${value}ms` : "-"),
    },
    {
      title: "上次测试",
      dataIndex: "last_tested_at",
      key: "last_tested_at",
      width: 150,
      render: formatDateTime,
    },
    {
      title: "操作",
      key: "actions",
      width: 210,
      fixed: "right",
      render: (_, record) => (
        <Space size={8}>
          <Button icon={<EditOutlined />} onClick={() => onEdit(record)}>
            编辑
          </Button>
          <Button
            icon={<ExperimentOutlined />}
            loading={testingProviderId === record.id}
            onClick={() => onTest(record)}
          >
            测试连接
          </Button>
        </Space>
      ),
    },
  ];

  const testedProviders = providers.filter(
    (provider) => provider.enabled && provider.last_test_status === "success",
  ).length;
  const failedProviders = providers.filter(
    (provider) => provider.enabled && provider.last_test_status === "failed",
  ).length;

  return (
    <Card className="document-panel table-card">
      <div className="table-section-header">
        <span className="table-section-header__copy">
          <Typography.Title level={4} className="table-section-header__title">
            模型供应商
          </Typography.Title>
          <Typography.Text className="table-section-header__meta">
            当前维护 {providers.length} 个供应商，{testedProviders} 个已通过测试
          </Typography.Text>
        </span>
        <Space size={8}>
          <StatusTag kind="health" value={failedProviders > 0 ? "error" : "ok"} variant="dot" />
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            新增供应商
          </Button>
        </Space>
      </div>
      {!allowExternalLlm ? (
        <Alert
          className="ai-config-provider-alert"
          type="warning"
          showIcon
          message="外部模型当前被禁用，外网 Base URL 的连接测试会被阻止。"
        />
      ) : null}
      <Table<AiProviderConfig>
        rowKey="id"
        columns={columns}
        dataSource={providers}
        locale={{ emptyText: "暂无模型供应商" }}
        pagination={false}
        scroll={{ x: 1460 }}
      />
    </Card>
  );
}

function PromptTemplatesPanel({ templates }: { templates: AiPromptTemplate[] }) {
  const columns: ColumnsType<AiPromptTemplate> = [
    {
      title: "模板名称",
      dataIndex: "name",
      key: "name",
      width: 220,
      render: (value: string, record) => (
        <span className="ai-config-provider-cell">
          <span className="ai-config-provider-cell__icon">
            <FileTextOutlined />
          </span>
          <span>
            <Typography.Text strong className="single-line-text" title={value}>
              {value}
            </Typography.Text>
            <Typography.Text
              type="secondary"
              className="single-line-text"
              title={record.template_key}
            >
              {record.template_key}
            </Typography.Text>
          </span>
        </span>
      ),
    },
    {
      title: "说明",
      dataIndex: "description",
      key: "description",
      render: (value?: string | null) => (
        <Typography.Text className="single-line-text" title={value ?? "-"}>
          {value ?? "-"}
        </Typography.Text>
      ),
    },
    {
      title: "默认模板",
      dataIndex: "is_default",
      key: "is_default",
      width: 110,
      render: (value: boolean) => (
        <StatusTag kind="dataset" value={value ? "required" : "skipped"} />
      ),
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (value: boolean) => (
        <StatusTag kind="dataset" value={value ? "enabled" : "disabled"} />
      ),
    },
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      width: 90,
      align: "right",
      render: (value: number) => `v${value}`,
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 150,
      render: formatDateTime,
    },
  ];

  const enabledTemplates = countEnabled(templates);
  const defaultTemplates = templates.filter((template) => template.is_default).length;

  return (
    <Card className="document-panel table-card">
      <div className="table-section-header">
        <span className="table-section-header__copy">
          <Typography.Title level={4} className="table-section-header__title">
            Prompt 模板
          </Typography.Title>
          <Typography.Text className="table-section-header__meta">
            当前维护 {templates.length} 个模板，{defaultTemplates} 个默认模板，{enabledTemplates}{" "}
            个启用
          </Typography.Text>
        </span>
        <StatusTag kind="health" value={defaultTemplates > 0 ? "ok" : "unknown"} variant="dot" />
      </div>
      <Table<AiPromptTemplate>
        rowKey="id"
        columns={columns}
        dataSource={templates}
        locale={{ emptyText: "暂无 Prompt 模板" }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 980 }}
      />
    </Card>
  );
}

function SensitiveRulesPanel({ rules }: { rules: AiSensitiveRule[] }) {
  const columns: ColumnsType<AiSensitiveRule> = [
    {
      title: "规则名称",
      dataIndex: "name",
      key: "name",
      width: 200,
      render: (value: string, record) => (
        <span className="ai-config-provider-cell">
          <span className="ai-config-provider-cell__icon">
            <SafetyCertificateOutlined />
          </span>
          <span>
            <Typography.Text strong className="single-line-text" title={value}>
              {value}
            </Typography.Text>
            <Typography.Text type="secondary" className="single-line-text" title={record.rule_type}>
              {record.rule_type}
            </Typography.Text>
          </span>
        </span>
      ),
    },
    {
      title: "风险等级",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 120,
      render: (value: string) => <StatusTag kind="risk" value={value} />,
    },
    {
      title: "处理方式",
      dataIndex: "action",
      key: "action",
      width: 140,
      render: (value: string) => ruleActionLabels[value] ?? value,
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (value: boolean) => (
        <StatusTag kind="dataset" value={value ? "enabled" : "disabled"} />
      ),
    },
    {
      title: "命中次数",
      dataIndex: "hit_count",
      key: "hit_count",
      width: 120,
      align: "right",
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 150,
      render: formatDateTime,
    },
  ];

  const enabledRules = countEnabled(rules);
  const ruleHits = rules.reduce((total, rule) => total + rule.hit_count, 0);

  return (
    <Card className="document-panel table-card">
      <div className="table-section-header">
        <span className="table-section-header__copy">
          <Typography.Title level={4} className="table-section-header__title">
            敏感规则
          </Typography.Title>
          <Typography.Text className="table-section-header__meta">
            当前维护 {rules.length} 条规则，{enabledRules} 条启用，{compactNumber.format(ruleHits)}{" "}
            次累计命中
          </Typography.Text>
        </span>
        <StatusTag kind="risk" value={ruleHits > 0 ? "high" : "low"} variant="dot" />
      </div>
      <Table<AiSensitiveRule>
        rowKey="id"
        columns={columns}
        dataSource={rules}
        locale={{ emptyText: "暂无敏感规则" }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 860 }}
      />
    </Card>
  );
}

export default function AiConfigPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [providerModal, setProviderModal] = useState<ProviderModalState | null>(null);

  const configQuery = useQuery({
    queryKey: aiConfigQueryKey,
    queryFn: getAiConfig,
  });

  const refreshConfig = async () => {
    await queryClient.invalidateQueries({ queryKey: aiConfigQueryKey });
  };

  const featureMutation = useMutation({
    mutationFn: ({ featureKey, enabled }: { featureKey: string; enabled: boolean }) =>
      updateAiFeature(featureKey, { enabled }),
    onSuccess: async (_, variables) => {
      message.success(variables.enabled ? "功能已启用" : "功能已停用");
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const createProviderMutation = useMutation({
    mutationFn: createAiProvider,
    onSuccess: async () => {
      message.success("供应商已创建");
      setProviderModal(null);
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const updateProviderMutation = useMutation({
    mutationFn: ({ providerId, payload }: { providerId: string; payload: AiProviderPayload }) =>
      updateAiProvider(providerId, payload),
    onSuccess: async () => {
      message.success("供应商已更新");
      setProviderModal(null);
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const providerTestMutation = useMutation({
    mutationFn: (provider: AiProviderConfig) => testAiProvider(provider.id),
    onSuccess: async (result: AiProviderTestResult) => {
      if (result.status === "success") {
        message.success(
          `连接测试成功${typeof result.latency_ms === "number" ? `，延迟 ${result.latency_ms}ms` : ""}`,
        );
      } else {
        message.error(result.message || "连接测试失败");
      }
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const submitProvider = (payload: AiProviderPayload) => {
    if (providerModal?.mode === "edit" && providerModal.provider) {
      updateProviderMutation.mutate({ providerId: providerModal.provider.id, payload });
      return;
    }

    createProviderMutation.mutate(payload);
  };

  const config = configQuery.data;
  const isLoading = configQuery.isLoading;

  return (
    <PageContainer
      title="AI 文档分析配置"
      description="配置 AI 分析能力、模型与规则，提升知识库内容理解与治理水平。"
      actions={
        <Button
          icon={<ClockCircleOutlined />}
          onClick={() => void refreshConfig()}
          loading={configQuery.isFetching}
        >
          刷新
        </Button>
      }
    >
      {configQuery.isError ? (
        <Alert
          className="ai-config-alert"
          type="error"
          showIcon
          message="AI 配置加载失败"
          description={configQuery.error.message}
          action={<Button onClick={() => void configQuery.refetch()}>重试</Button>}
        />
      ) : null}

      {config ? <AiOverview config={config} /> : null}
      {config ? <AiGovernanceStrip config={config} /> : null}

      <Card className="document-panel ai-config-tabs-card">
        {isLoading ? (
          <Table
            columns={[{ title: "配置项", dataIndex: "name" }]}
            dataSource={[]}
            loading
            pagination={false}
            locale={{ emptyText: "正在加载 AI 配置" }}
          />
        ) : config ? (
          <Tabs
            items={[
              {
                key: "features",
                label: "功能开关",
                children: (
                  <FeaturesPanel
                    features={config.features}
                    global={config.global}
                    togglingKey={
                      featureMutation.isPending ? featureMutation.variables?.featureKey : undefined
                    }
                    onFeatureToggle={(featureKey, enabled) =>
                      featureMutation.mutate({ featureKey, enabled })
                    }
                  />
                ),
              },
              {
                key: "providers",
                label: "模型供应商",
                children: (
                  <ProvidersPanel
                    providers={config.providers}
                    allowExternalLlm={config.global.allow_external_llm}
                    testingProviderId={
                      providerTestMutation.isPending
                        ? providerTestMutation.variables?.id
                        : undefined
                    }
                    onCreate={() => setProviderModal({ mode: "create" })}
                    onEdit={(provider) => setProviderModal({ mode: "edit", provider })}
                    onTest={(provider) => providerTestMutation.mutate(provider)}
                  />
                ),
              },
              {
                key: "prompts",
                label: "Prompt 模板",
                children: <PromptTemplatesPanel templates={config.prompt_templates} />,
              },
              {
                key: "rules",
                label: "敏感规则",
                children: <SensitiveRulesPanel rules={config.sensitive_rules} />,
              },
            ]}
          />
        ) : (
          <EmptyBlock description="暂无 AI 配置数据" />
        )}
      </Card>
      {providerModal ? (
        <ProviderFormModal
          open
          mode={providerModal.mode}
          provider={providerModal.provider}
          confirmLoading={createProviderMutation.isPending || updateProviderMutation.isPending}
          onCancel={() => setProviderModal(null)}
          onSubmit={submitProvider}
        />
      ) : null}
    </PageContainer>
  );
}
