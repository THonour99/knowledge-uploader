/**
 * 把后端返回的 Blob 以指定文件名触发浏览器下载。
 * 用于报表导出等场景，避免每个页面各自拼接 <a download> 逻辑。
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
