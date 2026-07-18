import { App as AntdApp, Button, Card, Form, Input, Space, Switch, Table, Typography } from "antd";
import {
  AppstoreOutlined,
  BulbOutlined,
  PlusOutlined,
  ReloadOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ColumnsType } from "antd/es/table";

import {
  type Category,
  type CategoryPayload,
  createCategory,
  listCategories,
  updateCategory,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { SessionBoundModal as Modal } from "../../components/SessionBoundActions";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import "./styles.css";

interface CategoryFormValues {
  name: string;
  code: string;
  description?: string;
  keywords?: string;
  allow_ai_recommend: boolean;
}

const defaultFormValues: CategoryFormValues = {
  name: "",
  code: "",
  description: "",
  keywords: "",
  allow_ai_recommend: true,
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
    allow_ai_recommend: values.allow_ai_recommend,
    keywords: parseKeywords(values.keywords),
  };
}

function toUpdatePayload(values: CategoryFormValues): Partial<CategoryPayload> {
  return {
    name: values.name.trim(),
    description: values.description?.trim() || null,
    parent_id: null,
    allow_ai_recommend: values.allow_ai_recommend,
    keywords: parseKeywords(values.keywords),
  };
}

function toFormValues(category: Category): CategoryFormValues {
  return {
    name: category.name,
    code: category.code,
    description: category.description ?? "",
    keywords: category.keywords.join(", "),
    allow_ai_recommend: category.allow_ai_recommend,
  };
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

  const categories = categoriesQuery.data?.items ?? [];
  const aiRecommendCount = categories.filter((category) => category.allow_ai_recommend).length;
  const keywordCoverageCount = categories.filter((category) => category.keywords.length > 0).length;

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
    mutationFn: ({ id, value }: { id: string; value: boolean }) =>
      updateCategory(id, { allow_ai_recommend: value }),
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
      title: "关键词",
      dataIndex: "keywords",
      key: "keywords",
      width: 200,
      render: (value: string[]) => (
        <Typography.Text type="secondary" ellipsis title={value.join("、")}>
          {value.length > 0 ? value.join("、") : "—"}
        </Typography.Text>
      ),
    },
    {
      title: "AI 可推荐",
      dataIndex: "allow_ai_recommend",
      key: "allow_ai_recommend",
      width: 110,
      render: (value: boolean, record) => (
        <Switch
          aria-label={`${record.name} AI 可推荐`}
          checked={value}
          size="small"
          loading={toggleMutation.isPending}
          onChange={(checked) => toggleMutation.mutate({ id: record.id, value: checked })}
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
      description="管理分类结构、说明与检索关键词；所有文档必须审核，禁止自动同步。"
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
          icon={<BulbOutlined />}
          title="AI 可推荐"
          value={aiRecommendCount}
          description="可参与分类推荐"
          tone="success"
        />
        <KpiCard
          icon={<TagsOutlined />}
          title="关键词已配置"
          value={keywordCoverageCount}
          description="支持检索与推荐"
          tone="purple"
        />
      </div>

      <Card className="document-panel table-card">
        <div className="table-section-header">
          <span className="table-section-header__copy">
            <Typography.Title level={4} className="table-section-header__title">
              分类策略列表
            </Typography.Title>
            <Typography.Text className="table-section-header__meta">
              当前维护 {categories.length} 个分类，{keywordCoverageCount} 个已配置关键词
            </Typography.Text>
          </span>
          <StatusTag kind="health" value={categoriesQuery.isError ? "error" : "ok"} variant="dot" />
        </div>

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
          scroll={{ x: 780 }}
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
        className="category-config-modal"
      >
        <section className="category-form-summary" role="region" aria-label="分类配置摘要">
          <span className="category-form-summary__icon">
            <AppstoreOutlined />
          </span>
          <span className="category-form-summary__copy">
            <Typography.Text strong>
              {editingCategory ? editingCategory.name : "新建分类策略"}
            </Typography.Text>
            <Typography.Text type="secondary">
              {editingCategory ? editingCategory.code : "待配置编码"}
            </Typography.Text>
          </span>
          <StatusTag kind="dataset" value="required" variant="dot" />
        </section>
        <Typography.Paragraph type="secondary" className="category-policy-note">
          所有文档必须审核，禁止自动同步；Dataset 仅在审批时明确选择。
        </Typography.Paragraph>

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

          <Form.Item label="关键词" name="keywords">
            <Input.TextArea rows={2} placeholder="用逗号或换行分隔" maxLength={500} />
          </Form.Item>

          <div className="category-switch-panel">
            <div className="category-switch-panel__header">
              <Typography.Text strong>推荐策略</Typography.Text>
              <StatusTag kind="dataset" value="enabled" variant="dot" />
            </div>
            <div className="switch-grid category-switch-panel__grid">
              <Form.Item label="AI 可推荐" name="allow_ai_recommend" valuePropName="checked">
                <Switch />
              </Form.Item>
            </div>
          </div>
        </Form>
      </Modal>
    </PageContainer>
  );
}
