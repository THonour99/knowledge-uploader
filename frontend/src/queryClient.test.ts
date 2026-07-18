import { createElement, type PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider, useMutation } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { installAuthQueryCacheGuard } from "./queryClient";
import {
  SESSION_SUPERSEDED_CODE,
  captureAuthSessionIdentity,
  createAuthSessionAbortScope,
  isCurrentAuthSessionIdentity,
  runAuthSessionLifecycleCallback,
} from "./sessionIdentity";
import { type CurrentUser, useAuthStore } from "./store/auth.store";

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

function createQueryClientWrapper(client: QueryClient) {
  return function QueryClientWrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client, children });
  };
}

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("installAuthQueryCacheGuard", () => {
  it("removes cached user data immediately when the authenticated user changes", () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const client = new QueryClient();
    const removeGuard = installAuthQueryCacheGuard(client);
    client.setQueryData(["documents", "file-1"], { title: "第一位用户的文件" });

    useAuthStore.setState({
      accessToken: "token-2",
      user: { ...firstUser, id: "employee-2", email: "second@example.com" },
    });

    expect(client.getQueryData(["documents", "file-1"])).toBeUndefined();
    removeGuard();
  });

  it("also removes cached user data when only the access token changes", () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const client = new QueryClient();
    const removeGuard = installAuthQueryCacheGuard(client);
    client.setQueryData(["documents", "file-1"], { title: "旧令牌缓存" });

    useAuthStore.setState({ accessToken: "token-refreshed", user: firstUser });

    expect(client.getQueryData(["documents", "file-1"])).toBeUndefined();
    removeGuard();
  });
  it("clears cached authorization data when same-token verification or department gates change", () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const client = new QueryClient();
    const removeGuard = installAuthQueryCacheGuard(client);

    client.setQueryData(["documents", "gate"], { visible: true });
    useAuthStore.setState({
      accessToken: "token-1",
      user: { ...firstUser, email_verified: false },
    });
    expect(client.getQueryData(["documents", "gate"])).toBeUndefined();

    client.setQueryData(["documents", "gate"], { visible: true });
    useAuthStore.setState({
      accessToken: "token-1",
      user: { ...firstUser, email_verified: false, department_assigned: false },
    });
    expect(client.getQueryData(["documents", "gate"])).toBeUndefined();

    client.setQueryData(["documents", "gate"], { visible: true });
    useAuthStore.setState({
      accessToken: "token-1",
      user: {
        ...firstUser,
        email_verified: false,
        department_assigned: false,
        department_code: "unassigned",
      },
    });
    expect(client.getQueryData(["documents", "gate"])).toBeUndefined();
    removeGuard();
  });

  it("destroys mutation state and rejects late query cache writes after identity changes", async () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const client = new QueryClient();
    const removeGuard = installAuthQueryCacheGuard(client);
    let resolveOldRequest!: (value: { title: string }) => void;
    const oldRequest = client
      .fetchQuery({
        queryKey: ["documents", "file-pending"],
        queryFn: () =>
          new Promise<{ title: string }>((resolve) => {
            resolveOldRequest = resolve;
          }),
      })
      .catch(() => undefined);
    client.getMutationCache().build(client, {
      mutationKey: ["documents", "old-user-update"],
      mutationFn: async () => undefined,
    });

    expect(client.getMutationCache().getAll()).toHaveLength(1);
    useAuthStore.setState({
      accessToken: "token-2",
      user: { ...firstUser, id: "employee-2", email: "second@example.com" },
    });

    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(
      client.getQueryCache().find({ queryKey: ["documents", "file-pending"] }),
    ).toBeUndefined();

    resolveOldRequest({ title: "不应回填的旧账号响应" });
    await oldRequest;
    await Promise.resolve();

    expect(client.getQueryData(["documents", "file-pending"])).toBeUndefined();
    removeGuard();
  });

  it("keeps ABA sessions distinct even when the final token and user match the first session", () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const firstIdentity = captureAuthSessionIdentity();

    useAuthStore.setState({ accessToken: "token-2", user: secondUser });
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const returnedIdentity = captureAuthSessionIdentity();

    expect(returnedIdentity).toMatchObject({
      accessToken: firstIdentity.accessToken,
      userId: firstIdentity.userId,
    });
    expect(returnedIdentity.generation).toBeGreaterThan(firstIdentity.generation);
    expect(isCurrentAuthSessionIdentity(firstIdentity)).toBe(false);
    expect(isCurrentAuthSessionIdentity(returnedIdentity)).toBe(true);
  });

  it("suppresses late successful hook and per-call mutation callbacks after identity changes", async () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const removeGuard = installAuthQueryCacheGuard(client);
    let activeRequest = createDeferred<string>();
    const hookSuccess = vi.fn();
    const hookError = vi.fn();
    const hookSettled = vi.fn();
    const oldCallSuccess = vi.fn();
    const oldCallError = vi.fn();
    const oldCallSettled = vi.fn();
    const { result, unmount } = renderHook(
      () =>
        useMutation<string, Error, string>({
          mutationFn: () => activeRequest.promise,
          onSuccess: hookSuccess,
          onError: hookError,
          onSettled: hookSettled,
        }),
      { wrapper: createQueryClientWrapper(client) },
    );

    try {
      let oldCompletion!: Promise<string>;
      act(() => {
        oldCompletion = result.current.mutateAsync("old", {
          onSuccess: oldCallSuccess,
          onError: oldCallError,
          onSettled: oldCallSettled,
        });
      });
      await waitFor(() => expect(result.current.isPending).toBe(true));

      act(() => {
        useAuthStore.setState({ accessToken: "token-2", user: secondUser });
      });
      await waitFor(() => expect(result.current.isIdle).toBe(true));
      expect(client.getMutationCache().getAll()).toHaveLength(0);

      activeRequest.resolve("old-success");
      await act(async () => {
        expect(await oldCompletion).toBe("old-success");
      });

      expect(hookSuccess).not.toHaveBeenCalled();
      expect(hookError).not.toHaveBeenCalled();
      expect(hookSettled).not.toHaveBeenCalled();
      expect(oldCallSuccess).not.toHaveBeenCalled();
      expect(oldCallError).not.toHaveBeenCalled();
      expect(oldCallSettled).not.toHaveBeenCalled();

      activeRequest = createDeferred<string>();
      const currentCallSuccess = vi.fn();
      const currentCallSettled = vi.fn();
      let currentCompletion!: Promise<string>;
      act(() => {
        currentCompletion = result.current.mutateAsync("current", {
          onSuccess: currentCallSuccess,
          onSettled: currentCallSettled,
        });
      });
      await waitFor(() => expect(result.current.isPending).toBe(true));
      activeRequest.resolve("current-success");
      await act(async () => {
        expect(await currentCompletion).toBe("current-success");
      });

      expect(hookSuccess).toHaveBeenCalledOnce();
      expect(hookError).not.toHaveBeenCalled();
      expect(hookSettled).toHaveBeenCalledOnce();
      expect(currentCallSuccess).toHaveBeenCalledOnce();
      expect(currentCallSettled).toHaveBeenCalledOnce();
    } finally {
      unmount();
      removeGuard();
    }
  });

  it("suppresses late failed hook and per-call mutation callbacks after identity changes", async () => {
    useAuthStore.setState({ accessToken: "token-1", user: firstUser });
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const removeGuard = installAuthQueryCacheGuard(client);
    const oldRequest = createDeferred<string>();
    const oldError = new Error("old mutation failed");
    const hookSuccess = vi.fn();
    const hookError = vi.fn();
    const hookSettled = vi.fn();
    const oldCallSuccess = vi.fn();
    const oldCallError = vi.fn();
    const oldCallSettled = vi.fn();
    const { result, unmount } = renderHook(
      () =>
        useMutation<string, Error, string>({
          mutationFn: () => oldRequest.promise,
          onSuccess: hookSuccess,
          onError: hookError,
          onSettled: hookSettled,
        }),
      { wrapper: createQueryClientWrapper(client) },
    );

    try {
      let oldCompletion!: Promise<string>;
      act(() => {
        oldCompletion = result.current.mutateAsync("old", {
          onSuccess: oldCallSuccess,
          onError: oldCallError,
          onSettled: oldCallSettled,
        });
      });
      await waitFor(() => expect(result.current.isPending).toBe(true));

      act(() => {
        useAuthStore.setState({ accessToken: "token-2", user: secondUser });
      });
      await waitFor(() => expect(result.current.isIdle).toBe(true));

      oldRequest.reject(oldError);
      await act(async () => {
        await expect(oldCompletion).rejects.toBe(oldError);
      });

      expect(hookSuccess).not.toHaveBeenCalled();
      expect(hookError).not.toHaveBeenCalled();
      expect(hookSettled).not.toHaveBeenCalled();
      expect(oldCallSuccess).not.toHaveBeenCalled();
      expect(oldCallError).not.toHaveBeenCalled();
      expect(oldCallSettled).not.toHaveBeenCalled();
      expect(client.getMutationCache().getAll()).toHaveLength(0);
    } finally {
      unmount();
      removeGuard();
    }
  });
  it("increments generation and aborts active work for same-token security-context changes", () => {
    const cases: Array<{ start: CurrentUser; end: CurrentUser }> = [
      {
        start: { ...firstUser, role: "system_admin" },
        end: { ...firstUser, role: "employee" },
      },
      {
        start: firstUser,
        end: { ...firstUser, department_id: "dept-2" },
      },
      {
        start: firstUser,
        end: { ...firstUser, email_verified: false },
      },
      {
        start: firstUser,
        end: { ...firstUser, department_assigned: false },
      },
    ];

    for (const { start, end } of cases) {
      useAuthStore.setState({ accessToken: "same-token", user: start });
      const identity = captureAuthSessionIdentity();
      const scope = createAuthSessionAbortScope(identity);

      useAuthStore.setState({ accessToken: "same-token", user: end });
      const currentIdentity = captureAuthSessionIdentity();

      expect(currentIdentity.generation).toBeGreaterThan(identity.generation);
      expect(isCurrentAuthSessionIdentity(identity)).toBe(false);
      expect(scope.signal.aborted).toBe(true);
      expect(scope.signal.reason).toMatchObject({ code: SESSION_SUPERSEDED_CODE });
      scope.dispose();
    }
  });

  it.each(["onSuccess", "onError", "onSettled"] as const)(
    "aborts a session-bound %s callback that already entered before an await",
    async (lifecycle) => {
      useAuthStore.setState({
        accessToken: "same-token",
        user: { ...firstUser, role: "dept_admin" },
      });
      const requestIdentity = captureAuthSessionIdentity();
      const callbackGate = createDeferred<void>();
      const callbackEntered = vi.fn();
      const continuationEffect = vi.fn();
      const mutationError = new Error("expected mutation failure");
      let callbackSignal: AbortSignal | undefined;
      const guardedCallback = () =>
        runAuthSessionLifecycleCallback(requestIdentity, async (context) => {
          callbackSignal = context.signal;
          callbackEntered();
          await context.waitFor(callbackGate.promise);
          context.run(continuationEffect);
        });
      const client = new QueryClient({
        defaultOptions: { mutations: { retry: false } },
      });
      const removeGuard = installAuthQueryCacheGuard(client);
      const { result, unmount } = renderHook(
        () =>
          useMutation<string, Error, string>({
            mutationFn: async () => {
              if (lifecycle === "onError") {
                throw mutationError;
              }
              return "ok";
            },
            onSuccess: lifecycle === "onSuccess" ? guardedCallback : undefined,
            onError: lifecycle === "onError" ? guardedCallback : undefined,
            onSettled: lifecycle === "onSettled" ? guardedCallback : undefined,
          }),
        { wrapper: createQueryClientWrapper(client) },
      );

      try {
        let completion!: Promise<string>;
        act(() => {
          completion = result.current.mutateAsync("start");
        });
        await waitFor(() => expect(callbackEntered).toHaveBeenCalledOnce());

        act(() => {
          useAuthStore.setState({
            accessToken: "same-token",
            user: { ...firstUser, role: "employee" },
          });
        });
        expect(callbackSignal?.aborted).toBe(true);

        callbackGate.resolve(undefined);
        await act(async () => {
          if (lifecycle === "onError") {
            await expect(completion).rejects.toBe(mutationError);
          } else {
            await expect(completion).resolves.toBe("ok");
          }
        });

        expect(continuationEffect).not.toHaveBeenCalled();
      } finally {
        unmount();
        removeGuard();
      }
    },
  );
});
