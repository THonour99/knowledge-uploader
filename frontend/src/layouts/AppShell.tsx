import { useEffect, useState } from "react";
import { Drawer, Layout } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Outlet } from "react-router-dom";

import { getMe, type UserProfile } from "../api/client";
import { type CurrentUser, useAuthStore } from "../store/auth.store";
import { layout } from "../theme/tokens";
import { useUiStore } from "../store/ui.store";
import { Sidebar } from "./Sidebar";
import { TopHeader } from "./TopHeader";

function currentUserFromProfile(profile: UserProfile): CurrentUser {
  return {
    id: profile.id,
    name: profile.name,
    email: profile.email,
    role: profile.role,
    email_verified: profile.email_verified,
    department_assigned: profile.department_assigned === true,
    department_id: profile.department_id ?? null,
    department_name: profile.department_name ?? profile.department,
    department_code: profile.department_code ?? null,
  };
}

export function AppShell() {
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const setCollapsed = useUiStore((state) => state.setSidebarCollapsed);
  const mobileNavigationOpen = useUiStore((state) => state.mobileNavigationOpen);
  const setMobileNavigationOpen = useUiStore((state) => state.setMobileNavigationOpen);
  const accessToken = useAuthStore((state) => state.accessToken);
  const userId = useAuthStore((state) => state.user?.id);
  const setUser = useAuthStore((state) => state.setUser);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 768px)").matches,
  );
  const profileQuery = useQuery({
    queryKey: ["auth", "me", userId],
    queryFn: getMe,
    enabled: Boolean(accessToken && userId),
    staleTime: 0,
    refetchOnWindowFocus: "always",
    retry: false,
  });

  useEffect(() => {
    if (profileQuery.data) {
      setUser(currentUserFromProfile(profileQuery.data));
    }
  }, [profileQuery.data, setUser]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 1279px)");
    const syncCollapsed = () => setCollapsed(mediaQuery.matches);

    syncCollapsed();
    mediaQuery.addEventListener("change", syncCollapsed);

    return () => mediaQuery.removeEventListener("change", syncCollapsed);
  }, [setCollapsed]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 768px)");
    const syncMobile = () => {
      setIsMobile(mediaQuery.matches);
      if (!mediaQuery.matches) {
        setMobileNavigationOpen(false);
      }
    };

    syncMobile();
    mediaQuery.addEventListener("change", syncMobile);

    return () => mediaQuery.removeEventListener("change", syncMobile);
  }, [setMobileNavigationOpen]);

  return (
    <Layout className="app-shell">
      {isMobile ? (
        <Drawer
          className="app-shell__mobile-drawer"
          open={mobileNavigationOpen}
          placement="left"
          width={layout.sidebarWidth}
          closable={false}
          onClose={() => setMobileNavigationOpen(false)}
          styles={{ body: { padding: 0 } }}
          aria-label="移动导航"
        >
          <Sidebar mobile onNavigate={() => setMobileNavigationOpen(false)} />
        </Drawer>
      ) : (
        <Layout.Sider
          className="app-shell__sider"
          width={layout.sidebarWidth}
          collapsedWidth={layout.sidebarCollapsedWidth}
          collapsed={collapsed}
        >
          <Sidebar />
        </Layout.Sider>
      )}
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
