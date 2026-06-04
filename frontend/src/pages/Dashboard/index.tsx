import { PlaceholderPage } from "../PlaceholderPage";

export default function DashboardPage() {
  return (
    <PlaceholderPage
      title="仪表盘"
      description="知识库运营总览、上传趋势、分类分布与失败动态。"
      primaryAction="刷新数据"
      samples={[
        { kind: "file", value: "parsed" },
        { kind: "file", value: "pending_review" },
      ]}
    />
  );
}
