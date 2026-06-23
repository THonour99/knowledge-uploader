import axios, { type AxiosError } from "axios";

import { type CurrentUser, useAuthStore } from "../store/auth.store";

export interface ApiEnvelope<T> {
  success: boolean;
  data: T;
  message: string;
  request_id?: string;
  error_code?: string;
}

export interface LoginRequest {
  email: string;
  password: string;
  remember_me: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  user: CurrentUser;
}

export interface RegisterRequest {
  name: string;
  email: string;
  password: string;
  department?: string;
  phone?: string;
}

export interface RegisterResponse {
  accepted: boolean;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ResetPasswordRequest {
  token: string;
  new_password: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface ResendVerificationRequest {
  email: string;
}

export interface UserProfile {
  id: string;
  name: string;
  email: string;
  role: CurrentUser["role"];
  status: string;
  email_verified: boolean;
  department_id?: string | null;
  department_name?: string | null;
  department_code?: string | null;
  department: string | null;
  phone: string | null;
  managed_department_ids?: string[];
}

export interface FileAnalysis {
  status: string;
  summary: string | null;
  sensitive_risk_level: string;
  quality_score: number | null;
  quality_detail?: Record<string, unknown> | null;
  extracted_text_preview: string | null;
  tables_json?: FileAnalysisTable[] | null;
  table_count?: number | null;
  similar_file_ids?: string[] | null;
  similar_files?: SimilarFileReference[] | null;
  detected_expire_at?: string | null;
  expires_at?: string | null;
  expiry_status?: string | null;
  error_message: string | null;
  finished_at: string | null;
}

export interface FileAnalysisTable {
  title?: string | null;
  name?: string | null;
  headers?: string[] | null;
  columns?: string[] | null;
  rows?: unknown[] | null;
  markdown?: string | null;
  text?: string | null;
}

export type SimilarFileReference =
  | string
  | {
      id?: string | null;
      file_id?: string | null;
      original_name?: string | null;
      name?: string | null;
      similarity?: number | null;
      score?: number | null;
    };

export interface KnowledgeFile {
  id: string;
  original_name: string;
  extension: string;
  mime_type: string;
  size: number;
  uploader_id: string;
  department_id?: string | null;
  department_name?: string | null;
  department_code?: string | null;
  department: string | null;
  category_id: string | null;
  dataset_mapping_id: string | null;
  visibility: "private" | "department" | "company";
  description: string | null;
  tags: string[];
  status: string;
  review_status: string;
  ragflow_dataset_id: string | null;
  ragflow_document_id: string | null;
  ragflow_parse_status: string | null;
  ai_analysis_enabled_at_upload: boolean;
  uploaded_at: string;
  expires_at?: string | null;
  expiry_status?: string | null;
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
  duplicate: boolean;
  duplicate_file_id: string | null;
  /** 仅文件详情接口返回, 列表接口无此字段 */
  category_name?: string | null;
  /** 仅文件详情接口返回; 无分析记录时为 null */
  analysis?: FileAnalysis | null;
  /** 仅文件详情接口返回; 最近一次失败同步任务的错误信息 */
  sync_error?: string | null;
}

export interface FileListResponse {
  items: KnowledgeFile[];
  total: number;
}

export interface Category {
  id: string;
  name: string;
  code: string;
  description: string | null;
  parent_id: string | null;
  require_review: boolean;
  default_dataset_id: string | null;
  allow_employee_select: boolean;
  allow_ai_recommend: boolean;
  default_visibility: KnowledgeFile["visibility"];
  keywords: string[];
  classification_prompt: string | null;
  ai_analysis_enabled: boolean;
  sensitive_detection_enabled: boolean;
  auto_sync_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface CategoryListResponse {
  items: Category[];
  total: number;
}

export interface CategoryPayload {
  name: string;
  code: string;
  description?: string | null;
  parent_id?: string | null;
  require_review: boolean;
  default_dataset_id?: string | null;
  allow_employee_select: boolean;
  allow_ai_recommend: boolean;
  default_visibility: KnowledgeFile["visibility"];
  keywords: string[];
  classification_prompt?: string | null;
  ai_analysis_enabled: boolean;
  sensitive_detection_enabled: boolean;
  auto_sync_enabled: boolean;
}

export interface DatasetMapping {
  id: string;
  name: string;
  category_id: string;
  ragflow_dataset_id: string;
  ragflow_dataset_name: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface DatasetMappingListResponse {
  items: DatasetMapping[];
  total: number;
}

export interface DatasetMappingPayload {
  name: string;
  category_id: string;
  ragflow_dataset_id: string;
  ragflow_dataset_name: string;
  enabled: boolean;
}

export interface AiGlobalConfig {
  ai_analysis_enabled: boolean;
  allow_external_llm: boolean;
  allow_sync_when_analysis_failed: boolean;
}

export interface AiFeatureConfig {
  key: string;
  name: string;
  description?: string;
  enabled: boolean;
}

export interface AiProviderConfig {
  id: string;
  name: string;
  provider_type: string;
  base_url?: string | null;
  chat_model?: string | null;
  embedding_model?: string | null;
  enabled: boolean;
  priority: number;
  api_key_masked?: string | null;
  last_test_status?: string | null;
  last_test_latency_ms?: number | null;
  last_tested_at?: string | null;
}

export interface AiPromptTemplate {
  id: string;
  template_key: string;
  name: string;
  description?: string | null;
  enabled: boolean;
  is_default: boolean;
  version: number;
  updated_at?: string | null;
}

export interface AiSensitiveRule {
  id: string;
  name: string;
  rule_type: string;
  risk_level: string;
  action: string;
  enabled: boolean;
  hit_count: number;
  updated_at?: string | null;
}

export interface AiConfigResponse {
  global: AiGlobalConfig;
  features: AiFeatureConfig[];
  providers: AiProviderConfig[];
  prompt_templates: AiPromptTemplate[];
  sensitive_rules: AiSensitiveRule[];
}

export interface StatisticsQueryParams {
  start_date?: string;
  end_date?: string;
  department?: string;
  user_id?: string;
  category_id?: string;
  status?: string;
  review_status?: string;
  sync_status?: string;
  group_by?: "day" | "week" | "month";
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_order?: "asc" | "desc";
}

export interface StatisticsOverviewResponse {
  total_files: number;
  active_uploaders: number;
  synced_files: number;
  pending_review_files: number;
  failed_files: number;
  failed_tasks: number;
  rejected_files: number;
  sensitive_files: number;
  total_file_size: number;
  sync_success_rate: number;
}

export interface StatisticsUserRow {
  rank: number;
  user_id: string;
  user_name: string;
  department: string | null;
  total_files: number;
  approved_files: number;
  synced_files: number;
  failed_files: number;
  pending_review_files: number;
  rejected_files: number;
  sensitive_files: number;
  total_file_size: number;
  last_upload_at: string | null;
  last_success_sync_at: string | null;
}

export interface StatisticsUserListResponse {
  items: StatisticsUserRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface StatisticsDepartmentRow {
  department: string;
  total_files: number;
  active_uploaders: number;
  synced_files: number;
  failed_files: number;
  pending_review_files: number;
  total_file_size: number;
}

export interface StatisticsDepartmentListResponse {
  items: StatisticsDepartmentRow[];
  total: number;
}

export interface StatisticsCategoryRow {
  category_id: string | null;
  category_name: string;
  total_files: number;
  synced_files: number;
  failed_files: number;
  pending_review_files: number;
  total_file_size: number;
}

export interface StatisticsCategoryListResponse {
  items: StatisticsCategoryRow[];
  total: number;
}

export interface StatisticsTrendPoint {
  period: string;
  total_files: number;
  synced_files: number;
  failed_files: number;
  pending_review_files: number;
}

export interface StatisticsTrendResponse {
  group_by: "day" | "week" | "month";
  items: StatisticsTrendPoint[];
}

export interface StatisticsFailureRow {
  reason: string;
  failed_tasks: number;
  failed_files: number;
}

export interface StatisticsFailureListResponse {
  items: StatisticsFailureRow[];
  total: number;
}

export type ExpiryStatus = "active" | "expiring" | "expired" | "never";

export interface StatisticsExpiryStatusRow {
  status: ExpiryStatus;
  count: number;
}

export interface StatisticsExpiryResponse {
  total: number;
  active: number;
  expiring: number;
  expired: number;
  never: number;
  remind_days: number;
  as_of: string;
  window_end: string;
  items: StatisticsExpiryStatusRow[];
}

export interface UpdateAiFeaturePayload {
  enabled: boolean;
}

export interface AiProviderTestResult {
  provider_id: string;
  status: "success" | "failed";
  latency_ms?: number | null;
  message?: string | null;
}

export interface ReviewDecisionPayload {
  category_id?: string | null;
  dataset_mapping_id?: string | null;
  reason?: string | null;
}

export interface UpdateFileClassificationPayload {
  category_id?: string | null;
  dataset_mapping_id?: string | null;
}

export interface UploadDocumentPayload {
  file: File;
  description?: string;
  visibility: KnowledgeFile["visibility"];
  submitAfterUpload?: boolean;
  aiAnalysisEnabled?: boolean;
}

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  timeout: 15000,
  withCredentials: false,
});

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;

  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiEnvelope<unknown>>) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().clearSession();

      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }

