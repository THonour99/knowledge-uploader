import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadBlob } from "./download";

describe("downloadBlob", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates an anchor with the given filename and triggers a click", () => {
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });

    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    const blob = new Blob(["a,b,c"], { type: "text/csv" });
    downloadBlob(blob, "report.csv");

    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    // 链接用完即移除，不残留在 DOM
    expect(document.querySelector("a[download]")).toBeNull();

    vi.unstubAllGlobals();
  });
});
