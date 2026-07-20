import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Form,
  Input,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  CheckCircleOutlined,
  CloudSyncOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  FolderAddOutlined,
  LinkOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { ColumnsType } from "antd/es/table";

import {
  type Category,
  type CategoryPayload,
  type DatasetMapping,
  type DatasetMappingPayload,
  type RagflowDatasetOption,
  type RagflowConnectionTestResult,
  createCategory,
  createDatasetMapping,
  disableDatasetMapping,
  discoverRagflowDatasets,
  getConfigs,
  listCategories,
  listDatasetMappings,
  testRagflowConnection,
  updateCategory,
  updateDatasetMapping,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import {
  SessionBoundModal as Modal,
  SessionBoundPopconfirm as Popconfirm,
} from "../../components/SessionBoundActions";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";

interface CategoryFormValues {
  name: string;
  code: string;
  description?: string;
  keywords?: string;
  allow_ai_recommend: boolean;
}

interface DatasetFormValues {
  name: string;
  category_id: string;
  ragflow_dataset_id: string;
  ragflow_dataset_name: string;
  enabled: boolean;
}

interface DatasetConfigRow {
  id: string;
  category: Category;
  mapping?: DatasetMapping;
  status: "enabled" | "pending" | "disabled";
}

const defaultCategoryValues: CategoryFormValues = {
  name: "",
  code: "",
  description: "",
  keywords: "",
  allow_ai_recommend: true,
};

const defaultDatasetValues: DatasetFormValues = {
  name: "",
  category_id: "",
  ragflow_dataset_id: "",
  ragflow_dataset_name: "",
  enabled: true,
};

const RAGFLOW_ALLOWED_DATASET_IDS_KEY = "ragflow.allowed_dataset_ids";

const statusOptions = [
  { label: "状态：全部", value: "all" },
  { label: "已启用", value: "enabled" },
  { label: "待完善", value: "pending" },
  { label: "已禁用", value: "disabled" },
];

function parseKeywords(value?: string): string[] {
  return (value ?? "")
    .split(/[,，\n]/)
    .map((keyword) => keyword.trim())
    .filter(Boolean);
}

function toCategoryCreatePayload(values: CategoryFormValues): CategoryPayload {
  return {
    name: values.name.trim(),
    code: values.code.trim().toLowerCase(),
    description: values.description?.trim() || null,
    parent_id: null,
    allow_ai_recommend: values.allow_ai_recommend,
    keywords: parseKeywords(values.keywords),
  };
}

function toCategoryUpdatePayload(values: CategoryFormValues): Partial<CategoryPayload> {
  return {
    name: values.name.trim(),
    description: values.description?.trim() || null,
    parent_id: null,
    allow_ai_recommend: values.allow_ai_recommend,
    keywords: parseKeywords(values.keywords),
  };
}

function toCategoryFormValues(category: Category): CategoryFormValues {
  return {
    name: category.name,
    code: category.code,
    description: category.description ?? "",
    keywords: category.keywords.join(", "),
    allow_ai_recommend: category.allow_ai_recommend,
  };
}

function toDatasetPayload(values: DatasetFormValues): DatasetMappingPayload {
  return {
    name: values.name.trim(),
    category_id: values.category_id,
    ragflow_dataset_id: values.ragflow_dataset_id.trim(),
    ragflow_dataset_name: values.ragflow_dataset_name.trim(),
    enabled: values.enabled,
  };
}

function toDatasetFormValues(mapping: DatasetMapping): DatasetFormValues {
  return {
    name: mapping.name,
    category_id: mapping.category_id,
    ragflow_dataset_id: mapping.ragflow_dataset_id,
    ragflow_dataset_name: mapping.ragflow_dataset_name,
    enabled: mapping.enabled,
  };
}

function mappingStatus(mapping?: DatasetMapping): DatasetConfigRow["status"] {
  if (!mapping) {
    return "pending";
  }
  return mapping.enabled ? "enabled" : "disabled";
}

function connectionStatus(
  result: RagflowConnectionTestResult | undefined,
  isPending: boolean,
): {
  tone: "info" | "success" | "warning" | "danger";
  label: string;
  detail: string;
} {
  if (isPending) {
    return {
      tone: "warning",
      label: "检测中",
      detail: "正在向 RAGFlow 发送连接测试请求",
    };
  }
  if (!result) {
    return {
      tone: "info",
      label: "待测试",
      detail: "使用系统配置中的 RAGFlow 地址和 API Key 进行只读探测",
    };
  }
  if (result.ok) {
    return {
      tone: "success",
      label: "连接正常",
      detail:
        result.latency_ms === null ? "服务已响应，未返回耗时" : `服务响应 ${result.latency_ms} ms`,
    };
  }

  return {
    tone: "danger",
    label: "连接异常",
    detail: result.error ?? "RAGFlow 未返回可用错误详情",
  };
}

export default function DatasetConfigPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [categoryForm] = Form.useForm<CategoryFormValues>();
  const [datasetForm] = Form.useForm<DatasetFormValues>();
  const [categoryModalOpen, setCategoryModalOpen] = useState(false);
  const [datasetModalOpen, setDatasetModalOpen] = useState(false);
  const [editingCategory, setEditingCategory] = useState<Category | null>(null);
  const [editingDataset, setEditingDataset] = useState<DatasetMapping | null>(null);
  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: listCategories,
  });
  const datasetsQuery = useQuery({
    queryKey: ["dataset-mappings"],
    queryFn: listDatasetMappings,
  });
  const ragflowConfigQuery = useQuery({
    queryKey: ["configs", "ragflow"],
    queryFn: () => getConfigs("ragflow"),
    enabled: datasetModalOpen,
    staleTime: 30_000,
  });
  const ragflowDatasetsQuery = useQuery({
    queryKey: ["ragflow-datasets", "mapping-options"],
    queryFn: () => discoverRagflowDatasets({}),
    enabled: datasetModalOpen,
    staleTime: 30_000,
  });

  const categories = categoriesQuery.data?.items ?? [];
  const datasets = datasetsQuery.data?.items ?? [];
  const enabledMappings = datasets.filter((mapping) => mapping.enabled);
  const categoryOptions = categories.map((category) => ({
    label: `${category.name} (${category.code})`,
    value: category.id,
  }));
  const allowedDatasetIds = useMemo(() => {
    const configuredValue = ragflowConfigQuery.data?.items.find(
      (item) => item.key === RAGFLOW_ALLOWED_DATASET_IDS_KEY,
    )?.value;

    if (!Array.isArray(configuredValue)) {
      return [];
    }

    return configuredValue.filter(
      (datasetId): datasetId is string =>
        typeof datasetId === "string" && datasetId.trim().length > 0,
    );
  }, [ragflowConfigQuery.data]);
  const ragflowDatasetById = useMemo(() => {
    const datasetsById = new Map<string, RagflowDatasetOption>();
    if (ragflowDatasetsQuery.data?.ok) {
      for (const dataset of ragflowDatasetsQuery.data.items) {
        datasetsById.set(dataset.dataset_id, dataset);
      }
    }
    return datasetsById;
  }, [ragflowDatasetsQuery.data]);
  const ragflowDatasetOptions = useMemo(
    () =>
      allowedDatasetIds.flatMap((datasetId) => {
        const dataset = ragflowDatasetById.get(datasetId);
        return dataset
          ? [
              {
                label: `${dataset.name}（${dataset.dataset_id}）`,
                value: dataset.dataset_id,
              },
            ]
          : [];
      }),
    [allowedDatasetIds, ragflowDatasetById],
  );
  const ragflowDatasetsLoading =
    ragflowConfigQuery.isLoading ||
    ragflowDatasetsQuery.isLoading ||
    ragflowDatasetsQuery.isFetching;
  const ragflowDatasetLoadError =
    (ragflowConfigQuery.error instanceof Error ? ragflowConfigQuery.error.message : null) ??
    (ragflowDatasetsQuery.error instanceof Error ? ragflowDatasetsQuery.error.message : null) ??
    (ragflowDatasetsQuery.data && !ragflowDatasetsQuery.data.ok
      ? (ragflowDatasetsQuery.data.error ?? "RAGFlow Dataset 加载失败")
      : null);

  const rows = useMemo<DatasetConfigRow[]>(() => {
    return categories.map((category) => {
      const mapping =
        datasets.find((item) => item.category_id === category.id && item.enabled) ??
        datasets.find((item) => item.category_id === category.id);

      return {
        id: category.id,
        category,
        mapping,
        status: mappingStatus(mapping),
      };
    });
  }, [categories, datasets]);

  const filteredRows = rows.filter((row) => {
    const keyword = searchText.trim().toLowerCase();
    const haystack = [
      row.category.name,
      row.category.code,
      row.mapping?.name,
      row.mapping?.ragflow_dataset_id,
      row.mapping?.ragflow_dataset_name,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const matchesKeyword = !keyword || haystack.includes(keyword);
    const matchesStatus = statusFilter === "all" || row.status === statusFilter;

    return matchesKeyword && matchesStatus;
  });
  const boundCategoryCount = rows.filter((row) => Boolean(row.mapping)).length;
  const pendingRowCount = rows.filter((row) => row.status === "pending").length;
  const bindingCoverage =
    categories.length === 0 ? 0 : Math.round((boundCategoryCount / categories.length) * 100);
  const visibleEnabledCount = filteredRows.filter((row) => row.status === "enabled").length;
  const visiblePendingCount = filteredRows.filter((row) => row.status === "pending").length;
  const visibleDisabledCount = filteredRows.filter((row) => row.status === "disabled").length;

  const refreshConfig = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["categories"] }),
      queryClient.invalidateQueries({ queryKey: ["dataset-mappings"] }),
    ]);
  };

  const categoryMutation = useMutation({
    mutationFn: (values: CategoryFormValues) =>
      editingCategory
        ? updateCategory(editingCategory.id, toCategoryUpdatePayload(values))
        : createCategory(toCategoryCreatePayload(values)),
    onSuccess: async () => {
      message.success(editingCategory ? "分类已更新" : "分类已创建");
      setCategoryModalOpen(false);
      setEditingCategory(null);
      categoryForm.resetFields();
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const datasetMutation = useMutation({
    mutationFn: (values: DatasetFormValues) =>
      editingDataset
        ? updateDatasetMapping(editingDataset.id, toDatasetPayload(values))
        : createDatasetMapping(toDatasetPayload(values)),
    onSuccess: async () => {
      message.success(editingDataset ? "Dataset 映射已更新" : "Dataset 映射已创建");
      setDatasetModalOpen(false);
      setEditingDataset(null);
      datasetForm.resetFields();
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      if (enabled) {
        await updateDatasetMapping(id, { enabled: true });
        return;
      }
      await disableDatasetMapping(id);
    },
    onSuccess: async (_, variables) => {
      message.success(variables.enabled ? "Dataset 映射已启用" : "Dataset 映射已禁用");
      await refreshConfig();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const connectionMutation = useMutation({
    mutationFn: testRagflowConnection,
    onSuccess: (result) => {
      if (result.ok) {
        message.success(
          result.latency_ms === null
            ? "RAGFlow 连接正常"
            : `RAGFlow 连接正常，响应 ${result.latency_ms} ms`,
        );
        return;
      }
      message.error(result.error ?? "RAGFlow 连接异常");
    },
    onError: (error) => {
      message.error(error.message);
    },
  });
  const connection = connectionStatus(connectionMutation.data, connectionMutation.isPending);

  const openCreateCategory = () => {
    setEditingCategory(null);
    categoryForm.setFieldsValue(defaultCategoryValues);
    setCategoryModalOpen(true);
  };

  const openEditCategory = (category: Category) => {
    setEditingCategory(category);
    categoryForm.setFieldsValue(toCategoryFormValues(category));
    setCategoryModalOpen(true);
  };

  const openCreateDataset = (categoryId?: string) => {
    setEditingDataset(null);
    datasetForm.setFieldsValue({ ...defaultDatasetValues, category_id: categoryId ?? "" });
    setDatasetModalOpen(true);
  };

  const openEditDataset = (mapping: DatasetMapping) => {
    setEditingDataset(mapping);
    datasetForm.setFieldsValue(toDatasetFormValues(mapping));
    setDatasetModalOpen(true);
  };

  const refreshRagflowDatasets = async () => {
    await Promise.all([ragflowConfigQuery.refetch(), ragflowDatasetsQuery.refetch()]);
  };

  const resetFilters = () => {
    setSearchText("");
    setStatusFilter("all");
  };

  const columns: ColumnsType<DatasetConfigRow> = [
    {
      title: "分类名称",
      dataIndex: ["category", "name"],
      key: "name",
      width: 180,
      render: (_value, record) => (
        <span className="category-name-cell">
          <span className="category-name-cell__icon">
            <AppstoreOutlined />
          </span>
          <span>
            <Typography.Text strong className="single-line-text" title={record.category.name}>
              {record.category.name}
            </Typography.Text>
            <Typography.Text
              type="secondary"
              className="single-line-text"
              title={record.category.code}
            >
              {record.category.code}
            </Typography.Text>
          </span>
        </span>
      ),
    },
    {
      title: "分类编码",
      dataIndex: ["category", "code"],
      key: "code",
      width: 130,
      render: (value: string) => (
        <Typography.Text code className="single-line-text" title={value}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "目标 Dataset",
      key: "dataset",
      width: 190,
      render: (_, record) =>
        record.mapping ? (
          <span className="dataset-name-cell">
            <span className="dataset-pill" title={record.mapping.ragflow_dataset_name}>
              <span className="single-line-text">{record.mapping.ragflow_dataset_name}</span>
              <LinkOutlined />
            </span>
          </span>
        ) : (
          <StatusTag kind="dataset" value="unbound" />
        ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (value: DatasetConfigRow["status"]) => (
        <StatusTag kind="dataset" value={value} variant="dot" />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 170,
      render: (_, record) => {
        const mapping = record.mapping;

        return (
          <Space size={8}>
            <Button
              type="link"
              className="table-link-button"
              onClick={() => openEditCategory(record.category)}
            >
              编辑分类
            </Button>
            {mapping ? (
              <Button
                type="link"
                className="table-link-button"
                onClick={() => openEditDataset(mapping)}
              >
                编辑映射
              </Button>
            ) : (
              <Button
                type="link"
                className="table-link-button"
                onClick={() => openCreateDataset(record.category.id)}
              >
                绑定
              </Button>
            )}
            {mapping ? (
              <Popconfirm
                title={mapping.enabled ? "禁用 Dataset 映射" : "启用 Dataset 映射"}
                okText={mapping.enabled ? "禁用" : "启用"}
                cancelText="取消"
                onConfirm={() =>
                  toggleMutation.mutate({
                    id: mapping.id,
                    enabled: !mapping.enabled,
                  })
                }
              >
                <Button type="link" danger={mapping.enabled} className="table-link-button">
                  {mapping.enabled ? "禁用" : "启用"}
                </Button>
              </Popconfirm>
            ) : null}
          </Space>
        );
      },
    },
  ];

  return (
    <PageContainer
      title="Dataset 配置"
      description="维护分类与 RAGFlow Dataset 映射；所有文档必须审核，禁止自动同步。"
    >
      <div className="metric-grid">
        <KpiCard
          icon={<AppstoreOutlined />}
          title="已配置分类数"
          value={categories.length}
          description="全部分类"
          tone="info"
        />
        <KpiCard
          icon={<CheckCircleOutlined />}
          title="启用映射数"
          value={enabledMappings.length}
          description="映射已生效"
          tone="success"
        />
        <KpiCard
          icon={<ExclamationCircleOutlined />}
          title="待完善映射"
          value={rows.filter((row) => row.status === "pending").length}
          description="未绑定 Dataset"
          tone="warning"
        />
      </div>

      <Card className="document-panel dataset-health-card">
        <div className="dataset-health-card__main">
          <span className="dataset-health-card__icon">
            <DatabaseOutlined />
          </span>
          <span className="dataset-health-card__copy">
            <Typography.Title level={3} className="dataset-health-card__title">
              RAGFlow 连接状态
            </Typography.Title>
            <Typography.Text type="secondary">
              在编辑分类映射前验证 RAGFlow 服务可达，减少后续同步失败。
            </Typography.Text>
          </span>
        </div>
        <div
          className={`dataset-health-card__result dataset-health-card__result--${connection.tone}`}
          aria-live="polite"
        >
          {connection.tone === "success" ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
          <span>
            <Typography.Text strong className="dataset-health-card__label">
              {connection.label}
            </Typography.Text>
            <Typography.Text type="secondary" className="dataset-health-card__detail">
              {connection.detail}
            </Typography.Text>
          </span>
        </div>
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={connectionMutation.isPending}
          onClick={() => connectionMutation.mutate()}
        >
          测试连接
        </Button>
      </Card>

      <Card className="document-panel table-card">
        <section className="dataset-command-strip" role="region" aria-label="Dataset 映射工作台">
          <div className="dataset-command-strip__main">
            <span className="dataset-command-strip__icon">
              <CloudSyncOutlined />
            </span>
            <span className="dataset-command-strip__copy">
              <span className="dataset-command-strip__title-row">
                <Typography.Text strong className="dataset-command-strip__title">
                  Dataset 映射工作台
                </Typography.Text>
                <StatusTag
                  kind="dataset"
                  value={pendingRowCount === 0 ? "enabled" : "pending"}
                  variant="dot"
                />
              </span>
              <Typography.Text type="secondary">
                当前筛选 {filteredRows.length} 类；同步目标在审核时从启用映射中明确选择。
              </Typography.Text>
            </span>
          </div>
          <div className="dataset-command-strip__stats" aria-label="当前筛选映射摘要">
            <span className="dataset-command-strip__stat dataset-command-strip__stat--success">
              <Typography.Text type="secondary">已启用</Typography.Text>
              <strong>{visibleEnabledCount}类</strong>
            </span>
            <span className="dataset-command-strip__stat dataset-command-strip__stat--warning">
              <Typography.Text type="secondary">待绑定</Typography.Text>
              <strong>{visiblePendingCount}类</strong>
            </span>
            <span className="dataset-command-strip__stat dataset-command-strip__stat--purple">
              <Typography.Text type="secondary">已禁用</Typography.Text>
              <strong>{visibleDisabledCount}类</strong>
            </span>
          </div>
          <div className="dataset-command-strip__action-panel">
            <div className="dataset-command-strip__coverage" aria-label="绑定覆盖率">
              <span className="dataset-command-strip__coverage-copy">
                <Typography.Text type="secondary">绑定覆盖率</Typography.Text>
                <strong>{bindingCoverage}%</strong>
              </span>
              <Progress percent={bindingCoverage} size="small" showInfo={false} />
            </div>
            <Space wrap className="dataset-command-strip__actions">
              <Button size="small" onClick={() => setStatusFilter("pending")}>
                只看待绑定
              </Button>
              <Button size="small" onClick={() => setStatusFilter("enabled")}>
                只看已启用
              </Button>
            </Space>
          </div>
        </section>

        <div className="config-card-actions">
          <Space wrap>
            <Button icon={<FolderAddOutlined />} onClick={openCreateCategory}>
              新增分类
            </Button>
            <Button type="primary" icon={<DatabaseOutlined />} onClick={() => openCreateDataset()}>
              新增映射
            </Button>
          </Space>
        </div>

        <div className="filter-toolbar">
          <Input.Search
            className="filter-toolbar__search"
            placeholder="搜索分类名称、编码或 Dataset 名称"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            allowClear
          />
          <Select
            className="filter-toolbar__control"
            value={statusFilter}
            options={statusOptions}
            onChange={setStatusFilter}
          />
          <Button onClick={resetFilters}>重置</Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void refreshConfig()}
            loading={categoriesQuery.isFetching || datasetsQuery.isFetching}
          />
        </div>

        <Table<DatasetConfigRow>
          className="dataset-config-table"
          rowKey="id"
          columns={columns}
          dataSource={filteredRows}
          loading={categoriesQuery.isLoading || datasetsQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{ emptyText: "暂无分类映射" }}
          scroll={{ x: 820 }}
        />
      </Card>

      <Modal
        title={editingCategory ? "编辑分类" : "新增分类"}
        open={categoryModalOpen}
        onCancel={() => setCategoryModalOpen(false)}
        onOk={() => categoryForm.submit()}
        confirmLoading={categoryMutation.isPending}
        width={720}
      >
        <section className="config-modal-summary" role="region" aria-label="分类配置摘要">
          <span className="config-modal-summary__icon">
            <AppstoreOutlined />
          </span>
          <span className="config-modal-summary__copy">
            <Typography.Text strong>{editingCategory?.name ?? "新分类策略"}</Typography.Text>
            <Typography.Text type="secondary">
              所有文档必须审核，禁止自动同步；Dataset 在审批时明确选择。
            </Typography.Text>
          </span>
          <span className="config-modal-summary__metric">
            <strong>{editingCategory ? "编辑" : "新增"}</strong>
            <small>分类</small>
          </span>
        </section>
        <Form<CategoryFormValues>
          form={categoryForm}
          layout="vertical"
          requiredMark={false}
          initialValues={defaultCategoryValues}
          onFinish={(values) => categoryMutation.mutate(values)}
        >
          <div className="form-grid form-grid--two">
            <Form.Item
              label="分类名称"
              name="name"
              rules={[{ required: true, message: "请输入分类名称" }]}
            >
              <Input maxLength={80} />
            </Form.Item>
            <Form.Item
              label="分类编码"
              name="code"
              rules={[{ required: true, message: "请输入分类编码" }]}
            >
              <Input maxLength={80} disabled={Boolean(editingCategory)} />
            </Form.Item>
          </div>

          <Form.Item label="说明" name="description">
            <Input.TextArea rows={2} maxLength={500} showCount />
          </Form.Item>

          <Form.Item label="关键词" name="keywords">
            <Input.TextArea rows={2} placeholder="用逗号或换行分隔" maxLength={500} />
          </Form.Item>

          <div className="switch-grid">
            <Form.Item label="AI 可推荐" name="allow_ai_recommend" valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>
        </Form>
      </Modal>

      <Modal
        title={editingDataset ? "编辑 Dataset 映射" : "新增 Dataset 映射"}
        open={datasetModalOpen}
        onCancel={() => setDatasetModalOpen(false)}
        onOk={() => datasetForm.submit()}
        confirmLoading={datasetMutation.isPending}
        width={620}
      >
        <section className="config-modal-summary" role="region" aria-label="Dataset 映射摘要">
          <span className="config-modal-summary__icon">
            <DatabaseOutlined />
          </span>
          <span className="config-modal-summary__copy">
            <Typography.Text strong>{editingDataset?.name ?? "新 Dataset 映射"}</Typography.Text>
            <Typography.Text type="secondary">
              关联分类与 RAGFlow Dataset，控制后续同步目标和启用状态。
            </Typography.Text>
          </span>
          <span className="config-modal-summary__metric">
            <strong>{editingDataset?.enabled === false ? "禁用" : "启用"}</strong>
            <small>映射</small>
          </span>
        </section>
        {ragflowDatasetLoadError ? (
          <Alert
            type="error"
            showIcon
            message="RAGFlow Dataset 加载失败"
            description={ragflowDatasetLoadError}
            style={{ marginBottom: 16 }}
          />
        ) : !ragflowDatasetsLoading && allowedDatasetIds.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="尚未配置允许同步的 Dataset"
            description="请先到“系统设置 → RAGFlow”加载并保存允许同步的 Dataset。"
            style={{ marginBottom: 16 }}
          />
        ) : !ragflowDatasetsLoading && ragflowDatasetOptions.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="未找到可用的 RAGFlow Dataset"
            description="允许列表中的 Dataset 可能已被删除或当前 API Key 无权访问，请刷新后重试。"
            style={{ marginBottom: 16 }}
          />
        ) : null}
        <Form<DatasetFormValues>
          form={datasetForm}
          layout="vertical"
          requiredMark={false}
          initialValues={defaultDatasetValues}
          onFinish={(values) => datasetMutation.mutate(values)}
        >
          <Form.Item
            label="映射名称"
            name="name"
            rules={[{ required: true, message: "请输入映射名称" }]}
          >
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item
            label="所属分类"
            name="category_id"
            rules={[{ required: true, message: "请选择分类" }]}
          >
            <Select
              options={categoryOptions}
              loading={categoriesQuery.isLoading}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item
            label="RAGFlow Dataset"
            name="ragflow_dataset_id"
            rules={[{ required: true, message: "请选择 RAGFlow Dataset" }]}
            extra={
              <Button
                type="link"
                size="small"
                icon={<ReloadOutlined />}
                loading={ragflowDatasetsLoading}
                onClick={() => void refreshRagflowDatasets()}
              >
                刷新 Dataset
              </Button>
            }
          >
            <Select
              showSearch
              optionFilterProp="label"
              loading={ragflowDatasetsLoading}
              options={ragflowDatasetOptions}
              placeholder="选择系统已允许同步的 Dataset"
              notFoundContent={ragflowDatasetsLoading ? "正在加载 Dataset…" : "没有可选 Dataset"}
              onChange={(datasetId: string) => {
                datasetForm.setFieldValue(
                  "ragflow_dataset_name",
                  ragflowDatasetById.get(datasetId)?.name ?? "",
                );
              }}
            />
          </Form.Item>
          <Form.Item name="ragflow_dataset_name" hidden>
            <input type="hidden" />
          </Form.Item>
          <Form.Item label="启用状态" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