    const message = error.response?.data?.message ?? error.message ?? "请求失败";
    return Promise.reject(new Error(message));
  },
);

function unwrapResponse<T>(payload: ApiEnvelope<T> | T): T {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "success" in payload &&
    "data" in payload
  ) {
    const envelope = payload as ApiEnvelope<T>;

    if (!envelope.success) {
      throw new Error(envelope.message || "请求失败");
    }

    return envelope.data;
  }

  return payload as T;
}

export async function login(payload: LoginRequest): Promise<LoginResponse> {
  const response = await apiClient.post<ApiEnvelope<LoginResponse> | LoginResponse>(
    "/auth/login",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function register(payload: RegisterRequest): Promise<RegisterResponse> {
  const response = await apiClient.post<ApiEnvelope<RegisterResponse> | RegisterResponse>(
    "/auth/register",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function forgotPassword(payload: ForgotPasswordRequest): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(
    "/auth/forgot-password",
    payload,
  );

  unwrapResponse(response.data);
}

export async function resetPassword(payload: ResetPasswordRequest): Promise<UserProfile> {
  const response = await apiClient.post<ApiEnvelope<UserProfile> | UserProfile>(
    "/auth/reset-password",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function changePassword(payload: ChangePasswordRequest): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(
    "/auth/change-password",
    payload,
  );

  unwrapResponse(response.data);
}

export async function resendVerification(payload: ResendVerificationRequest): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(
    "/auth/resend-verification",
    payload,
  );

  unwrapResponse(response.data);
}

export async function logout(): Promise<void> {
  await apiClient.post("/auth/logout");
}

export interface DocumentListQuery {
  extension?: string;
  tag_id?: string;
  page?: number;
  page_size?: number;
  status?: string;
  review_status?: string;
}

export async function uploadDocument(
  payload: UploadDocumentPayload,
  onUploadProgress?: (percent: number) => void,
): Promise<KnowledgeFile> {
  const formData = new FormData();
  formData.append("file", payload.file);
  formData.append("visibility", payload.visibility);
  formData.append("submit_after_upload", String(payload.submitAfterUpload ?? false));
  formData.append("ai_analysis_enabled", String(payload.aiAnalysisEnabled ?? true));

  if (payload.description?.trim()) {
    formData.append("description", payload.description.trim());
  }

  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    "/files/upload",
    formData,
    {
      timeout: 60_000,
      onUploadProgress: onUploadProgress
        ? (event) => {
            if (event.total && event.total > 0) {
              onUploadProgress(Math.round((event.loaded * 100) / event.total));
            }
          }
        : undefined,
    },
  );

  return unwrapResponse(response.data);
}

export async function listDocuments(params: DocumentListQuery = {}): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>("/files", {
    params,
  });

  return unwrapResponse(response.data);
}

export async function getDocument(id: string): Promise<KnowledgeFile> {
  const response = await apiClient.get<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(`/files/${id}`);

  return unwrapResponse(response.data);
}

export async function listCategories(): Promise<CategoryListResponse> {
  const response = await apiClient.get<ApiEnvelope<CategoryListResponse> | CategoryListResponse>(
    "/categories",
  );

  return unwrapResponse(response.data);
}

export async function createCategory(payload: CategoryPayload): Promise<Category> {
  const response = await apiClient.post<ApiEnvelope<Category> | Category>("/categories", payload);

  return unwrapResponse(response.data);
}

export async function updateCategory(
  id: string,
  payload: Partial<CategoryPayload>,
): Promise<Category> {
  const response = await apiClient.patch<ApiEnvelope<Category> | Category>(
    `/categories/${id}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function listDatasetMappings(): Promise<DatasetMappingListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<DatasetMappingListResponse> | DatasetMappingListResponse
  >("/datasets");

  return unwrapResponse(response.data);
}

export async function createDatasetMapping(
  payload: DatasetMappingPayload,
): Promise<DatasetMapping> {
  const response = await apiClient.post<ApiEnvelope<DatasetMapping> | DatasetMapping>(
    "/datasets",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function updateDatasetMapping(
  id: string,
  payload: Partial<DatasetMappingPayload>,
): Promise<DatasetMapping> {
  const response = await apiClient.patch<ApiEnvelope<DatasetMapping> | DatasetMapping>(
    `/datasets/${id}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function disableDatasetMapping(id: string): Promise<void> {
  await apiClient.delete(`/datasets/${id}`);
}

export async function getAiConfig(): Promise<AiConfigResponse> {
  const response = await apiClient.get<ApiEnvelope<AiConfigResponse> | AiConfigResponse>(
    "/admin/ai/config",
  );

  return unwrapResponse(response.data);
}

export async function getStatisticsOverview(
  params: StatisticsQueryParams = {},
): Promise<StatisticsOverviewResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsOverviewResponse> | StatisticsOverviewResponse
  >("/admin/statistics/overview", { params });

  return unwrapResponse(response.data);
}

export async function getStatisticsUsers(
  params: StatisticsQueryParams = {},
): Promise<StatisticsUserListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsUserListResponse> | StatisticsUserListResponse
  >("/admin/statistics/users", { params });

  return unwrapResponse(response.data);
}

export async function getStatisticsDepartments(
  params: StatisticsQueryParams = {},
): Promise<StatisticsDepartmentListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsDepartmentListResponse> | StatisticsDepartmentListResponse
  >("/admin/statistics/departments", { params });

  return unwrapResponse(response.data);
}

