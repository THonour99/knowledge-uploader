import { getApiBaseUrl } from "../api/client";
import {
  type AuthSessionIdentity,
  SessionSupersededError,
  assertCurrentAuthSessionIdentity,
  captureAuthSessionIdentity,
  createAuthSessionAbortScope,
  isCurrentAuthSessionIdentity,
} from "../sessionIdentity";
import { useAuthStore } from "../store/auth.store";
import { cancelResponseBody, readBoundedResponseBlob } from "./boundedResponse";

export const SAFE_BUFFERED_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024;

export type DocumentDownloadMode = "streamed" | "buffered" | "cancelled";

interface KnowledgeWritableFile {
  write(data: Uint8Array): Promise<void>;
  close(): Promise<void>;
  abort?(reason?: unknown): Promise<void>;
}

interface KnowledgeSaveFileHandle {
  createWritable(): Promise<KnowledgeWritableFile>;
}

type SaveFilePicker = (options: { suggestedName: string }) => Promise<KnowledgeSaveFileHandle>;

declare global {
  interface Window {
    showSaveFilePicker?: SaveFilePicker;
  }
}

export class DownloadCapabilityError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DownloadCapabilityError";
  }
}

class CurrentSessionUnauthorizedError extends Error {
  constructor() {
    super("登录状态已失效，请重新登录后下载");
    this.name = "CurrentSessionUnauthorizedError";
  }
}

interface DownloadDocumentOptions {
  id: string;
  fileName: string;
  sizeBytes: number;
  fetchImpl?: typeof fetch;
  saveFilePicker?: SaveFilePicker;
  signal?: AbortSignal;
}

function safeFileName(value: string): string {
  const pathSegments = value.split(/[\\/]/);
  const leafName = pathSegments[pathSegments.length - 1] ?? "document";
  const sanitized = [...leafName]
    .map((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || '<>:"|?*'.includes(character) ? "_" : character;
    })
    .join("")
    .replace(/[. ]+$/g, "")
    .slice(0, 180);
  return sanitized || "document";
}

export function fileNameFromContentDisposition(
  contentDisposition: string | null,
  fallback: string,
): string {
  if (!contentDisposition) {
    return safeFileName(fallback);
  }
  const encodedMatch = contentDisposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  if (encodedMatch) {
    try {
      return safeFileName(decodeURIComponent(encodedMatch[1].trim()));
    } catch {
      return safeFileName(fallback);
    }
  }
  const quotedMatch = contentDisposition.match(/filename\s*=\s*"([^"]*)"/i);
  if (quotedMatch) {
    return safeFileName(quotedMatch[1]);
  }
  const plainMatch = contentDisposition.match(/filename\s*=\s*([^;]+)/i);
  return safeFileName(plainMatch?.[1].trim() || fallback);
}

function assertAttachmentResponse(response: Response): string | null {
  if (!response.ok) {
    throw new Error(`原件下载失败（HTTP ${response.status}）`);
  }
  const disposition = response.headers.get("content-disposition");
  if (!disposition || !/^\s*attachment(?:;|$)/i.test(disposition)) {
    throw new Error("服务器未返回安全的附件下载响应");
  }
  return disposition;
}

function triggerBoundedBlobDownload(blob: Blob, fileName: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }
}

function isPickerUnavailable(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    ["NotAllowedError", "NotSupportedError", "SecurityError"].includes(error.name)
  );
}

function isPickerCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function downloadEndpoint(id: string): string {
  return `${getApiBaseUrl().replace(/\/$/, "")}/files/${encodeURIComponent(id)}/content?disposition=attachment`;
}

async function authorizedResponse(
  id: string,
  fetchImpl: typeof fetch,
  requestIdentity: AuthSessionIdentity,
  signal: AbortSignal,
): Promise<Response> {
  if (!requestIdentity.accessToken) {
    throw new Error("登录状态已失效，请重新登录后下载");
  }

  let response: Response;
  try {
    response = await fetchImpl(downloadEndpoint(id), {
      method: "GET",
      headers: {
        Accept: "application/octet-stream",
        Authorization: `Bearer ${requestIdentity.accessToken}`,
      },
      credentials: "same-origin",
      signal,
    });
  } catch (error) {
    if (!isCurrentAuthSessionIdentity(requestIdentity)) {
      throw new SessionSupersededError();
    }
    throw error;
  }

  if (!isCurrentAuthSessionIdentity(requestIdentity)) {
    const error = new SessionSupersededError();
    await cancelResponseBody(response, error);
    throw error;
  }
  if (response.status === 401) {
    const error = new CurrentSessionUnauthorizedError();
    await cancelResponseBody(response, error);
    if (!isCurrentAuthSessionIdentity(requestIdentity)) {
      throw new SessionSupersededError();
    }
    useAuthStore.getState().clearSession();
    throw error;
  }
  return response;
}

