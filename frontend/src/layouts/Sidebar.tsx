import { DatabaseOutlined, DoubleLeftOutlined, DoubleRightOutlined } from "@ant-design/icons";
import { Button, Menu } from "antd";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuthStore } from "../store/auth.store";
import { useUiStore } from "../store/ui.store";
import { appNavigationRoutes } from "../router/routes";

function getSelectedKey(pathname: string): string {
  if (pathname.startsWith("/files/")) {
    return "/files";
  }

  return pathname;
}

export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const role = useAuthStore((state) => state.user?.role);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);

  const menuItems = appNavigationRoutes
    .filter((route) => !route.roles || (role ? route.roles.includes(role) : false))
    .map((route) => ({
      key: route.path,
      icon: route.nav?.icon,
      label: route.nav?.label,
    }));

  return (
    <div className="sidebar">
      <div className="sidebar-logo">
        <span className="sidebar-logo__mark">
          <DatabaseOutlined />
        </span>
        {collapsed ? null : <span className="sidebar-logo__text">知识库贡献平台</span>}
      </div>
      <Menu
        className="sidebar-menu"
        mode="inline"
        selectedKeys={[getSelectedKey(location.pathname)]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
      />
      <div className="sidebar-footer">
        <Button
          type="text"
          icon={collapsed ? <DoubleRightOutlined /> : <DoubleLeftOutlined />}
          onClick={toggleSidebar}
          aria-label={collapsed ? "展开菜单" : "收起菜单"}
        >
          {collapsed ? null : "收起菜单"}
        </Button>
      </div>
    </div>
  );
}
