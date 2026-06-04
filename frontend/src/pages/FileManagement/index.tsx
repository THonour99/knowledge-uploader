import { PlaceholderPage } from "../PlaceholderPage";

export default function FileManagementPage() {
  return (
    <PlaceholderPage
      title="文件管理"
      description="管理平台内所有文件的审核与同步状态，保障数据质量与合规安全。"
      primaryAction="批量审核"
      samples={[
        { kind: "risk", value: "high" },
        { kind: "file", value: "sensitive_review_required" },
      ]}
    />
  );
}