async function streamToFile(
  options: DownloadDocumentOptions,
  picker: SaveFilePicker,
  requestIdentity: AuthSessionIdentity,
  signal: AbortSignal,
): Promise<DocumentDownloadMode> {
  let handle: KnowledgeSaveFileHandle;
  try {
    handle = await picker({ suggestedName: safeFileName(options.fileName) });
    assertCurrentAuthSessionIdentity(requestIdentity);
  } catch (error) {
    if (!isCurrentAuthSessionIdentity(requestIdentity)) {
      throw new SessionSupersededError();
    }
    if (isPickerCancelled(error)) {
      return "cancelled";
    }
    throw error;
  }

  let writable: KnowledgeWritableFile | null = null;
  let response: Response | null = null;
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  try {
    assertCurrentAuthSessionIdentity(requestIdentity);
    writable = await handle.createWritable();
    assertCurrentAuthSessionIdentity(requestIdentity);
    response = await authorizedResponse(
      options.id,
      options.fetchImpl ?? fetch,
      requestIdentity,
      signal,
    );
    assertCurrentAuthSessionIdentity(requestIdentity);
    assertAttachmentResponse(response);
    if (!response.body) {
      throw new Error("浏览器未提供可读取的下载流");
    }
    reader = response.body.getReader();
    for (;;) {
      assertCurrentAuthSessionIdentity(requestIdentity);
      const chunk = await reader.read();
      assertCurrentAuthSessionIdentity(requestIdentity);
      if (chunk.done) {
        break;
      }
      await writable.write(chunk.value);
      assertCurrentAuthSessionIdentity(requestIdentity);
    }
    await writable.close();
    assertCurrentAuthSessionIdentity(requestIdentity);
    return "streamed";
  } catch (error) {
    const currentSessionUnauthorized = error instanceof CurrentSessionUnauthorizedError;
    const cleanupError =
      isCurrentAuthSessionIdentity(requestIdentity) || currentSessionUnauthorized
        ? error
        : new SessionSupersededError();
    if (reader) {
      try {
        await reader.cancel(cleanupError);
      } catch {
        // Best effort only: the original download error remains actionable.
      }
    } else {
      await cancelResponseBody(response, cleanupError);
    }
    if (writable) {
      try {
        await writable.abort?.(cleanupError);
      } catch {
        // Best effort only: abort failures must not hide the original error.
      }
    }
    if (!currentSessionUnauthorized && !isCurrentAuthSessionIdentity(requestIdentity)) {
      throw new SessionSupersededError();
    }
    throw error;
  } finally {
    if (reader) {
      try {
        reader.releaseLock();
      } catch {
        // The stream may already have released its lock after a transport failure.
      }
    }
  }
}

export async function downloadDocument(
  options: DownloadDocumentOptions,
): Promise<DocumentDownloadMode> {
  const requestIdentity = captureAuthSessionIdentity();
  if (!requestIdentity.accessToken) {
    throw new Error("登录状态已失效，请重新登录后下载");
  }

  const sessionScope = createAuthSessionAbortScope(requestIdentity, options.signal);
  try {
    const picker = options.saveFilePicker ?? window.showSaveFilePicker;
    if (picker) {
      try {
        return await streamToFile(options, picker, requestIdentity, sessionScope.signal);
      } catch (error) {
        if (
          !(error instanceof CurrentSessionUnauthorizedError) &&
          !isCurrentAuthSessionIdentity(requestIdentity)
        ) {
          throw new SessionSupersededError();
        }
        if (!isPickerUnavailable(error)) {
          throw error;
        }
      }
    }

    assertCurrentAuthSessionIdentity(requestIdentity);
    if (options.sizeBytes > SAFE_BUFFERED_DOWNLOAD_MAX_BYTES) {
      throw new DownloadCapabilityError(
        "当前浏览器不支持大文件流式保存。为避免占满内存，已停止下载；请使用支持文件流保存的桌面版 Chrome 或 Edge。",
      );
    }

    const response = await authorizedResponse(
      options.id,
      options.fetchImpl ?? fetch,
      requestIdentity,
      sessionScope.signal,
    );
    let disposition: string | null;
    try {
      disposition = assertAttachmentResponse(response);
    } catch (error) {
      await cancelResponseBody(response, error);
      assertCurrentAuthSessionIdentity(requestIdentity);
      throw error;
    }
    const content = await readBoundedResponseBlob(response, {
      maxBytes: SAFE_BUFFERED_DOWNLOAD_MAX_BYTES,
      sizeError: () => new DownloadCapabilityError("服务器返回的文件超过安全缓冲上限，已停止下载"),
      missingBodyError: () => new DownloadCapabilityError("浏览器未提供可读取的下载流"),
      assertCanContinue: () => assertCurrentAuthSessionIdentity(requestIdentity),
    });
    assertCurrentAuthSessionIdentity(requestIdentity);
    triggerBoundedBlobDownload(
      content.blob,
      fileNameFromContentDisposition(disposition, options.fileName),
    );
    assertCurrentAuthSessionIdentity(requestIdentity);
    return "buffered";
  } catch (error) {
    if (
      !(error instanceof CurrentSessionUnauthorizedError) &&
      !isCurrentAuthSessionIdentity(requestIdentity)
    ) {
      throw new SessionSupersededError();
    }
    throw error;
  } finally {
    sessionScope.dispose();
  }
}
