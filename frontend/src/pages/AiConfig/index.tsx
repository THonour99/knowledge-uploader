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
  Popconfirm,
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
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  UndoOutlined,
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
  type AiPromptTemplatePayload,
  type AiSensitiveRulePayload,
  type AiSensitiveRuleTestResponse,
  type AiSensitiveRule,
  createAiPromptTemplate,
  createAiProvider,
  createAiSensitiveRule,
  deleteAiPromptTemplate,
  deleteAiSensitiveRule,
  getAiConfig,
  restoreAiPromptTemplateDefault,
  testAiProvider,
  testAiSensitiveRules,
  updateAiFeature,
  updateAiPromptTemplate,
  updateAiProvider,
  updateAiSensitiveRule,
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
type ProviderModelKind = "chat" | "embedding" | "vision";

type ProviderModalState = {
  mode: ProviderFormMode;
  provider?: AiProviderConfig;
};

type PromptModalState = {
  mode: ProviderFormMode;
  template?: AiPromptTemplate;
};

type SensitiveRuleModalState = {
  mode: ProviderFormMode;
  rule?: AiSensitiveRule;
};

type AiProviderFormValues = Omit<
  AiProviderPayload,
  "chat_model" | "embedding_model" | "vision_model"
> & {
  model_kind: ProviderModelKind;
  model_name: string;
};

interface AiPromptTemplateFormValues {
  template_key: string;
  name: string;
  description?: string;
  prompt_text: string;
  variables?: string;
  enabled: boolean;
}

interface AiSensitiveRuleFormValues {
  name: string;
  rule_type: "keyword" | "regex";
  pattern?: string;
  keywords?: string;
  risk_level: "low" | "medium" | "high" | "critical";
  action: "flag" | "require_review" | "block_sync";
  enabled: boolean;
}

const providerModelKindLabels: Record<ProviderModelKind, string> = {
  chat: "对话",
  embedding: "向量",
  vision: "视觉",
};

const providerModelKindOptions = [
  { label: "对话模型", value: "chat" },
  { label: "向量模型", value: "embedding" },
  { label: "视觉模型", value: "vision" },
];

const providerModelNamePlaceholders: Record<ProviderModelKind, string> = {
  chat: "gpt-4o-mini / deepseek-chat / qwen-plus",
  embedding: "text-embedding-3-small",
  vision: "gpt-4o-mini",
};

