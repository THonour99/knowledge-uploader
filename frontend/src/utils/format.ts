import dayjs from "dayjs";

const numberFormatter = new Intl.NumberFormat("zh-CN");

const FILE_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/**
 * 千分位整数格式化。空值 / NaN 统一回退为 "-"，避免页面误显示 0。
 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return numberFormatter.format(value);
}

/**
 * 比率（0~1）转百分比文案。空值 / NaN 回退为 "-"。
 */
export function formatPercent(value: number | null | undefined, fractionDigits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

/**
 * 字节数自动选用 B/KB/MB/GB/... 单位。空值 / 负数 / NaN 回退为 "-"。
 */
export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes) || bytes < 0) {
    return "-";
  }
  if (bytes === 0) {
    return "0 B";
  }

  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    FILE_SIZE_UNITS.length - 1,
  );
  const scaled = bytes / 1024 ** exponent;
  const formatted =
    exponent === 0 ? String(Math.round(scaled)) : scaled.toFixed(scaled >= 100 ? 0 : 1);

  return `${formatted} ${FILE_SIZE_UNITS[exponent]}`;
}

/**
 * 时间格式化，默认 YYYY-MM-DD HH:mm。空值 / 非法时间回退为 "-"。
 */
export function formatDateTime(
  value: string | number | Date | null | undefined,
  template = "YYYY-MM-DD HH:mm",
): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format(template) : "-";
}
