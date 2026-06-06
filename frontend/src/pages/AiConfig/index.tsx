import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Empty,
  Space,
  Switch,
  Table,
  Tabs,
  Typography,
} from "antd";
import {
  ApiOutlined,
  CheckCircleOutlined,
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
  type AiFeatureConfig,
  type AiPromptTemplate,
  type AiProviderConfig,
  type AiProviderTestResult,
  type AiSensitiveRule,
  getAiConfig,
  testAiProvider,
  updateAiFeature,
} from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const aiConfigQueryKey = ["ai-config"] as const;

const globalSwitches = [
  {
    key: "ai_analysis_enabled",
    title: "AI 总开关",
    description: "开启后，系统将使用 AI 能力进行文档分析。",
    checkedText: "已开启",
    uncheckedText: "已关闭",
  },
  {
    key: "allow_external_llm",
    title: "是否允许外部模型",
    description: "允许使用第三方 OpenAI-compatible 模型供应商。",
    checkedText: "允许",
    uncheckedText: "禁止",
  },
  {
    key: "allow_sync_when_analysis_failed",
    title: "分析失败后是否允许同步",
    description: "AI 分析失败时仍允许文档继续同步到知识库。",
    checkedText: "允许同步",
    uncheckedText: "禁止同步",
  },
] as const;

const ruleActionLabels: Record<string, string> = {
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

const formatDateTime = (value?: string | null) => (value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "-");

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
}: {
  title: string;
  description: string;
  checked: boolean;
  checkedText: string;
  uncheckedText: string;
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
        <Switch checked={checked} disabled />
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
  onFeatureToggle: (feature: AiFeatureConfig, enabled: boolean) => void;
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
                  onChange={(enabled) => onFeatureToggle(feature, enabled)}
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
            <Typography.Text type="secondary" className="single-line-text" title={record.provider_type}>
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
          <Typography.Text type="secondary" className="single-line-text" title={record.embedding_model ?? "-"}>
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
      render: (enabled: boolean) => <StatusTag kind="dataset" value={enabled ? "enabled" : "disabled"} />,
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
            <Typography.Text type="secondary" className="single-line-text" title={record.template_key}>
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
      render: (value: boolean) => <StatusTag kind="dataset" value={value ? "required" : "skipped"} />,
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (value: boolean) => <StatusTag kind="dataset" value={value ? "enabled" : "disabled"} />,
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
      render: (value: boolean) => <StatusTag kind="dataset" value={value ? "enabled" : "disabled"} />,
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
        message.success(`连接测试成功${typeof result.latency_ms === "number" ? `，延迟 ${result.latency_ms}ms` : ""}`);
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
                    onFeatureToggle={(feature, enabled) =>
                      featureMutation.mutate({ featureKey: feature.key, enabled })
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
                      providerTestMutation.isPending ? providerTestMutation.variables?.id : undefined
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
