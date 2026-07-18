import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE,
  type KnowledgeFile,
  apiClient,
  getDocumentContent,
  getGovernanceCapacity,
  getGovernanceLlmUsage,
  getGovernanceRagflowUsage,
  getUploadPolicy,
  getUserFacingErrorMessage,
  listDocumentOwnerOptions,
  listResponsibleDocuments,
  reconcileVersionSwitchTask,
  updateDocumentDraft,
  uploadDocument,
} from "./client";
import { SESSION_SUPERSEDED_CODE, captureAuthSessionIdentity } from "../sessionIdentity";
import { type CurrentUser, useAuthStore } from "../store/auth.store";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const firstUser: CurrentUser = {
  id: "employee-1",
  name: "张三",
  email: "first@example.com",
  role: "employee",
  department_id: "dept-1",
  email_verified: true,
  department_assigned: true,
  department_code: "tech",
};

const secondUser: CurrentUser = {
  ...firstUser,
  id: "employee-2",
  name: "李四",
  email: "second@example.com",
};

describe("getUserFacingErrorMessage", () => {
  it("maps missing department to an actionable message", () => {
    const error = new ApiError("forbidden", {
      status: 403,
      code: "DEPARTMENT_ASSIGNMENT_REQUIRED",
    });

    expect(getUserFacingErrorMessage(error, "提交失败")).toBe(
      DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE,
    );
  });

  it("keeps email verification separate from department assignment", () => {
    const error = new ApiError("请先验证邮箱", {
      status: 403,
      code: "EMAIL_NOT_VERIFIED",
    });

    expect(getUserFacingErrorMessage(error, "登录失败")).toBe("请先验证邮箱");
  });
});

describe("getUploadPolicy", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests the canonical files policy endpoint", async () => {
    const policy = {
      allowed_extensions: ["pdf", "docx"],
      allow_multi_file: true,
      upload_enabled: true,
      max_file_size_mb: 200,
      allow_user_delete: false,
    };
    const get = vi.spyOn(apiClient, "get").mockResolvedValue({
      data: {
        success: true,
        data: policy,
        message: "ok",
      },
    } as never);

    await expect(getUploadPolicy()).resolves.toEqual(policy);
    expect(get).toHaveBeenCalledOnce();
    expect(get).toHaveBeenCalledWith("/files/policy");
  });
});

describe("governance metrics API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the three system-admin statistics endpoints with explicit UTC and pagination params", async () => {
    const capacity = { basis: "database_file_rows_uploaded_in_window", items: [] };
    const llm = { basis: "ai_usage_logs_created_in_window", items: [] };
    const ragflow = { basis: "ragflow_api_calls_started_in_window", items: [] };
    const get = vi
      .spyOn(apiClient, "get")
      .mockResolvedValueOnce({
        data: { success: true, data: capacity, message: "ok" },
      } as never)
      .mockResolvedValueOnce({
        data: { success: true, data: llm, message: "ok" },
      } as never)
      .mockResolvedValueOnce({
        data: { success: true, data: ragflow, message: "ok" },
      } as never);

    const window = {
      start_at: "2026-06-01T00:00:00.000Z",
      end_before: "2026-07-01T00:00:00.000Z",
      page: 2,
      page_size: 5,
    };

    await expect(
      getGovernanceCapacity({
        ...window,
        group_by: "processing_stage",
        physical_dimension: "cluster",
      }),
    ).resolves.toEqual(capacity);
    await expect(getGovernanceLlmUsage({ ...window, group_by: "provider" })).resolves.toEqual(llm);
    await expect(getGovernanceRagflowUsage({ ...window, group_by: "result" })).resolves.toEqual(
      ragflow,
    );

    expect(get).toHaveBeenNthCalledWith(1, "/admin/statistics/capacity", {
      params: {
        ...window,
        group_by: "processing_stage",
        physical_dimension: "cluster",
      },
    });
    expect(get).toHaveBeenNthCalledWith(2, "/admin/statistics/llm-usage", {
      params: { ...window, group_by: "provider" },
    });
    expect(get).toHaveBeenNthCalledWith(3, "/admin/statistics/ragflow-usage", {
      params: { ...window, group_by: "result" },
    });
  });
});