export async function getStatisticsCategories(
  params: StatisticsQueryParams = {},
): Promise<StatisticsCategoryListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsCategoryListResponse> | StatisticsCategoryListResponse
  >("/admin/statistics/categories", { params });

  return unwrapResponse(response.data);
}

export async function getStatisticsTrends(
  params: StatisticsQueryParams = {},
): Promise<StatisticsTrendResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsTrendResponse> | StatisticsTrendResponse
  >("/admin/statistics/trends", { params });

  return unwrapResponse(response.data);
}

export async function getStatisticsFailures(
  params: StatisticsQueryParams = {},
): Promise<StatisticsFailureListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsFailureListResponse> | StatisticsFailureListResponse
  >("/admin/statistics/failures", { params });

  return unwrapResponse(response.data);
}

export async function getStatisticsExpiry(
  params: StatisticsQueryParams = {},
): Promise<StatisticsExpiryResponse> {
  const response = await apiClient.get<
    ApiEnvelope<StatisticsExpiryResponse> | StatisticsExpiryResponse
  >("/admin/statistics/expiry", { params });

  return unwrapResponse(response.data);
}

export async function exportStatistics(params: StatisticsQueryParams = {}): Promise<Blob> {
  const response = await apiClient.get<Blob>("/admin/statistics/export", {
    params,
    responseType: "blob",
  });

  return response.data;
}

