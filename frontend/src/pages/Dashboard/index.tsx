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
  TeamOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  Avatar,
  Button,
  Card,
  Empty,
  Progress,
  Skeleton,
  Space,
  Typography,
} from "antd";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getStatisticsCategories,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  type StatisticsCategoryRow,
  type StatisticsFailureRow,
  type StatisticsTrendPoint,
  type StatisticsUserRow,
} from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const numberFormatter = new Intl.NumberFormat("zh-CN");

function formatNumber(value: number): string {
  return numberFormatter.format(value);
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function cssVar(name: string, fallback = ""): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// ---------------------------------------------------------------------------
// Metric card
// ---------------------------------------------------------------------------

interface MetricCardProps {
  title: string;
  value: string;
  description: string;
  icon: ReactNode;
  tone: "primary" | "success" | "warning" | "danger" | "purple";
}

function DashboardMetricCard({ title, value, description, icon, tone }: MetricCardProps) {
  return (
    <Card className="dashboard-metric-card">
      <div className="dashboard-metric-card__body">
        <span className={`dashboard-metric-card__icon dashboard-metric-card__icon--${tone}`}>
          {icon}
        </span>
        <span className="dashboard-metric-card__content">
          <Typography.Text type="secondary">{title}</Typography.Text>
          <Typography.Title level={3} className="dashboard-metric-card__value">
            {value}
          </Typography.Title>
          <Typography.Text className={`dashboard-metric-card__delta dashboard-text--${tone}`}>
            {description}
          </Typography.Text>
        </span>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Chart option builders (read CSS vars at call time for correct theming)
// ---------------------------------------------------------------------------

function buildTrendOption(points: StatisticsTrendPoint[]): EChartsOption {
  const primaryColor = cssVar("--ku-color-primary", "#1677ff");
  const successColor = cssVar("--ku-color-success", "#16a34a");
  const warningColor = cssVar("--ku-color-orange", "#f59e0b");
  const borderColor = cssVar("--ku-border", "#E5EAF2");
  const textColor = cssVar("--ku-text-secondary", "#667085");

  return {
    color: [primaryColor, successColor, warningColor],
    grid: { top: 28, right: 20, bottom: 28, left: 42 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10 },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: points.map((p) => p.period),
      axisLine: { lineStyle: { color: borderColor } },
      axisTick: { show: false },
      axisLabel: { color: textColor },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: textColor },
      splitLine: { lineStyle: { color: borderColor } },
    },
    series: [
      {
        name: "上传",
        type: "line",
        smooth: true,
        data: points.map((p) => p.total_files),
        areaStyle: { color: "rgba(22, 119, 255, 0.12)" },
      },
      {
        name: "同步",
        type: "line",
        smooth: true,
        data: points.map((p) => p.synced_files),
      },
      {
        name: "待审",
        type: "line",
        smooth: true,
        data: points.map((p) => p.pending_review_files),
      },
    ],
  };
}

function buildCategoryOption(rows: StatisticsCategoryRow[]): EChartsOption {
  return {
    color: [
      cssVar("--ku-color-primary", "#1677ff"),
      cssVar("--ku-color-success", "#16a34a"),
      cssVar("--ku-color-orange", "#f59e0b"),
      cssVar("--ku-color-purple", "#7c3aed"),
      cssVar("--ku-color-cyan", "#06b6d4"),
    ],
    tooltip: { trigger: "item" },
    series: [
      {
        name: "分类占比",
        type: "pie",
        radius: ["56%", "76%"],
        center: ["50%", "50%"],
        label: { show: false },
        labelLine: { show: false },
        data: rows.map((row) => ({ value: row.total_files, name: row.category_name })),
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function UserRankingRow({ user, maxFiles }: { user: StatisticsUserRow; maxFiles: number }) {
  const percent = maxFiles > 0 ? Math.round((user.total_files / maxFiles) * 100) : 0;
  return (
    <div className="dashboard-ranking-row" key={user.user_id}>
      <span className="dashboard-ranking-row__rank">{user.rank}</span>
      <Avatar className="dashboard-ranking-row__avatar">{user.user_name.slice(0, 1)}</Avatar>
      <span className="dashboard-ranking-row__copy">
        <Typography.Text strong>{user.user_name}</Typography.Text>
        <Typography.Text type="secondary">{user.department ?? "未设置"}</Typography.Text>
      </span>
      <span className="dashboard-ranking-row__count">{formatNumber(user.total_files)}</span>
      <Progress percent={percent} showInfo={false} />
    </div>
  );
}

function FailureRow({ row, maxTasks }: { row: StatisticsFailureRow; maxTasks: number }) {
  const ratio = maxTasks > 0 ? (row.failed_tasks / maxTasks) * 100 : 0;
  return (
    <div className="dashboard-failure-row" key={row.reason}>
      <div className="dashboard-failure-row__header">
        <Typography.Text>{row.reason}</Typography.Text>
        <Typography.Text type="secondary">
          {formatNumber(row.failed_tasks)} 次 / {formatNumber(row.failed_files)} 个文件
        </Typography.Text>
      </div>
      <span className="dashboard-failure-row__track">
        <span className="dashboard-failure-row__bar" style={{ width: `${ratio}%` }} />
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const overviewQuery = useQuery({
    queryKey: ["dashboard", "overview"],
    queryFn: () => getStatisticsOverview({}),
  });

  const usersQuery = useQuery({
    queryKey: ["dashboard", "users"],
    queryFn: () =>
      getStatisticsUsers({ page: 1, page_size: 10, sort_by: "total_files", sort_order: "desc" }),
  });

  const categoriesQuery = useQuery({
    queryKey: ["dashboard", "categories"],
    queryFn: () => getStatisticsCategories({}),
  });

  const trendsQuery = useQuery({
    queryKey: ["dashboard", "trends"],
    queryFn: () => getStatisticsTrends({ group_by: "day" }),
  });

  const failuresQuery = useQuery({
    queryKey: ["dashboard", "failures"],
    queryFn: () => getStatisticsFailures({}),
  });

  const isLoading =
    overviewQuery.isLoading ||
    usersQuery.isLoading ||
    categoriesQuery.isLoading ||
    trendsQuery.isLoading ||
    failuresQuery.isLoading;

  const overview = overviewQuery.data;
  const users = usersQuery.data?.items ?? [];
  const categories = categoriesQuery.data?.items ?? [];
  const trends = trendsQuery.data?.items ?? [];
  const failures = failuresQuery.data?.items ?? [];

  const maxUserFiles = useMemo(
    () => (users.length > 0 ? Math.max(...users.map((u) => u.total_files)) : 1),
    [users],
  );

  const maxFailedTasks = useMemo(
    () => (failures.length > 0 ? Math.max(...failures.map((f) => f.failed_tasks)) : 1),
    [failures],
  );

  const trendOption = useMemo(() => buildTrendOption(trends), [trends]);
  const categoryOption = useMemo(() => buildCategoryOption(categories), [categories]);

  const categoryTotal = categories.reduce((acc, row) => acc + row.total_files, 0);

  const syncSuccessRateStr = overview
    ? formatPercent(overview.sync_success_rate)
    : "-";

  return (
    <PageContainer
      className="dashboard-page"
      title="知识库运营总览"
      description="汇总上传、审核、AI 分析与 RAGFlow 同步关键指标。"
      actions={
        <Space className="dashboard-page-actions" wrap>
          <Button icon={<ReloadOutlined />} loading={isLoading}>
            刷新
          </Button>
          <Button type="primary" icon={<DownloadOutlined />}>
            导出报表
          </Button>
        </Space>
      }
    >
      {/* KPI 指标卡 */}
      <div className="dashboard-kpi-grid">
        <DashboardMetricCard
          title="文件总数"
          value={isLoading ? "-" : formatNumber(overview?.total_files ?? 0)}
          description={`今日新增 ${formatNumber(overview?.active_uploaders ?? 0)} 位上传者`}
          icon={<CloudUploadOutlined />}
          tone="primary"
        />
        <DashboardMetricCard
          title="已同步 RAGFlow"
          value={isLoading ? "-" : formatNumber(overview?.synced_files ?? 0)}
          description={`成功率 ${syncSuccessRateStr}`}
          icon={<CloudSyncOutlined />}
          tone="success"
        />
        <DashboardMetricCard
          title="待审核"
          value={isLoading ? "-" : formatNumber(overview?.pending_review_files ?? 0)}
          description={`含 ${formatNumber(overview?.sensitive_files ?? 0)} 个敏感风险`}
          icon={<ClockCircleOutlined />}
          tone="warning"
        />
        <DashboardMetricCard
          title="失败任务"
          value={isLoading ? "-" : formatNumber(overview?.failed_tasks ?? 0)}
          description="需管理员介入"
          icon={<WarningOutlined />}
          tone="danger"
        />
        <DashboardMetricCard
          title="风险文件"
          value={isLoading ? "-" : formatNumber(overview?.sensitive_files ?? 0)}
          description={`已拒绝 ${formatNumber(overview?.rejected_files ?? 0)} 个`}
          icon={<FileDoneOutlined />}
          tone="purple"
        />
      </div>

      <div className="dashboard-content-grid">
        {/* 上传趋势图 */}
        <Card
          className="dashboard-panel dashboard-trend-card dashboard-span-8"
          title={
            <Space>
              <RiseOutlined />
              上传与同步趋势
            </Space>
          }
        >
          {trendsQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 6 }} />
          ) : trends.length > 0 ? (
            <ReactECharts option={trendOption} className="dashboard-chart" />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势数据" />
          )}
        </Card>

        {/* 待办摘要 + 失败任务列表 */}
        <Card
          className="dashboard-panel dashboard-activity-card dashboard-span-4"
          title="最近失败任务"
        >
          {failuresQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : failures.length > 0 ? (
            <>
              <div className="dashboard-todo-strip">
                <div>
                  <Typography.Text type="secondary">待审核</Typography.Text>
                  <Typography.Title level={4}>
                    {formatNumber(overview?.pending_review_files ?? 0)}
                  </Typography.Title>
                </div>
                <div>
                  <Typography.Text type="secondary">高风险</Typography.Text>
                  <Typography.Title level={4}>
                    {formatNumber(overview?.sensitive_files ?? 0)}
                  </Typography.Title>
                </div>
                <div>
                  <Typography.Text type="secondary">失败</Typography.Text>
                  <Typography.Title level={4}>
                    {formatNumber(overview?.failed_tasks ?? 0)}
                  </Typography.Title>
                </div>
              </div>
              <div className="dashboard-failure-list">
                {failures.map((row) => (
                  <FailureRow key={row.reason} row={row} maxTasks={maxFailedTasks} />
                ))}
              </div>
            </>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无失败任务" />
          )}
        </Card>

        {/* 用户上传排行 */}
        <Card
          className="dashboard-panel dashboard-ranking-card dashboard-insight-card dashboard-span-6"
          title={
            <Space>
              <TeamOutlined />
              用户上传排行
            </Space>
          }
        >
          {usersQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 5 }} />
          ) : users.length > 0 ? (
            <div className="dashboard-ranking-list">
              {users.slice(0, 6).map((user) => (
                <UserRankingRow key={user.user_id} user={user} maxFiles={maxUserFiles} />
              ))}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无上传记录" />
          )}
        </Card>

        {/* 知识分类占比 */}
        <Card
          className="dashboard-panel dashboard-category-card dashboard-insight-card dashboard-span-6"
          title={
            <Space>
              <BarChartOutlined />
              知识分类占比
            </Space>
          }
        >
          {categoriesQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 5 }} />
          ) : categories.length > 0 ? (
            <div className="dashboard-category-layout">
              <div className="dashboard-category-visual">
                <ReactECharts option={categoryOption} className="dashboard-category-chart" />
                <div className="dashboard-category-total">
                  <Typography.Text type="secondary">总条目</Typography.Text>
                  <Typography.Title level={4}>{formatNumber(categoryTotal)}</Typography.Title>
                </div>
              </div>
              <div className="dashboard-category-details">
                {categories.map((row) => {
                  const percent =
                    categoryTotal > 0
                      ? Math.round((row.total_files / categoryTotal) * 100)
                      : 0;
                  return (
                    <div className="dashboard-category-row" key={row.category_id ?? row.category_name}>
                      <div className="dashboard-category-row__header">
                        <span className="dashboard-category-row__swatch dashboard-category-row__swatch--primary" />
                        <Typography.Text strong>{row.category_name}</Typography.Text>
                        <Typography.Text type="secondary">{row.total_files} 条</Typography.Text>
                        <Typography.Text className="dashboard-category-row__percent">
                          {percent}%
                        </Typography.Text>
                      </div>
                      <Progress percent={percent} showInfo={false} />
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分类数据" />
          )}
        </Card>

        {/* 概览文件状态 */}
        <Card
          className="dashboard-panel dashboard-efficiency-card dashboard-span-8"
          title="文件状态概览"
        >
          <div className="dashboard-quick-stats">
            <div className="dashboard-quick-stat">
              <Typography.Text type="secondary">总文件数</Typography.Text>
              <Typography.Title level={4}>
                {formatNumber(overview?.total_files ?? 0)}
              </Typography.Title>
              <Typography.Text type="secondary">知识库全部文档</Typography.Text>
            </div>
            <div className="dashboard-quick-stat">
              <Typography.Text type="secondary">已同步成功</Typography.Text>
              <Typography.Title level={4}>
                {formatNumber(overview?.synced_files ?? 0)}
              </Typography.Title>
              <Typography.Text type="secondary">同步成功率 {syncSuccessRateStr}</Typography.Text>
            </div>
            <div className="dashboard-quick-stat">
              <Typography.Text type="secondary">待审核</Typography.Text>
              <Typography.Title level={4}>
                {formatNumber(overview?.pending_review_files ?? 0)}
              </Typography.Title>
              <Typography.Text type="secondary">等待管理员处理</Typography.Text>
            </div>
            <div className="dashboard-quick-stat">
              <Typography.Text type="secondary">失败 / 风险</Typography.Text>
              <Typography.Title level={4}>
                {formatNumber(overview?.failed_files ?? 0)} /{" "}
                {formatNumber(overview?.sensitive_files ?? 0)}
              </Typography.Title>
              <Typography.Text type="secondary">需关注处理</Typography.Text>
            </div>
          </div>
        </Card>

        {/* 系统状态 */}
        <Card className="dashboard-panel dashboard-health-card dashboard-span-4" title="系统状态">
          <div className="dashboard-health-list">
            <div className="dashboard-health-row">
              <Space>
                <span className="dashboard-text--success">
                  <CheckCircleOutlined />
                </span>
                <Typography.Text strong>RAGFlow 连接</Typography.Text>
              </Space>
              <StatusTag kind="sync" value={overview ? "synced" : "not_synced"} />
            </div>
            <div className="dashboard-health-row">
              <Space>
                <span className="dashboard-text--primary">
                  <FileTextOutlined />
                </span>
                <Typography.Text strong>文件同步率</Typography.Text>
              </Space>
              <Progress
                percent={
                  overview ? Math.round(overview.sync_success_rate * 100) : 0
                }
                size="small"
              />
            </div>
          </div>
        </Card>
      </div>
    </PageContainer>
  );
}