const providerDefaultValues: AiProviderFormValues = {
  name: "",
  provider_type: "openai_compatible",
  base_url: "",
  api_key: "",
  clear_api_key: false,
  model_kind: "chat",
  model_name: "",
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

const promptDefaultValues: AiPromptTemplateFormValues = {
  template_key: "",
  name: "",
  description: "",
  prompt_text: "",
  variables: "",
  enabled: true,
};

const sensitiveRuleDefaultValues: AiSensitiveRuleFormValues = {
  name: "",
  rule_type: "keyword",
  pattern: "",
  keywords: "",
  risk_level: "medium",
  action: "require_review",
  enabled: true,
};

const sensitiveRuleTypeOptions = [
  { label: "关键词", value: "keyword" },
  { label: "正则表达式", value: "regex" },
];

const sensitiveRiskOptions = [
  { label: "低风险", value: "low" },
  { label: "中风险", value: "medium" },
  { label: "高风险", value: "high" },
  { label: "严重风险", value: "critical" },
];

const sensitiveActionOptions = [
  { label: "仅标记", value: "flag" },
  { label: "进入敏感审核", value: "require_review" },
  { label: "阻断同步", value: "block_sync" },
];

function optionalText(value?: string | null): string | null {
  const cleaned = value?.trim();
  return cleaned ? cleaned : null;
}

function splitList(value?: string | null): string[] {
  return (value ?? "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function optionalNumber(value?: number | null): number | null {
  return typeof value === "number" ? value : null;
}

function providerModelKind(provider?: AiProviderConfig): ProviderModelKind {
  if (!provider) {
    return "chat";
  }
  if (provider.embedding_model && !provider.chat_model && !provider.vision_model) {
    return "embedding";
  }
  if (provider.vision_model && !provider.chat_model && !provider.embedding_model) {
    return "vision";
  }
  return "chat";
}

function providerModelName(
  provider: AiProviderConfig | undefined,
  modelKind: ProviderModelKind,
): string {
  if (!provider) {
    return "";
  }
  if (modelKind === "embedding") {
    return provider.embedding_model ?? "";
  }
  if (modelKind === "vision") {
    return provider.vision_model ?? "";
  }
  return provider.chat_model ?? "";
}

function providerModelInfo(provider: AiProviderConfig): {
  label: string;
  modelName: string;
} {
  const modelKind = providerModelKind(provider);
  return {
    label: providerModelKindLabels[modelKind],
    modelName: providerModelName(provider, modelKind),
  };
}

function isPrivateIPv4(hostname: string): boolean {
  const parts = hostname.split(".").map((part) => Number(part));
  if (
    parts.length !== 4 ||
    parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    return false;
  }
  const [first, second] = parts;
  return (
    first === 0 ||
    first === 10 ||
    first === 127 ||
    (first === 169 && second === 254) ||
    (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 && second === 168)
  );
}

function isLocalProviderHost(hostname: string): boolean {
  return ["localhost", "host.docker.internal", "ollama", "vllm", "lmstudio"].includes(hostname);
}

function isExternalProviderUrl(value?: string | null): boolean {
  const baseUrl = optionalText(value);
  if (!baseUrl) {
    return false;
  }

  try {
    const hostname = new URL(baseUrl).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (isLocalProviderHost(hostname) || isPrivateIPv4(hostname)) {
      return false;
    }
    if (
      hostname.includes(":") &&
      (hostname === "::1" ||
        hostname.startsWith("fc") ||
        hostname.startsWith("fd") ||
        hostname.startsWith("fe80"))
    ) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

function providerFormValues(provider?: AiProviderConfig): AiProviderFormValues {
  if (!provider) {
    return { ...providerDefaultValues };
  }

  const modelKind = providerModelKind(provider);
  return {
    name: provider.name,
    provider_type: provider.provider_type,
    base_url: provider.base_url ?? "",
    api_key: "",
    clear_api_key: false,
    model_kind: modelKind,
    model_name: providerModelName(provider, modelKind),
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
  const modelName = optionalText(values.model_name);
  const payload: AiProviderPayload = {
    name: values.name.trim(),
    provider_type: values.provider_type,
    base_url: optionalText(values.base_url),
    clear_api_key: Boolean(values.clear_api_key),
    chat_model: values.model_kind === "chat" ? modelName : null,
    embedding_model: values.model_kind === "embedding" ? modelName : null,
    vision_model: values.model_kind === "vision" ? modelName : null,
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

function promptFormValues(template?: AiPromptTemplate): AiPromptTemplateFormValues {
  if (!template) {
    return { ...promptDefaultValues };
  }
  return {
    template_key: template.template_key,
    name: template.name,
    description: template.description ?? "",
    prompt_text: template.prompt_text,
    variables: template.variables.join(", "),
    enabled: template.enabled,
  };
}

function promptPayloadFromValues(
  values: AiPromptTemplateFormValues,
  mode: ProviderFormMode,
): AiPromptTemplatePayload {
  const payload: AiPromptTemplatePayload = {
    name: values.name.trim(),
    description: optionalText(values.description),
    prompt_text: values.prompt_text.trim(),
    variables: splitList(values.variables),
    enabled: Boolean(values.enabled),
  };
  if (mode === "create") {
    payload.template_key = values.template_key.trim();
  }
  return payload;
}

function sensitiveRuleFormValues(rule?: AiSensitiveRule): AiSensitiveRuleFormValues {
  if (!rule) {
    return { ...sensitiveRuleDefaultValues };
  }
  return {
    name: rule.name,
    rule_type: rule.rule_type === "regex" ? "regex" : "keyword",
    pattern: rule.pattern ?? "",
    keywords: rule.keywords.join(", "),
    risk_level: rule.risk_level as AiSensitiveRuleFormValues["risk_level"],
    action: rule.action as AiSensitiveRuleFormValues["action"],
    enabled: rule.enabled,
  };
}

function sensitiveRulePayloadFromValues(values: AiSensitiveRuleFormValues): AiSensitiveRulePayload {
  return {
    name: values.name.trim(),
    rule_type: values.rule_type,
    pattern: values.rule_type === "regex" ? optionalText(values.pattern) : null,
    keywords: values.rule_type === "keyword" ? splitList(values.keywords) : [],
    risk_level: values.risk_level,
    action: values.action,
    enabled: Boolean(values.enabled),
  };
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
  const selectedModelKind = Form.useWatch("model_kind", form) ?? "chat";
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
      title={mode === "create" ? "新增模型配置" : "编辑模型配置"}
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
        message="Base URL 填 OpenAI 协议根地址；当前配置只对应一种模型用途。"
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
            label="模型用途"
            name="model_kind"
            rules={[{ required: true, message: "请选择模型用途" }]}
          >
            <Select options={providerModelKindOptions} />
          </Form.Item>
          <Form.Item
            label="模型名称"
            name="model_name"
            rules={
              requiresEndpoint
                ? [{ required: true, whitespace: true, message: "请输入模型名称" }]
                : []
            }
          >
            <Input placeholder={providerModelNamePlaceholders[selectedModelKind]} />
          </Form.Item>
          <Form.Item
            label="优先级"
            name="priority"
            rules={[{ required: true, message: "请输入优先级" }]}
          >
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

function PromptTemplateFormModal({
  open,
  mode,
  template,
  confirmLoading,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  mode: ProviderFormMode;
  template?: AiPromptTemplate;
  confirmLoading?: boolean;
  onCancel: () => void;
  onSubmit: (payload: AiPromptTemplatePayload) => void;
}) {
  const [form] = Form.useForm<AiPromptTemplateFormValues>();

  useEffect(() => {
    if (open) {
      form.setFieldsValue(promptFormValues(template));
    } else {
      form.resetFields();
    }
  }, [form, open, template]);

  return (
    <Modal
      title={mode === "create" ? "新增 Prompt 模板" : "编辑 Prompt 模板"}
      open={open}
      width={760}
      okText={mode === "create" ? "创建" : "保存"}
      cancelText="取消"
      confirmLoading={confirmLoading}
      onCancel={onCancel}
      onOk={() => form.submit()}
    >
      <Form<AiPromptTemplateFormValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => onSubmit(promptPayloadFromValues(values, mode))}
      >
        <div className="ai-provider-form-grid">
          <Form.Item
            label="模板 Key"
            name="template_key"
            rules={[{ required: true, whitespace: true, message: "请输入模板 Key" }]}
            extra="创建后不可修改，仅支持字母、数字、下划线和短横线。"
          >
            <Input disabled={mode === "edit"} placeholder="custom_summary" />
          </Form.Item>
          <Form.Item
            label="模板名称"
            name="name"
            rules={[{ required: true, whitespace: true, message: "请输入模板名称" }]}
          >
            <Input placeholder="文档摘要" />
          </Form.Item>
          <Form.Item className="ai-provider-form-grid__wide" label="说明" name="description">
            <Input placeholder="给审核人员使用的摘要模板" />
          </Form.Item>
          <Form.Item
            className="ai-provider-form-grid__wide"
            label="Prompt 内容"
            name="prompt_text"
            rules={[{ required: true, whitespace: true, message: "请输入 Prompt 内容" }]}
          >
            <Input.TextArea rows={7} placeholder="请总结文档核心内容：{text}" />
          </Form.Item>
          <Form.Item
            className="ai-provider-form-grid__wide"
            label="变量"
            name="variables"
            extra="用逗号或换行分隔，例如：text, categories。"
          >
            <Input.TextArea rows={2} placeholder="text, categories" />
          </Form.Item>
          <Form.Item label="启用模板" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  );
}

function SensitiveRuleFormModal({
  open,
  mode,
  rule,
  confirmLoading,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  mode: ProviderFormMode;
  rule?: AiSensitiveRule;
  confirmLoading?: boolean;
  onCancel: () => void;
  onSubmit: (payload: AiSensitiveRulePayload) => void;
}) {
  const [form] = Form.useForm<AiSensitiveRuleFormValues>();
  const ruleType = Form.useWatch("rule_type", form) ?? "keyword";

  useEffect(() => {
    if (open) {
      form.setFieldsValue(sensitiveRuleFormValues(rule));
    } else {
      form.resetFields();
    }
  }, [form, open, rule]);

  return (
    <Modal
      title={mode === "create" ? "新增敏感规则" : "编辑敏感规则"}
      open={open}
      width={720}
      okText={mode === "create" ? "创建" : "保存"}
      cancelText="取消"
      confirmLoading={confirmLoading}
      onCancel={onCancel}
      onOk={() => form.submit()}
    >
      <Form<AiSensitiveRuleFormValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => onSubmit(sensitiveRulePayloadFromValues(values))}
      >
        <div className="ai-provider-form-grid">
          <Form.Item
            label="规则名称"
            name="name"
            rules={[{ required: true, whitespace: true, message: "请输入规则名称" }]}
          >
            <Input placeholder="客户机密编号" />
          </Form.Item>
          <Form.Item
            label="匹配类型"
            name="rule_type"
            rules={[{ required: true, message: "请选择匹配类型" }]}
          >
            <Select options={sensitiveRuleTypeOptions} />
          </Form.Item>
          {ruleType === "regex" ? (
            <Form.Item
              className="ai-provider-form-grid__wide"
              label="正则表达式"
              name="pattern"
              rules={[{ required: true, whitespace: true, message: "请输入正则表达式" }]}
            >
              <Input.TextArea rows={3} placeholder="客户\\d{4}" />
            </Form.Item>
          ) : (
            <Form.Item
              className="ai-provider-form-grid__wide"
              label="关键词"
              name="keywords"
              rules={[{ required: true, whitespace: true, message: "请输入关键词" }]}
              extra="用逗号或换行分隔，至少填写一个关键词。"
            >
              <Input.TextArea rows={3} placeholder="密钥, 客户机密编号" />
            </Form.Item>
          )}
          <Form.Item
            label="风险等级"
            name="risk_level"
            rules={[{ required: true, message: "请选择风险等级" }]}
          >
            <Select options={sensitiveRiskOptions} />
          </Form.Item>
          <Form.Item
            label="处理方式"
            name="action"
            rules={[{ required: true, message: "请选择处理方式" }]}
          >
            <Select options={sensitiveActionOptions} />
          </Form.Item>
          <Form.Item label="启用规则" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  );
}

function SensitiveRuleTestModal({
  open,
  result,
  confirmLoading,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  result?: AiSensitiveRuleTestResponse | null;
  confirmLoading?: boolean;
  onCancel: () => void;
  onSubmit: (text: string) => void;
}) {
  const [form] = Form.useForm<{ text: string }>();

  useEffect(() => {
    if (!open) {
      form.resetFields();
    }
  }, [form, open]);

  return (
    <Modal
      title="测试敏感规则"
      open={open}
      width={720}
      okText="测试"
      cancelText="关闭"
      confirmLoading={confirmLoading}
      onCancel={onCancel}
      onOk={() => form.submit()}
    >
      <Form<{ text: string }>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => onSubmit(values.text)}
      >
        <Form.Item
          label="测试文本"
          name="text"
          rules={[{ required: true, whitespace: true, message: "请输入测试文本" }]}
        >
          <Input.TextArea rows={6} placeholder="输入一段文档内容，检查当前启用规则命中情况。" />
        </Form.Item>
      </Form>
      {result ? (
        <div className="ai-sensitive-test-result">
          <Typography.Text strong>命中结果</Typography.Text>
          {result.hits.length > 0 ? (
            result.hits.map((hit) => (
              <div className="ai-sensitive-test-hit" key={`${hit.rule_id}-${hit.match}`}>
                <span>
                  <Typography.Text strong>{hit.rule_name}</Typography.Text>
                  <Typography.Text type="secondary">命中：{hit.match}</Typography.Text>
                </span>
                <Space size={6}>
                  <StatusTag kind="risk" value={hit.risk_level} />
                  <Typography.Text>{ruleActionLabels[hit.action] ?? hit.action}</Typography.Text>
                </Space>
              </div>
            ))
          ) : (
            <Typography.Text type="secondary">未命中任何启用规则。</Typography.Text>
          )}
        </div>
      ) : null}
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
      title: "用途/模型",
      key: "models",
      width: 220,
      render: (_, record) => {
        const modelInfo = providerModelInfo(record);
        const modelName = modelInfo.modelName || "-";
        return (
          <span className="ai-config-models-cell">
            <Typography.Text className="single-line-text" title={modelName}>
              {modelInfo.label}：{modelName}
            </Typography.Text>
            <Typography.Text type="secondary" className="single-line-text" title={modelInfo.label}>
              模型用途：{modelInfo.label}
            </Typography.Text>
          </span>
        );
      },
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
  const hasExternalProviders = providers.some((provider) =>
    isExternalProviderUrl(provider.base_url),
  );

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
            新增模型配置
          </Button>
        </Space>
      </div>
      {!allowExternalLlm && hasExternalProviders ? (
        <Alert
          className="ai-config-provider-alert"
          type="warning"
          showIcon
          message="存在公网 Base URL，外部模型关闭时测试会被阻止。"
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

function PromptTemplatesPanel({
  templates,
  onCreate,
  onEdit,
  onRestore,
  onDelete,
}: {
  templates: AiPromptTemplate[];
  onCreate: () => void;
  onEdit: (template: AiPromptTemplate) => void;
  onRestore: (template: AiPromptTemplate) => void;
  onDelete: (template: AiPromptTemplate) => void;
}) {
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
      width: 220,
      render: (value?: string | null) => (
        <Typography.Text className="single-line-text" title={value ?? "-"}>
          {value ?? "-"}
        </Typography.Text>
      ),
    },
    {
      title: "Prompt",
      dataIndex: "prompt_text",
      key: "prompt_text",
      width: 260,
      render: (value: string) => (
        <Typography.Text className="single-line-text" title={value}>
          {value}
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
    {
      title: "操作",
      key: "actions",
      width: 230,
      fixed: "right",
      render: (_, record) => (
        <Space size={8}>
          <Button icon={<EditOutlined />} onClick={() => onEdit(record)}>
            编辑
          </Button>
          {record.is_default ? (
            <Button icon={<UndoOutlined />} onClick={() => onRestore(record)}>
              恢复默认
            </Button>
          ) : null}
          <Popconfirm
            title={record.is_default ? "停用默认模板" : "删除模板"}
            description={
              record.is_default
                ? "默认模板会被停用，不会物理删除。"
                : "删除后该自定义模板将不再可用。"
            }
            okText={record.is_default ? "停用" : "删除"}
            cancelText="取消"
            onConfirm={() => onDelete(record)}
          >
            <Button icon={<DeleteOutlined />} danger>
              {record.is_default ? "停用" : "删除"}
            </Button>
          </Popconfirm>
        </Space>
      ),
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
        <Space size={8}>
          <StatusTag kind="health" value={defaultTemplates > 0 ? "ok" : "unknown"} variant="dot" />
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            新增模板
          </Button>
        </Space>
      </div>
      <Table<AiPromptTemplate>
        rowKey="id"
        columns={columns}
        dataSource={templates}
        locale={{ emptyText: "暂无 Prompt 模板" }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 1260 }}
      />
    </Card>
  );
}

function SensitiveRulesPanel({
  rules,
  onCreate,
  onEdit,
  onDelete,
  onTest,
}: {
  rules: AiSensitiveRule[];
  onCreate: () => void;
  onEdit: (rule: AiSensitiveRule) => void;
  onDelete: (rule: AiSensitiveRule) => void;
  onTest: () => void;
}) {
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
      title: "匹配内容",
      key: "matcher",
      width: 220,
      render: (_, record) => {
        const value =
          record.rule_type === "regex" ? (record.pattern ?? "-") : record.keywords.join(", ");
        return (
          <Typography.Text className="single-line-text" title={value}>
            {value || "-"}
          </Typography.Text>
        );
      },
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
    {
      title: "操作",
      key: "actions",
      width: 180,
      fixed: "right",
      render: (_, record) => (
        <Space size={8}>
          <Button icon={<EditOutlined />} onClick={() => onEdit(record)}>
            编辑
          </Button>
          <Popconfirm
            title="删除敏感规则"
            description="删除后该规则将立即停止参与检测。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => onDelete(record)}
          >
            <Button icon={<DeleteOutlined />} danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
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
        <Space size={8}>
          <StatusTag kind="risk" value={ruleHits > 0 ? "high" : "low"} variant="dot" />
          <Button icon={<ExperimentOutlined />} onClick={onTest}>
            测试规则
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            新增规则
          </Button>
        </Space>
      </div>
      <Table<AiSensitiveRule>
        rowKey="id"
        columns={columns}
        dataSource={rules}
        locale={{ emptyText: "暂无敏感规则" }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 1120 }}
      />
    </Card>
  );
}

export default function AiConfigPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [providerModal, setProviderModal] = useState<ProviderModalState | null>(null);
  const [promptModal, setPromptModal] = useState<PromptModalState | null>(null);
  const [sensitiveRuleModal, setSensitiveRuleModal] = useState<SensitiveRuleModalState | null>(
    null,
  );
  const [sensitiveTestOpen, setSensitiveTestOpen] = useState(false);
  const [sensitiveTestResult, setSensitiveTestResult] =
    useState<AiSensitiveRuleTestResponse | null>(null);

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

  const createPromptMutation = useMutation({
    mutationFn: createAiPromptTemplate,
    onSuccess: async () => {
      message.success("Prompt 模板已创建");
      setPromptModal(null);
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const updatePromptMutation = useMutation({
    mutationFn: ({
      templateId,
      payload,
    }: {
      templateId: string;
      payload: AiPromptTemplatePayload;
    }) => updateAiPromptTemplate(templateId, payload),
    onSuccess: async () => {
      message.success("Prompt 模板已更新");
      setPromptModal(null);
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const restorePromptMutation = useMutation({
    mutationFn: (template: AiPromptTemplate) => restoreAiPromptTemplateDefault(template.id),
    onSuccess: async () => {
      message.success("Prompt 模板已恢复默认");
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const deletePromptMutation = useMutation({
    mutationFn: (template: AiPromptTemplate) => deleteAiPromptTemplate(template.id),
    onSuccess: async (_, template) => {
      message.success(template.is_default ? "默认模板已停用" : "Prompt 模板已删除");
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const createSensitiveRuleMutation = useMutation({
    mutationFn: createAiSensitiveRule,
    onSuccess: async () => {
      message.success("敏感规则已创建");
      setSensitiveRuleModal(null);
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const updateSensitiveRuleMutation = useMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: string; payload: AiSensitiveRulePayload }) =>
      updateAiSensitiveRule(ruleId, payload),
    onSuccess: async () => {
      message.success("敏感规则已更新");
      setSensitiveRuleModal(null);
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const deleteSensitiveRuleMutation = useMutation({
    mutationFn: (rule: AiSensitiveRule) => deleteAiSensitiveRule(rule.id),
    onSuccess: async () => {
      message.success("敏感规则已删除");
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const testSensitiveRuleMutation = useMutation({
    mutationFn: testAiSensitiveRules,
    onSuccess: (result) => {
      setSensitiveTestResult(result);
      message.success(result.hits.length > 0 ? "测试完成，存在命中" : "测试完成，未命中规则");
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

  const submitPromptTemplate = (payload: AiPromptTemplatePayload) => {
    if (promptModal?.mode === "edit" && promptModal.template) {
      updatePromptMutation.mutate({ templateId: promptModal.template.id, payload });
      return;
    }
    createPromptMutation.mutate(payload);
  };

  const submitSensitiveRule = (payload: AiSensitiveRulePayload) => {
    if (sensitiveRuleModal?.mode === "edit" && sensitiveRuleModal.rule) {
      updateSensitiveRuleMutation.mutate({ ruleId: sensitiveRuleModal.rule.id, payload });
      return;
    }
    createSensitiveRuleMutation.mutate(payload);
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
                children: (
                  <PromptTemplatesPanel
                    templates={config.prompt_templates}
                    onCreate={() => setPromptModal({ mode: "create" })}
                    onEdit={(template) => setPromptModal({ mode: "edit", template })}
                    onRestore={(template) => restorePromptMutation.mutate(template)}
                    onDelete={(template) => deletePromptMutation.mutate(template)}
                  />
                ),
              },
              {
                key: "rules",
                label: "敏感规则",
                children: (
                  <SensitiveRulesPanel
                    rules={config.sensitive_rules}
                    onCreate={() => setSensitiveRuleModal({ mode: "create" })}
                    onEdit={(rule) => setSensitiveRuleModal({ mode: "edit", rule })}
                    onDelete={(rule) => deleteSensitiveRuleMutation.mutate(rule)}
                    onTest={() => {
                      setSensitiveTestResult(null);
                      setSensitiveTestOpen(true);
                    }}
                  />
                ),
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
      {promptModal ? (
        <PromptTemplateFormModal
          open
          mode={promptModal.mode}
          template={promptModal.template}
          confirmLoading={createPromptMutation.isPending || updatePromptMutation.isPending}
          onCancel={() => setPromptModal(null)}
          onSubmit={submitPromptTemplate}
        />
      ) : null}
      {sensitiveRuleModal ? (
        <SensitiveRuleFormModal
          open
          mode={sensitiveRuleModal.mode}
          rule={sensitiveRuleModal.rule}
          confirmLoading={
            createSensitiveRuleMutation.isPending || updateSensitiveRuleMutation.isPending
          }
          onCancel={() => setSensitiveRuleModal(null)}
          onSubmit={submitSensitiveRule}
        />
      ) : null}
      <SensitiveRuleTestModal
        open={sensitiveTestOpen}
        result={sensitiveTestResult}
        confirmLoading={testSensitiveRuleMutation.isPending}
        onCancel={() => setSensitiveTestOpen(false)}
        onSubmit={(text) => testSensitiveRuleMutation.mutate(text)}
      />
    </PageContainer>
  );
}
