import {
  BarChartOutlined,
  ClockCircleOutlined,
  CloudSyncOutlined,
  CloudUploadOutlined,
  DownloadOutlined,
  FileDoneOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { App as AntdApp, Avatar, Button, Card, Progress, Skeleton, Space, Typography } from "antd";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  exportStatistics,
  getStatisticsCategories,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  getSystemReadiness,
  type StatisticsCategoryRow,
  type StatisticsFailureRow,
  type StatisticsTrendPoint,
  type StatisticsUserRow,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { QueryBoundary } from "../../components/QueryBoundary";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { downloadBlob } from "../../utils/download";
import { formatDateTime, formatNumber, formatPercent } from "../../utils/format";
import "./styles.css";

// ---------------------------------------------------------------------------
// Constants & helpers
// ---------------------------------------------------------------------------

// 分类明细色块顺序与饼图色板一致(warning 对应 --ku-color-orange,与饼图第三色相同)。
const CATEGORY_SWATCH_TONES = ["primary", "success", "warning", "purple", "cyan"] as const;

const DEPENDENCY_LABELS: Record<string, string> = {
  database: "数据库",
  redis: "缓存 Redis",
  rabbitmq: "消息队列",
  minio: "对象存储",
};

function cssVar(name: string, fallback = ""): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// 环比:序列最后一期 vs 前一期的百分比变化;不足两点或前值为 0 则不展示,避免误导。
function periodDelta(series: number[]): number | null {
  if (series.length < 2) {
    return null;
  }
  const last = series[series.length - 1];
  const prev = series[series.length - 2];
  if (prev === 0) {
    return null;
  }
  return ((last - prev) / prev) * 100;
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
    animationDuration: 700,
    animationEasing: "cubicOut",
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
        showSymbol: false,
        data: points.map((p) => p.total_files),
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(22, 119, 255, 0.18)" },
              { offset: 1, color: "rgba(22, 119, 255, 0)" },
            ],
          },
        },
      },
      {
        name: "同步",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: points.map((p) => p.synced_files),
      },
      {
        name: "待审",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: points.map((p) => p.pending_review_files),
      },
    ],
  };
}

