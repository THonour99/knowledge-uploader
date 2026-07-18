import { createElement, type PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSessionMutation } from "./useSessionMutation";
import { SESSION_SUPERSEDED_CODE } from "../sessionIdentity";
import { type CurrentUser, useAuthStore } from "../store/auth.store";

const userA: CurrentUser = {
  id: "user-a",
  name: "甲用户",
  email: "a@example.com",
  role: "system_admin",
};
const userB: CurrentUser = {
  ...userA,
  id: "user-b",
  name: "乙用户",
  email: "b@example.com",
  role: "employee",
};

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client, children });
  };
}

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("useSessionMutation", () => {
  it.each([
    {
      label: "A to B",
      switchSession: () => {
        useAuthStore.setState({ accessToken: "token-b", user: userB });
      },
    },
    {
      label: "ABA",
      switchSession: () => {
        useAuthStore.setState({ accessToken: "token-b", user: userB });
        useAuthStore.setState({ accessToken: "token-a", user: userA });
      },
    },
  ])("blocks a held $label mutate callback before API invocation", async ({ switchSession }) => {
    useAuthStore.setState({ accessToken: "token-a", user: userA });
    const mutationFn = vi.fn(async (value: string) => `ok:${value}`);
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const { result } = renderHook(() => useSessionMutation({ mutationFn }), {
      wrapper: wrapper(client),
    });
    const heldMutate = result.current.mutate;

    act(switchSession);
    act(() => heldMutate("old-intent"));
    await Promise.resolve();

    expect(mutationFn).not.toHaveBeenCalled();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
  });

  it("checks identity again after async onMutate before starting transport", async () => {
    useAuthStore.setState({ accessToken: "token-a", user: userA });
    let releaseOnMutate!: () => void;
    const onMutateGate = new Promise<void>((resolve) => {
      releaseOnMutate = resolve;
    });
    const mutationFn = vi.fn(async (value: string) => `ok:${value}`);
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const { result } = renderHook(
      () =>
        useSessionMutation({
          mutationFn,
          onMutate: () => onMutateGate,
        }),
      { wrapper: wrapper(client) },
    );

    let completion!: Promise<string>;
    act(() => {
      completion = result.current.mutateAsync("current-intent");
    });
    await Promise.resolve();
    act(() => {
      useAuthStore.setState({ accessToken: "token-b", user: userB });
    });
    releaseOnMutate();

    await expect(completion).rejects.toMatchObject({ code: SESSION_SUPERSEDED_CODE });
    expect(mutationFn).not.toHaveBeenCalled();
  });
  it("requires delayed mutation work to cross awaits through the session context", async () => {
    useAuthStore.setState({ accessToken: "token-a", user: userA });
    let releaseDelay!: () => void;
    const delay = new Promise<void>((resolve) => {
      releaseDelay = resolve;
    });
    const transport = vi.fn(async () => "ok");
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const { result } = renderHook(
      () =>
        useSessionMutation({
          mutationFn: async (_value: string, context) => {
            await context.waitFor(delay);
            return transport();
          },
        }),
      { wrapper: wrapper(client) },
    );

    let completion!: Promise<string>;
    act(() => {
      completion = result.current.mutateAsync("delayed-intent");
    });
    await Promise.resolve();
    act(() => {
      useAuthStore.setState({ accessToken: "token-b", user: userB });
    });
    releaseDelay();

    await expect(completion).rejects.toMatchObject({ code: SESSION_SUPERSEDED_CODE });
    expect(transport).not.toHaveBeenCalled();
  });
});
