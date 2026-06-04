import { Menu } from "antd";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuthStore } from "../store/auth.store";
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
        <span className="sidebar-logo__mark">K</span>
        <span className="sidebar-logo__text">Knowledge</span>
      </div>
      <Menu
        className="sidebar-menu"
        mode="inline"
        selectedKeys={[getSelectedKey(location.pathname)]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
      />
    </div>
  );
}
