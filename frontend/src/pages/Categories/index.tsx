import {
  App as AntdApp,
  Button,
  Card,
  Form,
  Input,
  Modal,
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
  PlusOutlined,
  SafetyCertificateOutlined,
  ReloadOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ColumnsType } from "antd/es/table";

import {
  type Category,
  type CategoryPayload,
  type DatasetMapping,
  createCategory,
  listCategories,
  listDatasetMappings,
  updateCategory,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

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

const defaultFormValues: CategoryFormValues = {
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

function parseKeywords(value?: string): string[] {
  return (value ?? "")
    .split(/[,，\n]/)
    .map((k) => k.trim())
    .filter(Boolean);
}

function toCreatePayload(values: CategoryFormValues): CategoryPayload {
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

function toUpdatePayload(values: CategoryFormValues): Partial<CategoryPayload> {
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

function toFormValues(category: Category): CategoryFormValues {
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

interface CategoryPolicyStripProps {
  aiEnabledCount: number;
  allowEmployeeSelectCount: number;
  autoSyncCount: number;
  boundCategoryCount: number;
  requireReviewCount: number;
  sensitiveDetectionCount: number;
  total: number;
  unboundCategoryCount: number;
}

function CategoryPolicyStrip({
  aiEnabledCount,
  allowEmployeeSelectCount,
  autoSyncCount,
  boundCategoryCount,
  requireReviewCount,
  sensitiveDetectionCount,
  total,
  unboundCategoryCount,
}: CategoryPolicyStripProps) {
  const lanes = [
    {
      key: "binding",
      icon: <AppstoreOutlined />,
      title: "Dataset 绑定",
      primary: `${boundCategoryCount} 个分类已绑定`,
      secondary: `${unboundCategoryCount} 个分类待绑定知识库`,
      status: unboundCategoryCount > 0 ? "unknown" : "ok",
    },
    {
      key: "ai",
      icon: <RobotOutlined />,
      title: "AI 分析策略",
      primary: `${aiEnabledCount} 个启用 AI`,
      secondary: `${sensitiveDetectionCount} 个启用敏感检测`,
      status: aiEnabledCount > 0 && sensitiveDetectionCount > 0 ? "ok" : "unknown",
    },
    {
      key: "review",
      icon: <SafetyCertificateOutlined />,
      title: "审核与可选",
      primary: `${requireReviewCount} 个需要审核`,
      secondary: `${allowEmployeeSelectCount} 个员工可选分类`,
      status: requireReviewCount > 0 ? "ok" : "unknown",
    },
    {
      key: "sync",
      icon: <CloudSyncOutlined />,
      title: "同步策略",
      primary: `${autoSyncCount} 个自动同步`,
      secondary: `平台共 ${total} 个分类策略`,
      status: autoSyncCount > 0 ? "ok" : "unknown",
    },
  ];

  return (
    <section className="categories-policy-strip" role="region" aria-label="分类策略状态">
      <div className="categories-policy-strip__summary">
        <span className="categories-policy-strip__icon">
          <AppstoreOutlined />
        </span>
        <span className="categories-policy-strip__copy">
          <Typography.Text strong className="categories-policy-strip__title">
            分类策略状态
          </Typography.Text>
          <Typography.Text type="secondary">
            汇总分类与 Dataset 绑定、AI 分析、审核可选和同步策略覆盖情况。
          </Typography.Text>
        </span>
        <span className="categories-policy-strip__total">
          <strong>{total}</strong>
          <Typography.Text type="secondary">分类策略</Typography.Text>
        </span>
      </div>

      <div className="categories-policy-strip__lanes" aria-label="分类策略指标">
        {lanes.map((lane) => (
          <div className="categories-policy-lane" key={lane.key}>
            <span className="categories-policy-lane__icon">{lane.icon}</span>
            <span className="categories-policy-lane__body">
              <span className="categories-policy-lane__topline">
                <Typography.Text strong>{lane.title}</Typography.Text>
                <StatusTag kind="health" value={lane.status} variant="dot" />
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

export default function CategoriesPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<CategoryFormValues>();
  const [modalOpen, setModalOpen] = useState(false);
  const [editingCategory, setEditingCategory] = useState<Category | null>(null);

  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: listCategories,
  });

  const datasetsQuery = useQuery({
    queryKey: ["dataset-mappings"],
    queryFn: listDatasetMappings,
  });

  const categories = categoriesQuery.data?.items ?? [];
  const datasetMappings = datasetsQuery.data?.items ?? [];

  const datasetOptions = datasetMappings.map((dm: DatasetMapping) => ({
    label: dm.name,
    value: dm.id,
  }));
  const boundCategoryCount = categories.filter((category) => category.default_dataset_id).length;
  const aiEnabledCount = categories.filter((category) => category.ai_analysis_enabled).length;
  const autoSyncCount = categories.filter((category) => category.auto_sync_enabled).length;
  const unboundCategoryCount = categories.length - boundCategoryCount;
  const sensitiveDetectionCount = categories.filter(
    (category) => category.sensitive_detection_enabled,
  ).length;
  const requireReviewCount = categories.filter((category) => category.require_review).length;
  const allowEmployeeSelectCount = categories.filter(
    (category) => category.allow_employee_select,
  ).length;

  const refreshCategories = async () => {
    await queryClient.invalidateQueries({ queryKey: ["categories"] });
  };

  const categoryMutation = useMutation({
    mutationFn: (values: CategoryFormValues) =>
      editingCategory
        ? updateCategory(editingCategory.id, toUpdatePayload(values))
        : createCategory(toCreatePayload(values)),
    onSuccess: async () => {
      message.success(editingCategory ? "分类已更新" : "分类已创建");
      setModalOpen(false);
      setEditingCategory(null);
      form.resetFields();
      await refreshCategories();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({
      id,
      field,
      value,
    }: {
      id: string;
      field: keyof Pick<
        CategoryPayload,
        "ai_analysis_enabled" | "sensitive_detection_enabled" | "auto_sync_enabled"
      >;
      value: boolean;
    }) => updateCategory(id, { [field]: value }),
    onSuccess: async () => {
      await refreshCategories();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  const openCreate = () => {
    setEditingCategory(null);
    form.setFieldsValue(defaultFormValues);
    setModalOpen(true);
  };

  const openEdit = (category: Category) => {
    setEditingCategory(category);
    form.setFieldsValue(toFormValues(category));
    setModalOpen(true);
  };

  const columns: ColumnsType<Category> = [
    {
      title: "分类名称",
      dataIndex: "name",
      key: "name",
      width: 160,
      render: (value: string, record) => (
        <span className="category-name-cell">
          <span className="category-name-cell__icon">
            <AppstoreOutlined />
          </span>
          <span>
            <Typography.Text strong className="single-line-text" title={value}>
              {value}
            </Typography.Text>
            <Typography.Text type="secondary" className="single-line-text" title={record.code}>
              {record.code}
            </Typography.Text>
          </span>
        </span>
      ),
    },
    {
      title: "分类编码",
      dataIndex: "code",
      key: "code",
      width: 120,
      render: (value: string) => (
        <Typography.Text code className="single-line-text" title={value}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      width: 180,
      render: (value: string | null) =>
        value ? (
          <Typography.Text type="secondary" ellipsis title={value}>
            {value}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: "启用状态",
      dataIndex: "allow_employee_select",
      key: "allow_employee_select",
      width: 100,
      render: (_: unknown, record) => (
        <StatusTag
          kind="dataset"
          value={record.allow_employee_select ? "enabled" : "disabled"}
          variant="dot"
        />
      ),
    },
    {
      title: "关联知识库",
      key: "dataset_mapping",
      width: 160,
      render: (_: unknown, record) => {
        const mapping = datasetMappings.find(
          (dm: DatasetMapping) => dm.id === record.default_dataset_id,
        );
        return mapping ? (
          <Typography.Text className="single-line-text" title={mapping.name}>
            {mapping.name}
          </Typography.Text>
        ) : (
          <StatusTag kind="dataset" value="unbound" />
        );
      },
    },
    {
      title: "AI 分析",
      dataIndex: "ai_analysis_enabled",
      key: "ai_analysis_enabled",
      width: 90,
      render: (value: boolean, record) => (
        <Switch
          checked={value}
          size="small"
          loading={toggleMutation.isPending}
          onChange={(checked) =>
            toggleMutation.mutate({
              id: record.id,
              field: "ai_analysis_enabled",
              value: checked,
            })
          }
        />
      ),
    },
    {
      title: "敏感检测",
      dataIndex: "sensitive_detection_enabled",
      key: "sensitive_detection_enabled",
      width: 90,
      render: (value: boolean, record) => (
        <Switch
          checked={value}
          size="small"
          loading={toggleMutation.isPending}
          onChange={(checked) =>
            toggleMutation.mutate({
              id: record.id,
              field: "sensitive_detection_enabled",
              value: checked,
            })
          }
        />
      ),
    },
    {
      title: "自动同步",
      dataIndex: "auto_sync_enabled",
      key: "auto_sync_enabled",
      width: 90,
      render: (value: boolean, record) => (
        <Switch
          checked={value}
          size="small"
          loading={toggleMutation.isPending}
          onChange={(checked) =>
            toggleMutation.mutate({
              id: record.id,
              field: "auto_sync_enabled",
              value: checked,
            })
          }
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      fixed: "right" as const,
      render: (_: unknown, record) => (
        <Button type="link" className="table-link-button" onClick={() => openEdit(record)}>
          编辑
        </Button>
      ),
    },
  ];

  return (
    <PageContainer
      title="分类管理"
      description="管理文档分类，配置 AI 分析、敏感检测和自动同步行为。"
    >
      <div className="metric-grid">
        <KpiCard
          icon={<AppstoreOutlined />}
          title="分类总数"
          value={categories.length}
          description="全部文档分类"
          tone="primary"
        />
        <KpiCard
          icon={<CheckCircleOutlined />}
          title="已绑定 Dataset"
          value={boundCategoryCount}
          description="配置默认知识库"
          tone="success"
        />
        <KpiCard
          icon={<RobotOutlined />}
          title="AI 启用分类"
          value={aiEnabledCount}
          description="允许自动分析"
          tone="info"
        />
        <KpiCard
          icon={<CloudSyncOutlined />}
          title="自动同步分类"
          value={autoSyncCount}
          description="审核后自动入库"
          tone="purple"
        />
      </div>

      <CategoryPolicyStrip
        aiEnabledCount={aiEnabledCount}
        allowEmployeeSelectCount={allowEmployeeSelectCount}
        autoSyncCount={autoSyncCount}
        boundCategoryCount={boundCategoryCount}
        requireReviewCount={requireReviewCount}
        sensitiveDetectionCount={sensitiveDetectionCount}
        total={categories.length}
        unboundCategoryCount={unboundCategoryCount}
      />

      <Card className="document-panel table-card">
        <div className="config-card-actions">
          <Space wrap>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增分类
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void refreshCategories()}
              loading={categoriesQuery.isFetching}
            />
          </Space>
        </div>

        <Table<Category>
          className="categories-table"
          rowKey="id"
          columns={columns}
          dataSource={categories}
          loading={categoriesQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{ emptyText: "暂无分类" }}
          scroll={{ x: 1150 }}
        />
      </Card>

      <Modal
        title={editingCategory ? "编辑分类" : "新增分类"}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setEditingCategory(null);
        }}
        okText="确定"
        cancelText="取消"
        onOk={() => form.submit()}
        confirmLoading={categoryMutation.isPending}
        width={720}
        destroyOnHidden
      >
        <Form<CategoryFormValues>
          form={form}
          layout="vertical"
          requiredMark={false}
          initialValues={defaultFormValues}
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

          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} maxLength={500} showCount />
          </Form.Item>

          <div className="form-grid form-grid--two">
            <Form.Item label="关联知识库" name="default_dataset_id">
              <Select
                options={datasetOptions}
                loading={datasetsQuery.isLoading}
                allowClear
                showSearch
                optionFilterProp="label"
                placeholder="请选择关联知识库"
              />
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
    </PageContainer>
  );
}
