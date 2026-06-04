import { PlaceholderPage } from "../PlaceholderPage";

export default function StatisticsPage() {
  return (
    <PlaceholderPage
      title="统计分析"
      description="查看上传趋势、部门贡献、分类分布和用户上传明细。"
      primaryAction="导出报表"
      samples={[
        { kind: "sync", value: "failed" },
        { kind: "file", value: "parsed" },
      ]}
    />
  );
}
