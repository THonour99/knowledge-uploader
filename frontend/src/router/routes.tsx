import type { ReactNode } from "react";
import {
  BarChartOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  RobotOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";

import AiConfigPage from "../pages/AiConfig";
import DashboardPage from "../pages/Dashboard";
import DatasetConfigPage from "../pages/DatasetConfig";
import FileDetailPage from "../pages/FileDetail";
import FileManagementPage from "../pages/FileManagement";
import ForgotPasswordPage from "../pages/ForgotPassword";
import LoginPage from "../pages/Login";
import MyFilesPage from "../pages/MyFiles";
import RegisterPage from "../pages/Register";
import ResetPasswordPage from "../pages/ResetPassword";
import SettingsPage from "../pages/Settings";
import StatisticsPage from "../pages/Statistics";
import UploadPage from "../pages/Upload";
import UsersPage from "../pages/Users";
import { type Role, Roles } from "../store/auth.store";

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
  { path: "/login", element: <LoginPage />, roles: [] },
  { path: "/register", element: <RegisterPage />, roles: [] },
  { path: "/forgot-password", element: <ForgotPasswordPage />, roles: [] },
  { path: "/reset-password/:token", element: <ResetPasswordPage />, roles: [] },
];

export const appRoutes: AppRoute[] = [
  {
    path: "/dashboard",
    element: <DashboardPage />,
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "仪表盘", icon: <DashboardOutlined /> },
  },
  {
    path: "/upload",
    element: <UploadPage />,
    nav: { label: "文件上传", icon: <CloudUploadOutlined /> },
  },
  {
    path: "/my-files",
    element: <MyFilesPage />,
    nav: { label: "我的文件", icon: <FileTextOutlined /> },
  },
  {
    path: "/files",
    element: <FileManagementPage />,
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "文件管理", icon: <FolderOpenOutlined /> },
  },
  {
    path: "/files/:id",
    element: <FileDetailPage />,
  },
  {
    path: "/datasets",
    element: <DatasetConfigPage />,
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "Dataset 配置", icon: <DatabaseOutlined /> },
  },
  {
    path: "/ai-config",
    element: <AiConfigPage />,
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "AI 配置", icon: <RobotOutlined /> },
  },
  {
    path: "/statistics",
    element: <StatisticsPage />,
    roles: [Roles.KNOWLEDGE_ADMIN, Roles.SYSTEM_ADMIN],
    nav: { label: "统计分析", icon: <BarChartOutlined /> },
  },
  {
    path: "/users",
    element: <UsersPage />,
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "用户管理", icon: <TeamOutlined /> },
  },
  {
    path: "/settings",
    element: <SettingsPage />,
    roles: [Roles.SYSTEM_ADMIN],
    nav: { label: "系统设置", icon: <SettingOutlined /> },
  },
];

export const appNavigationRoutes = appRoutes.filter((route) => route.nav);

export const utilityNavigation = {
  fileDetail: { label: "文件详情", icon: <FileSearchOutlined /> },
} as const;
