import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApartmentOutlined,
  ExclamationCircleOutlined,
  FilterOutlined,
  LockOutlined,
  MailOutlined,
  SearchOutlined,
  SafetyCertificateOutlined,
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
  type Department,
  changeUserRole,
  disableUser,
  enableUser,
  getManagedDepartments,
  listAdminUsers,
  listDepartments,
  replaceManagedDepartments,
  resetUserPassword,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const roleLabels: Record<AdminUserRole, string> = {
  system_admin: "系统管理员",
  dept_admin: "部门管理员",
  employee: "普通员工",
};

const roleOptions = [
  { label: "角色：全部", value: "" },
  { label: "系统管理员", value: "system_admin" },
  { label: "部门管理员", value: "dept_admin" },
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
  { label: "部门管理员", value: "dept_admin" },
  { label: "普通员工", value: "employee" },
];

const roleDistribution = [
  { label: "普通员工", percent: 82 },
  { label: "部门管理员", percent: 12 },
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

interface ManagedDepartmentsModalState {
  open: boolean;
  user: AdminUserItem | null;
  selectedDepartmentIds: string[];
}

interface UserGovernanceStripProps {
  activeCount: number;
  deptAdminCount: number;
  disabledOrLockedCount: number;
  managedDeptAdminCount: number;
  pageCount: number;
  pendingCount: number;
  roleFilter: AdminUserRole | "";
  search: string;
  statusFilter: string;
  total: number;
  verifiedCount: number;
}

function UserGovernanceStrip({
  activeCount,
  deptAdminCount,
  disabledOrLockedCount,
  managedDeptAdminCount,
  pageCount,
  pendingCount,
  roleFilter,
  search,
  statusFilter,
  total,
  verifiedCount,
}: UserGovernanceStripProps) {
  const hasFilters = Boolean(search.trim() || roleFilter || statusFilter);
  const lanes = [
    {
      key: "health",
      icon: <SafetyCertificateOutlined />,
      title: "账号健康",
      primary: `${activeCount} 个正常账号`,
      secondary: `当前视图 ${pageCount} 个账号，平台共 ${total} 条`,
      status: { kind: "health" as const, value: disabledOrLockedCount > 0 ? "unknown" : "ok" },
    },
    {
      key: "activation",
      icon: <MailOutlined />,
      title: "邮箱激活",
      primary: `${pendingCount} 个待激活`,
      secondary: `邮箱验证完成 ${verifiedCount}/${pageCount}`,
      status: {
        kind: "user" as const,
        value: pendingCount > 0 ? "pending_email_verification" : "active",
      },
    },
    {
      key: "permission",
      icon: <ApartmentOutlined />,
      title: "权限覆盖",
      primary: `${deptAdminCount} 个部门管理员`,
      secondary: `${managedDeptAdminCount} 个已配置管辖部门`,
      status: {
        kind: "health" as const,
        value: deptAdminCount === managedDeptAdminCount ? "ok" : "unknown",
      },
    },
    {
      key: "queue",
      icon: <FilterOutlined />,
      title: "治理队列",
      primary: `${disabledOrLockedCount} 个禁用/锁定`,
      secondary: hasFilters ? "当前列表已应用筛选条件" : "当前列表未应用筛选条件",
      status: { kind: "user" as const, value: disabledOrLockedCount > 0 ? "disabled" : "active" },
    },
  ];

  return (
    <section className="users-governance-strip" role="region" aria-label="账号治理状态">
      <div className="users-governance-strip__main">
        <span className="users-governance-strip__icon">
          <TeamOutlined />
        </span>
        <span className="users-governance-strip__copy">
          <Typography.Text strong className="users-governance-strip__title">
            账号治理状态
          </Typography.Text>
          <Typography.Text type="secondary">
            汇总当前列表的账号健康、激活进度、权限覆盖和待处理队列。
          </Typography.Text>
        </span>
        <span className="users-governance-strip__total">
          <strong>{total}</strong>
          <Typography.Text type="secondary">平台账号</Typography.Text>
        </span>
      </div>
      <div className="users-governance-strip__lanes" aria-label="账号治理指标">
        {lanes.map((lane) => (
          <div className="users-governance-lane" key={lane.key}>
            <span className="users-governance-lane__icon">{lane.icon}</span>
            <span className="users-governance-lane__body">
              <span className="users-governance-lane__topline">
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
  const [managedDepartmentsModal, setManagedDepartmentsModal] =
    useState<ManagedDepartmentsModalState>({
      open: false,
      user: null,
      selectedDepartmentIds: [],
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

  const pageStats = useMemo(() => {
    const deptAdmins = users.filter((user) => user.role === "dept_admin");

    return {
      active: users.filter((user) => user.status === "active").length,
      pending: users.filter((user) => user.status === "pending_email_verification").length,
      disabledOrLocked: users.filter(
        (user) => user.status === "disabled" || user.status === "locked",
      ).length,
      verified: users.filter((user) => user.email_verified).length,
      deptAdmin: deptAdmins.length,
      managedDeptAdmin: deptAdmins.filter((user) => (user.managed_department_ids?.length ?? 0) > 0)
        .length,
    };
  }, [users]);
  const departmentsQuery = useQuery({
    queryKey: ["admin-departments"],
    queryFn: listDepartments,
    enabled: managedDepartmentsModal.open,
  });
  const managedDepartmentsQuery = useQuery({
    queryKey: ["admin-users", managedDepartmentsModal.user?.id, "managed-departments"],
    queryFn: () => getManagedDepartments(managedDepartmentsModal.user?.id ?? ""),
    enabled: managedDepartmentsModal.open && Boolean(managedDepartmentsModal.user?.id),
  });
  const departmentOptions = useMemo(
    () =>
      (departmentsQuery.data?.items ?? [])
        .filter((department: Department) => department.status === "active")
        .map((department: Department) => ({
          label: `${department.name} (${department.code})`,
          value: department.id,
        })),
    [departmentsQuery.data?.items],
  );
  const loadedManagedDepartmentIds = useMemo(
    () =>
      managedDepartmentsQuery.data?.managed_department_ids ??
      managedDepartmentsQuery.data?.managed_departments?.map((department) => department.id) ??
      managedDepartmentsQuery.data?.departments?.map((department) => department.id) ??
      [],
    [managedDepartmentsQuery.data],
  );

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["admin-users", queryParams] });
  }, [queryClient, queryParams]);

  useEffect(() => {
    if (!managedDepartmentsModal.open || !managedDepartmentsQuery.isSuccess) {
      return;
    }

    setManagedDepartmentsModal((prev) => ({
      ...prev,
      selectedDepartmentIds: loadedManagedDepartmentIds,
    }));
  }, [loadedManagedDepartmentIds, managedDepartmentsModal.open, managedDepartmentsQuery.isSuccess]);

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

  const openManagedDepartmentsModal = useCallback((record: AdminUserItem) => {
    setManagedDepartmentsModal({
      open: true,
      user: record,
      selectedDepartmentIds: record.managed_department_ids ?? [],
    });
  }, []);

  const handleManagedDepartmentsChange = useCallback((departmentIds: string[]) => {
    setManagedDepartmentsModal((prev) => ({ ...prev, selectedDepartmentIds: departmentIds }));
  }, []);

  const handleSaveManagedDepartments = useCallback(async () => {
    const userId = managedDepartmentsModal.user?.id;
    if (!userId) {
      return;
    }

    setActionLoading((prev) => ({ ...prev, [`managed-${userId}`]: true }));
    try {
      await replaceManagedDepartments(userId, managedDepartmentsModal.selectedDepartmentIds);
      message.success("管辖部门已更新");
      setManagedDepartmentsModal({ open: false, user: null, selectedDepartmentIds: [] });
      invalidate();
      void queryClient.invalidateQueries({ queryKey: ["admin-users", userId, "managed-departments"] });
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActionLoading((prev) => ({ ...prev, [`managed-${userId}`]: false }));
    }
  }, [invalidate, managedDepartmentsModal, message, queryClient]);

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
      render: (value: string | null, record) => record.department_name ?? value ?? "-",
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
      width: 310,
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
            {record.role === "dept_admin" ? (
              <Button
                type="text"
                size="small"
                icon={<ApartmentOutlined />}
                loading={actionLoading[`managed-${record.id}`]}
                onClick={() => openManagedDepartmentsModal(record)}
                aria-label="管辖部门"
              >
                管辖部门
              </Button>
            ) : null}
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
      <div className="users-kpi-grid">
        <KpiCard
          icon={<TeamOutlined />}
          title="用户总数"
          value={total}
          description="平台账号总量"
          tone="primary"
        />
        <KpiCard
          icon={<UserSwitchOutlined />}
          title="当前页活跃"
          value={pageStats.active}
          description="状态正常账号"
          tone="success"
        />
        <KpiCard
          icon={<MailOutlined />}
          title="当前页待激活"
          value={pageStats.pending}
          description="邮箱验证待完成"
          tone="warning"
        />
        <KpiCard
          icon={<LockOutlined />}
          title="当前页禁用/锁定"
          value={pageStats.disabledOrLocked}
          description="需要管理员处理"
          tone="danger"
        />
      </div>

      <UserGovernanceStrip
        activeCount={pageStats.active}
        deptAdminCount={pageStats.deptAdmin}
        disabledOrLockedCount={pageStats.disabledOrLocked}
        managedDeptAdminCount={pageStats.managedDeptAdmin}
        pageCount={users.length}
        pendingCount={pageStats.pending}
        roleFilter={roleFilter}
        search={search}
        statusFilter={statusFilter}
        total={total}
        verifiedCount={pageStats.verified}
      />

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
            scroll={{ x: 1080 }}
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
              系统管理员可配置组织、Dataset、AI 与系统参数；部门管理员只处理管辖部门的文件审核、同步和任务日志；普通员工仅能上传与查看本人文件。
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

      {/* Managed departments modal */}
      <Modal
        title="配置管辖部门"
        open={managedDepartmentsModal.open}
        onCancel={() =>
          setManagedDepartmentsModal({ open: false, user: null, selectedDepartmentIds: [] })
        }
        onOk={() => void handleSaveManagedDepartments()}
        confirmLoading={
          Boolean(managedDepartmentsModal.user?.id) &&
          actionLoading[`managed-${managedDepartmentsModal.user?.id}`]
        }
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            为 <Typography.Text strong>{managedDepartmentsModal.user?.name ?? "用户"}</Typography.Text>{" "}
            选择可管理的部门。未选择任何部门时，该部门管理员看不到部门范围内的文件和任务。
          </Typography.Text>
          <Select
            className="users-managed-departments-select"
            mode="multiple"
            showSearch
            optionFilterProp="label"
            placeholder="选择管辖部门"
            style={{ width: "100%" }}
            loading={departmentsQuery.isLoading || managedDepartmentsQuery.isLoading}
            value={managedDepartmentsModal.selectedDepartmentIds}
            options={departmentOptions}
            onChange={handleManagedDepartmentsChange}
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
