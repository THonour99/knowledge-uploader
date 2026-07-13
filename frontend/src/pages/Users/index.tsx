import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApartmentOutlined,
  ExclamationCircleOutlined,
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
  setUserDepartment,
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
  { label: "锁定中", value: "locked" },
  { label: "已禁用", value: "disabled" },
];

const changeRoleOptions = [
  { label: "系统管理员", value: "system_admin" },
  { label: "部门管理员", value: "dept_admin" },
  { label: "普通员工", value: "employee" },
];

interface RoleModalState {
  open: boolean;
  userId: string;
  userName: string;
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

interface UserDepartmentModalState {
  open: boolean;
  user: AdminUserItem | null;
  selectedDepartmentId: string;
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
    userName: "",
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
  const [userDepartmentModal, setUserDepartmentModal] = useState<UserDepartmentModalState>({
    open: false,
    user: null,
    selectedDepartmentId: "",
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
      disabledOrLocked: users.filter(
        (user) => user.status === "disabled" || user.status === "locked",
      ).length,
      deptAdmin: deptAdmins.length,
      assignedDepartment: users.filter((user) => {
        const departmentName = user.department_name ?? user.department ?? "";
        return Boolean(departmentName) && departmentName !== "未分配";
      }).length,
    };
  }, [users]);

  const roleDistribution = useMemo(() => {
    const roleCounts = users.reduce<Record<AdminUserRole, number>>(
      (acc, user) => {
        acc[user.role] += 1;
        return acc;
      },
      { system_admin: 0, dept_admin: 0, employee: 0 },
    );

    return changeRoleOptions.map((option) => {
      const role = option.value as AdminUserRole;
      const count = roleCounts[role];
      const percent = users.length > 0 ? Math.round((count / users.length) * 100) : 0;

      return {
        label: roleLabels[role],
        count,
        percent,
      };
    });
  }, [users]);

