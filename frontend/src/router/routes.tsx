import {
  lazy,
  Suspense,
  type ComponentType,
  type LazyExoticComponent,
  type ReactNode,
} from "react";
import { Spin } from "antd";
import {
  AuditOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  OrderedListOutlined,
  RobotOutlined,
  SettingOutlined,
  TagsOutlined,
  TeamOutlined,
} from "@ant-design/icons";

import { type Role, Roles } from "../store/auth.store";

const AiConfigPage = lazy(() => import("../pages/AiConfig"));
const AuditLogsPage = lazy(() => import("../pages/AuditLogs"));
const CategoriesPage = lazy(() => import("../pages/Categories"));
const DashboardPage = lazy(() => import("../pages/Dashboard"));
const DatasetConfigPage = lazy(() => import("../pages/DatasetConfig"));
const FileDetailPage = lazy(() => import("../pages/FileDetail"));
const FileManagementPage = lazy(() => import("../pages/FileManagement"));
const ForgotPasswordPage = lazy(() => import("../pages/ForgotPassword"));
const LoginPage = lazy(() => import("../pages/Login"));
const MyFilesPage = lazy(() => import("../pages/MyFiles"));
const ProfilePage = lazy(() => import("../pages/Profile"));
const RegisterPage = lazy(() => import("../pages/Register"));
const ResetPasswordPage = lazy(() => import("../pages/ResetPassword"));
const SettingsPage = lazy(() => import("../pages/Settings"));
const StatisticsPage = lazy(() => import("../pages/Statistics"));
const TagsPage = lazy(() => import("../pages/Tags"));
const TaskLogsPage = lazy(() => import("../pages/TaskLogs"));
const UploadPage = lazy(() => import("../pages/Upload"));
const UsersPage = lazy(() => import("../pages/Users"));

function routeElement(Page: LazyExoticComponent<ComponentType>) {
  return (
    <Suspense
      fallback={
        <div className="route-loading">
          <Spin />
        </div>
      }
    >
      <Page />
    </Suspense>
  );
}

export interface RouteNavigation {
  label: string;
  icon: ReactNode;
}

export interface AppRoute {
  path: string;
  element: ReactNode;
  roles?: Role[];
  nav?: RouteNavigation;
}

export const publicRoutes: AppRoute[] = [
  { path: "/login", element: routeElement(LoginPage), roles: [] },
  { path: "/register", element: routeElement(RegisterPage), roles: [] },
  { path: "/forgot-password", element: routeElement(ForgotPasswordPage), roles: [] },
  { path: "/reset-password/:token", element: routeElement(ResetPasswordPage), roles: [] },
];

export const appRoutes: AppRoute[] = [
  {
    path: "/dashboard",
    element: routeElement(DashboardPage),
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "仪表盘", icon: <DashboardOutlined /> },
  },
  {
    path: "/upload",
    element: routeElement(UploadPage),
    nav: { label: "文件上传", icon: <CloudUploadOutlined /> },
  },
  {
    path: "/my-files",
    element: routeElement(MyFilesPage),
    nav: { label: "我的文件", icon: <FileTextOutlined /> },
  },
  {
    path: "/files",
    element: routeElement(FileManagementPage),
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "文件管理", icon: <FolderOpenOutlined /> },
  },
  {
    path: "/files/:id",
    element: routeElement(FileDetailPage),
  },
  {
    path: "/datasets",
    element: routeElement(DatasetConfigPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "Dataset 配置", icon: <DatabaseOutlined /> },
  },
  {
    path: "/ai-config",
    element: routeElement(AiConfigPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "AI 配置", icon: <RobotOutlined /> },
  },
  {
    path: "/statistics",
    element: routeElement(StatisticsPage),
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "统计分析", icon: <BarChartOutlined /> },
  },
  {
    path: "/users",
    element: routeElement(UsersPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "用户管理", icon: <TeamOutlined /> },
  },
  {
    path: "/settings",
    element: routeElement(SettingsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "系统设置", icon: <SettingOutlined /> },
  },
  {
    path: "/audit-logs",
    element: routeElement(AuditLogsPage),
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "操作日志", icon: <AuditOutlined /> },
  },
  {
    path: "/task-logs",
    element: routeElement(TaskLogsPage),
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "任务日志", icon: <OrderedListOutlined /> },
  },
  {
    path: "/categories",
    element: routeElement(CategoriesPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "分类管理", icon: <TagsOutlined /> },
  },
  {
    path: "/tags",
    element: routeElement(TagsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "标签管理", icon: <TagsOutlined /> },
  },
  {
    path: "/profile",
    element: routeElement(ProfilePage),
  },
];

export const appNavigationRoutes = appRoutes.filter((route) => route.nav);

export const utilityNavigation = {
  fileDetail: { label: "文件详情", icon: <FileSearchOutlined /> },
} as const;
