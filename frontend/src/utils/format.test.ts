import { describe, expect, it } from "vitest";

import { formatDateTime, formatFileSize, formatNumber, formatPercent } from "./format";

describe("formatNumber", () => {
  it("adds thousands separators", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("formats zero as 0, not '-'", () => {
    expect(formatNumber(0)).toBe("0");
  });

  it("falls back to '-' for null/undefined/NaN", () => {
    expect(formatNumber(null)).toBe("-");
    expect(formatNumber(undefined)).toBe("-");
    expect(formatNumber(Number.NaN)).toBe("-");
  });
});

describe("formatPercent", () => {
  it("converts ratio to percent with one fraction digit by default", () => {
    expect(formatPercent(0.876)).toBe("87.6%");
  });

  it("respects custom fraction digits", () => {
    expect(formatPercent(0.5, 0)).toBe("50%");
  });

  it("falls back to '-' for null/undefined/NaN", () => {
    expect(formatPercent(null)).toBe("-");
    expect(formatPercent(undefined)).toBe("-");
    expect(formatPercent(Number.NaN)).toBe("-");
  });
});

describe("formatFileSize", () => {
  it("returns '0 B' for zero", () => {
    expect(formatFileSize(0)).toBe("0 B");
  });

  it("selects the right unit", () => {
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatFileSize(3 * 1024 ** 3)).toBe("3.0 GB");
  });

  it("falls back to '-' for null/negative/NaN", () => {
    expect(formatFileSize(null)).toBe("-");
    expect(formatFileSize(-1)).toBe("-");
    expect(formatFileSize(Number.NaN)).toBe("-");
  });
});

describe("formatDateTime", () => {
  it("formats ISO strings with the default template", () => {
    expect(formatDateTime("2026-06-10T08:30:00")).toBe("2026-06-10 08:30");
  });

  it("respects a custom template", () => {
    expect(formatDateTime("2026-06-10T08:30:00", "YYYY/MM/DD")).toBe("2026/06/10");
  });

  it("falls back to '-' for empty / invalid values", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime("")).toBe("-");
    expect(formatDateTime("not-a-date")).toBe("-");
  });
});
