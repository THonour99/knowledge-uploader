import { useDeferredValue, useMemo, useState } from "react";
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
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
  BarChartOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";

import {
  type AnnouncementAudience,
  type AnnouncementAdminDetail,
  type AnnouncementPayload,
  type AnnouncementRole,
  type AnnouncementState,
  cloneAnnouncement,
  createAnnouncement,
  deleteAnnouncement,
  getAnnouncementStats,
  listAdminAnnouncements,
  publishAnnouncement,
  updateAnnouncement,
  withdrawAnnouncement,
} from "../../api/announcements";
import { listDepartments } from "../../api/client";
import { MarkdownContent } from "../../components/MarkdownContent";
import { SessionBoundPopconfirm as Popconfirm } from "../../components/SessionBoundActions";
import { StatusTag } from "../../components/StatusTag";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

interface AnnouncementFormValues {
  title: string;
  body_markdown: string;
  audience_type: AnnouncementAudience;
  department_ids?: string[];
  roles?: AnnouncementRole[];
  visible_from?: Dayjs | null;
  expires_at?: Dayjs | null;
  is_pinned: boolean;
}

const roleOptions = [
  { label: "员工", value: "employee" },
  { label: "部门管理员", value: "dept_admin" },
  { label: "系统管理员", value: "system_admin" },
];

function toPayload(values: AnnouncementFormValues): AnnouncementPayload {
  return {
    title: values.title.trim(),
    body_markdown: values.body_markdown.trim(),
    audience_type: values.audience_type,
    department_ids: values.audience_type === "departments" ? (values.department_ids ?? []) : [],
    roles: values.audience_type === "roles" ? (values.roles ?? []) : [],
    visible_from: values.visible_from?.toISOString() ?? null,
    expires_at: values.expires_at?.toISOString() ?? null,
    is_pinned: values.is_pinned,
  };
}

