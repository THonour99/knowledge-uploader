import {
  App as AntdApp,
  Button,
  Card,
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
  CheckCircleOutlined,
  DatabaseOutlined,
  MergeOutlined,
  PlusOutlined,
  ReloadOutlined,
  TagOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ColumnsType } from "antd/es/table";

import {
  type CreateTagPayload,
  type MergeTagPayload,
  type Tag,
  type TagListQuery,
  type UpdateTagPayload,
  createTag,
  deleteTag,
  listTags,
  mergeTag,
  updateTag,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

// ── Form value types ──────────────────────────────────────────────────────────

interface TagFormValues {
  name: string;
  description?: string;
}

interface MergeFormValues {
  target_tag_id: string;
}

// ── Tags page ─────────────────────────────────────────────────────────────────

export default function TagsPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();

  // search / filter state
  const [search, setSearch] = useState<string>("");
  const [searchInput, setSearchInput] = useState<string>("");

  // tag form modal
  const [tagForm] = Form.useForm<TagFormValues>();
  const [tagModalOpen, setTagModalOpen] = useState(false);
  const [editingTag, setEditingTag] = useState<Tag | null>(null);

  // merge modal
  const [mergeForm] = Form.useForm<MergeFormValues>();
  const [mergeModalOpen, setMergeModalOpen] = useState(false);
  const [mergingSourceTag, setMergingSourceTag] = useState<Tag | null>(null);

  const queryParams: TagListQuery = {
    search: search || undefined,
    page: 1,
    page_size: 50,
  };

  const tagsQuery = useQuery({
    queryKey: ["tags", queryParams],
    queryFn: () => listTags(queryParams),
  });

  const tags = tagsQuery.data?.items ?? [];
  const total = tagsQuery.data?.total ?? 0;
  const enabledTagCount = tags.filter((tag) => tag.enabled).length;
  const systemGeneratedCount = tags.filter((tag) => tag.is_system_generated).length;
  const unusedTagCount = tags.filter((tag) => tag.usage_count === 0).length;

  const refreshTags = async () => {
    await queryClient.invalidateQueries({ queryKey: ["tags"] });
  };

  // ── Create / edit mutation ────────────────────────────────────────────────

  const tagMutation = useMutation({
    mutationFn: (values: TagFormValues) => {
      if (editingTag) {
        const description = values.description?.trim();
        const payload: UpdateTagPayload = {
          name: values.name.trim(),
          description: description || null,
        };
        return updateTag(editingTag.id, payload);
      }
      const payload: CreateTagPayload = {
        name: values.name.trim(),
        description: values.description?.trim() || undefined,
      };
      return createTag(payload);
    },
    onSuccess: async () => {
      message.success(editingTag ? "标签已更新" : "标签已创建");
      setTagModalOpen(false);
      setEditingTag(null);
      tagForm.resetFields();
      await refreshTags();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  // ── Enable toggle mutation ────────────────────────────────────────────────

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateTag(id, { enabled }),
    onSuccess: async () => {
      await refreshTags();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  // ── Merge mutation ────────────────────────────────────────────────────────

  const mergeMutation = useMutation({
    mutationFn: ({ sourceId, payload }: { sourceId: string; payload: MergeTagPayload }) =>
      mergeTag(sourceId, payload),
    onSuccess: async () => {
      message.success("标签合并成功");
      setMergeModalOpen(false);
      setMergingSourceTag(null);
      mergeForm.resetFields();
      await refreshTags();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  // ── Delete mutation ───────────────────────────────────────────────────────

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTag(id),
    onSuccess: async () => {
      message.success("标签已删除");
      await refreshTags();
    },
    onError: (error: Error) => {
      // 409: has associated files, prompt to merge first
      if (error.message.includes("409") || error.message.includes("关联")) {
        message.warning("该标签已关联文件，请先合并到其他标签再删除");
      } else {
        message.error(error.message);
      }
    },
  });

  // ── Handlers ──────────────────────────────────────────────────────────────

  const openCreate = () => {
    setEditingTag(null);
    tagForm.resetFields();
    setTagModalOpen(true);
  };

  const openEdit = (tag: Tag) => {
    setEditingTag(tag);
    tagForm.setFieldsValue({
      name: tag.name,
      description: tag.description ?? "",
    });
    setTagModalOpen(true);
  };

  const openMerge = (tag: Tag) => {
    setMergingSourceTag(tag);
    mergeForm.resetFields();
    setMergeModalOpen(true);
  };

  const handleSearch = () => {
    setSearch(searchInput);
  };

  const handleSearchClear = () => {
    setSearchInput("");
    setSearch("");
  };

  // ── Target tag options for merge modal (exclude source) ──────────────────

  const mergeTargetOptions = tags
    .filter((t) => t.id !== mergingSourceTag?.id)
    .map((t) => ({ label: t.name, value: t.id }));

  // ── Table columns ─────────────────────────────────────────────────────────

  const columns: ColumnsType<Tag> = [
    {
      title: "标签名称",
      dataIndex: "name",
      key: "name",
      width: 160,
      render: (value: string) => (
        <span className="tags-name-cell">
          <TagOutlined className="tags-name-cell__icon" />
          <Typography.Text strong className="tags-name-cell__text" ellipsis title={value}>
            {value}
          </Typography.Text>
        </span>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      width: 200,
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
      title: "使用次数",
      dataIndex: "usage_count",
      key: "usage_count",
      width: 100,
      align: "right" as const,
      render: (value: number) => <Typography.Text>{value.toLocaleString()}</Typography.Text>,
    },
    {
      title: "来源",
      dataIndex: "is_system_generated",
      key: "is_system_generated",
      width: 100,
      render: (value: boolean) =>
        value ? (
          <Typography.Text type="secondary">系统生成</Typography.Text>
        ) : (
          <Typography.Text>手动创建</Typography.Text>
        ),
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 80,
      render: (value: boolean, record) => (
        <Switch
          checked={value}
          size="small"
          loading={toggleMutation.isPending}
          onChange={(checked) => toggleMutation.mutate({ id: record.id, enabled: checked })}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 160,
      fixed: "right" as const,
      render: (_: unknown, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            className="table-link-button"
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            icon={<MergeOutlined />}
            className="table-link-button"
            onClick={() => openMerge(record)}
          >
            合并
          </Button>
          {record.usage_count === 0 ? (
            <Popconfirm
              title="确认删除"
              description={`删除标签「${record.name}」？此操作不可撤销。`}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteMutation.mutate(record.id)}
            >
              <Button
                type="link"
                size="small"
                danger
                className="table-link-button"
                loading={deleteMutation.isPending}
              >
                删除
              </Button>
            </Popconfirm>
          ) : (
            <Button
              type="link"
              size="small"
              danger
              className="table-link-button"
              onClick={() => deleteMutation.mutate(record.id)}
              loading={deleteMutation.isPending}
            >
              删除
            </Button>
          )}
        </Space>
      ),
    },
  ];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <PageContainer title="标签管理" description="管理文档标签，合并重复标签，控制标签启用状态。">
      <div className="metric-grid">
        <KpiCard
          icon={<TagOutlined />}
          title="标签总数"
          value={total}
          description="当前标签库规模"
          tone="primary"
        />
        <KpiCard
          icon={<CheckCircleOutlined />}
          title="启用标签"
          value={enabledTagCount}
          description="可用于文件关联"
          tone="success"
        />
        <KpiCard
          icon={<DatabaseOutlined />}
          title="系统标签"
          value={systemGeneratedCount}
          description="AI 或规则生成"
          tone="info"
        />
        <KpiCard
          icon={<MergeOutlined />}
          title="空闲标签"
          value={unusedTagCount}
          description="可合并或清理"
          tone="warning"
        />
      </div>

      <Card className="document-panel table-card">
        <div className="table-section-header">
          <span className="table-section-header__copy">
            <Typography.Title level={4} className="table-section-header__title">
              标签治理列表
            </Typography.Title>
            <Typography.Text className="table-section-header__meta">
              当前显示 {tags.length} 个标签，共 {total} 个治理对象，{unusedTagCount} 个空闲
            </Typography.Text>
          </span>
          <StatusTag kind="health" value={tagsQuery.isError ? "error" : "ok"} variant="dot" />
        </div>

        <div className="config-card-actions">
          <Space wrap>
            <Input.Search
              placeholder="搜索标签名称"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onSearch={handleSearch}
              onClear={handleSearchClear}
              allowClear
              style={{ width: 240 }}
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增标签
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void refreshTags()}
              loading={tagsQuery.isFetching}
            />
          </Space>
        </div>

        <Table<Tag>
          className="tags-table"
          rowKey="id"
          columns={columns}
          dataSource={tags}
          loading={tagsQuery.isLoading}
          pagination={{ total, pageSize: 50, showSizeChanger: false }}
          locale={{ emptyText: "暂无标签" }}
          scroll={{ x: 900 }}
        />
      </Card>

      {/* Create / Edit Modal */}
      <Modal
        title={editingTag ? "编辑标签" : "新增标签"}
        open={tagModalOpen}
        onCancel={() => {
          setTagModalOpen(false);
          setEditingTag(null);
        }}
        okText="确定"
        cancelText="取消"
        onOk={() => tagForm.submit()}
        confirmLoading={tagMutation.isPending}
        destroyOnHidden
        width={480}
        className="tag-config-modal"
      >
        <section className="tag-form-summary" role="region" aria-label="标签配置摘要">
          <span className="tag-form-summary__icon">
            <TagOutlined />
          </span>
          <span className="tag-form-summary__copy">
            <Typography.Text strong>{editingTag ? editingTag.name : "新建标签"}</Typography.Text>
            <Typography.Text type="secondary">
              {editingTag ? `${editingTag.usage_count} 次文件关联` : "待创建标签"}
            </Typography.Text>
          </span>
          <StatusTag
            kind="dataset"
            value={editingTag?.enabled === false ? "disabled" : "enabled"}
            variant="dot"
          />
        </section>

        <Form<TagFormValues>
          form={tagForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => tagMutation.mutate(values)}
        >
          <Form.Item
            label="标签名称"
            name="name"
            rules={[{ required: true, message: "请输入标签名称" }]}
          >
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      {/* Merge Modal */}
      <Modal
        title={`合并标签：${mergingSourceTag?.name ?? ""}`}
        open={mergeModalOpen}
        onCancel={() => {
          setMergeModalOpen(false);
          setMergingSourceTag(null);
        }}
        okText="确认合并"
        cancelText="取消"
        onOk={() => mergeForm.submit()}
        confirmLoading={mergeMutation.isPending}
        destroyOnHidden
        width={480}
        className="tag-merge-modal"
      >
        {mergingSourceTag && (
          <section className="tag-merge-summary" role="region" aria-label="标签合并摘要">
            <span className="tag-merge-summary__icon">
              <MergeOutlined />
            </span>
            <span className="tag-merge-summary__copy">
              <Typography.Text strong>{mergingSourceTag.name}</Typography.Text>
              <Typography.Text type="secondary">
                {mergingSourceTag.usage_count} 个文件关联将迁移到目标标签，源标签会被删除。
              </Typography.Text>
            </span>
            <StatusTag kind="health" value="unknown" variant="dot" />
          </section>
        )}
        <Form<MergeFormValues>
          form={mergeForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => {
            if (!mergingSourceTag) return;
            mergeMutation.mutate({
              sourceId: mergingSourceTag.id,
              payload: { target_tag_id: values.target_tag_id },
            });
          }}
        >
          <Form.Item
            label="目标标签"
            name="target_tag_id"
            rules={[{ required: true, message: "请选择目标标签" }]}
          >
            <Select
              options={mergeTargetOptions}
              showSearch
              optionFilterProp="label"
              placeholder="请选择合并目标标签"
            />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
