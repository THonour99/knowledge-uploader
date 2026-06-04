import { PlaceholderPage } from "../PlaceholderPage";

export default function UploadPage() {
  return (
    <PlaceholderPage
      title="文件上传"
      description="上传知识文件并填写分类、Dataset、标签与可见范围。"
      primaryAction="开始上传"
      samples={[
        { kind: "file", value: "uploaded" },
        { kind: "sync", value: "not_synced" },
      ]}
    />
  );
}
