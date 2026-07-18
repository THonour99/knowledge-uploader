import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SESSION_SUPERSEDED_CODE } from "../sessionIdentity";
import { type CurrentUser, useAuthStore } from "../store/auth.store";
import {
  DownloadCapabilityError,
  SAFE_BUFFERED_DOWNLOAD_MAX_BYTES,
  downloadDocument,
  fileNameFromContentDisposition,
} from "./documentDownload";

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
  id: "employee-a",
  name: "甲用户",
  email: "a@example.com",
  role: "employee",
  department_id: "dept-1",
  email_verified: true,
  department_assigned: true,
  department_code: "tech",
};

const secondUser: CurrentUser = {
  ...firstUser,
  id: "employee-b",
  name: "乙用户",
  email: "b@example.com",
};

function attachmentResponse(body: BodyInit, fileName = "server.pdf"): Response {
  return new Response(body, {
    status: 200,
    headers: { "content-disposition": `attachment; filename="${fileName}"` },
  });
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "download-token", user: firstUser });
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(() => "blob:download"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("downloadDocument", () => {
  it("streams chunks to the file handle with bearer authentication", async () => {
    const writes: Uint8Array[] = [];
    const writable = {
      write: vi.fn(async (value: Uint8Array) => {
        writes.push(value);
      }),
      close: vi.fn(async () => undefined),
      abort: vi.fn(async () => undefined),
    };
    const fetchImpl = vi.fn(async () => attachmentResponse("streamed document"));
    const picker = vi.fn(async () => ({ createWritable: async () => writable }));

    await expect(
      downloadDocument({
        id: "file/1",
        fileName: "员工手册.pdf",
        sizeBytes: 500 * 1024 * 1024,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: picker,
      }),
    ).resolves.toBe("streamed");

    expect(fetchImpl).toHaveBeenCalledWith(
      expect.stringContaining("/files/file%2F1/content?disposition=attachment"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer download-token" }),
      }),
    );
    expect(writes.length).toBeGreaterThan(0);
    expect(writable.close).toHaveBeenCalledTimes(1);
  });
  it("aborts the file and cancels the response reader when the second chunk write fails", async () => {
    const diskError = new Error("磁盘写入失败");
    const writable = {
      write: vi.fn(async (value: Uint8Array): Promise<void> => {
        if (value[0] === 2) {
          throw diskError;
        }
      }),
      close: vi.fn(async () => undefined),
      abort: vi.fn(async () => undefined),
    };
    let readCount = 0;
    const reader = {
      read: vi.fn(async () => {
        readCount += 1;
        return {
          done: false as const,
          value: new Uint8Array([readCount]),
        };
      }),
      cancel: vi.fn(async () => {
        throw new Error("取消流失败");
      }),
      releaseLock: vi.fn(),
    };
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({ "content-disposition": 'attachment; filename="server.pdf"' }),
      body: { getReader: () => reader },
    } as unknown as Response;
    const fetchImpl = vi.fn(async () => response);
    const picker = vi.fn(async () => ({ createWritable: async () => writable }));

    await expect(
      downloadDocument({
        id: "file-1",
        fileName: "server.pdf",
        sizeBytes: 1024,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: picker,
      }),
    ).rejects.toBe(diskError);

    expect(writable.write).toHaveBeenCalledTimes(2);
    expect(writable.abort).toHaveBeenCalledWith(diskError);
    expect(reader.cancel).toHaveBeenCalledWith(diskError);
    expect(reader.releaseLock).toHaveBeenCalledTimes(1);
    expect(writable.close).not.toHaveBeenCalled();
  });

  it("fails before the network for a large file when streaming is unavailable", async () => {
    const fetchImpl = vi.fn();

    await expect(
      downloadDocument({
        id: "file-1",
        fileName: "large.pdf",
        sizeBytes: SAFE_BUFFERED_DOWNLOAD_MAX_BYTES + 1,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: undefined,
      }),
    ).rejects.toBeInstanceOf(DownloadCapabilityError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("uses a bounded Blob fallback and the attachment filename for small files", async () => {
    vi.useFakeTimers();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const fetchImpl = vi.fn(async () => attachmentResponse("small", "server-safe.pdf"));

    await expect(
      downloadDocument({
        id: "file-1",
        fileName: "client.pdf",
        sizeBytes: 5,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: undefined,
      }),
    ).resolves.toBe("buffered");

    expect(click).toHaveBeenCalledTimes(1);
    vi.runOnlyPendingTimers();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:download");
  });

  it("does not acquire a reader when the fallback response declares an oversized body", async () => {
    const getReader = vi.fn();
    const cancelBody = vi.fn().mockResolvedValue(undefined);
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({
        "content-disposition": 'attachment; filename="oversized.pdf"',
        "content-length": String(SAFE_BUFFERED_DOWNLOAD_MAX_BYTES + 1),
      }),
      body: {
        getReader,
        cancel: cancelBody,
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response);

    await expect(
      downloadDocument({
        id: "file-1",
        fileName: "client.pdf",
        sizeBytes: 1,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: undefined,
      }),
    ).rejects.toBeInstanceOf(DownloadCapabilityError);

    expect(getReader).not.toHaveBeenCalled();
    expect(cancelBody).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("cancels an unbounded fallback stream as soon as actual bytes exceed the limit", async () => {
    const oversizedChunk = {
      byteLength: SAFE_BUFFERED_DOWNLOAD_MAX_BYTES + 1,
    } as Uint8Array;
    const reader = {
      read: vi.fn().mockResolvedValueOnce({ done: false, value: oversizedChunk }),
      cancel: vi.fn().mockResolvedValue(undefined),
      releaseLock: vi.fn(),
    };
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({
        "content-disposition": 'attachment; filename="oversized.pdf"',
      }),
      body: {
        getReader: vi.fn().mockReturnValue(reader),
        cancel: vi.fn(),
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response);

    await expect(
      downloadDocument({
        id: "file-1",
        fileName: "client.pdf",
        sizeBytes: 1,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: undefined,
      }),
    ).rejects.toBeInstanceOf(DownloadCapabilityError);

    expect(reader.read).toHaveBeenCalledTimes(1);
    expect(reader.cancel).toHaveBeenCalledTimes(1);
    expect(reader.releaseLock).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it.each([
    ["streamed", 500, 'attachment; filename="server.pdf"', "原件下载失败（HTTP 500）"],
    ["streamed", 200, null, "服务器未返回安全的附件下载响应"],
    ["buffered", 500, 'attachment; filename="server.pdf"', "原件下载失败（HTTP 500）"],
    ["buffered", 200, null, "服务器未返回安全的附件下载响应"],
  ] as const)(
    "cancels a %s response before returning the validation error",
    async (mode, status, disposition, expectedMessage) => {
      const getReader = vi.fn();
      const cancelBody = vi.fn(async () => {
        throw new Error("取消响应体失败");
      });
      const headers = new Headers();
      if (disposition) {
        headers.set("content-disposition", disposition);
      }
      const response = {
        ok: status >= 200 && status < 300,
        status,
        headers,
        body: {
          getReader,
          cancel: cancelBody,
        },
      } as unknown as Response;
      const fetchImpl = vi.fn().mockResolvedValue(response);
      const writable = {
        write: vi.fn(async () => undefined),
        close: vi.fn(async () => undefined),
        abort: vi.fn(async () => {
          throw new Error("中止文件失败");
        }),
      };
      const picker = vi.fn(async () => ({ createWritable: async () => writable }));

      await expect(
        downloadDocument({
          id: "file-1",
          fileName: "client.pdf",
          sizeBytes: 6,
          fetchImpl: fetchImpl as typeof fetch,
          saveFilePicker: mode === "streamed" ? picker : undefined,
        }),
      ).rejects.toThrow(expectedMessage);

      expect(cancelBody).toHaveBeenCalledTimes(1);
      expect(getReader).not.toHaveBeenCalled();
      if (mode === "streamed") {
        expect(writable.abort).toHaveBeenCalledTimes(1);
        expect(writable.close).not.toHaveBeenCalled();
      } else {
        expect(picker).not.toHaveBeenCalled();
        expect(writable.abort).not.toHaveBeenCalled();
      }
    },
  );

  it.each(["streamed", "buffered"] as const)(
    "cancels the %s body when reader acquisition fails",
    async (mode) => {
      const readerError = new Error("无法获取下载 reader");
      const cancelBody = vi.fn(async () => {
        throw new Error("取消响应体失败");
      });
      const response = {
        ok: true,
        status: 200,
        headers: new Headers({
          "content-disposition": 'attachment; filename="server.pdf"',
        }),
        body: {
          getReader: vi.fn(() => {
            throw readerError;
          }),
          cancel: cancelBody,
        },
      } as unknown as Response;
      const fetchImpl = vi.fn().mockResolvedValue(response);
      const writable = {
        write: vi.fn(async () => undefined),
        close: vi.fn(async () => undefined),
        abort: vi.fn(async () => undefined),
      };
      const picker = vi.fn(async () => ({ createWritable: async () => writable }));

      await expect(
        downloadDocument({
          id: "file-1",
          fileName: "client.pdf",
          sizeBytes: 6,
          fetchImpl: fetchImpl as typeof fetch,
          saveFilePicker: mode === "streamed" ? picker : undefined,
        }),
      ).rejects.toBe(readerError);

      expect(cancelBody).toHaveBeenCalledWith(readerError);
      if (mode === "streamed") {
        expect(writable.abort).toHaveBeenCalledWith(readerError);
      } else {
        expect(writable.abort).not.toHaveBeenCalled();
      }
    },
  );

  it.each(["streamed", "buffered"] as const)(
    "preserves the current-session %s 401 error while clearing that session",
    async (mode) => {
      const cancelBody = vi.fn().mockResolvedValue(undefined);
      const response = {
        ok: false,
        status: 401,
        headers: new Headers({
          "content-disposition": 'attachment; filename="server.pdf"',
        }),
        body: { cancel: cancelBody },
      } as unknown as Response;
      const fetchImpl = vi.fn().mockResolvedValue(response);
      const writable = {
        write: vi.fn().mockResolvedValue(undefined),
        close: vi.fn().mockResolvedValue(undefined),
        abort: vi.fn().mockResolvedValue(undefined),
      };
      const picker = vi.fn().mockResolvedValue({
        createWritable: vi.fn().mockResolvedValue(writable),
      });

      await expect(
        downloadDocument({
          id: "file-a",
          fileName: "原件.pdf",
          sizeBytes: 6,
          fetchImpl: fetchImpl as typeof fetch,
          saveFilePicker: mode === "streamed" ? picker : undefined,
        }),
      ).rejects.toThrow("登录状态已失效，请重新登录后下载");

      expect(cancelBody).toHaveBeenCalledOnce();
      expect(useAuthStore.getState().accessToken).toBeNull();
      expect(URL.createObjectURL).not.toHaveBeenCalled();
    },
  );

  it.each(["streamed", "buffered"] as const)(
    "cancels a deferred %s 401 from session A without clearing session B",
    async (mode) => {
      const responseDeferred = createDeferred<Response>();
      const cancelBody = vi.fn().mockResolvedValue(undefined);
      const response = {
        ok: false,
        status: 401,
        headers: new Headers({
          "content-disposition": 'attachment; filename="server.pdf"',
        }),
        body: { cancel: cancelBody },
      } as unknown as Response;
      const fetchImpl = vi.fn(() => responseDeferred.promise) as unknown as typeof fetch;
      const writable = {
        write: vi.fn(async () => undefined),
        close: vi.fn(async () => undefined),
        abort: vi.fn(async () => undefined),
      };
      const picker = vi.fn(async () => ({ createWritable: async () => writable }));
      const pending = downloadDocument({
        id: "file-a",
        fileName: "甲用户原件.pdf",
        sizeBytes: 6,
        fetchImpl,
        saveFilePicker: mode === "streamed" ? picker : undefined,
      });
      const rejection = expect(pending).rejects.toMatchObject({
        code: SESSION_SUPERSEDED_CODE,
      });

      await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
      useAuthStore.setState({ accessToken: "token-b", user: secondUser });
      responseDeferred.resolve(response);

      await rejection;
      expect(cancelBody).toHaveBeenCalledWith(
        expect.objectContaining({ code: SESSION_SUPERSEDED_CODE }),
      );
      expect(useAuthStore.getState()).toMatchObject({
        accessToken: "token-b",
        user: { id: "employee-b" },
      });
      expect(writable.write).not.toHaveBeenCalled();
      expect(writable.close).not.toHaveBeenCalled();
      if (mode === "streamed") {
        expect(writable.abort).toHaveBeenCalledOnce();
      } else {
        expect(URL.createObjectURL).not.toHaveBeenCalled();
      }
    },
  );

  it("cancels an active stream before writing a chunk after the session changes", async () => {
    const firstRead = createDeferred<ReadableStreamReadResult<Uint8Array>>();
    const reader = {
      read: vi.fn(() => firstRead.promise),
      cancel: vi.fn().mockResolvedValue(undefined),
      releaseLock: vi.fn(),
    };
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({
        "content-disposition": 'attachment; filename="server.pdf"',
      }),
      body: {
        getReader: vi.fn().mockReturnValue(reader),
        cancel: vi.fn().mockResolvedValue(undefined),
      },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;
    const writable = {
      write: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
      abort: vi.fn(async () => undefined),
    };
    const picker = vi.fn(async () => ({ createWritable: async () => writable }));
    const pending = downloadDocument({
      id: "file-a",
      fileName: "甲用户原件.pdf",
      sizeBytes: 6,
      fetchImpl,
      saveFilePicker: picker,
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });

    await vi.waitFor(() => expect(reader.read).toHaveBeenCalledOnce());
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    firstRead.resolve({ done: false, value: new Uint8Array([1, 2, 3]) });

    await rejection;
    expect(writable.write).not.toHaveBeenCalled();
    expect(writable.close).not.toHaveBeenCalled();
    expect(writable.abort).toHaveBeenCalledWith(
      expect.objectContaining({ code: SESSION_SUPERSEDED_CODE }),
    );
    expect(reader.cancel).toHaveBeenCalledWith(
      expect.objectContaining({ code: SESSION_SUPERSEDED_CODE }),
    );
    expect(reader.releaseLock).toHaveBeenCalledOnce();
  });

  it("aborts a pending download when the department changes under the same token", async () => {
    const responseDeferred = createDeferred<Response>();
    const fetchImpl = vi.fn(() => responseDeferred.promise) as unknown as typeof fetch;
    const pending = downloadDocument({
      id: "file-a",
      fileName: "甲用户原件.pdf",
      sizeBytes: 6,
      fetchImpl,
      saveFilePicker: undefined,
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    const signal = (vi.mocked(fetchImpl).mock.calls[0]?.[1] as RequestInit).signal;
    useAuthStore.setState({
      accessToken: "download-token",
      user: { ...firstUser, department_id: "dept-2" },
    });

    expect(signal?.aborted).toBe(true);
    responseDeferred.reject(new DOMException("aborted", "AbortError"));
    await rejection;
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
  it.each(["picker", "create", "fetch", "read", "write", "close"] as const)(
    "reclassifies a stale %s rejection and suppresses completion effects",
    async (phase) => {
      const phaseDeferred = createDeferred<unknown>();
      const read = vi.fn(() =>
        phase === "read"
          ? (phaseDeferred.promise as Promise<ReadableStreamReadResult<Uint8Array>>)
          : Promise.resolve(
              phase === "write"
                ? { done: false as const, value: new Uint8Array([1]) }
                : { done: true as const, value: undefined },
            ),
      );
      const reader = {
        read,
        cancel: vi.fn().mockResolvedValue(undefined),
        releaseLock: vi.fn(),
      };
      const response = {
        ok: true,
        status: 200,
        headers: new Headers({
          "content-disposition": 'attachment; filename="server.pdf"',
        }),
        body: { getReader: vi.fn().mockReturnValue(reader) },
      } as unknown as Response;
      const writable = {
        write: vi.fn(() =>
          phase === "write" ? (phaseDeferred.promise as Promise<void>) : Promise.resolve(undefined),
        ),
        close: vi.fn(() =>
          phase === "close" ? (phaseDeferred.promise as Promise<void>) : Promise.resolve(undefined),
        ),
        abort: vi.fn().mockResolvedValue(undefined),
      };
      const createWritable = vi.fn(() =>
        phase === "create"
          ? (phaseDeferred.promise as Promise<typeof writable>)
          : Promise.resolve(writable),
      );
      const handle = { createWritable };
      const picker = vi.fn(() =>
        phase === "picker"
          ? (phaseDeferred.promise as Promise<typeof handle>)
          : Promise.resolve(handle),
      );
      const fetchImpl = vi.fn(() =>
        phase === "fetch"
          ? (phaseDeferred.promise as Promise<Response>)
          : Promise.resolve(response),
      );
      const pending = downloadDocument({
        id: "file-a",
        fileName: "甲用户原件.pdf",
        sizeBytes: 6,
        fetchImpl: fetchImpl as typeof fetch,
        saveFilePicker: picker,
      });
      const rejection = expect(pending).rejects.toMatchObject({
        code: SESSION_SUPERSEDED_CODE,
      });
      const phaseSpy = {
        picker,
        create: createWritable,
        fetch: fetchImpl,
        read,
        write: writable.write,
        close: writable.close,
      }[phase];

      await vi.waitFor(() => expect(phaseSpy).toHaveBeenCalledOnce());
      useAuthStore.setState({ accessToken: "token-b", user: secondUser });
      phaseDeferred.reject(new Error(`${phase} failed`));
      await rejection;

      expect(URL.createObjectURL).not.toHaveBeenCalled();
      if (phase === "read" || phase === "write") {
        expect(read).toHaveBeenCalledOnce();
      }
      if (phase !== "close") {
        expect(writable.close).not.toHaveBeenCalled();
      }
    },
  );

  it("rejects a buffered direct ABA race without reading a later chunk or creating a Blob URL", async () => {
    const firstRead = createDeferred<ReadableStreamReadResult<Uint8Array>>();
    const reader = {
      read: vi.fn(() => firstRead.promise),
      cancel: vi.fn().mockResolvedValue(undefined),
      releaseLock: vi.fn(),
    };
    const response = {
      ok: true,
      status: 200,
      headers: new Headers({
        "content-disposition": 'attachment; filename="server.pdf"',
      }),
      body: { getReader: vi.fn().mockReturnValue(reader) },
    } as unknown as Response;
    const fetchImpl = vi.fn().mockResolvedValue(response) as unknown as typeof fetch;
    const pending = downloadDocument({
      id: "file-a",
      fileName: "甲用户原件.pdf",
      sizeBytes: 6,
      fetchImpl,
      saveFilePicker: undefined,
    });
    const rejection = expect(pending).rejects.toMatchObject({
      code: SESSION_SUPERSEDED_CODE,
    });

    await vi.waitFor(() => expect(reader.read).toHaveBeenCalledOnce());
    const signal = (vi.mocked(fetchImpl).mock.calls[0]?.[1] as RequestInit).signal;
    useAuthStore.setState({ accessToken: "token-b", user: secondUser });
    useAuthStore.setState({ accessToken: "download-token", user: firstUser });
    expect(signal?.aborted).toBe(true);
    firstRead.resolve({ done: false, value: new Uint8Array([1, 2, 3]) });

    await rejection;
    expect(reader.read).toHaveBeenCalledOnce();
    expect(reader.cancel).toHaveBeenCalledWith(
      expect.objectContaining({ code: SESSION_SUPERSEDED_CODE }),
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("sanitizes RFC 5987 filenames", () => {
    expect(
      fileNameFromContentDisposition(
        "attachment; filename*=UTF-8''..%2F%E5%91%98%E5%B7%A5%3F%E6%89%8B%E5%86%8C.pdf",
        "fallback.pdf",
      ),
    ).toBe("员工_手册.pdf");
  });
});