export async function updateAiFeature(
  featureKey: string,
  payload: UpdateAiFeaturePayload,
): Promise<AiFeatureConfig> {
  const response = await apiClient.patch<ApiEnvelope<AiFeatureConfig> | AiFeatureConfig>(
    `/admin/ai/features/${featureKey}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function testAiProvider(providerId: string): Promise<AiProviderTestResult> {
  const response = await apiClient.post<ApiEnvelope<AiProviderTestResult> | AiProviderTestResult>(
    `/admin/ai/providers/${providerId}/test`,
  );

  return unwrapResponse(response.data);
}

export interface ReviewFilesQuery {
  extension?: string;
  tag_id?: string;
  page?: number;
  page_size?: number;
  status?: string;
}

export async function listReviewFiles(params: ReviewFilesQuery = {}): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>(
    "/review/files",
    { params },
  );

  return unwrapResponse(response.data);
}

export async function submitFileForReview(id: string): Promise<KnowledgeFile> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/files/${id}/submit-review`,
  );

  return unwrapResponse(response.data);
}

export async function approveFile(
  id: string,
  payload: ReviewDecisionPayload,
): Promise<KnowledgeFile> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/files/${id}/approve`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function rejectFile(id: string, reason: string): Promise<KnowledgeFile> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/files/${id}/reject`,
    { reason },
  );

  return unwrapResponse(response.data);
}

