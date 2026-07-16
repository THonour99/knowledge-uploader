export interface BoundedResponseBlob {
  blob: Blob;
  contentLength: number | null;
}

export interface BoundedResponseOptions {
  maxBytes: number;
  sizeError: (maxBytes: number) => Error;
  missingBodyError?: () => Error;
}

function parseContentLength(headers: Headers): number | null {
  const rawValue = headers.get("content-length");
  if (rawValue === null) {
    return null;
  }
  const parsed = Number(rawValue);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
}

export async function cancelResponseBody(
  response: Pick<Response, "body"> | null,
  reason: unknown,
): Promise<void> {
  if (!response?.body) {
    return;
  }
  try {
    await response.body.cancel(reason);
  } catch {
    // Best effort: cancellation failures must not replace the actionable error.
  }
}

export async function readBoundedResponseBlob(
  response: Response,
  options: BoundedResponseOptions,
): Promise<BoundedResponseBlob> {
  if (!Number.isSafeInteger(options.maxBytes) || options.maxBytes <= 0) {
    throw new Error("响应缓冲上限配置无效");
  }

  const contentLength = parseContentLength(response.headers);
  if (contentLength !== null && contentLength > options.maxBytes) {
    const error = options.sizeError(options.maxBytes);
    await cancelResponseBody(response, error);
    throw error;
  }
  if (!response.body) {
    throw options.missingBodyError?.() ?? new Error("浏览器未提供可读取的响应流");
  }

  let reader: ReadableStreamDefaultReader<Uint8Array>;
  try {
    reader = response.body.getReader();
  } catch (error) {
    await cancelResponseBody(response, error);
    throw error;
  }
  const chunks: ArrayBuffer[] = [];
  let totalBytes = 0;
  let readerCancelled = false;
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) {
        break;
      }
      totalBytes += chunk.value.byteLength;
      if (totalBytes > options.maxBytes) {
        const error = options.sizeError(options.maxBytes);
        try {
          await reader.cancel(error);
        } catch {
          // Best effort: cancellation failures must not replace the size error.
        }
        readerCancelled = true;
        throw error;
      }

      const copy = new Uint8Array(chunk.value.byteLength);
      copy.set(chunk.value);
      chunks.push(copy.buffer);
    }
  } catch (error) {
    if (!readerCancelled) {
      try {
        await reader.cancel(error);
      } catch {
        // Best effort: preserve the transport/read error.
      }
    }
    throw error;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // The stream can already be unlocked after a transport failure.
    }
  }

  return {
    blob: new Blob(chunks, {
      type: response.headers.get("content-type") ?? "application/octet-stream",
    }),
    contentLength,
  };
}