  const departmentsQuery = useQuery({
    queryKey: ["admin-departments"],
    queryFn: () => listDepartments({ page_size: 200 }),
    enabled: managedDepartmentsModal.open || userDepartmentModal.open,
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
      userName: record.name,
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

  const openUserDepartmentModal = useCallback((record: AdminUserItem) => {
    setUserDepartmentModal({
      open: true,
      user: record,
      selectedDepartmentId: record.department_id ?? "",
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
      void queryClient.invalidateQueries({
        queryKey: ["admin-users", userId, "managed-departments"],
      });
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActionLoading((prev) => ({ ...prev, [`managed-${userId}`]: false }));
    }
  }, [invalidate, managedDepartmentsModal, message, queryClient]);

  const handleSaveUserDepartment = useCallback(async () => {
    const userId = userDepartmentModal.user?.id;
    if (!userId || !userDepartmentModal.selectedDepartmentId) {
      return;
    }

    setActionLoading((prev) => ({ ...prev, [`department-${userId}`]: true }));
    try {
      await setUserDepartment(userId, userDepartmentModal.selectedDepartmentId);
      message.success("所属部门已更新");
      setUserDepartmentModal({ open: false, user: null, selectedDepartmentId: "" });
      invalidate();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActionLoading((prev) => ({ ...prev, [`department-${userId}`]: false }));
    }
  }, [invalidate, message, userDepartmentModal]);

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
      render: (value: string | null, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{record.department_name ?? value ?? "-"}</Typography.Text>
          {record.department_code ? (
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {record.department_code}
            </Typography.Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: "上传统计",
      key: "upload_stats",
      width: 140,
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{record.upload_count}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {record.last_upload_at ? dayjs(record.last_upload_at).format("YYYY-MM-DD") : "从未上传"}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 390,
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
              icon={<ApartmentOutlined />}
              loading={actionLoading[`department-${record.id}`]}
              onClick={() => openUserDepartmentModal(record)}
              aria-label="所属部门"
            >
              所属部门
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
    <PageContainer title="用户管理" description="管理员工账号、权限角色、所属部门与登录状态。">
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
          icon={<ApartmentOutlined />}
          title="当前页已归属"
          value={pageStats.assignedDepartment}
          description="已分配部门"
          tone="info"
        />
        <KpiCard
          icon={<LockOutlined />}
          title="当前页禁用/锁定"
          value={pageStats.disabledOrLocked}
          description="需要管理员处理"
          tone="danger"
        />
      </div>

      <div className="users-main-grid">
        <Card className="users-panel table-card">
          <div className="table-section-header users-table-header">
            <span className="table-section-header__copy">
              <Typography.Title level={4} className="table-section-header__title">
                账号治理列表
              </Typography.Title>
              <Typography.Text className="table-section-header__meta">
                当前显示 {users.length} 个账号，共 {total} 条记录，{pageStats.disabledOrLocked}{" "}
                个需处理
              </Typography.Text>
            </span>
            <StatusTag kind="health" value={usersQuery.isError ? "error" : "ok"} variant="dot" />
          </div>

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
            scroll={{ x: 1160 }}
            locale={{ emptyText: "暂无用户数据" }}
          />
        </Card>

        <Card className="users-panel users-role-panel">
          <section className="users-role-panel__header" role="region" aria-label="角色权限概览">
            <span className="users-role-panel__icon">
              <SafetyCertificateOutlined />
            </span>
            <span className="users-role-panel__copy">
              <Typography.Text strong>角色权限概览</Typography.Text>
              <Typography.Text type="secondary">
                {`当前页 ${users.length} 个账号，${pageStats.deptAdmin} 个部门管理员`}
              </Typography.Text>
            </span>
            <StatusTag
              kind="health"
              value={pageStats.deptAdmin > 0 ? "ok" : "unknown"}
              variant="dot"
            />
          </section>

          <div className="users-role-distribution">
            {roleDistribution.map((item) => (
              <div className="users-role-row" key={item.label}>
                <span className="users-role-row__header">
                  <Typography.Text strong>{item.label}</Typography.Text>
                  <Typography.Text type="secondary">{item.count} 人</Typography.Text>
                </span>
                <Progress percent={item.percent} showInfo={false} />
              </div>
            ))}
          </div>

          <div className="users-permission-summary">
            <Typography.Text strong>权限策略</Typography.Text>
            <Typography.Text type="secondary">
              系统管理员可配置组织、Dataset、AI
              与系统参数；部门管理员只处理管辖部门的文件审核、同步和任务日志；普通员工仅能上传与查看本人文件。
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
        <div className="user-modal-stack">
          <section className="user-modal-summary" role="region" aria-label="角色变更摘要">
            <span className="user-modal-summary__icon">
              <UserSwitchOutlined />
            </span>
            <span className="user-modal-summary__copy">
              <Typography.Text strong>{roleModal.userName || "当前用户"}</Typography.Text>
              <Typography.Text type="secondary">
                {roleLabels[roleModal.currentRole]} 至 {roleLabels[roleModal.selectedRole]}
              </Typography.Text>
            </span>
            <span className="user-modal-summary__metric">
              <strong>权限</strong>
              <small>即时生效</small>
            </span>
          </section>
          <Typography.Text type="secondary">
            <ExclamationCircleOutlined
              style={{ marginRight: 6, color: "var(--ku-color-warning)" }}
            />
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
        </div>
      </Modal>

      {/* User department modal */}
      <Modal
        title="编辑所属部门"
        open={userDepartmentModal.open}
        onCancel={() =>
          setUserDepartmentModal({ open: false, user: null, selectedDepartmentId: "" })
        }
        onOk={() => void handleSaveUserDepartment()}
        confirmLoading={
          Boolean(userDepartmentModal.user?.id) &&
          actionLoading[`department-${userDepartmentModal.user?.id}`]
        }
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <div className="user-modal-stack">
          <section className="user-modal-summary" role="region" aria-label="所属部门摘要">
            <span className="user-modal-summary__icon">
              <ApartmentOutlined />
            </span>
            <span className="user-modal-summary__copy">
              <Typography.Text strong>
                {userDepartmentModal.user?.name ?? "当前用户"}
              </Typography.Text>
              <Typography.Text type="secondary">
                {userDepartmentModal.user?.department_name ??
                  userDepartmentModal.user?.department ??
                  "未分配"}
              </Typography.Text>
            </span>
            <span className="user-modal-summary__metric">
              <strong>归属</strong>
              <small>立即生效</small>
            </span>
          </section>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="选择所属部门"
            style={{ width: "100%" }}
            loading={departmentsQuery.isLoading}
            value={userDepartmentModal.selectedDepartmentId || undefined}
            options={departmentOptions}
            onChange={(departmentId: string) =>
              setUserDepartmentModal((prev) => ({
                ...prev,
                selectedDepartmentId: departmentId,
              }))
            }
          />
        </div>
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
        <div className="user-modal-stack">
          <section className="user-modal-summary" role="region" aria-label="部门管辖摘要">
            <span className="user-modal-summary__icon">
              <ApartmentOutlined />
            </span>
            <span className="user-modal-summary__copy">
              <Typography.Text strong>
                {managedDepartmentsModal.user?.name ?? "当前部门管理员"}
              </Typography.Text>
              <Typography.Text type="secondary">部门管理员可见范围</Typography.Text>
            </span>
            <span className="user-modal-summary__metric">
              <strong>{managedDepartmentsModal.selectedDepartmentIds.length}</strong>
              <small>管辖部门</small>
            </span>
          </section>
          <Typography.Text type="secondary">
            为{" "}
            <Typography.Text strong>{managedDepartmentsModal.user?.name ?? "用户"}</Typography.Text>{" "}
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
        </div>
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
        <div className="user-modal-stack">
          <section className="user-modal-summary" role="region" aria-label="密码重置摘要">
            <span className="user-modal-summary__icon">
              <MailOutlined />
            </span>
            <span className="user-modal-summary__copy">
              <Typography.Text strong>{resetModal.userName || "当前用户"}</Typography.Text>
              <Typography.Text type="secondary">发送一次性密码重置邮件</Typography.Text>
            </span>
            <span className="user-modal-summary__metric">
              <strong>邮箱</strong>
              <small>重置链接</small>
            </span>
          </section>
          <Typography.Text>
            确定要向 <Typography.Text strong>{resetModal.userName}</Typography.Text>{" "}
            发送密码重置邮件吗？该用户将收到重置链接。
          </Typography.Text>
        </div>
      </Modal>
    </PageContainer>
  );
}
