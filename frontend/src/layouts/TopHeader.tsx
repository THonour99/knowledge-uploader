import { useEffect, useMemo, useState } from "react";
import {
  BellOutlined,
  DownOutlined,
  LeftOutlined,
  LogoutOutlined,
  MenuOutlined,
  ProfileOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  App as AntdApp,
  Avatar,
  Badge,
  Button,
  Drawer,
  Dropdown,
  Empty,
  Input,
  List,
  Pagination,
  Segmented,
  Space,
  Typography,
} from "antd";
import type { MenuProps } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useLocation, useNavigate } from "react-router-dom";

import {
  getApiBaseUrl,
  getSystemHealth,
  getSystemReadiness,
  listNotifications,
  logout,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "../api/client";
import { StatusTag } from "../components/StatusTag";
import { appNavigationRoutes, utilityNavigation } from "../router/routes";
import {
  type AuthSessionIdentity,
  type AuthSessionCallbackContext,
  assertCurrentAuthSessionIdentity,
  captureAuthSessionIdentity,
  isCurrentAuthSessionIdentity,
  isSessionSupersededError,
  runAuthSessionCallback,
  runAuthSessionLifecycleCallback,
} from "../sessionIdentity";
import { Roles, useAuthStore } from "../store/auth.store";
import { useUiStore } from "../store/ui.store";
import "./TopHeader.notification.css";

type HealthStatusValue = "ok" | "error" | "unknown";

const GLOBAL_SEARCH_ALIASES: Record<string, string[]> = {
  "/dashboard": ["运营", "看板", "总览", "概览", "仪表盘"],
  "/upload": ["上传", "贡献", "新增文件"],
  "/my-files": ["我的文档", "个人文件", "同步状态"],
  "/files": ["文件审核", "文件管理", "审核", "同步", "RAGFlow"],
  "/datasets": ["Dataset", "数据集", "分类映射", "RAGFlow 配置"],
  "/ai-config": ["AI", "模型", "Prompt", "敏感规则"],
  "/statistics": ["统计", "报表", "贡献排行", "统计分析"],
  "/users": ["用户", "账号", "权限", "部门"],
  "/departments": ["部门", "组织", "启停", "管辖"],
  "/settings": ["设置", "系统配置", "安全", "上传策略"],
  "/audit-logs": ["审计", "操作日志", "管理员操作"],
  "/task-logs": ["任务", "队列", "同步任务", "解析任务"],
  "/categories": ["分类", "知识分类", "目录"],
  "/tags": ["标签", "关键词"],
};

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

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NOTIFICATION_CENTER_PAGE_SIZE = 10;

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

interface NotificationDeepLinkOptions {
  canAccessTaskLogs?: boolean;
}

export function notificationDeepLink(
  notification: NotificationItem,
  options: NotificationDeepLinkOptions = {},
): string | null {
  const { metadata } = notification;
  const fileFallback = isUuid(metadata.file_id) ? `/files/${metadata.file_id}` : null;
  const hasResourceContract = "resource_type" in metadata || "resource_id" in metadata;

  if (hasResourceContract) {
    if (!isUuid(metadata.resource_id)) {
      return null;
    }
    if (metadata.resource_type === "file") {
      return `/files/${metadata.resource_id}`;
    }
    if (metadata.resource_type === "sync_task") {
      if (options.canAccessTaskLogs === false) {
        return fileFallback;
      }
      return `/task-logs?task_id=${metadata.resource_id}`;
    }
    return null;
  }

  if (isUuid(metadata.file_id)) {
    return `/files/${metadata.file_id}`;
  }
  if (isUuid(metadata.sync_task_id)) {
    if (options.canAccessTaskLogs === false) {
      return fileFallback;
    }
    return `/task-logs?task_id=${metadata.sync_task_id}`;
  }
  return null;
}

function resolveApiHealth(
  status: string | undefined,
  isError: boolean,
  isPending: boolean,
): HealthStatusValue {
  if (isError) {
    return "error";
  }
  if (isPending) {
    return "unknown";
  }
  return status === "ok" ? "ok" : "unknown";
}

function resolveDependencyHealth(status: string | undefined, isError: boolean): HealthStatusValue {
  if (isError) {
    return "error";
  }
  if (status === "ok" || status === "error") {
    return status;
  }
  return "unknown";
}

export function TopHeader() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { message } = AntdApp.useApp();
  const user = useAuthStore((state) => state.user);
  const clearSession = useAuthStore((state) => state.clearSession);
  const setMobileNavigationOpen = useUiStore((state) => state.setMobileNavigationOpen);
  const headerTitle = getHeaderTitle(location.pathname);
  const [now, setNow] = useState(() => dayjs());
  const [notificationCenterOpen, setNotificationCenterOpen] = useState(false);
  const [notificationPage, setNotificationPage] = useState(1);
  const [notificationUnreadOnly, setNotificationUnreadOnly] = useState(false);

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
  const notificationCenterQuery = useQuery({
    queryKey: ["notifications", "center", user?.id, notificationPage, notificationUnreadOnly],
    queryFn: () =>
      listNotifications({
        page: notificationPage,
        page_size: NOTIFICATION_CENTER_PAGE_SIZE,
        unread_only: notificationUnreadOnly,
      }),
    enabled: Boolean(user) && notificationCenterOpen,
    staleTime: 15_000,
  });
  const healthQuery = useQuery({
    queryKey: ["system", "health", "top-header"],
    queryFn: getSystemHealth,
    enabled: Boolean(user),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const readinessQuery = useQuery({
    queryKey: ["system", "readiness", "top-header"],
    queryFn: getSystemReadiness,
    enabled: Boolean(user),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const unreadCount = notificationsQuery.data?.unread_count ?? 0;
  const notificationCenterItems = notificationCenterQuery.data?.items ?? [];
  const notificationCenterTotal = notificationCenterQuery.data?.total ?? 0;
  const refreshNotifications = (context: AuthSessionCallbackContext) =>
    context.waitFor(() => queryClient.invalidateQueries({ queryKey: ["notifications"] }));
  const readMutation = useMutation({
    mutationFn: ({
      notificationId,
      requestIdentity,
    }: {
      notificationId: string;
      requestIdentity: AuthSessionIdentity;
    }) => {
      assertCurrentAuthSessionIdentity(requestIdentity);
      return markNotificationRead(notificationId);
    },
    onSuccess: (_result, variables) =>
      runAuthSessionLifecycleCallback(variables.requestIdentity, async (context) => {
        await refreshNotifications(context);
      }),
  });
  const readAllMutation = useMutation({
    mutationFn: (requestIdentity: AuthSessionIdentity) => {
      assertCurrentAuthSessionIdentity(requestIdentity);
      return markAllNotificationsRead();
    },
    onSuccess: (result, requestIdentity) =>
      runAuthSessionLifecycleCallback(requestIdentity, async (context) => {
        await refreshNotifications(context);
        context.run(() =>
          message.success(
            result.updated_count > 0
              ? `已将 ${result.updated_count} 条通知标为已读`
              : "没有新的未读通知",
          ),
        );
      }),
    onError: (error: Error, requestIdentity) => {
      if (isSessionSupersededError(error) || !isCurrentAuthSessionIdentity(requestIdentity)) {
        return;
      }
      message.error(error.message || "全部已读操作失败");
    },
  });
  const apiBaseUrl = getApiBaseUrl();
  const searchableRoutes = useMemo(
    () =>
      appNavigationRoutes
        .filter((route) => !route.roles || (user?.role ? route.roles.includes(user.role) : false))
        .map((route) => {
          const label = route.nav?.label ?? route.path;
          const aliases = GLOBAL_SEARCH_ALIASES[route.path] ?? [];
          return {
            label,
            path: route.path,
            keywords: [label, route.path, ...aliases].join(" ").toLowerCase(),
          };
        }),
    [user?.role],
  );
  const serviceStatusItems = [
    {
      key: "api",
      label: "API",
      value: resolveApiHealth(healthQuery.data?.status, healthQuery.isError, healthQuery.isPending),
    },
    {
      key: "queue",
      label: "队列",
      value: resolveDependencyHealth(
        readinessQuery.data?.dependencies["rabbitmq"]?.status,
        readinessQuery.isError,
      ),
    },
    {
      key: "storage",
      label: "存储",
      value: resolveDependencyHealth(
        readinessQuery.data?.dependencies["minio"]?.status,
        readinessQuery.isError,
      ),
    },
  ];

  const handleNotificationClick = async (notification: NotificationItem) => {
    const requestIdentity = captureAuthSessionIdentity();
    try {
      await runAuthSessionCallback(requestIdentity, async (context) => {
        if (!notification.read_at) {
          await context.waitFor(() =>
            readMutation.mutateAsync({
              notificationId: notification.id,
              requestIdentity,
            }),
          );
        }
        const target = notificationDeepLink(notification, {
          canAccessTaskLogs: user?.role !== Roles.EMPLOYEE,
        });
        if (target) {
          context.run(() => setNotificationCenterOpen(false));
          context.run(() => navigate(target));
        } else {
          context.run(() => message.info("该通知没有可访问的详情"));
        }
      });
    } catch (error) {
      if (isSessionSupersededError(error) || !isCurrentAuthSessionIdentity(requestIdentity)) {
        return;
      }
      message.error(error instanceof Error ? error.message : "通知状态更新失败");
    }
  };

  const handleLogout = () => {
    const requestIdentity = captureAuthSessionIdentity();
    void runAuthSessionCallback(requestIdentity, async (context) => {
      await context.waitFor(() => logout().catch(() => undefined));
      context.run(clearSession);
    }).catch(() => undefined);
  };

  const handleGlobalSearch = (value: string) => {
    const keyword = value.trim().toLowerCase();
    if (!keyword) {
      message.warning("请输入搜索关键词");
      return;
    }

    const exactMatch = searchableRoutes.find(
      (route) => route.label.toLowerCase() === keyword || route.path.toLowerCase() === keyword,
    );
    const fuzzyMatch = searchableRoutes.find((route) => route.keywords.includes(keyword));
    const targetRoute = exactMatch ?? fuzzyMatch;

    if (!targetRoute) {
      message.warning("未找到匹配页面");
      return;
    }

    navigate(targetRoute.path);
    message.success(`已跳转到${targetRoute.label}`);
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
          className="top-header__menu-button"
          icon={<MenuOutlined />}
          onClick={() => setMobileNavigationOpen(true)}
          aria-label="打开导航菜单"
        />
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
        aria-label="全局搜索"
        allowClear
        onSearch={handleGlobalSearch}
      />
      <div className="top-header__status" aria-label="顶部状态栏">
        <div className="top-header__status-group" aria-label="服务状态">
          {serviceStatusItems.map((item) => (
            <span className="top-header__status-item" key={item.key}>
              <span className="top-header__status-label">{item.label}</span>
              <StatusTag kind="health" value={item.value} variant="dot" />
            </span>
          ))}
        </div>
        <Typography.Text type="secondary" className="top-header__api-base">
          API {apiBaseUrl}
        </Typography.Text>
        <Typography.Text type="secondary" className="top-header__time">
          {now.format("YYYY/MM/DD HH:mm")}
        </Typography.Text>
      </div>
      <Space size={12} className="top-header__actions">
        <Badge count={unreadCount} size="small" overflowCount={99}>
          <Button
            type="text"
            icon={<BellOutlined />}
            aria-label="通知中心"
            onClick={() => {
              setNotificationPage(1);
              setNotificationCenterOpen(true);
            }}
          />
        </Badge>
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
      <Drawer
        title="通知中心"
        placement="right"
        width={440}
        open={notificationCenterOpen}
        onClose={() => setNotificationCenterOpen(false)}
        rootClassName="notification-center-drawer"
        extra={
          <Button
            type="link"
            size="small"
            disabled={
              readAllMutation.isPending ||
              (notificationCenterQuery.data?.unread_count ?? unreadCount) === 0
            }
            loading={readAllMutation.isPending}
            onClick={() => readAllMutation.mutate(captureAuthSessionIdentity())}
          >
            全部标为已读
          </Button>
        }
      >
        <div className="notification-center">
          <div className="notification-center__toolbar">
            <Segmented
              aria-label="通知筛选"
              value={notificationUnreadOnly ? "unread" : "all"}
              options={[
                { label: "全部", value: "all" },
                { label: "未读", value: "unread" },
              ]}
              onChange={(value) => {
                setNotificationUnreadOnly(value === "unread");
                setNotificationPage(1);
              }}
            />
            <Typography.Text type="secondary">
              {notificationCenterQuery.data?.unread_count ?? unreadCount} 条未读
            </Typography.Text>
          </div>

          {notificationCenterQuery.isError ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="通知加载失败">
              <Button onClick={() => void notificationCenterQuery.refetch()}>重新加载</Button>
            </Empty>
          ) : (
            <List
              className="notification-center__list"
              loading={notificationCenterQuery.isPending}
              dataSource={notificationCenterItems}
              locale={{
                emptyText: notificationUnreadOnly ? "没有未读通知" : "暂无通知",
              }}
              renderItem={(notification) => (
                <List.Item
                  className={
                    notification.read_at
                      ? "notification-center__item"
                      : "notification-center__item notification-center__item--unread"
                  }
                  onClick={() => void handleNotificationClick(notification)}
                >
                  <button
                    type="button"
                    className="notification-center__item-button"
                    aria-label={"打开通知：" + notification.title}
                  >
                    <span className="notification-center__item-copy">
                      <Typography.Text strong>{notification.title}</Typography.Text>
                      <Typography.Text type="secondary">{notification.body}</Typography.Text>
                    </span>
                    <Typography.Text type="secondary" className="notification-center__item-time">
                      {formatNotificationTime(notification.created_at)}
                    </Typography.Text>
                  </button>
                </List.Item>
              )}
            />
          )}

          {notificationCenterTotal > NOTIFICATION_CENTER_PAGE_SIZE ? (
            <Pagination
              className="notification-center__pagination"
              current={notificationPage}
              pageSize={NOTIFICATION_CENTER_PAGE_SIZE}
              total={notificationCenterTotal}
              showSizeChanger={false}
              hideOnSinglePage
              onChange={setNotificationPage}
            />
          ) : null}
        </div>
      </Drawer>
    </header>
  );
}
