import {
  lazy,
  Suspense,
  type ComponentType,
  type LazyExoticComponent,
  type ReactNode,
} from "react";
import { Spin } from "antd";
import {
  AppstoreOutlined,
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
const DepartmentsPage = lazy(() => import("../pages/Departments"));
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
  group?: string;
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
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "运营总览", icon: <DashboardOutlined />, group: "工作台" },
  },
  {
    path: "/upload",
    element: routeElement(UploadPage),
    nav: { label: "上传文件", icon: <CloudUploadOutlined />, group: "文档" },
  },
  {
    path: "/my-files",
    element: routeElement(MyFilesPage),
    nav: { label: "我的文件", icon: <FileTextOutlined />, group: "文档" },
  },
  {
    path: "/files",
    element: routeElement(FileManagementPage),
    roles: [Roles.DEPT_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "文件审核", icon: <FolderOpenOutlined />, group: "文档" },
  },
  {
    path: "/files/:id",
    element: routeElement(FileDetailPage),
  },
  {
    path: "/datasets",
    element: routeElement(DatasetConfigPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "Dataset 配置", icon: <DatabaseOutlined />, group: "知识库" },
  },
  {
    path: "/ai-config",
    element: routeElement(AiConfigPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "AI 配置", icon: <RobotOutlined />, group: "系统" },
  },
  {
    path: "/statistics",
    element: routeElement(StatisticsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "统计报表", icon: <BarChartOutlined />, group: "系统" },
  },
  {
    path: "/users",
    element: routeElement(UsersPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "用户管理", icon: <TeamOutlined />, group: "系统" },
  },
  {
    path: "/departments",
    element: routeElement(DepartmentsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "部门管理", icon: <TeamOutlined />, group: "系统" },
  },
  {
    path: "/settings",
    element: routeElement(SettingsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "系统设置", icon: <SettingOutlined />, group: "系统" },
  },
  {
    path: "/audit-logs",
    element: routeElement(AuditLogsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "操作日志", icon: <AuditOutlined />, group: "系统" },
  },
  {
    path: "/task-logs",
    element: routeElement(TaskLogsPage),
    roles: [Roles.DEPT_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "任务日志", icon: <OrderedListOutlined />, group: "系统" },
  },
  {
    path: "/categories",
    element: routeElement(CategoriesPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "分类管理", icon: <AppstoreOutlined />, group: "知识库" },
  },
  {
    path: "/tags",
    element: routeElement(TagsPage),
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "标签管理", icon: <TagsOutlined />, group: "知识库" },
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
