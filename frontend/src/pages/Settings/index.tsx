import { PlaceholderPage } from "../PlaceholderPage";

export default function SettingsPage() {
  return (
    <PlaceholderPage
      title="系统设置"
      description="配置基础信息、安全认证、上传策略、RAGFlow 集成和邮件通知。"
      primaryAction="保存设置"
      samples={[
        { kind: "sync", value: "syncing" },
        { kind: "user", value: "active" },
      ]}
    />
  );
}
