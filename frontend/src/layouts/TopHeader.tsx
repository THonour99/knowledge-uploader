import { useEffect, useMemo, useState } from "react";
import {
  BellOutlined,
  CheckCircleOutlined,
  DownOutlined,
  ExclamationCircleOutlined,
  LeftOutlined,
  LogoutOutlined,
  ProfileOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { App as AntdApp, Avatar, Badge, Button, Dropdown, Input, Space, Typography } from "antd";
import type { MenuProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useLocation, useNavigate } from "react-router-dom";

import { getApiBaseUrl, getSystemHealth, listNotifications, logout } from "../api/client";
import { appNavigationRoutes, utilityNavigation } from "../router/routes";
import { useAuthStore } from "../store/auth.store";

function getHeaderTitle(pathname: string): string {
  if (pathname.startsWith("/files/")) {
    return utilityNavigation.fileDetail.label;
  }

  return (
    appNavigationRoutes.find((route) => route.path === pathname)?.nav?.label ?? "知识库贡献平台"
  );
}

function formatRole(role?: string): string {
  if (role === "system_admin") {
    return "系统管理员";
  }
  if (role === "dept_admin") {
    return "部门管理员";
  }
  if (role === "employee") {
    return "员工";
  }
  return "未登录";
}

function formatNotificationTime(value: string): string {
  const createdAt = dayjs(value);
  if (!createdAt.isValid()) {
    return "刚刚";
  }
  if (createdAt.isSame(dayjs(), "day")) {
    return createdAt.format("HH:mm");
  }
  return createdAt.format("MM-DD HH:mm");
}

export function TopHeader() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntdApp.useApp();
  const user = useAuthStore((state) => state.user);
  const clearSession = useAuthStore((state) => state.clearSession);
  const headerTitle = getHeaderTitle(location.pathname);
  const [now, setNow] = useState(() => dayjs());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(dayjs()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const notificationsQuery = useQuery({
    queryKey: ["notifications", "top-header", user?.id],
    queryFn: () => listNotifications({ page: 1, page_size: 5 }),
    enabled: Boolean(user),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const healthQuery = useQuery({
    queryKey: ["system", "health", "top-header"],
    queryFn: getSystemHealth,
    enabled: Boolean(user),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const notifications = notificationsQuery.data?.items ?? [];
  const unreadCount = notificationsQuery.data?.unread_count ?? 0;
  const apiBaseUrl = getApiBaseUrl();
  const apiStatus = healthQuery.isError
    ? { label: "API 异常", tone: "danger" }
    : healthQuery.isFetching && !healthQuery.data
      ? { label: "检测中", tone: "warning" }
      : healthQuery.data?.status === "ok"
        ? { label: "API 已连接", tone: "success" }
        : { label: "API 未知", tone: "warning" };

  const notificationMenuItems: MenuProps["items"] = useMemo(() => {
    if (notifications.length === 0) {
      return [
        {
          key: "empty",
          disabled: true,
          label: <span className="top-header-notification-empty">暂无通知</span>,
        },
      ];
    }

    return notifications.map((notification) => ({
      key: notification.id,
      label: (
        <div
          className={[
            "top-header-notification",
            notification.read_at ? "" : "top-header-notification--unread",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <Typography.Text strong ellipsis>
            {notification.title}
          </Typography.Text>
          <Typography.Text type="secondary" ellipsis>
            {notification.body}
          </Typography.Text>
          <Typography.Text type="secondary" className="top-header-notification__time">
            {formatNotificationTime(notification.created_at)}
          </Typography.Text>
        </div>
      ),
    }));
  }, [notifications]);

  const handleLogout = () => {
    void logout()
      .catch(() => undefined)
      .finally(() => {
        clearSession();
        navigate("/login", { replace: true });
      });
  };

  const userMenuItems: MenuProps["items"] = [
    {
      key: "profile",
      icon: <ProfileOutlined />,
      label: "个人中心",
      onClick: () => {
        navigate("/profile");
      },
    },
    { type: "divider" },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: handleLogout,
    },
  ];

  return (
    <header className="top-header">
      <div className="top-header__context">
        <Button
          type="text"
          icon={<LeftOutlined />}
          onClick={() => navigate(-1)}
          aria-label="返回上一页"
        />
        <Typography.Text strong className="top-header__page-title">
          {headerTitle}
        </Typography.Text>
      </div>
      <Input.Search
        className="top-header__search"
        placeholder="搜索文件、内容、用户..."
        allowClear
        onSearch={() => message.info("搜索功能待实现")}
      />
      <div className="top-header__status" aria-label="顶部状态栏">
        <span className={`top-header__status-pill top-header__status-pill--${apiStatus.tone}`}>
          {apiStatus.tone === "success" ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
          {apiStatus.label}
        </span>
        <Typography.Text type="secondary" className="top-header__api-base">
          API {apiBaseUrl}
        </Typography.Text>
        <Typography.Text type="secondary" className="top-header__time">
          {now.format("YYYY/MM/DD HH:mm")}
        </Typography.Text>
      </div>
      <Space size={12} className="top-header__actions">
        <Dropdown
          menu={{ items: notificationMenuItems }}
          trigger={["click"]}
          placement="bottomRight"
        >
          <Badge count={unreadCount} size="small" overflowCount={99}>
            <Button type="text" icon={<BellOutlined />} aria-label="通知中心" />
          </Badge>
        </Dropdown>
        <Dropdown menu={{ items: userMenuItems }} trigger={["click"]}>
          <Button type="text" className="top-header__user">
            <Avatar size={28} icon={user?.name ? undefined : <UserOutlined />}>
              {user?.name?.slice(0, 1).toUpperCase()}
            </Avatar>
            <span className="top-header__user-text">
              <Typography.Text strong>{user?.name ?? "用户"}</Typography.Text>
              <Typography.Text type="secondary">{formatRole(user?.role)}</Typography.Text>
            </span>
            <DownOutlined />
          </Button>
        </Dropdown>
      </Space>
    </header>
  );
}
