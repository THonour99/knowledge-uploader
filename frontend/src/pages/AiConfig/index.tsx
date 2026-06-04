import { PlaceholderPage } from "../PlaceholderPage";

export default function AiConfigPage() {
  return (
    <PlaceholderPage
      title="AI 配置"
      description="配置 AI 开关、模型供应商、Prompt 模板与敏感规则。"
      primaryAction="保存配置"
      samples={[
        { kind: "file", value: "analyzing" },
        { kind: "risk", value: "critical" },
      ]}
    />
  );
}