describe("document governance API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("serializes the replacement predecessor in multipart upload", async () => {
    const uploaded = { id: "new-file" } as KnowledgeFile;
    const post = vi.spyOn(apiClient, "post").mockResolvedValue({
      data: { success: true, data: uploaded, message: "ok" },
    } as never);

    await uploadDocument({
      file: new File(["new"], "new.pdf", { type: "application/pdf" }),
      visibility: "private",
      replacesFileId: "old-file",
    });

    expect(post).toHaveBeenCalledOnce();
    expect(post.mock.calls[0][0]).toBe("/files/upload");
    const formData = post.mock.calls[0][1] as FormData;
    expect(formData.get("replaces_file_id")).toBe("old-file");
    expect(formData.get("file")).toBeInstanceOf(File);
  });

  it("uses scoped owner options and optimistic draft metadata endpoints", async () => {
    const owners = {
      items: [{ id: "owner-1", name: "李四" }],
      total: 21,
      page: 2,
      page_size: 20,
      total_pages: 2,
    };
    const get = vi.spyOn(apiClient, "get").mockResolvedValue({
      data: { success: true, data: owners, message: "ok" },
    } as never);

    await expect(listDocumentOwnerOptions({ q: "李", page: 2, page_size: 20 })).resolves.toEqual(
      owners,
    );
    expect(get).toHaveBeenCalledWith("/files/owner-options", {
      params: { q: "李", page: 2, page_size: 20 },
    });

    const updated = { id: "file-1" } as KnowledgeFile;
    const patch = vi.spyOn(apiClient, "patch").mockResolvedValue({
      data: { success: true, data: updated, message: "ok" },
    } as never);
    await updateDocumentDraft("file-1", {
      expected_version: 3,
      owner_id: "owner-1",
      expires_at: null,
    });

    expect(patch).toHaveBeenCalledWith("/files/file-1", {
      expected_version: 3,
      owner_id: "owner-1",
      expires_at: null,
    });
  });
  it("trims and posts the required manual version-switch reconciliation reason", async () => {
    const task = { id: "task-1" };
    const post = vi.spyOn(apiClient, "post").mockResolvedValue({
      data: { success: true, data: task, message: "ok" },
    } as never);

    await expect(
      reconcileVersionSwitchTask("task-1", { reason: "  已核对远端版本  " }),
    ).resolves.toEqual(task);
    expect(post).toHaveBeenCalledWith("/tasks/task-1/reconcile-version-switch", {
      reason: "已核对远端版本",
    });
    await expect(reconcileVersionSwitchTask("task-1", { reason: "   " })).rejects.toThrow(
      "人工协调原因必须为 1 至 1000 个字符",
    );
    await expect(
      reconcileVersionSwitchTask("task-1", { reason: "x".repeat(1001) }),
    ).rejects.toThrow("人工协调原因必须为 1 至 1000 个字符");
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("uses the delegated-responsibility listing endpoint with governance filters", async () => {
    const response = {
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      total_pages: 0,
    };
    const get = vi.spyOn(apiClient, "get").mockResolvedValue({
      data: { success: true, data: response, message: "ok" },
    } as never);

    await expect(
      listResponsibleDocuments({
        page: 1,
        page_size: 20,
        q: "制度",
        expiry_status: "expiring",
        sort: "updated_at",
        order: "desc",
      }),
    ).resolves.toEqual(response);
    expect(get).toHaveBeenCalledWith("/files/responsible", {
      params: {
        page: 1,
        page_size: 20,
        q: "制度",
        expiry_status: "expiring",
        sort: "updated_at",
        order: "desc",
      },
    });
  });
});

