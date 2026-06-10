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
  department: string | null;
  phone: string | null;
}

export interface KnowledgeFile {
  id: string;
  original_name: string;
  extension: string;
  mime_type: string;
  size: number;
  uploader_id: string;
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
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
  duplicate: boolean;
  duplicate_file_id: string | null;
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

export async function uploadDocument(payload: UploadDocumentPayload): Promise<KnowledgeFile> {
  const formData = new FormData();
  formData.append("file", payload.file);
  formData.append("visibility", payload.visibility);

  if (payload.description?.trim()) {
    formData.append("description", payload.description.trim());
  }

  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    "/files/upload",
    formData,
    { timeout: 60_000 },
  );

  return unwrapResponse(response.data);
}

export async function listDocuments(): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>("/files");

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
  const response = await apiClient.get<ApiEnvelope<StatisticsTrendResponse> | StatisticsTrendResponse>(
    "/admin/statistics/trends",
    { params },
  );

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

export async function listReviewFiles(): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>(
    "/review/files",
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
