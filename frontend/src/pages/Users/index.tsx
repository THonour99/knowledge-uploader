import { useCallback, useState } from "react";
import {
  ExclamationCircleOutlined,
  LockOutlined,
  MailOutlined,
  SearchOutlined,
  TeamOutlined,
  UnlockOutlined,
  UserSwitchOutlined,
} from "@ant-design/icons";
import {
  App,
  Avatar,
  Button,
  Card,
  Input,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";

import {
  type AdminUserItem,
  type AdminUserListQuery,
  type AdminUserRole,
  changeUserRole,
  disableUser,
  enableUser,
  listAdminUsers,
  resetUserPassword,
} from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const roleLabels: Record<AdminUserRole, string> = {
  system_admin: "系统管理员",
  knowledge_admin: "知识管理员",
  employee: "普通员工",
};

const roleOptions = [
  { label: "角色：全部", value: "" },
  { label: "系统管理员", value: "system_admin" },
  { label: "知识管理员", value: "knowledge_admin" },
  { label: "普通员工", value: "employee" },
];

const statusOptions = [
  { label: "状态：全部", value: "" },
  { label: "正常", value: "active" },
  { label: "待激活", value: "pending_email_verification" },
  { label: "锁定中", value: "locked" },
  { label: "已禁用", value: "disabled" },
];

const changeRoleOptions = [
  { label: "系统管理员", value: "system_admin" },
  { label: "知识管理员", value: "knowledge_admin" },
  { label: "普通员工", value: "employee" },
];

const roleDistribution = [
  { label: "普通员工", percent: 82 },
  { label: "知识管理员", percent: 12 },
  { label: "系统管理员", percent: 6 },
];

interface RoleModalState {
  open: boolean;
  userId: string;
  currentRole: AdminUserRole;
  selectedRole: AdminUserRole;
}

interface ResetModalState {
  open: boolean;
  userId: string;
  userName: string;
}

export default function UsersPage() {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [roleFilter, setRoleFilter] = useState<AdminUserRole | "">("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);

  const [roleModal, setRoleModal] = useState<RoleModalState>({
    open: false,
    userId: "",
    currentRole: "employee",
    selectedRole: "employee",
  });
  const [resetModal, setResetModal] = useState<ResetModalState>({
    open: false,
    userId: "",
    userName: "",
  });
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});

  const queryParams: AdminUserListQuery = {
    page,
    page_size: pageSize,
    ...(search.trim() ? { search: search.trim() } : {}),
    ...(roleFilter ? { role: roleFilter } : {}),
    ...(statusFilter ? { status: statusFilter } : {}),
  };

  const usersQuery = useQuery({
    queryKey: ["admin-users", queryParams],
    queryFn: () => listAdminUsers(queryParams),
  });

  const users = usersQuery.data?.items ?? [];
  const total = usersQuery.data?.total ?? 0;

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["admin-users", queryParams] });
  }, [queryClient, queryParams]);

  const handleSearch = useCallback((value: string) => {
    setSearch(value);
    setPage(1);
  }, []);

  const handleRoleFilterChange = useCallback((value: string) => {
    setRoleFilter(value as AdminUserRole | "");
    setPage(1);
  }, []);

  const handleStatusFilterChange = useCallback((value: string) => {
    setStatusFilter(value);
    setPage(1);
  }, []);

  const handleDisable = useCallback(
    (record: AdminUserItem) => {
      modal.confirm({
        title: "禁用用户",
        icon: <ExclamationCircleOutlined />,
        content: `确定要禁用 ${record.name} 的账号吗？禁用后该用户将无法登录。`,
        okText: "确定",
        cancelText: "取消",
        onOk: async () => {
          setActionLoading((prev) => ({ ...prev, [`disable-${record.id}`]: true }));
          try {
            await disableUser(record.id);
            message.success("用户已禁用");
            invalidate();
          } catch (err) {
            message.error(err instanceof Error ? err.message : "操作失败");
          } finally {
            setActionLoading((prev) => ({ ...prev, [`disable-${record.id}`]: false }));
          }
        },
      });
    },
    [modal, message, invalidate],
  );

  const handleEnable = useCallback(
    (record: AdminUserItem) => {
      modal.confirm({
        title: "启用用户",
        icon: <ExclamationCircleOutlined />,
        content: `确定要启用 ${record.name} 的账号吗？`,
        okText: "确定",
        cancelText: "取消",
        onOk: async () => {
          setActionLoading((prev) => ({ ...prev, [`enable-${record.id}`]: true }));
          try {
            await enableUser(record.id);
            message.success("用户已启用");
            invalidate();
          } catch (err) {
            message.error(err instanceof Error ? err.message : "操作失败");
          } finally {
            setActionLoading((prev) => ({ ...prev, [`enable-${record.id}`]: false }));
          }
        },
      });
    },
    [modal, message, invalidate],
  );

  const openRoleModal = useCallback((record: AdminUserItem) => {
    setRoleModal({
      open: true,
      userId: record.id,
      currentRole: record.role,
      selectedRole: record.role,
    });
  }, []);

  const handleRoleChange = useCallback(async () => {
    setActionLoading((prev) => ({ ...prev, [`role-${roleModal.userId}`]: true }));
    try {
      await changeUserRole(roleModal.userId, roleModal.selectedRole);
      message.success("角色已变更");
      setRoleModal((prev) => ({ ...prev, open: false }));
      invalidate();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActionLoading((prev) => ({ ...prev, [`role-${roleModal.userId}`]: false }));
    }
  }, [roleModal, message, invalidate]);

  const openResetModal = useCallback((record: AdminUserItem) => {
    setResetModal({ open: true, userId: record.id, userName: record.name });
  }, []);

  const handleResetPassword = useCallback(async () => {
    setActionLoading((prev) => ({ ...prev, [`reset-${resetModal.userId}`]: true }));
    try {
      await resetUserPassword(resetModal.userId);
      message.success("重置邮件已发送");
      setResetModal((prev) => ({ ...prev, open: false }));
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActionLoading((prev) => ({ ...prev, [`reset-${resetModal.userId}`]: false }));
    }
  }, [resetModal, message]);

  const columns: ColumnsType<AdminUserItem> = [
    {
      title: "用户",
      dataIndex: "name",
      key: "name",
      width: 240,
      render: (value: string, record) => (
        <div className="users-user-cell">
          <Avatar className="users-user-cell__avatar">{value.slice(0, 1)}</Avatar>
          <span className="users-user-cell__copy">
            <Typography.Text strong ellipsis>
              {value}
            </Typography.Text>
            <Typography.Text type="secondary" ellipsis>
              {record.email}
            </Typography.Text>
          </span>
        </div>
      ),
    },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      width: 130,
      render: (value: AdminUserRole) => (
        <span className="users-role-pill">{roleLabels[value]}</span>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 110,
      render: (value: string) => <StatusTag kind="user" value={value} />,
    },
    {
      title: "部门",
      dataIndex: "department",
      key: "department",
      width: 140,
      render: (value: string | null) => value ?? "-",
    },
    {
      title: "上传统计",
      key: "upload_stats",
      width: 140,
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{record.upload_count}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {record.last_upload_at
              ? dayjs(record.last_upload_at).format("YYYY-MM-DD")
              : "从未上传"}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      fixed: "right" as const,
      render: (_, record) => {
        const isDisabled = record.status === "disabled";
        return (
          <Space size={4}>
            {isDisabled ? (
              <Button
                type="text"
                size="small"
                icon={<UnlockOutlined />}
                loading={actionLoading[`enable-${record.id}`]}
                onClick={() => handleEnable(record)}
                aria-label="启用"
              >
                启用
              </Button>
            ) : (
              <Button
                type="text"
                size="small"
                icon={<LockOutlined />}
                loading={actionLoading[`disable-${record.id}`]}
                onClick={() => handleDisable(record)}
                aria-label="禁用"
              >
                禁用
              </Button>
            )}
            <Button
              type="text"
              size="small"
              icon={<UserSwitchOutlined />}
              onClick={() => openRoleModal(record)}
              aria-label="改角色"
            >
              改角色
            </Button>
            <Button
              type="text"
              size="small"
              icon={<MailOutlined />}
              onClick={() => openResetModal(record)}
              aria-label="重置密码"
            >
              重置密码
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <PageContainer
      title="用户管理"
      description="管理员工账号、权限角色、邮箱验证与登录状态。"
    >
      <div className="users-main-grid">
        <Card className="users-panel table-card" title="账号列表">
          <div className="filter-toolbar filter-toolbar--management">
            <Input
              className="filter-toolbar__search"
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索姓名、邮箱或部门"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              onPressEnter={() => handleSearch(searchInput)}
              onBlur={() => handleSearch(searchInput)}
            />
            <Select
              className="filter-toolbar__control users-role-filter"
              aria-label="角色筛选"
              value={roleFilter || undefined}
              placeholder="角色：全部"
              options={roleOptions}
              onChange={handleRoleFilterChange}
              allowClear
            />
            <Select
              className="filter-toolbar__control"
              value={statusFilter || undefined}
              placeholder="状态：全部"
              options={statusOptions}
              onChange={handleStatusFilterChange}
              allowClear
            />
          </div>
          <Table<AdminUserItem>
            rowKey="id"
            columns={columns}
            dataSource={users}
            loading={usersQuery.isLoading}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: false,
              showTotal: (t) => `共 ${t} 条`,
              onChange: (p) => setPage(p),
            }}
            scroll={{ x: 980 }}
            locale={{ emptyText: "暂无用户数据" }}
          />
        </Card>

        <Card className="users-panel" title="角色分布">
          <div className="users-role-distribution">
            {roleDistribution.map((item) => (
              <div className="users-role-row" key={item.label}>
                <span className="users-role-row__header">
                  <Typography.Text strong>{item.label}</Typography.Text>
                </span>
                <Progress percent={item.percent} showInfo={false} />
              </div>
            ))}
          </div>

          <div className="users-permission-summary">
            <Typography.Text strong>权限策略</Typography.Text>
            <Typography.Text type="secondary">
              系统管理员可配置 Dataset、AI 与系统参数；知识管理员负责审核和同步；普通员工仅能上传与查看本人文件。
            </Typography.Text>
          </div>
        </Card>
      </div>

      {/* Change role modal */}
      <Modal
        title="变更用户角色"
        open={roleModal.open}
        onCancel={() => setRoleModal((prev) => ({ ...prev, open: false }))}
        onOk={() => void handleRoleChange()}
        confirmLoading={actionLoading[`role-${roleModal.userId}`]}
        okText="确定"
        cancelText="取消"
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            <ExclamationCircleOutlined style={{ marginRight: 6, color: "var(--ku-color-warning)" }} />
            角色变更将立即生效，请确认操作。
          </Typography.Text>
          <Select
            className="users-role-modal-select"
            style={{ width: "100%" }}
            value={roleModal.selectedRole}
            options={changeRoleOptions}
            onChange={(value: AdminUserRole) =>
              setRoleModal((prev) => ({ ...prev, selectedRole: value }))
            }
          />
        </Space>
      </Modal>

      {/* Reset password modal */}
      <Modal
        title="重置密码"
        open={resetModal.open}
        onCancel={() => setResetModal((prev) => ({ ...prev, open: false }))}
        onOk={() => void handleResetPassword()}
        confirmLoading={actionLoading[`reset-${resetModal.userId}`]}
        okText="确定"
        cancelText="取消"
        destroyOnClose
      >
        <Space>
          <TeamOutlined />
          <Typography.Text>
            确定要向 <Typography.Text strong>{resetModal.userName}</Typography.Text>{" "}
            发送密码重置邮件吗？该用户将收到重置链接。
          </Typography.Text>
        </Space>
      </Modal>
    </PageContainer>
  );
}
