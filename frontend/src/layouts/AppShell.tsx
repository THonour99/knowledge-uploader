import { useEffect } from "react";
import { Layout } from "antd";
import { Outlet } from "react-router-dom";

import { layout } from "../theme/tokens";
import { useUiStore } from "../store/ui.store";
import { Sidebar } from "./Sidebar";
import { TopHeader } from "./TopHeader";

export function AppShell() {
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const setCollapsed = useUiStore((state) => state.setSidebarCollapsed);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 1279px)");
    const syncCollapsed = () => setCollapsed(mediaQuery.matches);

    syncCollapsed();
    mediaQuery.addEventListener("change", syncCollapsed);

    return () => mediaQuery.removeEventListener("change", syncCollapsed);
  }, [setCollapsed]);

  return (
    <Layout className="app-shell">
      <Layout.Sider
        className="app-shell__sider"
        width={layout.sidebarWidth}
        collapsedWidth={layout.sidebarCollapsedWidth}
        collapsed={collapsed}
      >
        <Sidebar />
      </Layout.Sider>
      <Layout className="app-shell__main">
        <Layout.Header className="app-shell__header">
          <TopHeader />
        </Layout.Header>
        <Layout.Content className="app-shell__content">
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
