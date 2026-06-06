import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  DatePicker,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DownloadOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  exportStatistics,
  getStatisticsCategories,
  getStatisticsDepartments,
  getStatisticsFailures,
  getStatisticsOverview,
  getStatisticsTrends,
  getStatisticsUsers,
  type StatisticsCategoryRow,
  type StatisticsDepartmentRow,
  type StatisticsFailureRow,
  type StatisticsQueryParams,
  type StatisticsTrendPoint,
  type StatisticsUserRow,
} from "../../api/client";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const { RangePicker } = DatePicker;

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

function KpiCard({
  icon,
  title,
  value,
  subtitle,
  tone,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  subtitle: string;
  tone: "primary" | "success" | "purple" | "warning" | "danger";
}) {
  return (
    <Card className="statistics-kpi-card">
      <div className="statistics-kpi-card__body">
        <span className={`statistics-kpi-card__icon statistics-kpi-card__icon--${tone}`}>
          {icon}
        </span>
        <span className="statistics-kpi-card__content">
          <Typography.Text type="secondary">{title}</Typography.Text>
          <Typography.Title level={3} className="statistics-kpi-card__value">
            {value}
          </Typography.Title>
          <Typography.Text type="secondary">{subtitle}</Typography.Text>
        </span>
      </div>
      <span className={`statistics-kpi-card__sparkline statistics-kpi-card__sparkline--${tone}`} />
    </Card>
  );
}

function chartTextColor(): string {
  return getComputedStyle(document.documentElement).getPropertyValue("--ku-text-secondary") || "#667085";
}

function buildTrendOption(points: StatisticsTrendPoint[]) {
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
    yAxis: { type: "value", axisLabel: { color: chartTextColor() }, splitLine: { lineStyle: { color: "#EEF2F7" } } },
    series: [
      {
        name: "上传文件数",
        type: "line",
        smooth: true,
        data: points.map((point) => point.total_files),
        lineStyle: { color: "#1677FF", width: 3 },
        itemStyle: { color: "#1677FF" },
        areaStyle: { color: "rgba(22, 119, 255, 0.08)" },
      },
      {
        name: "已同步数量",
        type: "line",
        smooth: true,
        data: points.map((point) => point.synced_files),
        lineStyle: { color: "#10B981", width: 3 },
        itemStyle: { color: "#10B981" },
        areaStyle: { color: "rgba(16, 185, 129, 0.08)" },
      },
    ],
  };
}

function buildDepartmentOption(rows: StatisticsDepartmentRow[]) {
  const topRows = rows.slice(0, 8).reverse();
  return {
    grid: { top: 12, right: 28, bottom: 20, left: 72 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", axisLabel: { color: chartTextColor() }, splitLine: { lineStyle: { color: "#EEF2F7" } } },
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
        itemStyle: { color: "#1677FF", borderRadius: [0, 6, 6, 0] },
      },
    ],
  };
}

function buildCategoryOption(rows: StatisticsCategoryRow[]) {
  return {
    tooltip: { trigger: "item" },
    legend: { orient: "vertical", right: 0, top: "middle", textStyle: { color: chartTextColor() } },
    series: [
      {
        type: "pie",
        radius: ["46%", "72%"],
        center: ["35%", "52%"],
        avoidLabelOverlap: true,
        label: { show: false },
        data: rows.map((row) => ({ name: row.category_name, value: row.total_files })),
      },
    ],
  };
}

function topFailureTotal(rows: StatisticsFailureRow[]): number {
  return Math.max(...rows.map((row) => row.failed_tasks), 1);
}

export default function StatisticsPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [dateRange, setDateRange] = useState<DateRange>([
    dayjs().subtract(30, "day"),
    dayjs(),
  ]);
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
  const failures = failuresQuery.data?.items ?? [];
  const isLoading =
    overviewQuery.isLoading ||
    usersQuery.isLoading ||
    departmentsQuery.isLoading ||
    categoriesQuery.isLoading ||
    trendsQuery.isLoading ||
    failuresQuery.isLoading;
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
    ...categories.map((item) => ({
      label: item.category_name,
      value: item.category_id ?? "uncategorized",
    })),
  ];

  const refreshStatistics = async () => {
    await queryClient.invalidateQueries({ queryKey: ["statistics"] });
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
          {formatNumber(value)} ({record.total_files ? ((value / record.total_files) * 100).toFixed(1) : "0.0"}%)
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
          {formatNumber(value)} ({record.total_files ? ((value / record.total_files) * 100).toFixed(1) : "0.0"}%)
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
      title="统计分析"
      description="查看上传趋势、部门贡献、分类分布和用户上传明细。"
      actions={
        <Space wrap className="statistics-page-actions">
          <RangePicker
            value={dateRange}
            onChange={(value) => setDateRange(value)}
            allowClear
          />
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
          value={formatNumber(overview?.total_files ?? 0)}
          subtitle={`总容量 ${formatSize(overview?.total_file_size ?? 0)}`}
          tone="primary"
        />
        <KpiCard
          icon={<TeamOutlined />}
          title="上传人数"
          value={formatNumber(overview?.active_uploaders ?? 0)}
          subtitle="有上传记录的用户"
          tone="success"
        />
        <KpiCard
          icon={<CheckCircleOutlined />}
          title="同步成功率"
          value={formatPercent(overview?.sync_success_rate ?? 0)}
          subtitle={`${formatNumber(overview?.synced_files ?? 0)} 个文件已同步`}
          tone="purple"
        />
        <KpiCard
          icon={<ClockCircleOutlined />}
          title="待审核数量"
          value={formatNumber(overview?.pending_review_files ?? 0)}
          subtitle={`${formatNumber(overview?.sensitive_files ?? 0)} 个敏感风险`}
          tone="warning"
        />
        <KpiCard
          icon={<WarningOutlined />}
          title="失败任务数"
          value={formatNumber(overview?.failed_tasks ?? 0)}
          subtitle={`${formatNumber(overview?.failed_files ?? 0)} 个文件失败`}
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
            <ReactECharts option={buildDepartmentOption(departments)} className="statistics-chart" />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无部门数据" />
          )}
        </Card>

        <Card className="document-panel statistics-chart-card" title="分类分布">
          {categories.length > 0 ? (
            <ReactECharts option={buildCategoryOption(categories)} className="statistics-chart" />
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
                    <Typography.Text type="secondary">{user.department ?? "未设置"}</Typography.Text>
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
                        {formatNumber(failure.failed_tasks)} ({formatNumber(failure.failed_files)} 文件)
                      </Typography.Text>
                    </div>
                    <span className="statistics-failure-row__track">
                      <span className="statistics-failure-row__bar" style={{ width: `${ratio}%` }} />
                    </span>
                  </div>
                );
              })}
              <div className="statistics-failure-total">
                <Typography.Text strong>总计</Typography.Text>
                <Typography.Text>{formatNumber(overview?.failed_tasks ?? 0)} 个任务</Typography.Text>
              </div>
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无失败任务" />
          )}
        </Card>
      </div>
    </PageContainer>
  );
}
