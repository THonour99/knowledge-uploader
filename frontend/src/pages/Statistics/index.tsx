import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  DatePicker,
  Empty,
  Input,
  Progress,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import {
  BellOutlined,
  CheckCircleOutlined,
  DownloadOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import ReactECharts from "echarts-for-react";
import { useMemo, useRef, useState } from "react";

import {
  exportStatistics,
  getStatisticsCategories,
  getStatisticsDepartments,
  getStatisticsExpiry,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  type ExpiryStatus,
  type StatisticsCategoryRow,
  type StatisticsDepartmentRow,
  type StatisticsExpiryStatusRow,
  type StatisticsFailureRow,
  type StatisticsQueryParams,
  type StatisticsTrendPoint,
  type StatisticsUserRow,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const { RangePicker } = DatePicker;

type CategoryChartRef = InstanceType<typeof ReactECharts>;
type DateRange = [Dayjs | null, Dayjs | null] | null;
type GroupBy = NonNullable<StatisticsQueryParams["group_by"]>;

const groupByOptions: Array<{ label: string; value: GroupBy }> = [
  { label: "按天", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
];

const syncStatusOptions = [
  { label: "同步状态：全部", value: "all" },
  { label: "已同步", value: "synced" },
  { label: "同步中", value: "syncing" },
  { label: "未同步", value: "not_synced" },
  { label: "同步失败", value: "failed" },
];

const reviewStatusOptions = [
  { label: "审核状态：全部", value: "all" },
  { label: "待审核", value: "pending" },
  { label: "已通过", value: "approved" },
  { label: "已拒绝", value: "rejected" },
];

const categoryColorTokens = [
  { token: "--ku-color-primary", fallback: "#1677ff" },
  { token: "--ku-color-success", fallback: "#16a34a" },
  { token: "--ku-color-warning", fallback: "#f59e0b" },
  { token: "--ku-color-danger", fallback: "#ef4444" },
  { token: "--ku-color-info", fallback: "#3b82f6" },
  { token: "--ku-color-cyan", fallback: "#06b6d4" },
  { token: "--ku-color-orange", fallback: "#f97316" },
  { token: "--ku-color-purple", fallback: "#7c3aed" },
  { token: "--ku-color-geekblue", fallback: "#2f54eb" },
  { token: "--ku-color-volcano", fallback: "#dc2626" },
];

const numberFormatter = new Intl.NumberFormat("zh-CN");

function formatNumber(value: number): string {
  return numberFormatter.format(value);
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 ** 4) {
    return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
  }
  if (bytes >= 1024 ** 3) {
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  }
  if (bytes >= 1024 ** 2) {
    return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
  }
  return `${formatNumber(bytes)} B`;
}

function formatDateTime(value?: string | null): string {
  return value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "-";
}

function syncRate(row: StatisticsUserRow): string {
  if (row.total_files === 0) {
    return "0.0%";
  }
  return `${((row.synced_files / row.total_files) * 100).toFixed(1)}%`;
}

function makeDownload(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

function cssVar(name: string, fallback = ""): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function chartTextColor(): string {
  return cssVar("--ku-text-secondary", "#57534E");
}

function categoryChartColors(): string[] {
  return categoryColorTokens.map(({ token, fallback }) => cssVar(token, fallback));
}

function buildTrendOption(points: StatisticsTrendPoint[]) {
  const primaryColor = cssVar("--ku-color-primary");
  const successColor = cssVar("--ku-color-success");
  const borderColor = cssVar("--ku-border");

  return {
    grid: { top: 28, right: 18, bottom: 32, left: 46 },
    tooltip: { trigger: "axis" },
    legend: {
      top: 0,
      left: 0,
      textStyle: { color: chartTextColor() },
      data: ["上传文件数", "已同步数量"],
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: points.map((point) => point.period),
      axisLabel: { color: chartTextColor() },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: chartTextColor() },
      splitLine: { lineStyle: { color: borderColor } },
    },
    series: [
      {
        name: "上传文件数",
        type: "line",
        smooth: true,
        data: points.map((point) => point.total_files),
        lineStyle: { color: primaryColor, width: 3 },
        itemStyle: { color: primaryColor },
      },
      {
        name: "已同步数量",
        type: "line",
        smooth: true,
        data: points.map((point) => point.synced_files),
        lineStyle: { color: successColor, width: 3 },
        itemStyle: { color: successColor },
      },
    ],
  };
}

function buildDepartmentOption(rows: StatisticsDepartmentRow[]) {
  const topRows = rows.slice(0, 8).reverse();
  const primaryColor = cssVar("--ku-color-primary");
  const borderColor = cssVar("--ku-border");

  return {
    grid: { top: 12, right: 28, bottom: 20, left: 72 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "value",
      axisLabel: { color: chartTextColor() },
      splitLine: { lineStyle: { color: borderColor } },
    },
    yAxis: {
      type: "category",
      data: topRows.map((row) => row.department),
      axisLabel: { color: chartTextColor() },
    },
    series: [
      {
        type: "bar",
        data: topRows.map((row) => row.total_files),
        barWidth: 10,
        itemStyle: { color: primaryColor, borderRadius: [0, 6, 6, 0] },
      },
    ],
  };
}

function buildCategoryOption(rows: StatisticsCategoryRow[], colors: string[]) {
  const cardColor = cssVar("--ku-bg-card", "#ffffff");

  return {
    color: colors,
    tooltip: { trigger: "item", formatter: "{b}: {c} 个文件 ({d}%)" },
    legend: { show: false },
    series: [
      {
        name: "分类分布",
        type: "pie",
        radius: ["42%", "64%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: true,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderRadius: 4, borderColor: cardColor, borderWidth: 2 },
        emphasis: { scale: true, scaleSize: 5 },
        data: rows.map((row) => ({ name: row.category_name, value: row.total_files })),
      },
    ],
  };
}

function topFailureTotal(rows: StatisticsFailureRow[]): number {
  return Math.max(...rows.map((row) => row.failed_tasks), 1);
}

const expiryStatusOrder: ExpiryStatus[] = ["expired", "expiring", "active", "never"];

function expiryStatusLabel(status: ExpiryStatus): string {
  const labels: Record<ExpiryStatus, string> = {
    active: "有效文件",
    expiring: "即将过期",
    expired: "已过期",
    never: "长期有效",
  };

  return labels[status];
}

function normalizeExpiryBreakdown(rows: StatisticsExpiryStatusRow[]): StatisticsExpiryStatusRow[] {
  const rowByStatus = new Map(rows.map((row) => [row.status, row]));

  return expiryStatusOrder.map((status) => rowByStatus.get(status) ?? { status, count: 0 });
}

interface CategoryDistributionLegendProps {
  colors: string[];
  rows: StatisticsCategoryRow[];
  onClearHighlight: () => void;
  onHighlight: (dataIndex: number) => void;
}

function CategoryDistributionLegend({
  colors,
  rows,
  onClearHighlight,
  onHighlight,
}: CategoryDistributionLegendProps) {
  const totalFiles = rows.reduce((sum, row) => sum + row.total_files, 0);

  return (
    <div className="statistics-category-legend" aria-label="分类分布图例">
      {rows.map((row, index) => {
        const percent = totalFiles > 0 ? row.total_files / totalFiles : 0;
        const color = colors[index % colors.length];
        const meta = `${formatNumber(row.total_files)} · ${formatPercent(percent)}`;

        return (
          <button
            key={row.category_id ?? row.category_name}
            type="button"
            className="statistics-category-legend__item"
            title={`${row.category_name}：${meta}`}
            aria-label={`${row.category_name}，${formatNumber(row.total_files)} 个文件，占比 ${formatPercent(percent)}`}
            onBlur={onClearHighlight}
            onFocus={() => onHighlight(index)}
            onMouseEnter={() => onHighlight(index)}
            onMouseLeave={onClearHighlight}
          >
            <span className="statistics-category-legend__swatch" style={{ background: color }} />
            <span className="statistics-category-legend__name">{row.category_name}</span>
            <span className="statistics-category-legend__meta">{meta}</span>
          </button>
        );
      })}
    </div>
  );
}
function StatisticsContributionWorkbench({
  exportLoading,
  filteredUsers,
  hasKeyword,
  onClearKeyword,
  onExport,
  totalUsers,
}: {
  exportLoading: boolean;
  filteredUsers: StatisticsUserRow[];
  hasKeyword: boolean;
  onClearKeyword: () => void;
  onExport: () => void;
  totalUsers: number;
}) {
  const visibleTotalFiles = filteredUsers.reduce((total, user) => total + user.total_files, 0);
  const visibleSyncedFiles = filteredUsers.reduce((total, user) => total + user.synced_files, 0);
  const visibleFailedFiles = filteredUsers.reduce((total, user) => total + user.failed_files, 0);
  const visiblePendingReviewFiles = filteredUsers.reduce(
    (total, user) => total + user.pending_review_files,
    0,
  );
  const syncQualityPercent =
    visibleTotalFiles === 0 ? 0 : Math.round((visibleSyncedFiles / visibleTotalFiles) * 100);
  const hasRisk = visibleFailedFiles > 0 || visiblePendingReviewFiles > 0;

  return (
    <section
      className="statistics-contribution-workbench"
      role="region"
      aria-label="贡献明细工作台"
    >
      <div className="statistics-contribution-workbench__main">
        <span className="statistics-contribution-workbench__icon">
          <TeamOutlined />
        </span>
        <span className="statistics-contribution-workbench__copy">
          <span className="statistics-contribution-workbench__title-row">
            <Typography.Text strong className="statistics-contribution-workbench__title">
              贡献明细工作台
            </Typography.Text>
            <StatusTag kind="health" value={hasRisk ? "unknown" : "ok"} variant="dot" />
          </span>
          <Typography.Text type="secondary">
            当前视图 {formatNumber(filteredUsers.length)} 位用户，样本总数{" "}
            {formatNumber(totalUsers)} 位。
          </Typography.Text>
        </span>
      </div>
      <div className="statistics-contribution-workbench__stats" aria-label="贡献明细摘要">
        <span className="statistics-contribution-workbench__stat statistics-contribution-workbench__stat--info">
          <Typography.Text type="secondary">上传文件</Typography.Text>
          <strong>{formatNumber(visibleTotalFiles)}</strong>
        </span>
        <span className="statistics-contribution-workbench__stat statistics-contribution-workbench__stat--success">
          <Typography.Text type="secondary">同步成功</Typography.Text>
          <strong>{formatNumber(visibleSyncedFiles)}</strong>
        </span>
        <span className="statistics-contribution-workbench__stat statistics-contribution-workbench__stat--warning">
          <Typography.Text type="secondary">待审核</Typography.Text>
          <strong>{formatNumber(visiblePendingReviewFiles)}</strong>
        </span>
        <span className="statistics-contribution-workbench__stat statistics-contribution-workbench__stat--danger">
          <Typography.Text type="secondary">失败文件</Typography.Text>
          <strong>{formatNumber(visibleFailedFiles)}</strong>
        </span>
      </div>
      <div className="statistics-contribution-workbench__action-panel">
        <div className="statistics-contribution-workbench__quality" aria-label="当前视图同步质量">
          <span className="statistics-contribution-workbench__quality-copy">
            <Typography.Text type="secondary">同步质量</Typography.Text>
            <strong>{syncQualityPercent}%</strong>
          </span>
          <Progress percent={syncQualityPercent} size="small" showInfo={false} />
        </div>
        <Space wrap className="statistics-contribution-workbench__actions">
          <Button size="small" disabled={!hasKeyword} onClick={onClearKeyword}>
            清空搜索
          </Button>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={exportLoading}
            onClick={onExport}
          >
            导出明细
          </Button>
        </Space>
      </div>
    </section>
  );
}
export default function StatisticsPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const categoryChartRef = useRef<CategoryChartRef | null>(null);
  const [dateRange, setDateRange] = useState<DateRange>([dayjs().subtract(30, "day"), dayjs()]);
  const [department, setDepartment] = useState<string | undefined>();
  const [categoryId, setCategoryId] = useState<string | undefined>();
  const [syncStatus, setSyncStatus] = useState<string | undefined>();
  const [reviewStatus, setReviewStatus] = useState<string | undefined>();
  const [groupBy, setGroupBy] = useState<GroupBy>("day");
  const [userKeyword, setUserKeyword] = useState("");

  const queryParams = useMemo<StatisticsQueryParams>(
    () => ({
      start_date: dateRange?.[0]?.format("YYYY-MM-DD"),
      end_date: dateRange?.[1]?.format("YYYY-MM-DD"),
      department,
      category_id: categoryId,
      sync_status: syncStatus,
      review_status: reviewStatus,
      group_by: groupBy,
    }),
    [categoryId, dateRange, department, groupBy, reviewStatus, syncStatus],
  );

  const usersParams = useMemo<StatisticsQueryParams>(
    () => ({
      ...queryParams,
      page: 1,
      page_size: 100,
      sort_by: "total_files",
      sort_order: "desc",
    }),
    [queryParams],
  );

  const overviewQuery = useQuery({
    queryKey: ["statistics", "overview", queryParams],
    queryFn: () => getStatisticsOverview(queryParams),
  });
  const usersQuery = useQuery({
    queryKey: ["statistics", "users", usersParams],
    queryFn: () => getStatisticsUsers(usersParams),
  });
  const departmentsQuery = useQuery({
    queryKey: ["statistics", "departments", queryParams],
    queryFn: () => getStatisticsDepartments(queryParams),
  });
  const categoriesQuery = useQuery({
    queryKey: ["statistics", "categories", queryParams],
    queryFn: () => getStatisticsCategories(queryParams),
  });
  const trendsQuery = useQuery({
    queryKey: ["statistics", "trends", queryParams],
    queryFn: () => getStatisticsTrends(queryParams),
  });
  const failuresQuery = useQuery({
    queryKey: ["statistics", "failures", queryParams],
    queryFn: () => getStatisticsFailures(queryParams),
  });
  const expiryQuery = useQuery({
    queryKey: ["statistics", "expiry", queryParams],
    queryFn: () => getStatisticsExpiry(queryParams),
  });

  const exportMutation = useMutation({
    mutationFn: () => exportStatistics(queryParams),
    onSuccess: (blob) => {
      makeDownload(blob, `statistics-${dayjs().format("YYYYMMDD-HHmm")}.csv`);
      message.success("统计报表已导出");
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const overview = overviewQuery.data;
  const users = usersQuery.data?.items ?? [];
  const departments = departmentsQuery.data?.items ?? [];
  const categories = categoriesQuery.data?.items ?? [];
  const trends = trendsQuery.data?.items ?? [];
  const categoryColors = useMemo(() => categoryChartColors(), []);
  const failures = failuresQuery.data?.items ?? [];
  const expiry = expiryQuery.data;
  const expiryBreakdown = normalizeExpiryBreakdown(expiry?.items ?? []);
  const userSampleCount = usersQuery.data?.total ?? users.length;
  const isLoading =
    overviewQuery.isLoading ||
    usersQuery.isLoading ||
    departmentsQuery.isLoading ||
    categoriesQuery.isLoading ||
    trendsQuery.isLoading ||
    failuresQuery.isLoading ||
    expiryQuery.isLoading;
  const firstError = [
    overviewQuery,
    usersQuery,
    departmentsQuery,
    categoriesQuery,
    trendsQuery,
    failuresQuery,
  ].find((query) => query.isError)?.error;

  const filteredUsers = useMemo(() => {
    const keyword = userKeyword.trim().toLowerCase();
    if (!keyword) {
      return users;
    }
    return users.filter((user) =>
      [user.user_name, user.department].filter(Boolean).join(" ").toLowerCase().includes(keyword),
    );
  }, [userKeyword, users]);

  const departmentOptions = [
    { label: "部门：全部", value: "all" },
    ...departments.map((item) => ({ label: item.department, value: item.department })),
  ];
  const categoryOptions = [
    { label: "分类：全部", value: "all" },
    ...categories.flatMap((item) =>
      item.category_id ? [{ label: item.category_name, value: item.category_id }] : [],
    ),
  ];

  const refreshStatistics = async () => {
    await queryClient.invalidateQueries({ queryKey: ["statistics"] });
  };
  const clearCategoryHighlight = () => {
    const chart = categoryChartRef.current?.getEchartsInstance();

    chart?.dispatchAction({ type: "downplay", seriesIndex: 0 });
    chart?.dispatchAction({ type: "hideTip" });
  };

  const highlightCategory = (dataIndex: number) => {
    const chart = categoryChartRef.current?.getEchartsInstance();

    if (!chart) {
      return;
    }

    chart.dispatchAction({ type: "downplay", seriesIndex: 0 });
    chart.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex });
    chart.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex });
  };

  const columns: ColumnsType<StatisticsUserRow> = [
    { title: "排名", dataIndex: "rank", key: "rank", width: 72, align: "center" },
    {
      title: "用户",
      dataIndex: "user_name",
      key: "user_name",
      width: 150,
      render: (value: string, record) => (
        <span className="statistics-user-cell">
          <span className="statistics-user-cell__avatar">{value.slice(0, 1)}</span>
          <span>
            <Typography.Text strong className="single-line-text" title={value}>
              {value}
            </Typography.Text>
            <Typography.Text type="secondary" className="single-line-text" title={record.user_id}>
              {record.user_id}
            </Typography.Text>
          </span>
        </span>
      ),
    },
    {
      title: "部门",
      dataIndex: "department",
      key: "department",
      width: 130,
      render: (value?: string | null) => value ?? "未设置",
    },
    {
      title: "上传文件总数",
      dataIndex: "total_files",
      key: "total_files",
      width: 130,
      align: "right",
      render: (value: number) => formatNumber(value),
    },
    {
      title: "已同步成功数量",
      dataIndex: "synced_files",
      key: "synced_files",
      width: 150,
      align: "right",
      render: (value: number, record) => (
        <Typography.Text className="statistics-positive">
          {formatNumber(value)} ({syncRate(record)})
        </Typography.Text>
      ),
    },
    {
      title: "同步失败数量",
      dataIndex: "failed_files",
      key: "failed_files",
      width: 130,
      align: "right",
      render: (value: number, record) => (
        <Typography.Text className="statistics-negative">
          {formatNumber(value)} (
          {record.total_files ? ((value / record.total_files) * 100).toFixed(1) : "0.0"}%)
        </Typography.Text>
      ),
    },
    {
      title: "待审核数量",
      dataIndex: "pending_review_files",
      key: "pending_review_files",
      width: 120,
      align: "right",
      render: (value: number, record) => (
        <Typography.Text className="statistics-warning">
          {formatNumber(value)} (
          {record.total_files ? ((value / record.total_files) * 100).toFixed(1) : "0.0"}%)
        </Typography.Text>
      ),
    },
    {
      title: "总文件大小",
      dataIndex: "total_file_size",
      key: "total_file_size",
      width: 120,
      align: "right",
      render: formatSize,
    },
    {
      title: "最近上传时间",
      dataIndex: "last_upload_at",
      key: "last_upload_at",
      width: 150,
      render: formatDateTime,
    },
  ];

  return (
    <PageContainer
      title="统计报表"
      description="查看上传趋势、部门贡献、分类分布和用户上传明细。"
      actions={
        <Space wrap className="statistics-page-actions">
          <RangePicker value={dateRange} onChange={(value) => setDateRange(value)} allowClear />
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={exportMutation.isPending}
            onClick={() => exportMutation.mutate()}
          >
            导出报表
          </Button>
        </Space>
      }
    >
      {firstError ? (
        <Alert
          className="statistics-alert"
          type="error"
          showIcon
          message="统计数据加载失败"
          description={firstError.message}
          action={<Button onClick={() => void refreshStatistics()}>重试</Button>}
        />
      ) : null}

      <div className="statistics-filter-bar">
        <Select
          className="filter-toolbar__control"
          value={department ?? "all"}
          options={departmentOptions}
          onChange={(value) => setDepartment(value === "all" ? undefined : value)}
        />
        <Select
          className="filter-toolbar__control"
          value={categoryId ?? "all"}
          options={categoryOptions}
          onChange={(value) => setCategoryId(value === "all" ? undefined : value)}
        />
        <Select
          className="filter-toolbar__control"
          value={syncStatus ?? "all"}
          options={syncStatusOptions}
          onChange={(value) => setSyncStatus(value === "all" ? undefined : value)}
        />
        <Select
          className="filter-toolbar__control"
          value={reviewStatus ?? "all"}
          options={reviewStatusOptions}
          onChange={(value) => setReviewStatus(value === "all" ? undefined : value)}
        />
        <Select
          className="filter-toolbar__control"
          value={groupBy}
          options={groupByOptions}
          onChange={setGroupBy}
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={() => void refreshStatistics()}
          loading={isLoading}
        />
      </div>

      <div className="statistics-kpi-grid">
        <KpiCard
          icon={<FileTextOutlined />}
          title="总上传文件数"
          value={overview?.total_files ?? 0}
          formatter={formatNumber}
          description={`总容量 ${formatSize(overview?.total_file_size ?? 0)}`}
          trend={trends.map((point) => point.total_files)}
          tone="primary"
        />
        <KpiCard
          icon={<CheckCircleOutlined />}
          title="同步成功率"
          value={overview?.sync_success_rate ?? 0}
          formatter={formatPercent}
          description={`${formatNumber(overview?.synced_files ?? 0)} 个文件已同步`}
          trend={trends.map((point) => point.synced_files)}
          tone="purple"
        />
        <KpiCard
          icon={<WarningOutlined />}
          title="失败任务数"
          value={overview?.failed_tasks ?? 0}
          formatter={formatNumber}
          description={`${formatNumber(overview?.failed_files ?? 0)} 个文件失败`}
          tone="danger"
        />
      </div>

      <div className="statistics-main-grid">
        <Card className="document-panel statistics-chart-card" title="上传趋势">
          {trends.length > 0 ? (
            <ReactECharts option={buildTrendOption(trends)} className="statistics-chart" />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势数据" />
          )}
        </Card>

        <Card className="document-panel statistics-chart-card" title="部门贡献排行">
          {departments.length > 0 ? (
            <ReactECharts
              option={buildDepartmentOption(departments)}
              className="statistics-chart"
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无部门数据" />
          )}
        </Card>

        <Card
          className="document-panel statistics-chart-card statistics-category-card"
          title="分类分布"
        >
          {categories.length > 0 ? (
            <div className="statistics-category-distribution">
              <ReactECharts
                ref={categoryChartRef}
                option={buildCategoryOption(categories, categoryColors)}
                className="statistics-chart statistics-category-chart"
              />
              <CategoryDistributionLegend
                colors={categoryColors}
                rows={categories}
                onClearHighlight={clearCategoryHighlight}
                onHighlight={highlightCategory}
              />
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分类数据" />
          )}
        </Card>

        <Card className="document-panel statistics-ranking-card" title="活跃贡献用户排行">
          {filteredUsers.length > 0 ? (
            <div className="statistics-ranking-list">
              {filteredUsers.slice(0, 6).map((user) => (
                <div className="statistics-ranking-row" key={user.user_id}>
                  <span className="statistics-ranking-row__rank">{user.rank}</span>
                  <span className="statistics-user-cell__avatar">{user.user_name.slice(0, 1)}</span>
                  <span className="statistics-ranking-row__name">
                    <Typography.Text strong>{user.user_name}</Typography.Text>
                    <Typography.Text type="secondary">
                      {user.department ?? "未设置"}
                    </Typography.Text>
                  </span>
                  <Typography.Text>{formatNumber(user.total_files)}</Typography.Text>
                </div>
              ))}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无用户排行" />
          )}
        </Card>
      </div>

      <div className="statistics-bottom-grid">
        <Card className="document-panel table-card statistics-users-card" title="用户上传统计">
          <StatisticsContributionWorkbench
            exportLoading={exportMutation.isPending}
            filteredUsers={filteredUsers}
            hasKeyword={userKeyword.trim().length > 0}
            onClearKeyword={() => setUserKeyword("")}
            onExport={() => exportMutation.mutate()}
            totalUsers={userSampleCount}
          />
          <div className="statistics-table-toolbar">
            <Input.Search
              className="statistics-user-search"
              placeholder="搜索用户姓名、部门"
              value={userKeyword}
              allowClear
              prefix={<RiseOutlined />}
              onChange={(event) => setUserKeyword(event.target.value)}
            />
          </div>
          <Table<StatisticsUserRow>
            rowKey="user_id"
            columns={columns}
            dataSource={filteredUsers}
            loading={usersQuery.isLoading}
            pagination={{ pageSize: 10, showSizeChanger: false, total: filteredUsers.length }}
            locale={{ emptyText: "暂无用户上传统计" }}
            scroll={{ x: 1220 }}
          />
        </Card>

        <div className="statistics-side-stack">
          <Card className="document-panel statistics-failure-card" title="失败统计">
            {failures.length > 0 ? (
              <div className="statistics-failure-list">
                {failures.map((failure) => {
                  const ratio = (failure.failed_tasks / topFailureTotal(failures)) * 100;
                  return (
                    <div className="statistics-failure-row" key={failure.reason}>
                      <div className="statistics-failure-row__header">
                        <Typography.Text>{failure.reason}</Typography.Text>
                        <Typography.Text type="secondary">
                          {formatNumber(failure.failed_tasks)} ({formatNumber(failure.failed_files)}{" "}
                          文件)
                        </Typography.Text>
                      </div>
                      <span className="statistics-failure-row__track">
                        <span
                          className="statistics-failure-row__bar"
                          style={{ width: `${ratio}%` }}
                        />
                      </span>
                    </div>
                  );
                })}
                <div className="statistics-failure-total">
                  <Typography.Text strong>总计</Typography.Text>
                  <Typography.Text>
                    {formatNumber(overview?.failed_tasks ?? 0)} 个任务
                  </Typography.Text>
                </div>
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无失败任务" />
            )}
          </Card>

          <Card
            className="document-panel statistics-expiry-card"
            loading={expiryQuery.isLoading}
            title={
              <Space>
                <BellOutlined />
                过期提醒
              </Space>
            }
            extra={
              expiry ? (
                <Typography.Text type="secondary">
                  {formatNumber(expiry.remind_days)} 天窗口
                </Typography.Text>
              ) : null
            }
          >
            {expiryQuery.isError ? (
              <Alert
                type="warning"
                showIcon
                message="过期统计接口暂不可用"
                description={expiryQuery.error.message}
              />
            ) : expiry ? (
              <Space direction="vertical" size={16} className="statistics-expiry-content">
                <div className="statistics-expiry-summary">
                  <div>
                    <Typography.Text type="secondary">过期规则文件</Typography.Text>
                    <Typography.Title level={4}>{formatNumber(expiry.total)}</Typography.Title>
                  </div>
                  <div>
                    <Typography.Text type="secondary">即将过期</Typography.Text>
                    <Typography.Title level={4} className="statistics-warning">
                      {formatNumber(expiry.expiring)}
                    </Typography.Title>
                  </div>
                  <div>
                    <Typography.Text type="secondary">已过期</Typography.Text>
                    <Typography.Title level={4} className="statistics-negative">
                      {formatNumber(expiry.expired)}
                    </Typography.Title>
                  </div>
                </div>

                <div className="statistics-expiry-breakdown">
                  <div className="statistics-expiry-section-title">
                    <Typography.Text strong>状态分布</Typography.Text>
                    <Typography.Text type="secondary">
                      {dayjs(expiry.as_of).format("YYYY-MM-DD")} 至{" "}
                      {dayjs(expiry.window_end).format("YYYY-MM-DD")}
                    </Typography.Text>
                  </div>
                  {expiryBreakdown.map((row) => (
                    <div className="statistics-expiry-breakdown-row" key={row.status}>
                      <StatusTag kind="expiry" value={row.status} />
                      <Typography.Text>{expiryStatusLabel(row.status)}</Typography.Text>
                      <Typography.Text strong>{formatNumber(row.count)}</Typography.Text>
                    </div>
                  ))}
                </div>
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无过期统计" />
            )}
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
