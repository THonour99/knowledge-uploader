import { useMemo, useState } from "react";
import { App as AntdApp, Button, Card, Form, Input, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CheckCircleOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  TeamOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";

import {
  type Department,
  type DepartmentListQuery,
  createDepartment,
  disableDepartment,
  listDepartments,
  updateDepartment,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import {
  SessionBoundModal as Modal,
  SessionBoundPopconfirm as Popconfirm,
} from "../../components/SessionBoundActions";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import "./styles.css";

const UNASSIGNED_DEPARTMENT_ID = "00000000-0000-0000-0000-000000000001";
const UNASSIGNED_DEPARTMENT_CODE = "unassigned";

interface DepartmentFormValues {
  name: string;
  code?: string;
  status?: Department["status"];
}

function isUnassignedDepartment(department: Department): boolean {
  return (
    department.id === UNASSIGNED_DEPARTMENT_ID || department.code === UNASSIGNED_DEPARTMENT_CODE
  );
}

export default function DepartmentsPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<DepartmentFormValues>();

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<Department["status"] | undefined>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [editingDepartment, setEditingDepartment] = useState<Department | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const queryParams: DepartmentListQuery = {
    page,
    page_size: pageSize,
    search: search.trim() || undefined,
    status: statusFilter,
  };

  const departmentsQuery = useQuery({
    queryKey: ["admin-departments", queryParams],
    queryFn: () => listDepartments(queryParams),
  });

  const departments = departmentsQuery.data?.items ?? [];
  const total = departmentsQuery.data?.total ?? 0;
  const activeCount = departments.filter((department) => department.status === "active").length;
  const disabledCount = departments.filter((department) => department.status === "disabled").length;

  const refreshDepartments = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin-departments"] });
  };

  const saveMutation = useMutation({
    mutationFn: (values: DepartmentFormValues) => {
      if (editingDepartment) {
        return updateDepartment(editingDepartment.id, {
          name: values.name.trim(),
          status: values.status,
        });
      }
      return createDepartment({
        name: values.name.trim(),
        code: values.code?.trim() ?? "",
      });
    },
    onSuccess: async () => {
      message.success(editingDepartment ? "部门已更新" : "部门已创建");
      setModalOpen(false);
      setEditingDepartment(null);
      form.resetFields();
      await refreshDepartments();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  const disableMutation = useMutation({
    mutationFn: (departmentId: string) => disableDepartment(departmentId),
    onSuccess: async () => {
      message.success("部门已停用");
      await refreshDepartments();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  const restoreMutation = useMutation({
    mutationFn: (departmentId: string) => updateDepartment(departmentId, { status: "active" }),
    onSuccess: async () => {
      message.success("部门已恢复启用");
      await refreshDepartments();
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
  });

  const openCreateModal = () => {
    setEditingDepartment(null);
    form.setFieldsValue({ name: "", code: "", status: "active" });
    setModalOpen(true);
  };

  const openEditModal = (department: Department) => {
    setEditingDepartment(department);
    form.setFieldsValue({
      name: department.name,
      code: department.code,
      status: department.status,
    });
    setModalOpen(true);
  };

  const submitSearch = () => {
    setPage(1);
    setSearch(searchInput);
  };

  const resetFilters = () => {
    setSearchInput("");
    setSearch("");
    setStatusFilter(undefined);
    setPage(1);
  };

  const columns = useMemo<ColumnsType<Department>>(
    () => [
      {
        title: "部门",
        dataIndex: "name",
        key: "name",
        render: (_value, record) => (
          <Space size={8}>
            <TeamOutlined className="departments-name-icon" />
            <span className="departments-name-cell">
              <Typography.Text strong>{record.name}</Typography.Text>
              {isUnassignedDepartment(record) ? (
                <Typography.Text type="secondary">未分配保护</Typography.Text>
              ) : null}
            </span>
          </Space>
        ),
      },
      {
        title: "编码",
        dataIndex: "code",
        key: "code",
        width: 180,
        render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        width: 120,
        render: (value: string) => <StatusTag kind="user" value={value} />,
      },
      {
        title: "更新时间",
        dataIndex: "updated_at",
        key: "updated_at",
        width: 180,
        render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm"),
      },
      {
        title: "操作",
        key: "actions",
        width: 220,
        render: (_, record) => {
          const unassigned = isUnassignedDepartment(record);
          return (
            <Space size={8}>
              <Button
                icon={<EditOutlined />}
                disabled={unassigned}
                onClick={() => openEditModal(record)}
              >
                编辑
              </Button>
              {record.status === "active" ? (
                <Popconfirm
                  title="停用部门"
                  description="停用后不能再分配给用户或管辖范围。"
                  okText="停用"
                  cancelText="取消"
                  disabled={unassigned}
                  onConfirm={() => disableMutation.mutate(record.id)}
                >
                  <Button
                    icon={<StopOutlined />}
                    danger
                    disabled={unassigned}
                    loading={disableMutation.isPending}
                  >
                    停用
                  </Button>
                </Popconfirm>
              ) : (
                <Button
                  icon={<UndoOutlined />}
                  disabled={unassigned}
                  loading={restoreMutation.isPending}
                  onClick={() => restoreMutation.mutate(record.id)}
                >
                  恢复
                </Button>
              )}
            </Space>
          );
        },
      },
    ],
    [disableMutation, restoreMutation],
  );

  return (
    <PageContainer title="部门管理" description="维护组织部门、状态和用户归属的基础数据。">
      <div className="departments-kpi-grid">
        <KpiCard
          icon={<TeamOutlined />}
          title="部门总数"
          value={total}
          tone="primary"
          description="当前筛选范围"
        />
        <KpiCard
          icon={<CheckCircleOutlined />}
          title="启用部门"
          value={activeCount}
          tone="success"
          description="可分配给用户"
        />
        <KpiCard
          icon={<StopOutlined />}
          title="停用部门"
          value={disabledCount}
          tone="warning"
          description="暂停分配"
        />
      </div>

      <Card className="departments-panel">
        <div className="departments-toolbar">
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索部门名称或编码"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              onSearch={submitSearch}
              className="departments-search"
            />
            <Select
              allowClear
              placeholder="状态"
              value={statusFilter}
              onChange={(value) => {
                setStatusFilter(value);
                setPage(1);
              }}
              options={[
                { label: "启用", value: "active" },
                { label: "停用", value: "disabled" },
              ]}
              className="departments-status-filter"
            />
            <Button onClick={resetFilters}>重置</Button>
            <Button
              icon={<ReloadOutlined />}
              title="刷新"
              loading={departmentsQuery.isFetching}
              onClick={() => void refreshDepartments()}
            />
          </Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
            新建部门
          </Button>
        </div>

        <Table<Department>
          rowKey="id"
          columns={columns}
          dataSource={departments}
          loading={departmentsQuery.isLoading}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
          }}
          onChange={(pagination) => {
            setPage(pagination.current ?? 1);
            setPageSize(pagination.pageSize ?? pageSize);
          }}
        />
      </Card>

      <Modal
        title={editingDepartment ? "编辑部门" : "新建部门"}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setEditingDepartment(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        okText={editingDepartment ? "保存" : "创建"}
        cancelText="取消"
      >
        <Form<DepartmentFormValues>
          form={form}
          layout="vertical"
          className="departments-form"
          onFinish={(values) => saveMutation.mutate(values)}
        >
          <Form.Item
            label="部门名称"
            name="name"
            rules={[{ required: true, message: "请输入部门名称" }]}
          >
            <Input placeholder="例如：技术部" maxLength={100} />
          </Form.Item>
          <Form.Item
            label="部门编码"
            name="code"
            rules={[
              { required: !editingDepartment, message: "请输入部门编码" },
              { pattern: /^[A-Za-z0-9_-]+$/, message: "仅支持字母、数字、下划线和短横线" },
            ]}
          >
            <Input
              placeholder="例如：engineering"
              disabled={Boolean(editingDepartment)}
              maxLength={50}
            />
          </Form.Item>
          {editingDepartment ? (
            <Form.Item label="状态" name="status">
              <Select
                options={[
                  { label: "启用", value: "active" },
                  { label: "停用", value: "disabled" },
                ]}
              />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>
    </PageContainer>
  );
}
