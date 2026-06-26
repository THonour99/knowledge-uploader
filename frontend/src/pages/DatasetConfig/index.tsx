import {
  App as AntdApp,
  Button,
  Card,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  AuditOutlined,
  CheckCircleOutlined,
  CloudSyncOutlined,
  DatabaseOutlined,
  DownOutlined,
  ExclamationCircleOutlined,
  FolderAddOutlined,
  LinkOutlined,
  ReloadOutlined,
  StopOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import type { ColumnsType } from "antd/es/table";

import {
  type Category,
  type CategoryPayload,
  type DatasetMapping,
  type DatasetMappingPayload,
  type RagflowConnectionTestResult,
  createCategory,
  createDatasetMapping,
  disableDatasetMapping,
  listCategories,
  listDatasetMappings,
  testRagflowConnection,
  updateCategory,
  updateDatasetMapping,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";

interface CategoryFormValues {
  name: string;
  code: string;
  description?: string;
  default_dataset_id?: string;
  default_visibility: Category["default_visibility"];
  keywords?: string;
  classification_prompt?: string;
  require_review: boolean;
  allow_employee_select: boolean;
  allow_ai_recommend: boolean;
  ai_analysis_enabled: boolean;
  sensitive_detection_enabled: boolean;
  auto_sync_enabled: boolean;
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

interface DatasetPolicyStripProps {
  aiRecommendCount: number;
  autoSyncCount: number;
  boundCategoryCount: number;
  disabledMappingCount: number;
  employeeSelectableCount: number;
  enabledMappingCount: number;
  pendingMappingCount: number;
  reviewRequiredCount: number;
  totalCategoryCount: number;
  totalMappingCount: number;
}

interface DatasetPolicyLane {
  key: string;
  icon: ReactNode;
  title: string;
  primary: string;
  secondary: string;
  status: {
    kind: "dataset" | "health";
    value: string;
  };
}

const defaultCategoryValues: CategoryFormValues = {
  name: "",
  code: "",
  description: "",
  default_dataset_id: "",
  default_visibility: "private",
  keywords: "",
  classification_prompt: "",
  require_review: true,
  allow_employee_select: true,
  allow_ai_recommend: true,
  ai_analysis_enabled: true,
  sensitive_detection_enabled: true,
  auto_sync_enabled: false,
};

const defaultDatasetValues: DatasetFormValues = {
  name: "",
  category_id: "",
  ragflow_dataset_id: "",
  ragflow_dataset_name: "",
  enabled: true,
};

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
    require_review: values.require_review,
    default_dataset_id: values.default_dataset_id?.trim() || null,
    allow_employee_select: values.allow_employee_select,
    allow_ai_recommend: values.allow_ai_recommend,
    default_visibility: "private",
    keywords: parseKeywords(values.keywords),
    classification_prompt: values.classification_prompt?.trim() || null,
    ai_analysis_enabled: values.ai_analysis_enabled,
    sensitive_detection_enabled: values.sensitive_detection_enabled,
    auto_sync_enabled: values.auto_sync_enabled,
  };
}

function toCategoryUpdatePayload(values: CategoryFormValues): Partial<CategoryPayload> {
  return {
    name: values.name.trim(),
    description: values.description?.trim() || null,
    parent_id: null,
    require_review: values.require_review,
    default_dataset_id: values.default_dataset_id?.trim() || null,
    allow_employee_select: values.allow_employee_select,
    allow_ai_recommend: values.allow_ai_recommend,
    default_visibility: "private",
    keywords: parseKeywords(values.keywords),
    classification_prompt: values.classification_prompt?.trim() || null,
    ai_analysis_enabled: values.ai_analysis_enabled,
    sensitive_detection_enabled: values.sensitive_detection_enabled,
    auto_sync_enabled: values.auto_sync_enabled,
  };
}

function toCategoryFormValues(category: Category): CategoryFormValues {
  return {
    name: category.name,
    code: category.code,
    description: category.description ?? "",
    default_dataset_id: category.default_dataset_id ?? "",
    default_visibility: "private",
    keywords: category.keywords.join(", "),
    classification_prompt: category.classification_prompt ?? "",
    require_review: category.require_review,
    allow_employee_select: category.allow_employee_select,
    allow_ai_recommend: category.allow_ai_recommend,
    ai_analysis_enabled: category.ai_analysis_enabled,
    sensitive_detection_enabled: category.sensitive_detection_enabled,
    auto_sync_enabled: category.auto_sync_enabled,
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

function DatasetPolicyStrip({
  aiRecommendCount,
  autoSyncCount,
  boundCategoryCount,
  disabledMappingCount,
  employeeSelectableCount,
  enabledMappingCount,
  pendingMappingCount,
  reviewRequiredCount,
  totalCategoryCount,
  totalMappingCount,
}: DatasetPolicyStripProps) {
  const coverageReady = totalCategoryCount > 0 && pendingMappingCount === 0;
  const lanes: DatasetPolicyLane[] = [
    {
      key: "coverage",
      icon: <AppstoreOutlined />,
      title: "绑定覆盖",
      primary: `${boundCategoryCount}/${totalCategoryCount} 分类已绑定`,
      secondary: `${pendingMappingCount} 个待绑定，${disabledMappingCount} 个禁用映射`,
      status: { kind: "health", value: coverageReady ? "ok" : "unknown" },
    },
    {
      key: "review",
      icon: <AuditOutlined />,
      title: "审核策略",
      primary: `${reviewRequiredCount} 类需要审核`,
      secondary: `${autoSyncCount} 类开启自动同步`,
      status: { kind: "dataset", value: reviewRequiredCount > 0 ? "required" : "skipped" },
    },
    {
      key: "employee",
      icon: <TeamOutlined />,
      title: "员工入口",
      primary: `${employeeSelectableCount} 类员工可选`,
      secondary: `${aiRecommendCount} 类允许 AI 推荐分类`,
      status: { kind: "health", value: employeeSelectableCount > 0 ? "ok" : "unknown" },
    },
    {
      key: "sync",
      icon: <CloudSyncOutlined />,
      title: "同步就绪",
      primary: `${enabledMappingCount} 个映射生效`,
      secondary: `${disabledMappingCount} 个已禁用，${totalMappingCount} 个总映射`,
      status: { kind: "dataset", value: enabledMappingCount > 0 ? "enabled" : "pending" },
    },
  ];

  return (
    <section className="dataset-policy-strip" role="region" aria-label="Dataset 配置总览">
      <div className="dataset-policy-strip__main">
        <span className="dataset-policy-strip__icon">
          <DatabaseOutlined />
        </span>
        <span className="dataset-policy-strip__copy">
          <Typography.Text strong className="dataset-policy-strip__title">
            Dataset 配置总览
          </Typography.Text>
          <Typography.Text type="secondary">
            集中检查分类覆盖、审核口径、员工入口与同步就绪度。
          </Typography.Text>
        </span>
        <span className="dataset-policy-strip__total">
          <strong>{enabledMappingCount}</strong>
          <Typography.Text type="secondary">生效映射</Typography.Text>
        </span>
      </div>
      <div className="dataset-policy-strip__lanes" aria-label="Dataset 配置指标">
        {lanes.map((lane) => (
          <div className="dataset-policy-lane" key={lane.key}>
            <span className="dataset-policy-lane__icon">{lane.icon}</span>
            <span className="dataset-policy-lane__body">
              <span className="dataset-policy-lane__topline">
                <Typography.Text strong>{lane.title}</Typography.Text>
                <StatusTag kind={lane.status.kind} value={lane.status.value} variant="dot" />
              </span>
              <strong>{lane.primary}</strong>
              <Typography.Text type="secondary">{lane.secondary}</Typography.Text>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
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
  const [reviewFilter, setReviewFilter] = useState("all");
  const [employeeSelectFilter, setEmployeeSelectFilter] = useState("all");

  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: listCategories,
  });
  const datasetsQuery = useQuery({
    queryKey: ["dataset-mappings"],
    queryFn: listDatasetMappings,
  });

  const categories = categoriesQuery.data?.items ?? [];
  const datasets = datasetsQuery.data?.items ?? [];
  const enabledMappings = datasets.filter((mapping) => mapping.enabled);
  const disabledMappings = datasets.filter((mapping) => !mapping.enabled);
  const reviewRequiredCount = categories.filter((category) => category.require_review).length;
  const employeeSelectableCount = categories.filter(
    (category) => category.allow_employee_select,
  ).length;
  const aiRecommendCount = categories.filter((category) => category.allow_ai_recommend).length;
  const autoSyncCount = categories.filter((category) => category.auto_sync_enabled).length;
  const categoryOptions = categories.map((category) => ({
    label: `${category.name} (${category.code})`,
    value: category.id,
  }));

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
    const matchesReview =
      reviewFilter === "all" ||
      (reviewFilter === "required" && row.category.require_review) ||
      (reviewFilter === "skipped" && !row.category.require_review);
    const matchesEmployeeSelect =
      employeeSelectFilter === "all" ||
      (employeeSelectFilter === "allowed" && row.category.allow_employee_select) ||
      (employeeSelectFilter === "blocked" && !row.category.allow_employee_select);

    return matchesKeyword && matchesStatus && matchesReview && matchesEmployeeSelect;
  });

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

  const resetFilters = () => {
    setSearchText("");
    setStatusFilter("all");
    setReviewFilter("all");
    setEmployeeSelectFilter("all");
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
      title: "是否需审核",
      dataIndex: ["category", "require_review"],
      key: "require_review",
      width: 110,
      render: (value: boolean) => (
        <StatusTag kind="dataset" value={value ? "required" : "skipped"} />
      ),
    },
    {
      title: "是否允许员工选择",
      dataIndex: ["category", "allow_employee_select"],
      key: "allow_employee_select",
      width: 140,
      render: (value: boolean) => <Switch checked={value} size="small" disabled />,
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
      description="配置知识库分类与 RAGFlow Dataset 的映射关系，控制审核、AI 分析和自动同步行为。"
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
        <KpiCard
          icon={<StopOutlined />}
          title="禁用映射数"
          value={disabledMappings.length}
          description="已禁用映射"
          tone="purple"
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

      <DatasetPolicyStrip
        aiRecommendCount={aiRecommendCount}
        autoSyncCount={autoSyncCount}
        boundCategoryCount={rows.filter((row) => Boolean(row.mapping)).length}
        disabledMappingCount={disabledMappings.length}
        employeeSelectableCount={employeeSelectableCount}
        enabledMappingCount={enabledMappings.length}
        pendingMappingCount={rows.filter((row) => row.status === "pending").length}
        reviewRequiredCount={reviewRequiredCount}
        totalCategoryCount={categories.length}
        totalMappingCount={datasets.length}
      />

      <Card className="document-panel table-card">
        <div className="config-card-actions">
          <Space wrap>
            <Button icon={<FolderAddOutlined />} onClick={openCreateCategory}>
              新增分类
            </Button>
            <Dropdown
              menu={{
                items: [
                  { key: "enable", label: "批量启用", disabled: true },
                  { key: "disable", label: "批量禁用", disabled: true },
                ],
              }}
            >
              <Button>
                批量操作 <DownOutlined />
              </Button>
            </Dropdown>
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
          <Select
            className="filter-toolbar__control"
            value={reviewFilter}
            options={[
              { label: "是否需审核：全部", value: "all" },
              { label: "需要审核", value: "required" },
              { label: "无需审核", value: "skipped" },
            ]}
            onChange={setReviewFilter}
          />
          <Select
            className="filter-toolbar__control"
            value={employeeSelectFilter}
            options={[
              { label: "员工选择：全部", value: "all" },
              { label: "允许选择", value: "allowed" },
              { label: "禁止选择", value: "blocked" },
            ]}
            onChange={setEmployeeSelectFilter}
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
          scroll={{ x: 1120 }}
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

          <div className="form-grid form-grid--two">
            <Form.Item label="默认 Dataset ID" name="default_dataset_id">
              <Input maxLength={128} />
            </Form.Item>
          </div>

          <Form.Item label="关键词" name="keywords">
            <Input.TextArea rows={2} placeholder="用逗号或换行分隔" maxLength={500} />
          </Form.Item>

          <Form.Item label="分类 Prompt" name="classification_prompt">
            <Input.TextArea rows={3} maxLength={2000} showCount />
          </Form.Item>

          <div className="switch-grid">
            <Form.Item label="需要审核" name="require_review" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="员工可选" name="allow_employee_select" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="AI 可推荐" name="allow_ai_recommend" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="AI 分析" name="ai_analysis_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="敏感检测" name="sensitive_detection_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="自动同步" name="auto_sync_enabled" valuePropName="checked">
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
          <div className="form-grid form-grid--two">
            <Form.Item
              label="RAGFlow Dataset ID"
              name="ragflow_dataset_id"
              rules={[{ required: true, message: "请输入 Dataset ID" }]}
            >
              <Input maxLength={128} />
            </Form.Item>
            <Form.Item
              label="RAGFlow Dataset 名称"
              name="ragflow_dataset_name"
              rules={[{ required: true, message: "请输入 Dataset 名称" }]}
            >
              <Input maxLength={128} />
            </Form.Item>
          </div>
          <Form.Item label="启用状态" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
