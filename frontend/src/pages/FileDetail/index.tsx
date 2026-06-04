import { PlaceholderPage } from "../PlaceholderPage";

export default function FileDetailPage() {
  return (
    <PlaceholderPage
      title="文件详情"
      description="查看文件基本信息、AI 分析、RAGFlow 同步日志和审核历史。"
      primaryAction="通过审核"
      samples={[
        { kind: "file", value: "analyzed" },
        { kind: "risk", value: "medium" },
      ]}
    />
  );
}
