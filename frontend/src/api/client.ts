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

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  timeout: 15000,
  withCredentials: true,
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
