import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE,
  apiClient,
  getDocumentContent,
  getUploadPolicy,
  getUserFacingErrorMessage,
} from "./client";
import { useAuthStore } from "../store/auth.store";

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

describe("getDocumentContent bounded streaming", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: "preview-token" });
  });

  afterEach(() => {
    useAuthStore.setState({ accessToken: null, user: null });
    vi.clearAllMocks();
  });

  it.each([401, 500])(
    "cancels an HTTP %s body before preserving the API error",
    async (status) => {
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
      expect(useAuthStore.getState().accessToken).toBe(
        status === 401 ? null : "preview-token",
      );
    },
  );

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
