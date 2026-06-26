import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Empty,
  Progress,
  Space,
  Switch,
  Table,
  Tabs,
  Typography,
} from "antd";
import {
  ApiOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  FileTextOutlined,
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
  type AiProviderTestResult,
  type AiSensitiveRule,
  getAiConfig,
  testAiProvider,
  updateAiFeature,
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

function ProvidersPanel({
  providers,
  onTest,
  testingProviderId,
}: {
  providers: AiProviderConfig[];
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
      width: 120,
      fixed: "right",
      render: (_, record) => (
        <Button
          icon={<ExperimentOutlined />}
          loading={testingProviderId === record.id}
          onClick={() => onTest(record)}
        >
          测试连接
        </Button>
      ),
    },
  ];

  return (
    <Card className="document-panel table-card" title="模型供应商">
      <Table<AiProviderConfig>
        rowKey="id"
        columns={columns}
        dataSource={providers}
        locale={{ emptyText: "暂无模型供应商" }}
        pagination={false}
        scroll={{ x: 1370 }}
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

  return (
    <Card className="document-panel table-card" title="Prompt 模板">
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

  return (
    <Card className="document-panel table-card" title="敏感规则">
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
                    testingProviderId={
                      providerTestMutation.isPending
                        ? providerTestMutation.variables?.id
                        : undefined
                    }
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
    </PageContainer>
  );
}