describe("API session identity guard", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: "token-a", user: firstUser });
  });

  afterEach(() => {
    useAuthStore.setState({ accessToken: null, user: null });
    vi.clearAllMocks();
  });

  it("rejects a late Axios success from session A without exposing it to session B", async () => {
    const responseDeferred = createDeferred<AxiosResponse>();
    let requestConfig: InternalAxiosRequestConfig | undefined;
    const pending = apiClient.get("/session-race-success", {
      adapter: async (config) => {
        requestConfig = config;
        return responseDeferred.promise;
      },
    });

    await vi.waitFor(() => expect(requestConfig).toBeDefined());
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    responseDeferred.resolve({
      data: { success: true, data: { owner: "A" }, message: "ok" },
      status: 200,
      statusText: "OK",
      headers: {},
      config: requestConfig!,
    });

    await rejection;
    expect(useAuthStore.getState()).toMatchObject({
      accessToken: "token-b",
      user: { id: "employee-2" },
    });
  });

  it("rejects a late Axios 401 from session A without clearing session B", async () => {
    const responseDeferred = createDeferred<AxiosResponse>();
    let requestConfig: InternalAxiosRequestConfig | undefined;
    const pending = apiClient.get("/session-race-401", {
      adapter: async (config) => {
        requestConfig = config;
        return responseDeferred.promise;
      },
    });

    await vi.waitFor(() => expect(requestConfig).toBeDefined());
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    const errorResponse: AxiosResponse = {
      data: { success: false, data: null, message: "A 已过期" },
      status: 401,
      statusText: "Unauthorized",
      headers: {},
      config: requestConfig!,
    };
    responseDeferred.reject(
      new AxiosError("Unauthorized", "ERR_BAD_REQUEST", requestConfig, undefined, errorResponse),
    );

    await rejection;
    expect(useAuthStore.getState()).toMatchObject({
      accessToken: "token-b",
      user: { id: "employee-2" },
    });
  });

  it("keeps only a numeric generation on error.config and rejects an A-to-B retry before the adapter", async () => {
    let adapterError: AxiosError | undefined;
    let requestConfig: InternalAxiosRequestConfig | undefined;
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      requestConfig = config;
      adapterError = new AxiosError("temporary failure", "ERR_NETWORK", config);
      throw adapterError;
    });

    await expect(
      apiClient.get("/session-bound-retry", {
        adapter,
      }),
    ).rejects.toBeInstanceOf(ApiError);

    expect(adapter).toHaveBeenCalledOnce();
    expect(requestConfig).toBeDefined();
    const sessionEntries = Object.entries(requestConfig!).filter(([key]) =>
      key.toLowerCase().includes("session"),
    );
    expect(sessionEntries).toEqual([["__knowledgeUploaderSessionGeneration", expect.any(Number)]]);
    expect(Object.getOwnPropertySymbols(requestConfig!)).toEqual([]);

    const retryConfig = adapterError?.config;
    if (!retryConfig) {
      throw new Error("adapter error did not retain its request config");
    }

    useAuthStore.setState({ accessToken: "token-b", user: secondUser });

    await expect(apiClient(retryConfig)).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    expect(adapter).toHaveBeenCalledOnce();
  });
  it("allows a same-session multipart retry and preserves its bound generation", async () => {
    let failedConfig: InternalAxiosRequestConfig | undefined;
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      if (!failedConfig) {
        failedConfig = config;
        throw new AxiosError("temporary multipart failure", "ERR_NETWORK", config);
      }
      return {
        data: { success: true, data: { uploaded: true }, message: "ok" },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      };
    });
    const formData = new FormData();
    formData.append("file", new File(["same"], "same-session.pdf", { type: "application/pdf" }));

    await expect(
      apiClient.post("/same-session-multipart-retry", formData, { adapter }),
    ).rejects.toBeInstanceOf(ApiError);
    if (!failedConfig) {
      throw new Error("multipart adapter did not retain its first config");
    }
    const firstGeneration = (
      failedConfig as InternalAxiosRequestConfig & {
        __knowledgeUploaderSessionGeneration?: number;
      }
    ).__knowledgeUploaderSessionGeneration;

    const response = await apiClient(failedConfig);

    expect(response.data).toMatchObject({ data: { uploaded: true } });
    expect(adapter).toHaveBeenCalledTimes(2);
    const retryConfig = vi.mocked(adapter).mock.calls[1][0];
    expect(retryConfig.data).toBeInstanceOf(FormData);
    expect((retryConfig.data as FormData).get("file")).toBeInstanceOf(File);
    expect(
      (
        retryConfig as InternalAxiosRequestConfig & {
          __knowledgeUploaderSessionGeneration?: number;
        }
      ).__knowledgeUploaderSessionGeneration,
    ).toBe(firstGeneration);
    expect(retryConfig.headers.Authorization).toBe("Bearer token-a");
  });

  it("clears the current session on a current-generation Axios 401", async () => {
    const previousPath = window.location.pathname;
    window.history.replaceState({}, "", "/login");
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response: AxiosResponse = {
        data: { success: false, data: null, message: "当前登录已过期" },
        status: 401,
        statusText: "Unauthorized",
        headers: {},
        config,
      };
      throw new AxiosError("Unauthorized", "ERR_BAD_REQUEST", config, undefined, response);
    });

    try {
      await expect(apiClient.get("/current-session-401", { adapter })).rejects.toMatchObject({
        name: "ApiError",
        status: 401,
      });
      expect(useAuthStore.getState()).toMatchObject({
        accessToken: null,
        user: null,
      });
      expect(adapter).toHaveBeenCalledOnce();
    } finally {
      window.history.replaceState({}, "", previousPath);
    }
  });

  it("cancels a late raw-fetch 401 from session A without clearing session B", async () => {
    const responseDeferred = createDeferred<Response>();
    const cancelBody = vi.fn().mockResolvedValue(undefined);
    const fetchImpl = vi.fn(() => responseDeferred.promise) as unknown as typeof fetch;
    const pending = getDocumentContent("document-a", "inline", {
      maxBytes: 5,
      fetchImpl,
    });

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    responseDeferred.resolve({
      ok: false,
      status: 401,
      headers: new Headers(),
      body: { cancel: cancelBody },
    } as unknown as Response);

    await rejection;
    expect(cancelBody).toHaveBeenCalledOnce();
    expect(useAuthStore.getState()).toMatchObject({
      accessToken: "token-b",
      user: { id: "employee-2" },
    });
  });

  it("rejects a raw-fetch 200 that finishes reading after the session changes", async () => {
    const firstRead = createDeferred<ReadableStreamReadResult<Uint8Array>>();
    let readCount = 0;
    const read = vi.fn((): Promise<ReadableStreamReadResult<Uint8Array>> => {
      readCount += 1;
      return readCount === 1
        ? firstRead.promise
        : Promise.resolve({ done: true, value: undefined });
    });
    const releaseLock = vi.fn();
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/pdf" }),
      body: {
        getReader: vi.fn().mockReturnValue({
          read,
          cancel: vi.fn().mockResolvedValue(undefined),
          releaseLock,
        }),
        cancel: vi.fn().mockResolvedValue(undefined),
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;
    const pending = getDocumentContent("document-a", "inline", {
      maxBytes: 5,
      fetchImpl,
    });

    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    firstRead.resolve({ done: false, value: new Uint8Array([1, 2, 3]) });

    await rejection;
    expect(releaseLock).toHaveBeenCalledOnce();
    expect(useAuthStore.getState()).toMatchObject({
      accessToken: "token-b",
      user: { id: "employee-2" },
    });
  });
  it("aborts an in-flight fetch and reclassifies its rejection after session replacement", async () => {
    const responseDeferred = createDeferred<Response>();
    const fetchImpl = vi.fn(() => responseDeferred.promise) as unknown as typeof fetch;
    const pending = getDocumentContent("document-a", "inline", {
      maxBytes: 5,
      fetchImpl,
    });

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    const signal = (vi.mocked(fetchImpl).mock.calls[0]?.[1] as RequestInit).signal;
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });

    expect(signal?.aborted).toBe(true);
    responseDeferred.reject(new DOMException("aborted", "AbortError"));
    await rejection;
  });

  it("rejects a direct ABA session race without reading another preview chunk", async () => {
    const firstRead = createDeferred<ReadableStreamReadResult<Uint8Array>>();
    const cancelReader = vi.fn().mockResolvedValue(undefined);
    const read = vi.fn(() => firstRead.promise);
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/pdf" }),
      body: {
        getReader: vi.fn().mockReturnValue({
          read,
          cancel: cancelReader,
          releaseLock: vi.fn(),
        }),
        cancel: vi.fn().mockResolvedValue(undefined),
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;
    const pending = getDocumentContent("document-a", "inline", {
      maxBytes: 5,
      fetchImpl,
    });

    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    const signal = (vi.mocked(fetchImpl).mock.calls[0]?.[1] as RequestInit).signal;
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    useAuthStore.setState({ accessToken: "token-a", user: firstUser });

    expect(signal?.aborted).toBe(true);
    firstRead.resolve({ done: false, value: new Uint8Array([1, 2, 3]) });
    await rejection;
    expect(read).toHaveBeenCalledOnce();
    expect(cancelReader).toHaveBeenCalledWith(
      expect.objectContaining({ code: SESSION_SUPERSEDED_CODE }),
    );
  });
  it("aborts an in-flight preview on a same-token role downgrade", async () => {
    useAuthStore.setState({
      accessToken: "same-token",
      user: { ...firstUser, role: "system_admin" },
    });
    const responseDeferred = createDeferred<Response>();
    const fetchImpl = vi.fn(() => responseDeferred.promise) as unknown as typeof fetch;
    const pending = getDocumentContent("document-a", "inline", {
      maxBytes: 5,
      fetchImpl,
    });

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    const signal = (vi.mocked(fetchImpl).mock.calls[0]?.[1] as RequestInit).signal;
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });

    useAuthStore.setState({ accessToken: "same-token", user: firstUser });

    expect(signal?.aborted).toBe(true);
    responseDeferred.reject(new DOMException("aborted", "AbortError"));
    await rejection;
  });

  it.each(["A→B", "ABA"] as const)(
    "rejects a stale multipart upload before an adapter can apply later credentials on %s",
    async (switchMode) => {
      const requestIdentity = captureAuthSessionIdentity();
      const previousAdapter = apiClient.defaults.adapter;
      const adapter = vi.fn();
      apiClient.defaults.adapter = adapter;

      try {
        useAuthStore.setState({ accessToken: "token-b", user: secondUser });
        if (switchMode === "ABA") {
          useAuthStore.setState({ accessToken: "token-a", user: firstUser });
        }

        await expect(
          uploadDocument(
            {
              file: new File(["old"], "session-a.pdf", { type: "application/pdf" }),
              visibility: "private",
            },
            undefined,
            { requestIdentity },
          ),
        ).rejects.toMatchObject({ code: SESSION_SUPERSEDED_CODE });

        expect(adapter).not.toHaveBeenCalled();
        expect(useAuthStore.getState().accessToken).toBe(
          switchMode === "ABA" ? "token-a" : "token-b",
        );
      } finally {
        apiClient.defaults.adapter = previousAdapter;
      }
    },
  );
});
describe("getDocumentContent bounded streaming", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: "preview-token" });
  });

  afterEach(() => {
    useAuthStore.setState({ accessToken: null, user: null });
    vi.clearAllMocks();
  });

  it.each([401, 500])("cancels an HTTP %s body before preserving the API error", async (status) => {
    const cancelBody = vi.fn(async (_reason: unknown) => {
      expect(_reason).toMatchObject({ name: "ApiError", status });
      expect(useAuthStore.getState().accessToken).toBe("preview-token");
      throw new Error("取消响应体失败");
    });
    const response = {
      ok: false,
      status,
      headers: new Headers(),
      body: {
        cancel: cancelBody,
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;

    await expect(
      getDocumentContent("document-1", "inline", {
        maxBytes: 5,
        fetchImpl,
      }),
    ).rejects.toMatchObject({
      name: "ApiError",
      message: `原件读取失败（HTTP ${status}）`,
      status,
    });

    expect(cancelBody).toHaveBeenCalledTimes(1);
    expect(cancelBody.mock.calls[0][0]).toMatchObject({
      name: "ApiError",
      status,
    });
    expect(useAuthStore.getState().accessToken).toBe(status === 401 ? null : "preview-token");
  });

  it("rejects a declared oversized body without acquiring or reading a reader", async () => {
    const getReader = vi.fn();
    const cancelBody = vi.fn().mockResolvedValue(undefined);
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({
        "content-length": "6",
        "content-type": "application/pdf",
      }),
      body: {
        getReader,
        cancel: cancelBody,
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;

    await expect(
      getDocumentContent("document/unsafe", "inline", {
        maxBytes: 5,
        fetchImpl,
      }),
    ).rejects.toThrow("安全预览上限");

    expect(getReader).not.toHaveBeenCalled();
    expect(cancelBody).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/files/document%2Funsafe/content?disposition=inline",
      expect.objectContaining({
        credentials: "same-origin",
        headers: expect.objectContaining({
          Authorization: "Bearer preview-token",
        }),
      }),
    );
  });

  it("cancels the body when reader acquisition fails without hiding that failure", async () => {
    const readerError = new Error("无法获取预览 reader");
    const cancelBody = vi.fn(async () => {
      throw new Error("取消响应体失败");
    });
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/pdf" }),
      body: {
        getReader: vi.fn(() => {
          throw readerError;
        }),
        cancel: cancelBody,
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;

    await expect(
      getDocumentContent("document-1", "inline", {
        maxBytes: 5,
        fetchImpl,
      }),
    ).rejects.toBe(readerError);

    expect(cancelBody).toHaveBeenCalledWith(readerError);
  });

  it.each([
    ["missing Content-Length", undefined],
    ["deceptively small Content-Length", "4"],
  ])("cancels %s when streamed bytes cross the bound", async (_caseName, declaredLength) => {
    const read = vi
      .fn()
      .mockResolvedValueOnce({ done: false, value: new Uint8Array([1, 2, 3]) })
      .mockResolvedValueOnce({ done: false, value: new Uint8Array([4, 5, 6]) });
    const cancelReader = vi.fn().mockResolvedValue(undefined);
    const releaseLock = vi.fn();
    const getReader = vi.fn().mockReturnValue({
      read,
      cancel: cancelReader,
      releaseLock,
    });
    const headers = new Headers({ "content-type": "application/pdf" });
    if (declaredLength !== undefined) {
      headers.set("content-length", declaredLength);
    }
    const response = {
      ok: true,
      status: 200,
      headers,
      body: {
        getReader,
        cancel: vi.fn(),
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;

    await expect(
      getDocumentContent("document-1", "inline", {
        maxBytes: 5,
        fetchImpl,
      }),
    ).rejects.toThrow("安全预览上限");

    expect(read).toHaveBeenCalledTimes(2);
    expect(cancelReader).toHaveBeenCalledTimes(1);
    expect(releaseLock).toHaveBeenCalledTimes(1);
  });
});
