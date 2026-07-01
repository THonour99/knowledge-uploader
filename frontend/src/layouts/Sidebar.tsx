import {
  CheckCircleOutlined,
  DatabaseOutlined,
  DoubleLeftOutlined,
  DoubleRightOutlined,
} from "@ant-design/icons";
import { Button, Menu } from "antd";
import type { MenuProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { getSystemHealth } from "../api/client";
import { StatusTag } from "../components/StatusTag";
import { useAuthStore } from "../store/auth.store";
import { useUiStore } from "../store/ui.store";
import { appNavigationRoutes } from "../router/routes";

type SidebarHealthValue = "ok" | "error" | "unknown";

function getSelectedKey(pathname: string): string {
  if (pathname.startsWith("/files/")) {
    return "/files";
  }

  return pathname;
}

function resolveSidebarHealth(status: string | undefined, isError: boolean): SidebarHealthValue {
  if (isError) {
    return "error";
  }
  return status === "ok" ? "ok" : "unknown";
}

function formatHealthDescription(value: SidebarHealthValue): string {
  if (value === "ok") {
    return "服务链路正常";
  }
  if (value === "error") {
    return "服务状态异常";
  }
  return "等待健康检查";
}

export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const role = useAuthStore((state) => state.user?.role);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const healthQuery = useQuery({
    queryKey: ["system", "health", "sidebar"],
    queryFn: getSystemHealth,
    enabled: Boolean(role),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const healthValue = resolveSidebarHealth(healthQuery.data?.status, healthQuery.isError);
  const healthDescription = formatHealthDescription(healthValue);

  const menuItems: MenuProps["items"] = useMemo(() => {
    const visibleRoutes = appNavigationRoutes.filter(
      (route) => !route.roles || (role ? route.roles.includes(role) : false),
    );

    const grouped = new Map<string, typeof visibleRoutes>();
    for (const route of visibleRoutes) {
      const group = route.nav?.group ?? "";
      const list = grouped.get(group) ?? [];
      list.push(route);
      grouped.set(group, list);
    }

    const items: MenuProps["items"] = [];
    for (const [group, routes] of grouped) {
      if (!group) {
        for (const route of routes) {
          items.push({ key: route.path, icon: route.nav?.icon, label: route.nav?.label });
        }
        continue;
      }
      items.push({
        type: "group" as const,
        label: group,
        children: routes.map((route) => ({
          key: route.path,
          icon: route.nav?.icon,
          label: route.nav?.label,
        })),
      });
    }
    return items;
  }, [role]);

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
        <div className="sidebar-health" aria-label="平台运行状态">
          <span className="sidebar-health__icon">
            <CheckCircleOutlined />
          </span>
          {collapsed ? null : (
            <span className="sidebar-health__content">
              <span className="sidebar-health__meta">
                <span>运行状态</span>
                <StatusTag kind="health" value={healthValue} variant="dot" />
              </span>
              <span className="sidebar-health__text">{healthDescription}</span>
            </span>
          )}
        </div>
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
