import { useMemo, useState, type ReactNode } from "react";
import {
  EditOutlined,
  ExportOutlined,
  LockOutlined,
  MailOutlined,
  PlusOutlined,
  SearchOutlined,
  TeamOutlined,
  UnlockOutlined,
  UserAddOutlined,
  UserSwitchOutlined,
} from "@ant-design/icons";
import { Avatar, Button, Card, Input, Progress, Select, Space, Table, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

interface UserRow {
  id: string;
  name: string;
  email: string;
  department: string;
  role: "system_admin" | "knowledge_admin" | "employee";
  status: "active" | "pending_email_verification" | "disabled" | "locked";
  uploads: number;
  reviews: number;
  lastActiveAt: string;
}

interface UserMetric {
  title: string;
  value: string;
  description: string;
  icon: ReactNode;
  tone: "primary" | "success" | "warning" | "purple";
}

const roleLabels: Record<UserRow["role"], string> = {
  system_admin: "系统管理员",
  knowledge_admin: "知识管理员",
  employee: "普通员工",
};

const userRows: UserRow[] = [
  {
    id: "u-001",
    name: "张维",
    email: "zhangwei@company.com",
    department: "产品运营部",
    role: "system_admin",
    status: "active",
    uploads: 84,
    reviews: 146,
    lastActiveAt: "2026-06-07 10:16",
  },
  {
    id: "u-002",
    name: "李雪",
    email: "lixue@company.com",
    department: "技术支持部",
    role: "knowledge_admin",
    status: "active",
    uploads: 126,
    reviews: 232,
    lastActiveAt: "2026-06-07 09:58",
  },
  {
    id: "u-003",
    name: "王明",
    email: "wangming@company.com",
    department: "研发中心",
    role: "employee",
    status: "active",
    uploads: 58,
    reviews: 0,
    lastActiveAt: "2026-06-06 18:42",
  },
  {
    id: "u-004",
    name: "陈晨",
    email: "chenchen@company.com",
    department: "市场品牌部",
    role: "employee",
    status: "pending_email_verification",
    uploads: 12,
    reviews: 0,
    lastActiveAt: "2026-06-05 14:23",
  },
  {
    id: "u-005",
    name: "赵琪",
    email: "zhaoqi@company.com",
    department: "人力资源部",
    role: "employee",
    status: "locked",
    uploads: 36,
    reviews: 0,
    lastActiveAt: "2026-06-04 17:08",
  },
  {
    id: "u-006",
    name: "周航",
    email: "zhouhang@company.com",
    department: "客户成功部",
    role: "knowledge_admin",
    status: "disabled",
    uploads: 48,
    reviews: 74,
    lastActiveAt: "2026-05-28 12:35",
  },
];

const userMetrics: UserMetric[] = [
  {
    title: "用户总数",
    value: "286",
    description: "较上月 +24",
    icon: <TeamOutlined />,
    tone: "primary",
  },
  {
    title: "活跃用户",
    value: "214",
    description: "近 30 天有登录",
    icon: <UserSwitchOutlined />,
    tone: "success",
  },
  {
    title: "待激活",
    value: "18",
    description: "等待邮箱验证",
    icon: <MailOutlined />,
    tone: "warning",
  },
  {
    title: "管理员",
    value: "12",
    description: "系统 / 知识管理员",
    icon: <UserAddOutlined />,
    tone: "purple",
  },
];

const roleDistribution = [
  { label: "普通员工", value: 236, percent: 82 },
  { label: "知识管理员", value: 10, percent: 12 },
  { label: "系统管理员", value: 2, percent: 6 },
];

function roleMatches(row: UserRow, roleFilter: string): boolean {
  return roleFilter === "all" || row.role === roleFilter;
}

function statusMatches(row: UserRow, statusFilter: string): boolean {
  return statusFilter === "all" || row.status === statusFilter;
}

export default function UsersPage() {
  const [keyword, setKeyword] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const filteredUsers = useMemo(
    () =>
      userRows.filter((user) => {
        const normalizedKeyword = keyword.trim().toLowerCase();
        const keywordMatched =
          normalizedKeyword.length === 0 ||
          [user.name, user.email, user.department].some((value) =>
            value.toLowerCase().includes(normalizedKeyword),
          );
        return keywordMatched && roleMatches(user, roleFilter) && statusMatches(user, statusFilter);
      }),
    [keyword, roleFilter, statusFilter],
  );

  const columns: ColumnsType<UserRow> = [
    {
      title: "用户",
      dataIndex: "name",
      key: "name",
      width: 260,
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
      title: "部门",
      dataIndex: "department",
      key: "department",
      width: 150,
    },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      width: 140,
      render: (value: UserRow["role"]) => <span className="users-role-pill">{roleLabels[value]}</span>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 130,
      render: (value: UserRow["status"]) => <StatusTag kind="user" value={value} />,
    },
    {
      title: "上传 / 审核",
      key: "contribution",
      width: 140,
      render: (_, record) => `${record.uploads} / ${record.reviews}`,
    },
    {
      title: "最近活跃",
      dataIndex: "lastActiveAt",
      key: "lastActiveAt",
      width: 170,
    },
    {
      title: "操作",
      key: "actions",
      width: 132,
      render: (_, record) => (
        <Space size={4}>
          <Tooltip title="编辑用户">
            <Button type="text" icon={<EditOutlined />} aria-label="编辑用户" />
          </Tooltip>
          <Tooltip title={record.status === "locked" ? "解锁用户" : "锁定用户"}>
            <Button
              type="text"
              icon={record.status === "locked" ? <UnlockOutlined /> : <LockOutlined />}
              aria-label={record.status === "locked" ? "解锁用户" : "锁定用户"}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <PageContainer
      title="用户管理"
      description="管理员工账号、权限角色、邮箱验证与登录状态。"
      actions={
        <Space className="users-page-actions" wrap>
          <Button icon={<ExportOutlined />}>导出用户</Button>
          <Button type="primary" icon={<PlusOutlined />}>
            新增用户
          </Button>
        </Space>
      }
    >
      <div className="users-kpi-grid">
        {userMetrics.map((metric) => (
          <Card className="users-kpi-card" key={metric.title}>
            <div className="users-kpi-card__body">
              <span className={`users-kpi-card__icon users-kpi-card__icon--${metric.tone}`}>
                {metric.icon}
              </span>
              <span className="users-kpi-card__copy">
                <Typography.Text type="secondary">{metric.title}</Typography.Text>
                <Typography.Title level={3}>{metric.value}</Typography.Title>
                <Typography.Text type="secondary">{metric.description}</Typography.Text>
              </span>
            </div>
          </Card>
        ))}
      </div>

      <div className="users-main-grid">
        <Card className="users-panel table-card" title="账号列表">
          <div className="filter-toolbar filter-toolbar--management">
            <Input
              className="filter-toolbar__search"
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索姓名、邮箱或部门"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
            <Select
              className="filter-toolbar__control"
              value={roleFilter}
              onChange={setRoleFilter}
              options={[
                { label: "角色：全部", value: "all" },
                { label: "系统管理员", value: "system_admin" },
                { label: "知识管理员", value: "knowledge_admin" },
                { label: "普通员工", value: "employee" },
              ]}
            />
            <Select
              className="filter-toolbar__control"
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { label: "状态：全部", value: "all" },
                { label: "正常", value: "active" },
                { label: "待激活", value: "pending_email_verification" },
                { label: "锁定中", value: "locked" },
                { label: "已禁用", value: "disabled" },
              ]}
            />
          </div>
          <Table<UserRow>
            rowKey="id"
            columns={columns}
            dataSource={filteredUsers}
            pagination={{ pageSize: 6, showSizeChanger: false }}
            rowSelection={{}}
            scroll={{ x: 920 }}
          />
        </Card>

        <Card className="users-panel" title="角色分布">
          <div className="users-role-distribution">
            {roleDistribution.map((item) => (
              <div className="users-role-row" key={item.label}>
                <span className="users-role-row__header">
                  <Typography.Text strong>{item.label}</Typography.Text>
                  <Typography.Text type="secondary">{item.value} 人</Typography.Text>
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
    </PageContainer>
  );
}
