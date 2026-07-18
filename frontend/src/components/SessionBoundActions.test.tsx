import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSessionIntent } from "./SessionBoundActions";
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

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("useSessionIntent", () => {
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
  ])("invalidates a held pre-confirm $label callback", ({ switchSession }) => {
    useAuthStore.setState({ accessToken: "token-a", user: userA });
    const { result, rerender } = renderHook(({ open }) => useSessionIntent(open), {
      initialProps: { open: false },
    });
    rerender({ open: true });
    const heldRun = result.current.run;
    const apiCall = vi.fn();

    act(switchSession);

    expect(result.current.isCurrent).toBe(false);
    expect(heldRun(apiCall)).toBeUndefined();
    expect(apiCall).not.toHaveBeenCalled();
  });

  it("captures a fresh identity only after the previous intent closes", () => {
    useAuthStore.setState({ accessToken: "token-a", user: userA });
    const { result, rerender } = renderHook(({ open }) => useSessionIntent(open), {
      initialProps: { open: true },
    });
    const identityA = result.current.identity;

    act(() => {
      useAuthStore.setState({ accessToken: "token-b", user: userB });
    });
    rerender({ open: false });
    rerender({ open: true });

    expect(result.current.identity?.generation).toBeGreaterThan(identityA?.generation ?? -1);
    expect(result.current.identity?.userId).toBe("user-b");
    expect(result.current.isCurrent).toBe(true);
  });
});