function buildCategoryOption(rows: StatisticsCategoryRow[]): EChartsOption {
  const cardColor = cssVar("--ku-bg-card", "#ffffff");

  return {
    color: [
      cssVar("--ku-color-primary", "#1677ff"),
      cssVar("--ku-color-success", "#16a34a"),
      cssVar("--ku-color-orange", "#f59e0b"),
      cssVar("--ku-color-purple", "#7c3aed"),
      cssVar("--ku-color-cyan", "#06b6d4"),
    ],
    tooltip: { trigger: "item", formatter: "{b}: {c} 条 ({d}%)" },
    series: [
      {
        name: "分类占比",
        type: "pie",
        radius: ["56%", "76%"],
        center: ["50%", "50%"],
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderRadius: 4, borderColor: cardColor, borderWidth: 2 },
        emphasis: { scale: true, scaleSize: 6 },
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

type HealthTone = "success" | "warning" | "danger" | "empty";

interface TimelineLane {
  key: string;
  label: string;
  valueOf: (point: StatisticsTrendPoint) => number;
  toneOf: (value: number) => HealthTone;
}

const TIMELINE_LANES: TimelineLane[] = [
  {
    key: "upload",
    label: "上传活跃",
    valueOf: (point) => point.total_files,
    toneOf: (value) => (value > 0 ? "success" : "empty"),
  },
  {
    key: "synced",
    label: "同步成功",
    valueOf: (point) => point.synced_files,
    toneOf: (value) => (value > 0 ? "success" : "empty"),
  },
  {
    key: "pending",
    label: "待审积压",
    valueOf: (point) => point.pending_review_files,
    toneOf: (value) => (value > 0 ? "warning" : "success"),
  },
  {
    key: "failed",
    label: "失败文件",
    valueOf: (point) => point.failed_files,
    toneOf: (value) => (value > 0 ? "danger" : "success"),
  },
];

function DashboardHealthTimeline({ points }: { points: StatisticsTrendPoint[] }) {
  const visiblePoints = points.slice(-8);

  return (
    <section className="dashboard-health-timeline" aria-label="运行健康时间线">
      <div className="dashboard-health-timeline__header">
        <div>
          <Typography.Text strong>近周期健康矩阵</Typography.Text>
          <Typography.Text type="secondary">按上传、同步、待审和失败状态聚合</Typography.Text>
        </div>
        <div className="dashboard-health-timeline__legend" aria-label="健康状态图例">
          <span className="dashboard-health-timeline__legend-item">
            <i className="dashboard-health-timeline__cell dashboard-health-timeline__cell--success" />
            正常
          </span>
          <span className="dashboard-health-timeline__legend-item">
            <i className="dashboard-health-timeline__cell dashboard-health-timeline__cell--warning" />
            关注
          </span>
          <span className="dashboard-health-timeline__legend-item">
            <i className="dashboard-health-timeline__cell dashboard-health-timeline__cell--danger" />
            失败
          </span>
        </div>
      </div>
      <div className="dashboard-health-timeline__grid">
        <span className="dashboard-health-timeline__axis" />
        {visiblePoints.map((point) => (
          <span className="dashboard-health-timeline__period" key={point.period}>
            {point.period}
          </span>
        ))}
        {TIMELINE_LANES.map((lane) => (
          <div className="dashboard-health-timeline__lane" key={lane.key}>
            <Typography.Text type="secondary" className="dashboard-health-timeline__lane-label">
              {lane.label}
            </Typography.Text>
            {visiblePoints.map((point) => {
              const value = lane.valueOf(point);
              const tone = lane.toneOf(value);
              return (
                <span
                  aria-label={`${point.period} ${lane.label} ${formatNumber(value)}`}
                  className={`dashboard-health-timeline__cell dashboard-health-timeline__cell--${tone}`}
                  key={`${lane.key}-${point.period}`}
                  title={`${point.period} ${lane.label}: ${formatNumber(value)}`}
                />
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { message } = AntdApp.useApp();
  const [exporting, setExporting] = useState(false);

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

  const readinessQuery = useQuery({
    queryKey: ["dashboard", "readiness"],
    queryFn: getSystemReadiness,
  });

  const isFetching =
    overviewQuery.isFetching ||
    usersQuery.isFetching ||
    categoriesQuery.isFetching ||
    trendsQuery.isFetching ||
    failuresQuery.isFetching ||
    readinessQuery.isFetching;

  const overview = overviewQuery.data;
  const users = usersQuery.data?.items ?? [];
  const categories = categoriesQuery.data?.items ?? [];
  const trends = trendsQuery.data?.items ?? [];
  const failures = failuresQuery.data?.items ?? [];
  const readiness = readinessQuery.data;

  const trendTotals = trends.map((point) => point.total_files);
  const trendSynced = trends.map((point) => point.synced_files);
  const trendPending = trends.map((point) => point.pending_review_files);
  const trendFailed = trends.map((point) => point.failed_files);

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

  const syncSuccessRateStr = formatPercent(overview?.sync_success_rate);

  function handleRefresh() {
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }

  async function handleExport() {
    setExporting(true);
    try {
      const blob = await exportStatistics({});
      downloadBlob(blob, `知识库统计报表_${formatDateTime(new Date(), "YYYYMMDD_HHmm")}.csv`);
      message.success("报表已开始下载");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }

  const kpiSkeleton = (
    <div className="dashboard-kpi-grid">
      {Array.from({ length: 5 }).map((_, index) => (
        <Card key={index} className="kpi-card">
          <Skeleton active title={false} paragraph={{ rows: 2 }} />
        </Card>
      ))}
    </div>
  );

  return (
    <PageContainer
      className="dashboard-page"
      title="知识库运营总览"
      description="汇总上传、审核、AI 分析与 RAGFlow 同步关键指标。"
      actions={
        <Space className="dashboard-page-actions" wrap>
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={handleRefresh}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={exporting}
            onClick={() => void handleExport()}
          >
            导出报表
          </Button>
        </Space>
      }
    >
      {/* KPI 指标卡 */}
      <QueryBoundary
        isLoading={overviewQuery.isLoading}
        isError={overviewQuery.isError}
        error={overviewQuery.error}
        onRetry={() => void overviewQuery.refetch()}
        skeleton={kpiSkeleton}
        errorTitle="运营指标加载失败"
      >
        <div className="dashboard-kpi-grid">
          <KpiCard
            icon={<CloudUploadOutlined />}
            title="文件总数"
            value={overview?.total_files ?? 0}
            description={`活跃上传者 ${formatNumber(overview?.active_uploaders ?? 0)} 位`}
            tone="primary"
            trend={trendTotals}
            deltaPct={periodDelta(trendTotals)}
            deltaLabel="环比上期"
            onClick={() => navigate("/files")}
          />
          <KpiCard
            icon={<CloudSyncOutlined />}
            title="已同步 RAGFlow"
            value={overview?.synced_files ?? 0}
            description={`成功率 ${syncSuccessRateStr}`}
            tone="success"
            trend={trendSynced}
            deltaPct={periodDelta(trendSynced)}
            deltaLabel="环比上期"
            onClick={() => navigate("/files")}
          />
          <KpiCard
            icon={<ClockCircleOutlined />}
            title="待审核"
            value={overview?.pending_review_files ?? 0}
            description="等待管理员处理"
            tone="warning"
            trend={trendPending}
            deltaPct={periodDelta(trendPending)}
            deltaLabel="环比上期"
            deltaPositiveIsGood={false}
            onClick={() => navigate("/files")}
          />
          <KpiCard
            icon={<WarningOutlined />}
            title="失败任务"
            value={overview?.failed_tasks ?? 0}
            description="需管理员介入"
            tone="danger"
            trend={trendFailed}
            deltaPct={periodDelta(trendFailed)}
            deltaLabel="环比上期"
            deltaPositiveIsGood={false}
            onClick={() => navigate("/task-logs")}
          />
          <KpiCard
            icon={<FileDoneOutlined />}
            title="风险文件"
            value={overview?.sensitive_files ?? 0}
            description={`已拒绝 ${formatNumber(overview?.rejected_files ?? 0)} 个`}
            tone="purple"
            onClick={() => navigate("/files")}
          />
        </div>
      </QueryBoundary>

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
          <QueryBoundary
            isLoading={trendsQuery.isLoading}
            isError={trendsQuery.isError}
            error={trendsQuery.error}
            onRetry={() => void trendsQuery.refetch()}
            isEmpty={trends.length === 0}
            emptyDescription="暂无趋势数据"
            skeletonRows={6}
            errorTitle="趋势数据加载失败"
          >
            <ReactECharts option={trendOption} className="dashboard-chart" />
          </QueryBoundary>
        </Card>

        {/* 失败任务列表 */}
        <Card
          className="dashboard-panel dashboard-activity-card dashboard-span-4"
          title="最近失败任务"
        >
          <QueryBoundary
            isLoading={failuresQuery.isLoading}
            isError={failuresQuery.isError}
            error={failuresQuery.error}
            onRetry={() => void failuresQuery.refetch()}
            isEmpty={failures.length === 0}
            emptyDescription="暂无失败任务"
            skeletonRows={4}
            errorTitle="失败任务加载失败"
          >
            <div className="dashboard-failure-list">
              {failures.map((row) => (
                <FailureRow key={row.reason} row={row} maxTasks={maxFailedTasks} />
              ))}
            </div>
          </QueryBoundary>
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
          <QueryBoundary
            isLoading={usersQuery.isLoading}
            isError={usersQuery.isError}
            error={usersQuery.error}
            onRetry={() => void usersQuery.refetch()}
            isEmpty={users.length === 0}
            emptyDescription="暂无上传记录"
            skeletonRows={5}
            errorTitle="排行数据加载失败"
          >
            <div className="dashboard-ranking-list">
              {users.slice(0, 6).map((user) => (
                <UserRankingRow key={user.user_id} user={user} maxFiles={maxUserFiles} />
              ))}
            </div>
          </QueryBoundary>
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
          <QueryBoundary
            isLoading={categoriesQuery.isLoading}
            isError={categoriesQuery.isError}
            error={categoriesQuery.error}
            onRetry={() => void categoriesQuery.refetch()}
            isEmpty={categories.length === 0}
            emptyDescription="暂无分类数据"
            skeletonRows={5}
            errorTitle="分类数据加载失败"
          >
            <div className="dashboard-category-layout">
              <div className="dashboard-category-visual">
                <ReactECharts option={categoryOption} className="dashboard-category-chart" />
                <div className="dashboard-category-total">
                  <Typography.Text type="secondary">总条目</Typography.Text>
                  <Typography.Title level={4}>{formatNumber(categoryTotal)}</Typography.Title>
                </div>
              </div>
              <div className="dashboard-category-details">
                {categories.map((row, index) => {
                  const tone = CATEGORY_SWATCH_TONES[index % CATEGORY_SWATCH_TONES.length];
                  const percent =
                    categoryTotal > 0 ? Math.round((row.total_files / categoryTotal) * 100) : 0;
                  return (
                    <div
                      className="dashboard-category-row"
                      key={row.category_id ?? row.category_name}
                    >
                      <div className="dashboard-category-row__header">
                        <span
                          className={`dashboard-category-row__swatch dashboard-category-row__swatch--${tone}`}
                        />
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
          </QueryBoundary>
        </Card>

        {/* 概览文件状态 */}
        <Card
          className="dashboard-panel dashboard-efficiency-card dashboard-span-8"
          title="文件状态概览"
        >
          <QueryBoundary
            isLoading={overviewQuery.isLoading}
            isError={overviewQuery.isError}
            error={overviewQuery.error}
            onRetry={() => void overviewQuery.refetch()}
            skeletonRows={3}
            errorTitle="文件状态加载失败"
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
            <DashboardHealthTimeline points={trends} />
          </QueryBoundary>
        </Card>

        {/* 系统状态 */}
        <Card className="dashboard-panel dashboard-health-card dashboard-span-4" title="系统状态">
          <QueryBoundary
            isLoading={readinessQuery.isLoading}
            isError={readinessQuery.isError}
            error={readinessQuery.error}
            onRetry={() => void readinessQuery.refetch()}
            skeletonRows={4}
            errorTitle="系统状态加载失败"
          >
            <div className="dashboard-health-list">
              <div className="dashboard-health-row dashboard-health-row--overall">
                <Typography.Text strong>整体状态</Typography.Text>
                <StatusTag kind="health" value={readiness?.status ?? "unknown"} />
              </div>
              {Object.entries(readiness?.dependencies ?? {}).map(([key, dep]) => (
                <div className="dashboard-health-row" key={key}>
                  <Typography.Text>{DEPENDENCY_LABELS[key] ?? key}</Typography.Text>
                  <StatusTag kind="health" value={dep.status} />
                </div>
              ))}
            </div>
          </QueryBoundary>
        </Card>
      </div>
    </PageContainer>
  );
}
