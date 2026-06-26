import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCountUp } from "./useCountUp";

function stubMatchMedia(reduce: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion") ? reduce : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe("useCountUp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the target immediately when reduced motion is preferred", () => {
    stubMatchMedia(true);
    const { result } = renderHook(() => useCountUp(1234));
    expect(result.current).toBe(1234);
  });

  it("animates toward and settles on the target value", async () => {
    stubMatchMedia(false);
    const { result } = renderHook(() => useCountUp(500, 50));
    await waitFor(() => expect(result.current).toBe(500));
  });
});
