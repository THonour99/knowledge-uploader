import type { ReactNode } from "react";
import {
  BarChartOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudSyncOutlined,
  CloudUploadOutlined,
  DownloadOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
  RobotOutlined,
  TeamOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { Avatar, Button, Card, Progress, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";

import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

interface DashboardMetric {
  title: string;
  value: string;
  description: string;
  icon: ReactNode;
  tone: "primary" | "success" | "warning" | "danger" | "purple";
}

interface RankingItem {
  rank: number;
  name: string;
  department: string;
  count: number;
  percent: number;
}

interface ActivityItem {
  title: string;
  description: string;
  time: string;
  tone: "primary" | "success" | "warning" | "danger";
}

interface RecentFile {
  id: string;
  name: string;
  owner: string;
  department: string;
  status: string;
  review: string;
  sync: string;
  uploadedAt: string;
}

const dashboardMetrics: DashboardMetric[] = [
  {
    title: "本月上传",
    value: "1,248",
    description: "较上月 +18.6%",
    icon: <CloudUploadOutlined />,
    tone: "primary",
  },
  {
    title: "审核通过",
    value: "1,102",
    description: "通过率 88.3%",
    icon: <FileDoneOutlined />,
    tone: "success",
  },
  {
    title: "已同步 RAGFlow",
    value: "976",
    description: "成功率 96.4%",
    icon: <CloudSyncOutlined />,
    tone: "purple",
  },
  {
    title: "待处理",
    value: "64",
    description: "含 12 个敏感审核",
    icon: <ClockCircleOutlined />,
    tone: "warning",
  },
  {
    title: "失败任务",
    value: "8",
    description: "需管理员介入",
    icon: <WarningOutlined />,
    tone: "danger",
  },
];

const rankingItems: RankingItem[] = [
  { rank: 1, name: "产品运营部", department: "知识贡献", count: 328, percent: 88 },
  { rank: 2, name: "技术支持部", department: "FAQ 文档", count: 276, percent: 76 },
  { rank: 3, name: "研发中心", department: "技术规范", count: 221, percent: 63 },
  { rank: 4, name: "市场品牌部", department: "素材资料", count: 168, percent: 52 },
  { rank: 5, name: "人力资源部", department: "制度流程", count: 126, percent: 44 },
];

const activityItems: ActivityItem[] = [
  {
    title: "AI 分析完成",
    description: "产品手册 V3.2 已生成标签和摘要",
    time: "2 分钟前",
    tone: "success",
  },
  {
    title: "文件进入审核",
    description: "技术支持 FAQ 2026 等待管理员处理",
    time: "8 分钟前",
    tone: "primary",
  },
  {
    title: "同步失败",
    description: "销售话术库同步到 RAGFlow 超时",
    time: "21 分钟前",
    tone: "danger",
  },
  {
    title: "敏感内容提醒",
    description: "客户案例汇编检测到高风险片段",
    time: "34 分钟前",
    tone: "warning",
  },
];

const recentFiles: RecentFile[] = [
  {
    id: "f-001",
    name: "产品发布 FAQ 汇总.docx",
    owner: "王明",
    department: "产品运营部",
    status: "analyzed",
    review: "pending",
    sync: "not_synced",
    uploadedAt: "06-07 09:42",
  },
  {
    id: "f-002",
    name: "技术支持知识库补充.xlsx",
    owner: "李雪",
    department: "技术支持部",
    status: "parsed",
    review: "approved",
    sync: "synced",
    uploadedAt: "06-07 09:18",
  },
  {
    id: "f-003",
    name: "客户成功案例精选.pdf",
    owner: "陈晨",
    department: "市场品牌部",
    status: "sensitive_review_required",
    review: "in_review",
    sync: "not_synced",
    uploadedAt: "06-06 18:25",
  },
  {
    id: "f-004",
    name: "内部流程制度说明.md",
    owner: "赵琪",
    department: "人力资源部",
    status: "failed",
    review: "approved",
    sync: "failed",
    uploadedAt: "06-06 16:04",
  },
];

const trendOption: EChartsOption = {
  color: ["#1677ff", "#16a34a", "#f59e0b"],
  grid: { top: 28, right: 20, bottom: 28, left: 42 },
  tooltip: { trigger: "axis" },
  legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10 },
  xAxis: {
    type: "category",
    boundaryGap: false,
    data: ["06-01", "06-02", "06-03", "06-04", "06-05", "06-06", "06-07"],
    axisLine: { lineStyle: { color: "#E5EAF2" } },
    axisTick: { show: false },
  },
  yAxis: {
    type: "value",
    axisLabel: { color: "#667085" },
    splitLine: { lineStyle: { color: "#EEF2F7" } },
  },
  series: [
    {
      name: "上传",
      type: "line",
      smooth: true,
      data: [120, 142, 165, 188, 214, 236, 258],
      areaStyle: { color: "rgba(22, 119, 255, 0.12)" },
    },
    {
      name: "通过",
      type: "line",
      smooth: true,
      data: [96, 118, 132, 151, 178, 194, 218],
    },
    {
      name: "待审",
      type: "line",
      smooth: true,
      data: [28, 32, 29, 34, 37, 41, 36],
    },
  ],
};

const categoryOption: EChartsOption = {
  color: ["#1677ff", "#16a34a", "#f59e0b", "#7c3aed", "#06b6d4"],
  tooltip: { trigger: "item" },
  legend: { bottom: 0, left: "center", itemWidth: 10, itemHeight: 10 },
  series: [
    {
      name: "分类占比",
      type: "pie",
      radius: ["46%", "68%"],
      center: ["50%", "43%"],
      label: { formatter: "{b}\n{d}%", color: "#475467" },
      data: [
        { value: 34, name: "产品" },
        { value: 24, name: "技术" },
        { value: 18, name: "客服" },
        { value: 14, name: "制度" },
        { value: 10, name: "市场" },
      ],
    },
  ],
};

const recentFileColumns: ColumnsType<RecentFile> = [
  {
    title: "文件",
    dataIndex: "name",
    key: "name",
    render: (value: string, record) => (
      <div className="dashboard-file-cell">
        <span className="dashboard-file-cell__icon">
          <FileTextOutlined />
        </span>
        <span className="dashboard-file-cell__copy">
          <Typography.Text strong ellipsis>
            {value}
          </Typography.Text>
          <Typography.Text type="secondary">
            {record.department} / {record.owner}
          </Typography.Text>
        </span>
      </div>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 150,
    render: (value: string) => <StatusTag kind="file" value={value} />,
  },
  {
    title: "审核",
    dataIndex: "review",
    key: "review",
    width: 112,
    render: (value: string) => <StatusTag kind="review" value={value} />,
  },
  {
    title: "同步",
    dataIndex: "sync",
    key: "sync",
    width: 112,
    render: (value: string) => <StatusTag kind="sync" value={value} />,
  },
  {
    title: "上传时间",
    dataIndex: "uploadedAt",
    key: "uploadedAt",
    width: 120,
  },
];

function DashboardMetricCard({ metric }: { metric: DashboardMetric }) {
  return (
    <Card className="dashboard-metric-card">
      <div className="dashboard-metric-card__body">
        <span className={`dashboard-metric-card__icon dashboard-metric-card__icon--${metric.tone}`}>
          {metric.icon}
        </span>
        <span className="dashboard-metric-card__content">
          <Typography.Text type="secondary">{metric.title}</Typography.Text>
          <Typography.Title level={3} className="dashboard-metric-card__value">
            {metric.value}
          </Typography.Title>
          <Typography.Text className={`dashboard-metric-card__delta dashboard-text--${metric.tone}`}>
            {metric.description}
          </Typography.Text>
        </span>
      </div>
    </Card>
  );
}

export default function DashboardPage() {
  return (
    <PageContainer
      title="知识库运营总览"
      description="汇总上传、审核、AI 分析与 RAGFlow 同步关键指标。"
      actions={
        <Space className="dashboard-page-actions" wrap>
          <Button icon={<ReloadOutlined />}>刷新</Button>
          <Button type="primary" icon={<DownloadOutlined />}>
            导出报表
          </Button>
        </Space>
      }
    >
      <div className="dashboard-kpi-grid">
        {dashboardMetrics.map((metric) => (
          <DashboardMetricCard key={metric.title} metric={metric} />
        ))}
      </div>

      <div className="dashboard-main-grid">
        <div className="dashboard-primary">
          <Card
            className="dashboard-panel dashboard-trend-card"
            title={
              <Space>
                <RiseOutlined />
                上传与审核趋势
              </Space>
            }
          >
            <ReactECharts option={trendOption} className="dashboard-chart" />
          </Card>

          <div className="dashboard-secondary-grid">
            <Card
              className="dashboard-panel"
              title={
                <Space>
                  <TeamOutlined />
                  部门贡献排行
                </Space>
              }
            >
              <div className="dashboard-ranking-list">
                {rankingItems.map((item) => (
                  <div className="dashboard-ranking-row" key={item.name}>
                    <span className="dashboard-ranking-row__rank">{item.rank}</span>
                    <Avatar className="dashboard-ranking-row__avatar">{item.name.slice(0, 1)}</Avatar>
                    <span className="dashboard-ranking-row__copy">
                      <Typography.Text strong>{item.name}</Typography.Text>
                      <Typography.Text type="secondary">{item.department}</Typography.Text>
                    </span>
                    <span className="dashboard-ranking-row__count">{item.count}</span>
                    <Progress percent={item.percent} showInfo={false} />
                  </div>
                ))}
              </div>
            </Card>

            <Card
              className="dashboard-panel"
              title={
                <Space>
                  <BarChartOutlined />
                  知识分类占比
                </Space>
              }
            >
              <ReactECharts option={categoryOption} className="dashboard-category-chart" />
            </Card>
          </div>

          <Card className="dashboard-panel table-card" title="最近上传文件">
            <Table<RecentFile>
              rowKey="id"
              columns={recentFileColumns}
              dataSource={recentFiles}
              pagination={false}
              size="middle"
            />
          </Card>
        </div>

        <aside className="dashboard-rail">
          <Card className="dashboard-panel" title="实时动态">
            <div className="dashboard-activity-list">
              {activityItems.map((item) => (
                <div className="dashboard-activity-row" key={item.title}>
                  <span className={`dashboard-activity-row__dot dashboard-text--${item.tone}`} />
                  <span className="dashboard-activity-row__copy">
                    <Typography.Text strong>{item.title}</Typography.Text>
                    <Typography.Text type="secondary">{item.description}</Typography.Text>
                    <Typography.Text type="secondary">{item.time}</Typography.Text>
                  </span>
                </div>
              ))}
            </div>
          </Card>

          <Card className="dashboard-panel" title="快捷统计">
            <div className="dashboard-quick-stats">
              <div>
                <Typography.Text type="secondary">AI 分析耗时</Typography.Text>
                <Typography.Title level={4}>2.8 分钟</Typography.Title>
              </div>
              <div>
                <Typography.Text type="secondary">平均审核时长</Typography.Text>
                <Typography.Title level={4}>1.6 小时</Typography.Title>
              </div>
              <div>
                <Typography.Text type="secondary">活跃上传人</Typography.Text>
                <Typography.Title level={4}>86 人</Typography.Title>
              </div>
              <div>
                <Typography.Text type="secondary">本周新增标签</Typography.Text>
                <Typography.Title level={4}>342 个</Typography.Title>
              </div>
            </div>
          </Card>

          <Card className="dashboard-panel" title="系统状态">
            <div className="dashboard-health-list">
              <div>
                <Space>
                  <CheckCircleOutlined className="dashboard-text--success" />
                  <Typography.Text strong>RAGFlow API</Typography.Text>
                </Space>
                <Progress percent={98} size="small" status="active" />
              </div>
              <div>
                <Space>
                  <RobotOutlined className="dashboard-text--purple" />
                  <Typography.Text strong>AI Provider</Typography.Text>
                </Space>
                <Progress percent={92} size="small" strokeColor="#7c3aed" />
              </div>
              <div>
                <Space>
                  <CloudSyncOutlined className="dashboard-text--primary" />
                  <Typography.Text strong>同步队列</Typography.Text>
                </Space>
                <Progress percent={76} size="small" strokeColor="#1677ff" />
              </div>
            </div>
          </Card>
        </aside>
      </div>
    </PageContainer>
  );
}