export async function updateFileClassification(
  id: string,
  payload: UpdateFileClassificationPayload,
): Promise<KnowledgeFile> {
  const response = await apiClient.patch<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/files/${id}`,
    payload,
  );

  return unwrapResponse(response.data);
}

// ── Audit log types ──────────────────────────────────────────────────────────

export interface AuditLogItem {
  id: string;
  actor_id: string;
  actor_name: string | null;
  actor_email: string | null;
  action: string;
  target_type: string;
  target_id: string;
  ip_address: string | null;
  user_agent: string | null;
  reason: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AuditLogQuery {
  page?: number;
  page_size?: number;
  actor_id?: string;
  action?: string;
  target_type?: string;
  created_from?: string;
  created_to?: string;
}

// ── Sync task types ───────────────────────────────────────────────────────────

export interface SyncTaskLog {
  id: number;
  task_id: string;
  status: string;
  message: string;
  created_at: string;
}

export interface SyncTask {
  id: string;
  file_id: string;
  task_type: string;
  status: string;
  retry_count: number;
  max_retry_count: number;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  logs: SyncTaskLog[];
}

export interface SyncTaskListResponse {
  items: SyncTask[];
  total: number;
}

export interface TaskListQuery {
  task_type?: string;
  status?: string;
  file_id?: string;
}

// ── System config types ──────────────────────────────────────────────────────

export type ConfigGroup = "basic" | "upload" | "processing" | "security" | "ragflow";

export type ConfigValueType = "string" | "int" | "bool" | "list" | "secret";

export interface ConfigItem {
  key: string;
  value: unknown | null;
  value_type: ConfigValueType;
  is_secret: boolean;
  masked_value: string | null;
  description: string;
  updated_at: string | null;
}

export interface ConfigGroupResponse {
  group: ConfigGroup;
  items: ConfigItem[];
}

export interface RagflowConnectionTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

// ── System config API functions ──────────────────────────────────────────────

export async function getConfigs(group: ConfigGroup): Promise<ConfigGroupResponse> {
  const response = await apiClient.get<ApiEnvelope<ConfigGroupResponse> | ConfigGroupResponse>(
    "/admin/configs",
    { params: { group } },
  );

  return unwrapResponse(response.data);
}

export async function updateConfigs(
  group: ConfigGroup,
  items: Record<string, unknown>,
): Promise<ConfigGroupResponse> {
  const response = await apiClient.put<ApiEnvelope<ConfigGroupResponse> | ConfigGroupResponse>(
    `/admin/configs/${group}`,
    { items },
  );

  return unwrapResponse(response.data);
}

export async function testRagflowConnection(): Promise<RagflowConnectionTestResult> {
  const response = await apiClient.post<
    ApiEnvelope<RagflowConnectionTestResult> | RagflowConnectionTestResult
  >("/admin/ragflow/test-connection");

  return unwrapResponse(response.data);
}

// ── Audit log API functions ──────────────────────────────────────────────────

export async function listAuditLogs(params: AuditLogQuery = {}): Promise<AuditLogListResponse> {
  const response = await apiClient.get<ApiEnvelope<AuditLogListResponse> | AuditLogListResponse>(
    "/admin/audit-logs",
    { params },
  );

  return unwrapResponse(response.data);
}

// ── Task API functions ────────────────────────────────────────────────────────

export async function listTasks(params: TaskListQuery = {}): Promise<SyncTaskListResponse> {
  const response = await apiClient.get<ApiEnvelope<SyncTaskListResponse> | SyncTaskListResponse>(
    "/tasks",
    { params },
  );

  return unwrapResponse(response.data);
}

export async function getTask(id: string): Promise<SyncTask> {
  const response = await apiClient.get<ApiEnvelope<SyncTask> | SyncTask>(`/tasks/${id}`);

  return unwrapResponse(response.data);
}

export async function retryTask(id: string): Promise<SyncTask> {
  const response = await apiClient.post<ApiEnvelope<SyncTask> | SyncTask>(`/tasks/${id}/retry`);

  return unwrapResponse(response.data);
}

export async function cancelTask(id: string): Promise<SyncTask> {
  const response = await apiClient.post<ApiEnvelope<SyncTask> | SyncTask>(`/tasks/${id}/cancel`);

  return unwrapResponse(response.data);
}

// ── User profile API functions ────────────────────────────────────────────────

export async function getMe(): Promise<UserProfile> {
  const response = await apiClient.get<ApiEnvelope<UserProfile> | UserProfile>("/auth/me");

  return unwrapResponse(response.data);
}

// ── Tag types ─────────────────────────────────────────────────────────────────

export interface Tag {
  id: string;
  name: string;
  description: string | null;
  usage_count: number;
  is_system_generated: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface TagListResponse {
  items: Tag[];
  total: number;
  page: number;
  page_size: number;
}

export interface TagListQuery {
  enabled?: boolean;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface CreateTagPayload {
  name: string;
  description?: string;
}

export interface UpdateTagPayload {
  name?: string;
  description?: string | null;
  enabled?: boolean;
}

export interface MergeTagPayload {
  target_tag_id: string;
}

// ── Tag API functions ─────────────────────────────────────────────────────────

export async function listTags(params: TagListQuery = {}): Promise<TagListResponse> {
  const response = await apiClient.get<ApiEnvelope<TagListResponse> | TagListResponse>("/tags", {
    params,
  });

  return unwrapResponse(response.data);
}

export async function createTag(payload: CreateTagPayload): Promise<Tag> {
  const response = await apiClient.post<ApiEnvelope<Tag> | Tag>("/tags", payload);

  return unwrapResponse(response.data);
}

export async function updateTag(id: string, payload: UpdateTagPayload): Promise<Tag> {
  const response = await apiClient.patch<ApiEnvelope<Tag> | Tag>(`/tags/${id}`, payload);

  return unwrapResponse(response.data);
}

export async function mergeTag(id: string, payload: MergeTagPayload): Promise<Tag> {
  const response = await apiClient.post<ApiEnvelope<Tag> | Tag>(`/tags/${id}/merge`, payload);

  return unwrapResponse(response.data);
}

export async function deleteTag(id: string): Promise<void> {
  const response = await apiClient.delete<ApiEnvelope<Record<string, never>>>(`/tags/${id}`);

  unwrapResponse(response.data);
}

// ── Admin user management types ───────────────────────────────────────────────

export type AdminUserRole = "employee" | "dept_admin" | "system_admin";

export interface AdminUserItem {
  id: string;
  name: string;
  email: string;
  role: AdminUserRole;
  status: string;
  department_id?: string | null;
  department_name?: string | null;
  department_code?: string | null;
  department: string | null;
  managed_department_ids?: string[];
  email_verified: boolean;
  created_at: string;
  upload_count: number;
  last_upload_at: string | null;
}

export interface Department {
  id: string;
  name: string;
  code: string;
  status: "active" | "disabled";
  created_at: string;
  updated_at: string;
}

export interface DepartmentListResponse {
  items: Department[];
  total: number;
}

export interface ManagedDepartmentsResponse {
  user_id: string;
  managed_department_ids: string[];
  managed_departments?: Department[];
  departments?: Department[];
}

export interface AdminUserListResponse {
  items: AdminUserItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminUserListQuery {
  page?: number;
  page_size?: number;
  search?: string;
  role?: AdminUserRole;
  status?: string;
}

// ── Admin user management API functions ──────────────────────────────────────

export async function listAdminUsers(
  params: AdminUserListQuery = {},
): Promise<AdminUserListResponse> {
  const response = await apiClient.get<ApiEnvelope<AdminUserListResponse> | AdminUserListResponse>(
    "/users",
    { params },
  );

  return unwrapResponse(response.data);
}

export async function changeUserRole(id: string, role: AdminUserRole): Promise<UserProfile> {
  const response = await apiClient.patch<ApiEnvelope<UserProfile> | UserProfile>(
    `/users/${id}/role`,
    { role },
  );

  return unwrapResponse(response.data);
}

export async function listDepartments(): Promise<DepartmentListResponse> {
  const response = await apiClient.get<ApiEnvelope<DepartmentListResponse> | DepartmentListResponse>(
    "/admin/departments",
  );

  return unwrapResponse(response.data);
}

export async function getManagedDepartments(id: string): Promise<ManagedDepartmentsResponse> {
  const response = await apiClient.get<
    ApiEnvelope<ManagedDepartmentsResponse> | ManagedDepartmentsResponse
  >(`/admin/users/${id}/managed-departments`);

  return unwrapResponse(response.data);
}

export async function replaceManagedDepartments(
  id: string,
  departmentIds: string[],
): Promise<ManagedDepartmentsResponse> {
  const response = await apiClient.put<
    ApiEnvelope<ManagedDepartmentsResponse> | ManagedDepartmentsResponse
  >(`/admin/users/${id}/managed-departments`, { department_ids: departmentIds });

  return unwrapResponse(response.data);
}

export async function resetUserPassword(id: string): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(
    `/users/${id}/reset-password`,
  );

  unwrapResponse(response.data);
}

export async function disableUser(id: string): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(`/users/${id}/disable`);

  unwrapResponse(response.data);
}

export async function enableUser(id: string): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(`/users/${id}/enable`);

  unwrapResponse(response.data);
}

// ── File operation API functions ──────────────────────────────────────────────

export async function deleteFile(id: string): Promise<void> {
  const response = await apiClient.delete<ApiEnvelope<Record<string, never>>>(`/files/${id}`);

  unwrapResponse(response.data);
}

export async function archiveFile(id: string): Promise<KnowledgeFile> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/admin/files/${id}/archive`,
  );

  return unwrapResponse(response.data);
}

export async function reparseFile(id: string): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(
    `/admin/files/${id}/reparse`,
  );

  unwrapResponse(response.data);
}

export async function reanalyzeFile(id: string): Promise<void> {
  const response = await apiClient.post<ApiEnvelope<Record<string, never>>>(
    `/admin/files/${id}/reanalyze`,
  );

  unwrapResponse(response.data);
}

export async function syncFile(id: string): Promise<SyncTask> {
  const response = await apiClient.post<ApiEnvelope<SyncTask> | SyncTask>(
    `/admin/files/${id}/sync`,
  );

  return unwrapResponse(response.data);
}
