import { apiClient, type ApiEnvelope } from "./client";

export interface DashboardAccess {
  scope: "self" | "managed_departments" | "all";
  ready: boolean;
  blocker?: "department_required" | "managed_departments_required" | null;
  department_ids: string[];
}

export interface EmployeeStatusCounts {
  total: number;
  draft: number;
  ai_processing: number;
  analysis_failed: number;
  sensitive_review: number;
  pending_review: number;
  approved: number;
  rejected: number;
  sync_processing: number;
  parsed: number;
  sync_failed: number;
  archived: number;
}

export interface EmployeeActionCounts {
  total: number;
  submit_draft: number;
  revise_rejected: number;
  confirm_sensitive: number;
  analysis_failed: number;
}

export interface DashboardRecentDocument {
  id: string;
  original_name: string;
  /** Product-facing editable title; optional only for cached/rolling-deploy responses. */
  title?: string | null;
  extension: string;
  status: string;
  review_status: string;
  updated_at: string;
  next_action:
    | "submit_review"
    | "revise_rejected"
    | "confirm_sensitive"
    | "view_progress"
    | "view_detail";
}

export interface DashboardRecentNotification {
  id: string;
  type: string;
  title: string;
  body_excerpt: string;
  is_read: boolean;
  created_at: string;
  resource_type: "file" | "sync_task" | null;
  resource_id: string | null;
}

export interface EmployeeWorkbench {
  status_counts: EmployeeStatusCounts;
  action_counts: EmployeeActionCounts;
  recent_documents: DashboardRecentDocument[];
  recent_notifications: DashboardRecentNotification[];
  unread_notification_count: number;
}

export interface EmployeeDashboard {
  role: "employee";
  generated_at: string;
  access: DashboardAccess;
  employee: EmployeeWorkbench | null;
  admin: null;
  system: null;
}

export interface DashboardQuery {
  page?: number;
  page_size?: number;
  q?: string;
}

export async function getEmployeeDashboard(
  params: DashboardQuery = {},
): Promise<EmployeeDashboard> {
  const response = await apiClient.get<ApiEnvelope<EmployeeDashboard>>("/dashboard", { params });
  return response.data.data;
}
