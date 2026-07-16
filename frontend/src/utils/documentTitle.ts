export interface DocumentNaming {
  title?: string | null;
  original_name: string;
}

/**
 * The editable title is the product-facing name. The immutable original name
 * remains available for provenance and rolling-deploy responses without title.
 */
export function documentDisplayTitle(document: DocumentNaming): string {
  const title = document.title?.trim();
  return title || document.original_name;
}

export function originalFileNameLabel(document: DocumentNaming): string {
  return `原始文件：${document.original_name}`;
}
