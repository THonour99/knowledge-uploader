import { PlaceholderPage } from "../PlaceholderPage";

export default function MyFilesPage() {
  return (
    <PlaceholderPage
      title="我的文件"
      description="查看我的上传、审核、同步和解析状态。"
      primaryAction="上传文件"
      samples={[
        { kind: "review", value: "pending" },
        { kind: "sync", value: "synced" },
      ]}
    />
  );
}
