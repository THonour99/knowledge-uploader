import {
  BellOutlined,
  DownOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { App as AntdApp, Avatar, Button, Dropdown, Input, Space, Typography } from "antd";
import type { MenuProps } from "antd";
import { useNavigate } from "react-router-dom";

import { logout } from "../api/client";
import { useAuthStore } from "../store/auth.store";
import { useUiStore } from "../store/ui.store";

export function TopHeader() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const user = useAuthStore((state) => state.user);
  const clearSession = useAuthStore((state) => state.clearSession);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);

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
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: handleLogout,
    },
  ];

  return (
    <header className="top-header">
      <Button
        type="text"
        icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        onClick={toggleSidebar}
        aria-label={collapsed ? "展开导航" : "收起导航"}
      />
      <Input.Search
        className="top-header__search"
        placeholder="搜索文件、用户、Dataset"
        allowClear
        onSearch={() => message.info("搜索功能待实现")}
      />
      <Space size={16} className="top-header__actions">
        <Button type="text" icon={<BellOutlined />} aria-label="通知" />
        <Dropdown menu={{ items }} trigger={["click"]}>
          <Button type="text" className="top-header__user">
            <Avatar size={28} icon={<UserOutlined />} />
            <span className="top-header__user-text">
              <Typography.Text strong>{user?.name ?? "用户"}</Typography.Text>
              <Typography.Text type="secondary">{user?.role ?? "未登录"}</Typography.Text>
            </span>
            <DownOutlined />
          </Button>
        </Dropdown>
      </Space>
    </header>
  );
}
