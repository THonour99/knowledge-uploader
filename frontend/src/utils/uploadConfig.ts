import type { ConfigItem } from "../api/client";

export const DEFAULT_ALLOWED_EXTENSIONS = ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"];

function valueFor(items: ConfigItem[] | undefined, key: string): unknown {
  return items?.find((item) => item.key === key)?.value;
}

export function allowedExtensionsFromConfig(items: ConfigItem[] | undefined): string[] {
  const value = valueFor(items, "upload.allowed_extensions");
  const rawExtensions = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(",")
      : DEFAULT_ALLOWED_EXTENSIONS;
  const extensions = rawExtensions
    .map((item) => String(item).trim().toLowerCase().replace(/^\./, ""))
    .filter(Boolean);
  return extensions.length > 0 ? Array.from(new Set(extensions)) : DEFAULT_ALLOWED_EXTENSIONS;
}

export function allowMultiFileFromConfig(items: ConfigItem[] | undefined): boolean {
  return valueFor(items, "upload.allow_multi_file") !== false;
}

export function uploadEnabledFromConfig(items: ConfigItem[] | undefined): boolean {
  const enabled = valueFor(items, "upload.enabled") ?? valueFor(items, "upload.enable_upload");
  return enabled !== false;
}

export function extensionAcceptValue(extensions: string[]): string {
  return extensions.map((extension) => `.${extension}`).join(",");
}
