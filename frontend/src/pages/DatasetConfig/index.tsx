import { PlaceholderPage } from "../PlaceholderPage";

export default function DatasetConfigPage() {
  return (
    <PlaceholderPage
      title="Dataset 配置"
      description="配置文档分类与 RAGFlow Dataset 的映射关系。"
      primaryAction="新增映射"
      samples={[{ kind: "user", value: "active" }]}
    />
  );
}
