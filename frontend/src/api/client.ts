import axios, {
  type AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  type AuthSessionIdentity,
  SessionSupersededError,
  assertCurrentAuthSessionIdentity,
  captureAuthSessionIdentity,
  createAuthSessionAbortScope,
  isCurrentAuthSessionIdentity,
} from "../sessionIdentity";
import { type CurrentUser, useAuthStore } from "../store/auth.store";
import { cancelResponseBody, readBoundedResponseBlob } from "../utils/boundedResponse";

export interface ApiEnvelope<T> {
  success: boolean;
  data: T;
  message: string;
  request_id?: string;
  error_code?: string;
  details?: unknown;
}

export class ApiError extends Error {
  readonly status: number | undefined;
  readonly code: string | undefined;
  readonly requestId: string | undefined;
  readonly details: unknown;

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      requestId?: string;
      details?: unknown;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId;
    this.details = options.details;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export const DEPARTMENT_ASSIGNMENT_REQUIRED_CODE = "DEPARTMENT_ASSIGNMENT_REQUIRED";
export const DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE =
  "账号尚未分配有效部门，请联系系统管理员分配部门后重试";

export function getUserFacingErrorMessage(error: unknown, fallback: string): string {
  if (isApiError(error) && error.code === DEPARTMENT_ASSIGNMENT_REQUIRED_CODE) {
    return DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

export interface LoginRequest {
  email: string;
  password: string;
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
  department_id?: string;
  phone?: string;
}

export interface RegisterResponse {
  accepted: boolean;
}

export interface RegistrationDepartment {
  id: string;
  name: string;
  code: string;
}

export interface VerifyEmailRequest {
  token: string;
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
  department_assigned?: boolean;
  department_id?: string | null;
  department_name?: string | null;
  department_code?: string | null;
  department: string | null;
  phone: string | null;
  managed_department_ids?: string[];
}

export interface NotificationItem {
  id: string;
  type: string;
  title: string;
  body: string;
  metadata: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  total: number;
  unread_count: number;
  page: number;
  page_size: number;
}

export interface NotificationListQuery {
  page?: number;
  page_size?: number;
  unread_only?: boolean;
}

export interface FileAnalysis {
  status: string;
  engine_type?: "rule" | "llm" | "hybrid";
  provider_name?: string | null;
  model_name?: string | null;
  prompt_template_key?: string | null;
  prompt_version?: number | null;
  input_char_count?: number | null;
  input_sha256?: string | null;
  category_count?: number | null;
  input_truncated?: boolean | null;
  attempt_number?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  latency_ms?: number;
  failure_category?: string | null;
  estimated_cost_microunits: number | string | null;
  cost_status?: "known" | "unknown_pricing" | "unknown_usage" | "legacy_unverifiable";
  cost_currency?: string;
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

export type RemoteVisibility = "candidate" | "current" | "not_current" | "unknown";
export type ReplacementRemoteAction = "delete" | "archive";

export type VersionSwitchStatus =
  | "not_required"
  | "pending"
  | "old_remote_deactivated"
  | "local_switched"
  | "completed"
  | "failed_old_deactivate"
  | "failed_new_activate";

export interface DocumentVersionChainItem {
  id: string;
  version_number: number;
  replaces_file_id: string | null;
  replacement_remote_action?: ReplacementRemoteAction | null;
  title: string;
  status: string;
  is_current_version: boolean;
  remote_visibility: RemoteVisibility;
  version_switch_status: VersionSwitchStatus;
  version_switch_error: string | null;
  created_at: string;
}

export interface DocumentOwnerOption {
  id: string;
  name: string;
}

export interface DocumentOwnerOptionListResponse {
  items: DocumentOwnerOption[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface KnowledgeFile {
  id: string;
  original_name: string;
  /** Product-facing editable title; optional only for cached/rolling-deploy responses. */
  title?: string | null;
  extension: string;
  mime_type: string;
  size: number;
  uploader_id: string;
  uploader_name?: string | null;
  owner_id: string | null;
  owner_name?: string | null;
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
  submitted_at?: string | null;
  review_due_at?: string | null;
  claimed_by?: string | null;
  claimed_by_name?: string | null;
  claimed_at?: string | null;
  claim_expires_at?: string | null;
  review_version?: number;
  sensitive_risk_level?: "none" | "low" | "medium" | "high" | "critical" | null;
  ragflow_dataset_id: string | null;
  ragflow_document_id: string | null;
  ragflow_parse_status: string | null;
  ai_analysis_enabled_at_upload: boolean;
  series_id: string;
  version_number: number;
  replaces_file_id: string | null;
  replacement_remote_action?: ReplacementRemoteAction | null;
  is_current_version: boolean;
  remote_visibility: RemoteVisibility;
  version_switch_status: VersionSwitchStatus;
  version_switch_error: string | null;
  version_switch_attempt_count: number;
  predecessor_remote_deactivated_at: string | null;
  local_version_activated_at: string | null;
  remote_version_activated_at: string | null;
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
  /** 仅文件详情接口返回；按 version_number 降序。 */
  version_chain?: DocumentVersionChainItem[];
}

export interface FileListResponse {
  items: KnowledgeFile[];
  total: number;
  page?: number;
  page_size?: number;
  total_pages?: number;
}

export interface Category {
  id: string;
  name: string;
  code: string;
  description: string | null;
  parent_id: string | null;
  allow_ai_recommend: boolean;
  keywords: string[];
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
  allow_ai_recommend: boolean;
  keywords: string[];
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
  ai_analysis_environment_enabled: boolean;
  ai_analysis_db_enabled: boolean;
  allow_external_llm_environment_enabled: boolean;
  allow_external_llm_db_enabled: boolean;
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
  is_internal: boolean;
  enabled: boolean;
  priority: number;
  timeout_seconds: number;
  max_retry_count: number;
  max_input_tokens?: number | null;
  max_output_tokens?: number | null;
  temperature: number;
  top_p?: number | null;
  input_price_microunits_per_million_tokens?: number;
  output_price_microunits_per_million_tokens?: number;
  pricing_currency?: string;
  pricing_configured?: boolean | null;
  has_api_key: boolean;
  api_key_masked?: string | null;
  last_test_status?: string | null;
  last_test_latency_ms?: number | null;
  last_tested_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface AiProviderPayload {
  name: string;
  provider_type: string;
  base_url?: string | null;
  api_key?: string | null;
  clear_api_key?: boolean;
  chat_model?: string | null;
  is_internal: boolean;
  enabled: boolean;
  priority: number;
  timeout_seconds: number;
  max_retry_count: number;
  max_input_tokens?: number | null;
  max_output_tokens?: number | null;
  temperature: number;
  top_p?: number | null;
  input_price_microunits_per_million_tokens: number;
  output_price_microunits_per_million_tokens: number;
  pricing_currency: string;
  pricing_configured?: boolean;
}

export interface AiPromptTemplate {
  id: string;
  template_key: string;
  name: string;
  description?: string | null;
  prompt_text: string;
  variables: string[];
  enabled: boolean;
  is_default: boolean;
  version: number;
  updated_at?: string | null;
}

export interface AiPromptTemplatePayload {
  template_key?: string;
  name?: string;
  description?: string | null;
  prompt_text?: string;
  variables?: string[];
  enabled?: boolean;
}

export interface AiSensitiveRule {
  id: string;
  name: string;
  rule_type: string;
  pattern?: string | null;
  keywords: string[];
  risk_level: string;
  action: string;
  enabled: boolean;
  hit_count: number;
  updated_at?: string | null;
}

export interface AiSensitiveRulePayload {
  name?: string;
  rule_type?: "keyword" | "regex";
  pattern?: string | null;
  keywords?: string[];
  risk_level?: "low" | "medium" | "high" | "critical";
  action?: "flag" | "require_review" | "block_sync";
  enabled?: boolean;
}

export interface AiSensitiveRuleTestHit {
  rule_id: string;
  rule_name: string;
  risk_level: string;
  action: string;
  match: string;
}

export interface AiSensitiveRuleTestResponse {
  hits: AiSensitiveRuleTestHit[];
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
  user_q?: string;
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

export type GovernanceCapacityGroupBy =
  | "none"
  | "department"
  | "file_type"
  | "processing_stage"
  | "day";
export type GovernanceLlmGroupBy = "none" | "department" | "provider" | "model" | "day";
export type GovernanceRagflowGroupBy =
  | "none"
  | "department"
  | "operation"
  | "result"
  | "failure_category"
  | "day";
export type GovernancePhysicalDimension = "cluster" | "department" | "file_type";

export interface GovernanceMetricsWindow {
  start_at: string;
  end_before: string;
  timezone: "UTC";
}

export interface GovernanceMetricsPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface GovernanceMetricsBaseQuery {
  start_at?: string;
  end_before?: string;
  page?: number;
  page_size?: number;
}

export interface GovernanceCapacityQuery extends GovernanceMetricsBaseQuery {
  group_by?: GovernanceCapacityGroupBy;
  physical_dimension?: GovernancePhysicalDimension;
}

export interface GovernanceLlmQuery extends GovernanceMetricsBaseQuery {
  group_by?: GovernanceLlmGroupBy;
}

export interface GovernanceRagflowQuery extends GovernanceMetricsBaseQuery {
  group_by?: GovernanceRagflowGroupBy;
}

export interface GovernanceCapacityRow {
  dimension_key: string;
  dimension_label: string;
  file_count: string;
  active_logical_bytes: string;
  retained_inactive_bytes: string;
  total_referenced_bytes: string;
}

export interface GovernancePhysicalCapacity {
  status: "available" | "stale" | "unavailable" | "unsupported_dimension";
  requested_dimension: GovernancePhysicalDimension;
  scope: "cluster";
  measurement_basis: "minio_raw_cluster_capacity" | null;
  source_kind: "minio_cluster_metrics" | null;
  total_bytes: string | null;
  used_bytes: string | null;
  free_bytes: string | null;
  captured_at: string | null;
  collected_at: string | null;
}

export interface GovernanceCapacityResponse {
  basis: "database_file_rows_uploaded_in_window";
  group_by: GovernanceCapacityGroupBy;
  window: GovernanceMetricsWindow;
  physical: GovernancePhysicalCapacity;
  items: GovernanceCapacityRow[];
  pagination: GovernanceMetricsPagination;
}

export interface GovernanceKnownCurrencyCost {
  currency: string;
  calls: string;
  prompt_tokens: string;
  completion_tokens: string;
  estimated_cost_microunits: string;
}

export interface GovernanceUnknownCostBucket {
  status: "unknown_pricing" | "unknown_usage" | "legacy_unverifiable";
  calls: string;
  known_prompt_tokens: string;
  known_completion_tokens: string;
  calls_with_unknown_tokens: string;
}

export interface GovernanceLlmUsageRow {
  dimension_key: string;
  dimension_label: string;
  total_calls: string;
  known_costs: GovernanceKnownCurrencyCost[];
  unknown_costs: GovernanceUnknownCostBucket[];
}

export interface GovernanceLlmUsageResponse {
  basis: "ai_usage_logs_created_in_window";
  group_by: GovernanceLlmGroupBy;
  window: GovernanceMetricsWindow;
  items: GovernanceLlmUsageRow[];
  pagination: GovernanceMetricsPagination;
}

export interface GovernanceRagflowUsageRow {
  dimension_key: string;
  dimension_label: string;
  calls: string;
  completed_calls: string;
  failure_calls: string;
  in_progress_calls: string;
  total_latency_ms: string;
}

export interface GovernanceRagflowUsageResponse {
  basis: "ragflow_api_calls_started_in_window";
  group_by: GovernanceRagflowGroupBy;
  window: GovernanceMetricsWindow;
  items: GovernanceRagflowUsageRow[];
  pagination: GovernanceMetricsPagination;
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
  sync_decision: "sync" | "approve_only";
  category_id?: string | null;
  dataset_mapping_id?: string | null;
  reason?: string | null;
}

export interface UpdateFileClassificationPayload {
  category_id?: string | null;
  dataset_mapping_id?: string | null;
}

export interface UploadPolicy {
  allowed_extensions: string[];
  allow_multi_file: boolean;
  upload_enabled: boolean;
  max_file_size_mb: number;
  allow_user_delete: boolean;
}

export interface UploadDocumentPayload {
  file: File;
  description?: string;
  visibility: KnowledgeFile["visibility"];
  submitAfterUpload?: boolean;
  aiAnalysisEnabled?: boolean;
  replacesFileId?: string;
}

export interface UpdateDocumentDraftPayload {
  expected_version: number;
  title?: string;
  description?: string | null;
  visibility?: KnowledgeFile["visibility"];
  owner_id?: string;
  expires_at?: string | null;
}

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  timeout: 15000,
  withCredentials: false,
});

const REQUEST_SESSION_GENERATION = "__knowledgeUploaderSessionGeneration" as const;

interface SessionBoundRequestConfig extends InternalAxiosRequestConfig {
  [REQUEST_SESSION_GENERATION]?: number;
}

interface SessionBoundAxiosRequestConfig extends AxiosRequestConfig {
  [REQUEST_SESSION_GENERATION]?: number;
}

apiClient.interceptors.request.use(
  (config) => {
    const requestConfig = config as SessionBoundRequestConfig;
    const boundGeneration = requestConfig[REQUEST_SESSION_GENERATION];
    const requestIdentity = captureAuthSessionIdentity();
    if (
      boundGeneration !== undefined &&
      (!Number.isSafeInteger(boundGeneration) ||
        boundGeneration < 0 ||
        boundGeneration !== requestIdentity.generation)
    ) {
      throw new SessionSupersededError();
    }
    assertCurrentAuthSessionIdentity(requestIdentity);
    requestConfig[REQUEST_SESSION_GENERATION] = requestIdentity.generation;

    if (requestIdentity.accessToken) {
      config.headers.Authorization = `Bearer ${requestIdentity.accessToken}`;
    }

    return config;
  },
  (error: unknown) => {
    throw error;
  },
  { synchronous: true },
);

apiClient.interceptors.response.use(
  (response) => {
    const requestGeneration = (response.config as SessionBoundRequestConfig)[
      REQUEST_SESSION_GENERATION
    ];
    const currentGeneration = captureAuthSessionIdentity().generation;
    if (requestGeneration === undefined || requestGeneration !== currentGeneration) {
      throw new SessionSupersededError();
    }
    return response;
  },
  (error: AxiosError<ApiEnvelope<unknown>>) => {
    if (error instanceof SessionSupersededError) {
      return Promise.reject(error);
    }
    const requestGeneration = (error.config as SessionBoundRequestConfig | undefined)?.[
      REQUEST_SESSION_GENERATION
    ];
    const currentGeneration = captureAuthSessionIdentity().generation;
    if (requestGeneration === undefined || requestGeneration !== currentGeneration) {
      return Promise.reject(new SessionSupersededError());
    }
    if (error.response?.status === 401 && requestGeneration === currentGeneration) {
      useAuthStore.getState().clearSession();

      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }

    const payload = error.response?.data;
    const message = payload?.message ?? error.message ?? "请求失败";
    return Promise.reject(
      new ApiError(message, {
        status: error.response?.status,
        code: payload?.error_code,
        requestId: payload?.request_id,
        details: payload?.details,
      }),
    );
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

export async function listRegistrationDepartments(): Promise<RegistrationDepartment[]> {
  const response = await apiClient.get<
    ApiEnvelope<RegistrationDepartment[]> | RegistrationDepartment[]
  >("/auth/registration-departments");

  return unwrapResponse(response.data);
}

export async function verifyEmail(payload: VerifyEmailRequest): Promise<UserProfile> {
  const response = await apiClient.post<ApiEnvelope<UserProfile> | UserProfile>(
    "/auth/verify-email",
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
  q?: string;
  extension?: string;
  tag_id?: string;
  page?: number;
  page_size?: number;
  status?: string;
  review_status?: string;
  expiry_status?: string;
  sort?: string;
  order?: "asc" | "desc";
}
export type ResponsibleDocumentListQuery = Omit<DocumentListQuery, "tag_id" | "review_status">;

export async function getUploadPolicy(): Promise<UploadPolicy> {
  const response = await apiClient.get<ApiEnvelope<UploadPolicy> | UploadPolicy>("/files/policy");

  return unwrapResponse(response.data);
}

export interface UploadDocumentRequestOptions {
  signal?: AbortSignal;
  requestIdentity?: AuthSessionIdentity;
}

export async function uploadDocument(
  payload: UploadDocumentPayload,
  onUploadProgress?: (percent: number) => void,
  options: UploadDocumentRequestOptions = {},
): Promise<KnowledgeFile> {
  const formData = new FormData();
  formData.append("file", payload.file);
  formData.append("visibility", payload.visibility);
  formData.append("submit_after_upload", String(payload.submitAfterUpload ?? false));
  formData.append("ai_analysis_enabled", String(payload.aiAnalysisEnabled ?? true));

  if (payload.description?.trim()) {
    formData.append("description", payload.description.trim());
  }

  if (payload.replacesFileId) {
    formData.append("replaces_file_id", payload.replacesFileId);
  }

  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    "/files/upload",
    formData,
    {
      timeout: 60_000,
      signal: options.signal,
      [REQUEST_SESSION_GENERATION]: options.requestIdentity?.generation,
      onUploadProgress: onUploadProgress
        ? (event) => {
            if (event.total && event.total > 0) {
              onUploadProgress(Math.round((event.loaded * 100) / event.total));
            }
          }
        : undefined,
    } as SessionBoundAxiosRequestConfig,
  );

  return unwrapResponse(response.data);
}

export async function listDocuments(params: DocumentListQuery = {}): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>("/files", {
    params,
  });

  return unwrapResponse(response.data);
}

export async function listResponsibleDocuments(
  params: ResponsibleDocumentListQuery = {},
): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>(
    "/files/responsible",
    { params },
  );

  return unwrapResponse(response.data);
}

export async function getDocument(id: string): Promise<KnowledgeFile> {
  const response = await apiClient.get<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(`/files/${id}`);

  return unwrapResponse(response.data);
}

export const DEFAULT_INLINE_CONTENT_MAX_BYTES = 20 * 1024 * 1024;

export interface DocumentContent {
  blob: Blob;
  contentType: string;
  contentDisposition: string | null;
  contentLength: number | null;
  etag: string | null;
}

export interface DocumentContentRequestOptions {
  maxBytes?: number;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
}

function documentContentSizeError(maxBytes: number): Error {
  return new Error(
    `原件超过 ${Math.floor(maxBytes / (1024 * 1024))} MiB 安全预览上限，请流式下载后查看`,
  );
}

export async function getDocumentContent(
  id: string,
  disposition: "inline" | "attachment" = "inline",
  options: DocumentContentRequestOptions = {},
): Promise<DocumentContent> {
  const maxBytes = options.maxBytes ?? DEFAULT_INLINE_CONTENT_MAX_BYTES;
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) {
    throw new Error("原件预览缓冲上限配置无效");
  }
  const requestIdentity = captureAuthSessionIdentity();
  if (!requestIdentity.accessToken) {
    throw new ApiError("登录状态已失效，请重新登录后查看原件", { status: 401 });
  }

  const sessionScope = createAuthSessionAbortScope(requestIdentity, options.signal);
  let clearedCurrentSessionForUnauthorized = false;
  try {
    const endpoint = `${getApiBaseUrl().replace(/\/$/, "")}/files/${encodeURIComponent(
      id,
    )}/content?disposition=${encodeURIComponent(disposition)}`;
    const response = await (options.fetchImpl ?? fetch)(endpoint, {
      method: "GET",
      headers: {
        Accept:
          disposition === "inline"
            ? "application/pdf,image/*,text/plain,text/csv,text/markdown"
            : "application/octet-stream",
        Authorization: `Bearer ${requestIdentity.accessToken}`,
      },
      credentials: "same-origin",
      signal: sessionScope.signal,
    });
    if (!isCurrentAuthSessionIdentity(requestIdentity)) {
      const error = new SessionSupersededError();
      await cancelResponseBody(response, error);
      throw error;
    }

    if (!response.ok) {
      const error = new ApiError(`原件读取失败（HTTP ${response.status}）`, {
        status: response.status,
      });
      await cancelResponseBody(response, error);
      assertCurrentAuthSessionIdentity(requestIdentity);
      if (response.status === 401) {
        clearedCurrentSessionForUnauthorized = true;
        useAuthStore.getState().clearSession();
      }
      throw error;
    }

    const contentType = response.headers.get("content-type") ?? "application/octet-stream";
    const content = await readBoundedResponseBlob(response, {
      maxBytes,
      sizeError: documentContentSizeError,
      missingBodyError: () => new Error("浏览器未提供可读取的原件预览流"),
      assertCanContinue: () => assertCurrentAuthSessionIdentity(requestIdentity),
    });
    assertCurrentAuthSessionIdentity(requestIdentity);

    return {
      blob: content.blob,
      contentType,
      contentDisposition: response.headers.get("content-disposition"),
      contentLength: content.contentLength,
      etag: response.headers.get("etag"),
    };
  } catch (error) {
    if (!clearedCurrentSessionForUnauthorized && !isCurrentAuthSessionIdentity(requestIdentity)) {
      throw new SessionSupersededError();
    }
    throw error;
  } finally {
    sessionScope.dispose();
  }
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

export async function getGovernanceCapacity(
  params: GovernanceCapacityQuery = {},
): Promise<GovernanceCapacityResponse> {
  const response = await apiClient.get<
    ApiEnvelope<GovernanceCapacityResponse> | GovernanceCapacityResponse
  >("/admin/statistics/capacity", { params });

  return unwrapResponse(response.data);
}

export async function getGovernanceLlmUsage(
  params: GovernanceLlmQuery = {},
): Promise<GovernanceLlmUsageResponse> {
  const response = await apiClient.get<
    ApiEnvelope<GovernanceLlmUsageResponse> | GovernanceLlmUsageResponse
  >("/admin/statistics/llm-usage", { params });

  return unwrapResponse(response.data);
}

export async function getGovernanceRagflowUsage(
  params: GovernanceRagflowQuery = {},
): Promise<GovernanceRagflowUsageResponse> {
  const response = await apiClient.get<
    ApiEnvelope<GovernanceRagflowUsageResponse> | GovernanceRagflowUsageResponse
  >("/admin/statistics/ragflow-usage", { params });

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

export async function createAiProvider(payload: AiProviderPayload): Promise<AiProviderConfig> {
  const response = await apiClient.post<ApiEnvelope<AiProviderConfig> | AiProviderConfig>(
    "/admin/ai/providers",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function updateAiProvider(
  providerId: string,
  payload: AiProviderPayload,
): Promise<AiProviderConfig> {
  const response = await apiClient.patch<ApiEnvelope<AiProviderConfig> | AiProviderConfig>(
    `/admin/ai/providers/${providerId}`,
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

export async function createAiPromptTemplate(
  payload: AiPromptTemplatePayload,
): Promise<AiPromptTemplate> {
  const response = await apiClient.post<ApiEnvelope<AiPromptTemplate> | AiPromptTemplate>(
    "/admin/ai/prompt-templates",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function updateAiPromptTemplate(
  templateId: string,
  payload: AiPromptTemplatePayload,
): Promise<AiPromptTemplate> {
  const response = await apiClient.patch<ApiEnvelope<AiPromptTemplate> | AiPromptTemplate>(
    `/admin/ai/prompt-templates/${templateId}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function restoreAiPromptTemplateDefault(
  templateId: string,
): Promise<AiPromptTemplate> {
  const response = await apiClient.post<ApiEnvelope<AiPromptTemplate> | AiPromptTemplate>(
    `/admin/ai/prompt-templates/${templateId}/restore-default`,
  );

  return unwrapResponse(response.data);
}

export async function deleteAiPromptTemplate(templateId: string): Promise<void> {
  const response = await apiClient.delete<ApiEnvelope<Record<string, never>>>(
    `/admin/ai/prompt-templates/${templateId}`,
  );

  unwrapResponse(response.data);
}

export async function createAiSensitiveRule(
  payload: AiSensitiveRulePayload,
): Promise<AiSensitiveRule> {
  const response = await apiClient.post<ApiEnvelope<AiSensitiveRule> | AiSensitiveRule>(
    "/admin/ai/sensitive-rules",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function updateAiSensitiveRule(
  ruleId: string,
  payload: AiSensitiveRulePayload,
): Promise<AiSensitiveRule> {
  const response = await apiClient.patch<ApiEnvelope<AiSensitiveRule> | AiSensitiveRule>(
    `/admin/ai/sensitive-rules/${ruleId}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function deleteAiSensitiveRule(ruleId: string): Promise<void> {
  const response = await apiClient.delete<ApiEnvelope<Record<string, never>>>(
    `/admin/ai/sensitive-rules/${ruleId}`,
  );

  unwrapResponse(response.data);
}

export async function testAiSensitiveRules(text: string): Promise<AiSensitiveRuleTestResponse> {
  const response = await apiClient.post<
    ApiEnvelope<AiSensitiveRuleTestResponse> | AiSensitiveRuleTestResponse
  >("/admin/ai/sensitive-rules/test", { text });

  return unwrapResponse(response.data);
}

export interface ReviewFilesQuery {
  q?: string;
  extension?: string;
  tag_id?: string;
  page?: number;
  page_size?: number;
  status?: string;
  queue?: "unclaimed" | "mine" | "due_soon" | "overdue";
  department_id?: string;
  sensitive_risk_level?: string;
  sort?: string;
  order?: "asc" | "desc";
}

export async function listReviewFiles(params: ReviewFilesQuery = {}): Promise<FileListResponse> {
  const response = await apiClient.get<ApiEnvelope<FileListResponse> | FileListResponse>(
    "/review/files",
    { params },
  );

  return unwrapResponse(response.data);
}

export async function claimReviewFile(id: string): Promise<KnowledgeFile> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/review/files/${id}/claim`,
  );

  return unwrapResponse(response.data);
}

export async function releaseReviewClaim(id: string, reason?: string): Promise<KnowledgeFile> {
  const response = await apiClient.delete<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/review/files/${id}/claim`,
    { data: reason?.trim() ? { reason: reason.trim() } : undefined },
  );

  return unwrapResponse(response.data);
}

export interface SubmitReviewRequest {
  acknowledge_sensitive_risk?: boolean;
}

export async function submitFileForReview(
  id: string,
  payload?: SubmitReviewRequest,
): Promise<KnowledgeFile> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/files/${id}/submit-review`,
    payload,
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

export interface SyncTaskStatusCounts {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  canceled: number;
}

export interface SyncTaskListResponse {
  items: SyncTask[];
  total: number;
  status_counts: SyncTaskStatusCounts;
  page?: number;
  page_size?: number;
  total_pages?: number;
}

export type SyncTaskType =
  | "ragflow_upload"
  | "ragflow_parse"
  | "ragflow_status_check"
  | "ragflow_delete";
export type SyncTaskStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";
export type SyncTaskSort = "created_at" | "updated_at" | "started_at" | "finished_at";

export interface TaskListQuery {
  task_type?: SyncTaskType;
  status?: SyncTaskStatus;
  file_id?: string;
  department_id?: string;
  sort?: SyncTaskSort;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export const SAVED_VIEW_DEFINITION_SCHEMA_VERSION = 2;

export type SavedViewPageKey = "my_files" | "review_files" | "task_logs" | "statistics";
export type SavedViewScope = "private" | "department";
export type SavedViewCompatibility = "current" | "migrated" | "unsupported";

export interface SavedViewDefinition {
  query_definition: Record<string, unknown>;
  column_preferences: Record<string, unknown>;
}

export interface SavedViewItem {
  id: string;
  owner_id: string;
  scope: SavedViewScope;
  department_id: string | null;
  page_key: SavedViewPageKey;
  name: string;
  stored_schema_version: number;
  effective_schema_version: number | null;
  compatibility: SavedViewCompatibility;
  effective_definition: SavedViewDefinition | null;
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface SavedViewQuotaPolicy {
  private_per_owner_page: number;
  department_per_department_page: number;
}

export interface SavedViewListResponse {
  items: SavedViewItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  quota: SavedViewQuotaPolicy;
}

export interface SavedViewListQuery {
  page_key: SavedViewPageKey;
  scope?: SavedViewScope;
  q?: string;
  page?: number;
  page_size?: number;
}

export interface SavedViewCreatePayload {
  page_key: SavedViewPageKey;
  name: string;
  scope: SavedViewScope;
  department_id?: string;
  definition_schema_version: number;
  query_definition: Record<string, unknown>;
  column_preferences: Record<string, unknown>;
}

export interface SavedViewUpdatePayload {
  row_version: number;
  name?: string;
  definition_schema_version?: number;
  query_definition?: Record<string, unknown>;
  column_preferences?: Record<string, unknown>;
}

// ── System config types ──────────────────────────────────────────────────────

export type ConfigGroup = "upload" | "processing" | "security" | "review" | "ragflow" | "outbox";

export type ConfigValueType = "string" | "int" | "bool" | "list" | "secret";

export interface ConfigItem {
  key: string;
  value: unknown | null;
  value_type: ConfigValueType;
  is_secret: boolean;
  immutable?: boolean;
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
export async function getSavedView(id: string): Promise<SavedViewItem> {
  const response = await apiClient.get<ApiEnvelope<SavedViewItem> | SavedViewItem>(
    `/saved-views/${id}`,
  );

  return unwrapResponse(response.data);
}

export async function listSavedViews(params: SavedViewListQuery): Promise<SavedViewListResponse> {
  const response = await apiClient.get<ApiEnvelope<SavedViewListResponse> | SavedViewListResponse>(
    "/saved-views",
    { params },
  );

  return unwrapResponse(response.data);
}

export async function createSavedView(payload: SavedViewCreatePayload): Promise<SavedViewItem> {
  const response = await apiClient.post<ApiEnvelope<SavedViewItem> | SavedViewItem>(
    "/saved-views",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function updateSavedView(
  id: string,
  payload: SavedViewUpdatePayload,
): Promise<SavedViewItem> {
  const response = await apiClient.patch<ApiEnvelope<SavedViewItem> | SavedViewItem>(
    `/saved-views/${id}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function deleteSavedView(id: string): Promise<void> {
  await apiClient.delete(`/saved-views/${id}`);
}

export async function getTask(id: string): Promise<SyncTask> {
  const response = await apiClient.get<ApiEnvelope<SyncTask> | SyncTask>(`/tasks/${id}`);

  return unwrapResponse(response.data);
}

export async function retryTask(id: string): Promise<SyncTask> {
  const response = await apiClient.post<ApiEnvelope<SyncTask> | SyncTask>(`/tasks/${id}/retry`);

  return unwrapResponse(response.data);
}

export const VERSION_SWITCH_RECONCILE_REASON_MAX_LENGTH = 1000;

export interface VersionSwitchReconcilePayload {
  reason: string;
}

export async function reconcileVersionSwitchTask(
  id: string,
  payload: VersionSwitchReconcilePayload,
): Promise<SyncTask> {
  const reason = payload.reason.trim();
  if (!reason || reason.length > VERSION_SWITCH_RECONCILE_REASON_MAX_LENGTH) {
    throw new Error("人工协调原因必须为 1 至 1000 个字符");
  }
  const response = await apiClient.post<ApiEnvelope<SyncTask> | SyncTask>(
    `/tasks/${id}/reconcile-version-switch`,
    { reason },
  );
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

// ── Notification API functions ────────────────────────────────────────────────

export async function listNotifications(
  params: NotificationListQuery = {},
): Promise<NotificationListResponse> {
  const response = await apiClient.get<ApiEnvelope<NotificationListResponse>>("/notifications", {
    params,
  });

  return unwrapResponse(response.data);
}

export async function markNotificationRead(notificationId: string): Promise<NotificationItem> {
  const response = await apiClient.post<ApiEnvelope<NotificationItem>>(
    `/notifications/${notificationId}/read`,
  );

  return unwrapResponse(response.data);
}

export async function markAllNotificationsRead(): Promise<{ updated_count: number }> {
  const response =
    await apiClient.post<ApiEnvelope<{ updated_count: number }>>("/notifications/read-all");

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
  page?: number;
  page_size?: number;
}

export interface DepartmentListQuery {
  page?: number;
  page_size?: number;
  search?: string;
  status?: Department["status"];
}

export interface DepartmentPayload {
  name: string;
  code: string;
}

export interface DepartmentUpdatePayload {
  name?: string;
  status?: Department["status"];
}

export interface ManagedDepartmentsResponse {
  user_id: string;
  managed_department_ids?: string[];
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

export async function setUserDepartment(id: string, departmentId: string): Promise<UserProfile> {
  const response = await apiClient.patch<ApiEnvelope<UserProfile> | UserProfile>(
    `/users/${id}/department`,
    { department_id: departmentId },
  );

  return unwrapResponse(response.data);
}

export async function listDepartments(
  params: DepartmentListQuery = {},
): Promise<DepartmentListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<DepartmentListResponse> | DepartmentListResponse
  >("/admin/departments", { params });

  return unwrapResponse(response.data);
}

export async function getDepartment(id: string): Promise<Department> {
  const response = await apiClient.get<ApiEnvelope<Department> | Department>(
    `/admin/departments/${id}`,
  );

  return unwrapResponse(response.data);
}

export async function createDepartment(payload: DepartmentPayload): Promise<Department> {
  const response = await apiClient.post<ApiEnvelope<Department> | Department>(
    "/admin/departments",
    payload,
  );

  return unwrapResponse(response.data);
}

export async function updateDepartment(
  id: string,
  payload: DepartmentUpdatePayload,
): Promise<Department> {
  const response = await apiClient.patch<ApiEnvelope<Department> | Department>(
    `/admin/departments/${id}`,
    payload,
  );

  return unwrapResponse(response.data);
}

export async function disableDepartment(id: string): Promise<void> {
  const response = await apiClient.delete<ApiEnvelope<Record<string, never>>>(
    `/admin/departments/${id}`,
  );

  unwrapResponse(response.data);
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

export type DependencyStatus = "ok" | "error";

export interface DependencyHealth {
  status: DependencyStatus;
  detail?: string;
}

export interface SystemReadiness {
  status: DependencyStatus;
  dependencies: Record<string, DependencyHealth>;
}

export interface SystemHealth {
  status: string;
}

export function getApiBaseUrl(): string {
  return apiClient.defaults.baseURL ?? "/api";
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const response = await apiClient.get<ApiEnvelope<SystemHealth> | SystemHealth>("/system/health");

  return unwrapResponse(response.data);
}

/**
 * 系统就绪探针（database / redis / rabbitmq / minio 真实健康）。
 * 后端在依赖异常时返回 503，但仍带结构化 body，因此放行 503 以便展示具体异常项。
 */
export async function getSystemReadiness(): Promise<SystemReadiness> {
  const response = await apiClient.get<SystemReadiness>("/system/ready", {
    validateStatus: (status) => status === 200 || status === 503,
  });

  return response.data;
}

export async function listDocumentOwnerOptions(
  params: { q?: string; page?: number; page_size?: number } = {},
): Promise<DocumentOwnerOptionListResponse> {
  const response = await apiClient.get<
    ApiEnvelope<DocumentOwnerOptionListResponse> | DocumentOwnerOptionListResponse
  >("/files/owner-options", { params });

  return unwrapResponse(response.data);
}

export async function updateDocumentDraft(
  id: string,
  payload: UpdateDocumentDraftPayload,
): Promise<KnowledgeFile> {
  const response = await apiClient.patch<ApiEnvelope<KnowledgeFile> | KnowledgeFile>(
    `/files/${id}`,
    payload,
  );

  return unwrapResponse(response.data);
}
