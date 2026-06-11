import {
  BellOutlined,
  DownOutlined,
  LeftOutlined,
  LogoutOutlined,
  UserOutlined,
  ProfileOutlined,
} from "@ant-design/icons";
import { App as AntdApp, Avatar, Badge, Button, Dropdown, Input, Space, Typography } from "antd";
import type { MenuProps } from "antd";
import { useLocation, useNavigate } from "react-router-dom";

import { logout } from "../api/client";
import { appNavigationRoutes, utilityNavigation } from "../router/routes";
import { useAuthStore } from "../store/auth.store";

function getHeaderTitle(pathname: string): string {
  if (pathname.startsWith("/files/")) {
    return utilityNavigation.fileDetail.label;
  }

  return appNavigationRoutes.find((route) => route.path === pathname)?.nav?.label ?? "知识库贡献平台";
}

function formatRole(role?: string): string {
  if (role === "system_admin" || role === "knowledge_admin") {
    return "管理员";
  }
  if (role === "employee") {
    return "员工";
  }
  return "未登录";
}

export function TopHeader() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntdApp.useApp();
  const user = useAuthStore((state) => state.user);
  const clearSession = useAuthStore((state) => state.clearSession);
  const headerTitle = getHeaderTitle(location.pathname);

  const handleLogout = () => {
    void logout()
      .catch(() => undefined)
      .finally(() => {
        clearSession();
        navigate("/login", { replace: true });
      });
  };

  const items: MenuProps["items"] = [
    {
      key: "profile",
      icon: <ProfileOutlined />,
      label: "个人中心",
      onClick: () => { navigate("/profile"); },
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
        <Button type="text" icon={<LeftOutlined />} onClick={() => navigate(-1)} aria-label="返回上一页" />
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
      <Space size={16} className="top-header__actions">
        <Badge count={12} size="small">
          <Button type="text" icon={<BellOutlined />} aria-label="通知" />
        </Badge>
        <Dropdown menu={{ items }} trigger={["click"]}>
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
