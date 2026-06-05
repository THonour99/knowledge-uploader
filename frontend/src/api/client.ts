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