export default function AnnouncementManagementPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<AnnouncementFormValues>();
  const [state, setState] = useState<AnnouncementState | "all">("all");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<AnnouncementAdminDetail | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorTab, setEditorTab] = useState("edit");
  const [statsFor, setStatsFor] = useState<AnnouncementAdminDetail | null>(null);
  const [departmentSearch, setDepartmentSearch] = useState("");
  const deferredDepartmentSearch = useDeferredValue(departmentSearch);

  const listQuery = useQuery({
    queryKey: ["admin-announcements", state, search, page],
    queryFn: () =>
      listAdminAnnouncements({ state, search: search || undefined, page, page_size: 20 }),
  });
  const departmentsQuery = useQuery({
    queryKey: ["admin-departments", "announcement-targets", deferredDepartmentSearch],
    queryFn: () =>
      listDepartments({
        status: "active",
        page: 1,
        page_size: 100,
        search: deferredDepartmentSearch || undefined,
      }),
    staleTime: 30_000,
  });
  const statsQuery = useQuery({
    queryKey: ["admin-announcements", "stats", statsFor?.id],
    queryFn: () => getAnnouncementStats(statsFor?.id ?? ""),
    enabled: Boolean(statsFor),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin-announcements"] });
  const saveMutation = useMutation({
    mutationFn: async (values: AnnouncementFormValues) => {
      const payload = toPayload(values);
      return editing
        ? updateAnnouncement(editing.id, { ...payload, row_version: editing.row_version })
        : createAnnouncement(payload);
    },
    onSuccess: async () => {
      message.success(editing ? "草稿已更新" : "草稿已创建");
      setEditorOpen(false);
      await refresh();
    },
    onError: (error: Error) => message.error(error.message),
  });
  const publishMutation = useMutation({
    mutationFn: (item: AnnouncementAdminDetail) =>
      publishAnnouncement(item.id, { row_version: item.row_version }),
    onSuccess: async () => {
      message.success("公告已发布");
      await refresh();
    },
    onError: (error: Error) => message.error(error.message),
  });
  const withdrawMutation = useMutation({
    mutationFn: ({ item, reason }: { item: AnnouncementAdminDetail; reason: string }) =>
      withdrawAnnouncement(item.id, { row_version: item.row_version, reason }),
    onSuccess: async () => {
      message.success("公告已撤回");
      await refresh();
    },
    onError: (error: Error) => message.error(error.message),
  });
  const cloneMutation = useMutation({
    mutationFn: (item: AnnouncementAdminDetail) => cloneAnnouncement(item.id, item.row_version),
    onSuccess: async () => {
      message.success("已复制为新草稿");
      await refresh();
    },
    onError: (error: Error) => message.error(error.message),
  });
  const deleteMutation = useMutation({
    mutationFn: (item: AnnouncementAdminDetail) => deleteAnnouncement(item.id, item.row_version),
    onSuccess: async () => {
      message.success("草稿已删除");
      await refresh();
    },
    onError: (error: Error) => message.error(error.message),
  });

  const openEditor = (item?: AnnouncementAdminDetail) => {
    setEditing(item ?? null);
    setDepartmentSearch("");
    setEditorTab("edit");
    form.setFieldsValue(
      item
        ? {
            title: item.title,
            body_markdown: item.body_markdown,
            audience_type: item.audience_type,
            department_ids: item.department_ids,
            roles: item.roles,
            visible_from: item.visible_from ? dayjs(item.visible_from) : null,
            expires_at: item.expires_at ? dayjs(item.expires_at) : null,
            is_pinned: item.is_pinned,
          }
        : {
            title: "",
            body_markdown: "",
            audience_type: "all",
            department_ids: [],
            roles: [],
            visible_from: null,
            expires_at: null,
            is_pinned: false,
          },
    );
    setEditorOpen(true);
  };

  const confirmWithdraw = (item: AnnouncementAdminDetail) => {
    let reason = "";
    Modal.confirm({
      title: "撤回公告",
      content: (
        <Input.TextArea
          autoFocus
          maxLength={500}
          showCount
          placeholder="请输入撤回原因"
          onChange={(event) => {
            reason = event.target.value;
          }}
        />
      ),
      okText: "确认撤回",
      okButtonProps: { danger: true },
      onOk: () => {
        if (!reason.trim()) {
          message.warning("请输入撤回原因");
          return Promise.reject(new Error("reason required"));
        }
        withdrawMutation.mutate({ item, reason: reason.trim() });
      },
    });
  };

  const columns = useMemo<ColumnsType<AnnouncementAdminDetail>>(
    () => [
      {
        title: "标题",
        dataIndex: "title",
        render: (value, item) => (
          <Space>
            <Typography.Text strong>{value}</Typography.Text>
            {item.is_pinned ? <Typography.Text type="success">置顶</Typography.Text> : null}
          </Space>
        ),
      },
      {
        title: "状态",
        dataIndex: "state",
        width: 110,
        render: (value) => <StatusTag kind="announcement" value={value} />,
      },
      {
        title: "受众",
        dataIndex: "audience_type",
        width: 120,
        render: (value) =>
          ({ all: "全员", departments: "指定部门", roles: "指定角色" })[
            value as AnnouncementAudience
          ],
      },
      {
        title: "生效时间",
        dataIndex: "visible_from",
        width: 170,
        render: (value) => (value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "发布时立即生效"),
      },
      {
        title: "更新时间",
        dataIndex: "updated_at",
        width: 170,
        render: (value) => dayjs(value).format("YYYY-MM-DD HH:mm"),
      },
      {
        title: "操作",
        key: "actions",
        width: 360,
        fixed: "right",
        render: (_, item) => (
          <Space size={4} wrap>
            {item.state === "draft" ? (
              <Button size="small" icon={<EditOutlined />} onClick={() => openEditor(item)}>
                编辑
              </Button>
            ) : null}
            {item.state === "draft" ? (
              <Popconfirm
                title="确认发布此公告？"
                description={
                  item.visible_from && dayjs(item.visible_from).isAfter(dayjs())
                    ? "将按设定时间生效。"
                    : "将立即对目标用户生效。"
                }
                onConfirm={() => publishMutation.mutate(item)}
              >
                <Button size="small" type="primary" icon={<SendOutlined />}>
                  发布
                </Button>
              </Popconfirm>
            ) : null}
            {["scheduled", "published", "expired"].includes(item.state) ? (
              <Button
                size="small"
                danger
                icon={<StopOutlined />}
                onClick={() => confirmWithdraw(item)}
              >
                撤回
              </Button>
            ) : null}
            <Button
              size="small"
              icon={<CopyOutlined />}
              loading={cloneMutation.isPending && cloneMutation.variables?.id === item.id}
              disabled={cloneMutation.isPending}
              onClick={() => cloneMutation.mutate(item)}
            >
              复制
            </Button>
            {item.state !== "draft" ? (
              <Button size="small" icon={<BarChartOutlined />} onClick={() => setStatsFor(item)}>
                阅读统计
              </Button>
            ) : null}
            {item.state === "draft" ? (
              <Popconfirm
                title="删除草稿？"
                description="删除后无法恢复。"
                onConfirm={() => deleteMutation.mutate(item)}
              >
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            ) : null}
          </Space>
        ),
      },
    ],
    [cloneMutation, deleteMutation, publishMutation],
  );

  const audienceType = Form.useWatch("audience_type", form);
  const markdownPreview = Form.useWatch("body_markdown", form) ?? "";
  return (
    <PageContainer
      title="公告管理"
      description="创建站内公告，按当前部门或角色动态触达用户。"
      actions={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>
          新建公告
        </Button>
      }
    >
      <Card>
        <div className="announcement-admin-toolbar">
          <Select
            value={state}
            style={{ width: 150 }}
            options={[
              { label: "全部状态", value: "all" },
              { label: "草稿", value: "draft" },
              { label: "待发布", value: "scheduled" },
              { label: "已发布", value: "published" },
              { label: "已到期", value: "expired" },
              { label: "已撤回", value: "withdrawn" },
            ]}
            onChange={(value) => {
              setState(value);
              setPage(1);
            }}
          />
          <Input.Search
            placeholder="搜索公告标题"
            allowClear
            onSearch={(value) => {
              setSearch(value.trim());
              setPage(1);
            }}
          />
        </div>
        {listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message="公告列表加载失败"
            description="请检查网络或权限后重试。"
            action={<Button onClick={() => void listQuery.refetch()}>重新加载</Button>}
          />
        ) : null}
        <Table
          rowKey="id"
          loading={listQuery.isPending}
          columns={columns}
          dataSource={listQuery.data?.items ?? []}
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize: 20,
            total: listQuery.data?.total ?? 0,
            onChange: setPage,
            showSizeChanger: false,
          }}
        />
      </Card>

      <Modal
        title={editing ? "编辑公告草稿" : "新建公告草稿"}
        width={1000}
        open={editorOpen}
        okText="保存草稿"
        confirmLoading={saveMutation.isPending}
        onCancel={() => setEditorOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
          <Form.Item name="title" label="公告标题" rules={[{ required: true }, { max: 200 }]}>
            <Input showCount maxLength={200} />
          </Form.Item>
          <Tabs
            activeKey={editorTab}
            onChange={setEditorTab}
            items={[
              {
                key: "edit",
                label: "编辑 Markdown",
                children: (
                  <Form.Item name="body_markdown" rules={[{ required: true }, { max: 50000 }]}>
                    <Input.TextArea
                      className="announcement-markdown-editor"
                      showCount
                      maxLength={50000}
                      placeholder="支持标题、列表、表格、引用、任务列表、链接和代码块；不支持 HTML 与图片。"
                    />
                  </Form.Item>
                ),
              },
              {
                key: "preview",
                label: "预览",
                children: (
                  <div className="announcement-preview">
                    <MarkdownContent>{markdownPreview || "暂无内容"}</MarkdownContent>
                  </div>
                ),
              },
            ]}
          />
          <div className="announcement-form-grid">
            <Form.Item name="audience_type" label="受众范围" rules={[{ required: true }]}>
              <Select
                options={[
                  { label: "全员", value: "all" },
                  { label: "指定部门", value: "departments" },
                  { label: "指定角色", value: "roles" },
                ]}
              />
            </Form.Item>
            {audienceType === "departments" ? (
              <Form.Item
                name="department_ids"
                label="目标部门"
                rules={[{ required: true, type: "array", min: 1 }]}
                validateStatus={departmentsQuery.isError ? "error" : undefined}
                help={
                  departmentsQuery.isError ? "部门列表加载失败，请重新搜索或稍后重试" : undefined
                }
              >
                <Select
                  mode="multiple"
                  showSearch
                  filterOption={false}
                  loading={departmentsQuery.isFetching}
                  onSearch={setDepartmentSearch}
                  onClear={() => setDepartmentSearch("")}
                  notFoundContent={departmentsQuery.isError ? "部门加载失败" : "未找到匹配部门"}
                  options={(departmentsQuery.data?.items ?? []).map((item) => ({
                    label: `${item.name} (${item.code})`,
                    value: item.id,
                  }))}
                  allowClear
                />
              </Form.Item>
            ) : null}
            {audienceType === "roles" ? (
              <Form.Item
                name="roles"
                label="目标角色"
                rules={[{ required: true, type: "array", min: 1 }]}
              >
                <Select mode="multiple" options={roleOptions} />
              </Form.Item>
            ) : null}
            <Form.Item name="visible_from" label="生效时间">
              <DatePicker showTime style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="expires_at" label="到期时间">
              <DatePicker showTime style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="is_pinned" label="全局置顶条" valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>
        </Form>
      </Modal>

      <Modal
        title="阅读统计"
        open={Boolean(statsFor)}
        footer={null}
        onCancel={() => setStatsFor(null)}
      >
        {statsQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message="阅读统计加载失败"
            action={<Button onClick={() => void statsQuery.refetch()}>重新加载</Button>}
          />
        ) : statsQuery.data ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Typography.Text strong>{statsFor?.title}</Typography.Text>
            <Progress percent={Math.round(statsQuery.data.read_rate * 100)} />
            <Typography.Text>
              目标用户 {statsQuery.data.target_user_count} 人 · 已读{" "}
              {statsQuery.data.read_user_count} 人 · 未读 {statsQuery.data.unread_user_count} 人
            </Typography.Text>
            <Typography.Text type="secondary">
              统计按当前仍有效的受众身份动态计算，不展示人员明细。
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="secondary">正在加载统计...</Typography.Text>
        )}
      </Modal>
    </PageContainer>
  );
}
