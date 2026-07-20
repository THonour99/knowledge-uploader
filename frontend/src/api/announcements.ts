import { type ApiEnvelope, apiClient } from "./client";

export type AnnouncementAudience = "all" | "departments" | "roles";
export type AnnouncementState = "draft" | "scheduled" | "published" | "expired" | "withdrawn";
export type AnnouncementRole = "employee" | "dept_admin" | "system_admin";

export interface AnnouncementSummary {
  id: string;
  title: string;
  state: AnnouncementState;
  visible_from: string | null;
  expires_at: string | null;
  is_pinned: boolean;
  is_read: boolean;
}

export interface AnnouncementDetail extends AnnouncementSummary {
  body_markdown: string;
}

export interface AnnouncementAdminDetail extends AnnouncementDetail {
  audience_type: AnnouncementAudience;
  department_ids: string[];
  roles: AnnouncementRole[];
  lifecycle_state: "draft" | "released" | "withdrawn";
  published_at: string | null;
  withdrawn_at: string | null;
  withdraw_reason: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface AnnouncementListResponse {
  items: AnnouncementSummary[];
  total: number;
  unread_count: number;
  page: number;
  page_size: number;
}

export interface AnnouncementAdminListResponse {
  items: AnnouncementAdminDetail[];
  total: number;
  page: number;
  page_size: number;
}

export interface AnnouncementPayload {
  title: string;
  body_markdown: string;
  audience_type: AnnouncementAudience;
  department_ids: string[];
  roles: AnnouncementRole[];
  visible_from: string | null;
  expires_at: string | null;
  is_pinned: boolean;
}

export interface AnnouncementStats {
  announcement_id: string;
  target_user_count: number;
  read_user_count: number;
  unread_user_count: number;
  read_rate: number;
}

function unwrap<T>(payload: ApiEnvelope<T> | T): T {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "success" in payload &&
    "data" in payload
  ) {
    return (payload as ApiEnvelope<T>).data;
  }
  return payload as T;
}

export async function listAnnouncements(
  params: {
    state?: "active" | "expired" | "all";
    unread_only?: boolean;
    page?: number;
    page_size?: number;
  } = {},
): Promise<AnnouncementListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<AnnouncementListResponse> | AnnouncementListResponse
  >("/announcements", { params });
  return unwrap(response.data);
}

export async function getAnnouncement(id: string): Promise<AnnouncementDetail> {
  const response = await apiClient.get<ApiEnvelope<AnnouncementDetail> | AnnouncementDetail>(
    `/announcements/${id}`,
  );
  return unwrap(response.data);
}

export async function markAnnouncementRead(id: string): Promise<void> {
  await apiClient.post(`/announcements/${id}/read`);
}

export async function listAdminAnnouncements(
  params: {
    state?: AnnouncementState | "all";
    search?: string;
    page?: number;
    page_size?: number;
  } = {},
): Promise<AnnouncementAdminListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<AnnouncementAdminListResponse> | AnnouncementAdminListResponse
  >("/admin/announcements", { params });
  return unwrap(response.data);
}

export async function createAnnouncement(
  payload: AnnouncementPayload,
): Promise<AnnouncementAdminDetail> {
  const response = await apiClient.post<
    ApiEnvelope<AnnouncementAdminDetail> | AnnouncementAdminDetail
  >("/admin/announcements", payload);
  return unwrap(response.data);
}

export async function updateAnnouncement(
  id: string,
  payload: AnnouncementPayload & { row_version: number },
): Promise<AnnouncementAdminDetail> {
  const response = await apiClient.patch<
    ApiEnvelope<AnnouncementAdminDetail> | AnnouncementAdminDetail
  >(`/admin/announcements/${id}`, payload);
  return unwrap(response.data);
}

export async function publishAnnouncement(
  id: string,
  payload: { row_version: number; visible_from?: string | null; expires_at?: string | null },
): Promise<AnnouncementAdminDetail> {
  const response = await apiClient.post<
    ApiEnvelope<AnnouncementAdminDetail> | AnnouncementAdminDetail
  >(`/admin/announcements/${id}/publish`, payload);
  return unwrap(response.data);
}

export async function withdrawAnnouncement(
  id: string,
  payload: { row_version: number; reason: string },
): Promise<AnnouncementAdminDetail> {
  const response = await apiClient.post<
    ApiEnvelope<AnnouncementAdminDetail> | AnnouncementAdminDetail
  >(`/admin/announcements/${id}/withdraw`, payload);
  return unwrap(response.data);
}

export async function cloneAnnouncement(
  id: string,
  rowVersion: number,
): Promise<AnnouncementAdminDetail> {
  const response = await apiClient.post<
    ApiEnvelope<AnnouncementAdminDetail> | AnnouncementAdminDetail
  >(`/admin/announcements/${id}/clone`, { row_version: rowVersion });
  return unwrap(response.data);
}

export async function deleteAnnouncement(id: string, rowVersion: number): Promise<void> {
  await apiClient.delete(`/admin/announcements/${id}`, { data: { row_version: rowVersion } });
}

export async function getAnnouncementStats(id: string): Promise<AnnouncementStats> {
  const response = await apiClient.get<ApiEnvelope<AnnouncementStats> | AnnouncementStats>(
    `/admin/announcements/${id}/stats`,
  );
  return unwrap(response.data);
}
