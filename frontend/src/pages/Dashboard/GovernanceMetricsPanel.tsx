import {
  ApiOutlined,
  CloudServerOutlined,
  DollarOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Card, DatePicker, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { useMemo, useState } from "react";

import {
  getGovernanceCapacity,
  getGovernanceLlmUsage,
  getGovernanceRagflowUsage,
  type GovernanceCapacityGroupBy,
  type GovernanceCapacityQuery,
  type GovernanceCapacityRow,
  type GovernanceKnownCurrencyCost,
  type GovernanceLlmGroupBy,
  type GovernanceLlmQuery,
  type GovernanceLlmUsageRow,
  type GovernancePhysicalCapacity,
  type GovernancePhysicalDimension,
  type GovernanceRagflowGroupBy,
  type GovernanceRagflowQuery,
  type GovernanceRagflowUsageRow,
  type GovernanceUnknownCostBucket,
} from "../../api/client";
import { QueryBoundary } from "../../components/QueryBoundary";
import { StatusTag } from "../../components/StatusTag";

const { RangePicker } = DatePicker;
const PAGE_SIZE = 5;
const MAX_INCLUSIVE_DAYS = 366;

type GovernanceDateRange = [Dayjs | null, Dayjs | null] | null;

const CAPACITY_GROUP_OPTIONS: Array<{ label: string; value: GovernanceCapacityGroupBy }> = [
  { label: "全部", value: "none" },
  { label: "处理阶段", value: "processing_stage" },
  { label: "部门", value: "department" },
  { label: "文件类型", value: "file_type" },
  { label: "日期", value: "day" },
];

const PHYSICAL_DIMENSION_OPTIONS: Array<{
  label: string;
  value: GovernancePhysicalDimension;
}> = [
  { label: "集群物理容量", value: "cluster" },
  { label: "部门物理容量", value: "department" },
  { label: "文件类型物理容量", value: "file_type" },
];

const LLM_GROUP_OPTIONS: Array<{ label: string; value: GovernanceLlmGroupBy }> = [
  { label: "全部", value: "none" },
  { label: "供应商", value: "provider" },
  { label: "模型", value: "model" },
  { label: "部门", value: "department" },
  { label: "日期", value: "day" },
];

const RAGFLOW_GROUP_OPTIONS: Array<{ label: string; value: GovernanceRagflowGroupBy }> = [
  { label: "全部", value: "none" },
  { label: "结果", value: "result" },
  { label: "操作", value: "operation" },
  { label: "失败类型", value: "failure_category" },
  { label: "部门", value: "department" },
  { label: "日期", value: "day" },
];

const UNKNOWN_COST_LABELS: Record<GovernanceUnknownCostBucket["status"], string> = {
  unknown_pricing: "定价未确认",
  unknown_usage: "Token 用量未知",
  legacy_unverifiable: "历史记录不可核验",
};

interface ResolvedWindow {
  params: { start_at: string; end_before: string } | null;
  error: string | null;
}

export function resolveGovernanceWindow(dateRange: GovernanceDateRange): ResolvedWindow {
  const start = dateRange?.[0]?.startOf("day");
  const end = dateRange?.[1]?.startOf("day");
  if (!start || !end || !start.isValid() || !end.isValid()) {
    return { params: null, error: "请选择完整的 UTC 日期范围" };
  }
  const startDate = start.format("YYYY-MM-DD");
  const endDate = end.format("YYYY-MM-DD");
  const startUtcMs = Date.parse(startDate + "T00:00:00.000Z");
  const endUtcMs = Date.parse(endDate + "T00:00:00.000Z");
  const inclusiveDays = (endUtcMs - startUtcMs) / 86_400_000 + 1;
  if (inclusiveDays < 1) {
    return { params: null, error: "开始日期必须早于或等于结束日期" };
  }
  if (inclusiveDays > MAX_INCLUSIVE_DAYS) {
    return { params: null, error: "治理指标查询范围不能超过 366 个 UTC 自然日" };
  }
  return {
    params: {
      start_at: `${start.format("YYYY-MM-DD")}T00:00:00.000Z`,
      end_before: `${end.add(1, "day").format("YYYY-MM-DD")}T00:00:00.000Z`,
    },
    error: null,
  };
}

export function getDefaultGovernanceDateRange(now = new Date()): [Dayjs, Dayjs] {
  const utcCalendarDay = dayjs(now.toISOString().slice(0, 10));
  return [utcCalendarDay.subtract(29, "day"), utcCalendarDay];
}

function normalizeInteger(value: string): { negative: boolean; digits: string } | null {
  if (!/^-?\d+$/.test(value)) {
    return null;
  }
  const negative = value.startsWith("-");
  const rawDigits = negative ? value.slice(1) : value;
  const digits = rawDigits.replace(/^0+(?=\d)/, "");
  return { negative: negative && digits !== "0", digits };
}

function formatIntegerString(value: string | null | undefined): string {
  if (value === null || value === undefined) {
    return "-";
  }
  const normalized = normalizeInteger(value);
  if (!normalized) {
    return "-";
  }
  const grouped = normalized.digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${normalized.negative ? "-" : ""}${grouped}`;
}

function formatByteString(value: string | null | undefined): string {
  if (value === null || value === undefined || !/^\d+$/.test(value)) {
    return "-";
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"] as const;
  const bytes = BigInt(value);
  let divisor = 1n;
  let unitIndex = 0;
  while (unitIndex < units.length - 1 && bytes >= divisor * 1024n) {
    divisor *= 1024n;
    unitIndex += 1;
  }
  const whole = bytes / divisor;
  const tenth = ((bytes % divisor) * 10n) / divisor;
  const decimal = tenth > 0n ? `.${tenth.toString()}` : "";
  return `${formatIntegerString(whole.toString())}${decimal} ${units[unitIndex]}`;
}

function formatMicrounits(value: string): string {
  const normalized = normalizeInteger(value);
  if (!normalized) {
    return "-";
  }
  const amount = BigInt(value);
  const absolute = amount < 0n ? -amount : amount;
  const whole = absolute / 1_000_000n;
  const fraction = (absolute % 1_000_000n).toString().padStart(6, "0").replace(/0+$/, "");
  const sign = amount < 0n ? "-" : "";
  return `${sign}${formatIntegerString(whole.toString())}${fraction ? `.${fraction}` : ""}`;
}

function isIntegerZero(value: string): boolean {
  const normalized = normalizeInteger(value);
  return normalized?.digits === "0";
}

function formatUtcDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }
  return `${parsed.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function DimensionLabel({ label }: { label: string }) {
  return (
    <span className="dashboard-governance-dimension">
      <Typography.Text strong>{label}</Typography.Text>
    </span>
  );
}

function KnownCostList({ rows }: { rows: GovernanceKnownCurrencyCost[] }) {
  if (rows.length === 0) {
    return <Typography.Text type="secondary">无可核验成本</Typography.Text>;
  }
  return (
    <div className="dashboard-governance-cost-list">
      {rows.map((row) => (
        <span key={row.currency}>
          <Typography.Text strong>
            {row.currency} {formatMicrounits(row.estimated_cost_microunits)}
          </Typography.Text>
          <Typography.Text type="secondary">
            {formatIntegerString(row.calls)} 次 · Prompt {formatIntegerString(row.prompt_tokens)} ·
            Completion {formatIntegerString(row.completion_tokens)}
            {isIntegerZero(row.estimated_cost_microunits) ? " · 估算金额为 0" : ""}
          </Typography.Text>
        </span>
      ))}
    </div>
  );
}

function UnknownCostList({ rows }: { rows: GovernanceUnknownCostBucket[] }) {
  if (rows.length === 0) {
    return <Typography.Text type="secondary">无未知成本</Typography.Text>;
  }
  return (
    <div className="dashboard-governance-cost-list">
      {rows.map((row) => (
        <span key={row.status}>
          <Typography.Text type="warning">
            {UNKNOWN_COST_LABELS[row.status]} {formatIntegerString(row.calls)} 次
          </Typography.Text>
          <Typography.Text type="secondary">
            已知 Token {formatIntegerString(row.known_prompt_tokens)} +{" "}
            {formatIntegerString(row.known_completion_tokens)} · Token 未知调用{" "}
            {formatIntegerString(row.calls_with_unknown_tokens)} 次
          </Typography.Text>
        </span>
      ))}
    </div>
  );
}

function PhysicalCapacitySummary({ physical }: { physical: GovernancePhysicalCapacity }) {
  const hasCapacity = physical.total_bytes !== null;
  return (
    <div className="dashboard-governance-physical" aria-label="MinIO 物理容量快照">
      <div className="dashboard-governance-physical__heading">
        <span>
          <CloudServerOutlined />
          <Typography.Text strong>MinIO 集群物理容量</Typography.Text>
        </span>
        <StatusTag kind="capacity" value={physical.status} />
      </div>
      {hasCapacity ? (
        <div className="dashboard-governance-physical__metrics">
          <span>
            <Typography.Text type="secondary">总容量</Typography.Text>
            <Typography.Text strong>{formatByteString(physical.total_bytes)}</Typography.Text>
          </span>
          <span>
            <Typography.Text type="secondary">已使用</Typography.Text>
            <Typography.Text strong>{formatByteString(physical.used_bytes)}</Typography.Text>
          </span>
          <span>
            <Typography.Text type="secondary">可用</Typography.Text>
            <Typography.Text strong>{formatByteString(physical.free_bytes)}</Typography.Text>
          </span>
          <span>
            <Typography.Text type="secondary">采集时间</Typography.Text>
            <Typography.Text strong>{formatUtcDateTime(physical.captured_at)}</Typography.Text>
          </span>
        </div>
      ) : (
        <Typography.Text type="secondary">
          {physical.status === "unsupported_dimension"
            ? "MinIO 仅提供集群级原始物理容量，无法按部门或文件类型可靠拆分。"
            : "尚未取得可信的 MinIO 容量快照，请检查指标采集与 JWT/CA 配置。"}
        </Typography.Text>
      )}
      <Typography.Text type="secondary" className="dashboard-governance-basis">
        物理口径：MinIO 原始集群容量；逻辑口径：所选 UTC 窗口内上传的数据库文件记录。
      </Typography.Text>
    </div>
  );
}

const capacityColumns: ColumnsType<GovernanceCapacityRow> = [
  {
    title: "分组",
    dataIndex: "dimension_label",
    key: "dimension",
    fixed: "left",
    width: 160,
    render: (label: string) => <DimensionLabel label={label} />,
  },
  {
    title: "文件数",
    dataIndex: "file_count",
    key: "file_count",
    align: "right",
    width: 100,
    render: formatIntegerString,
  },
  {
    title: "活跃逻辑容量",
    dataIndex: "active_logical_bytes",
    key: "active_logical_bytes",
    align: "right",
    width: 130,
    render: formatByteString,
  },
  {
    title: "保留的非活跃容量",
    dataIndex: "retained_inactive_bytes",
    key: "retained_inactive_bytes",
    align: "right",
    width: 150,
    render: formatByteString,
  },
  {
    title: "被引用总容量",
    dataIndex: "total_referenced_bytes",
    key: "total_referenced_bytes",
    align: "right",
    width: 130,
    render: formatByteString,
  },
];

const llmColumns: ColumnsType<GovernanceLlmUsageRow> = [
  {
    title: "分组",
    dataIndex: "dimension_label",
    key: "dimension",
    width: 150,
    render: (label: string) => <DimensionLabel label={label} />,
  },
  {
    title: "调用",
    dataIndex: "total_calls",
    key: "total_calls",
    align: "right",
    width: 90,
    render: formatIntegerString,
  },
  {
    title: "可核验估算成本",
    dataIndex: "known_costs",
    key: "known_costs",
    width: 260,
    render: (rows: GovernanceKnownCurrencyCost[]) => <KnownCostList rows={rows} />,
  },
  {
    title: "未知口径",
    dataIndex: "unknown_costs",
    key: "unknown_costs",
    width: 250,
    render: (rows: GovernanceUnknownCostBucket[]) => <UnknownCostList rows={rows} />,
  },
];

const ragflowColumns: ColumnsType<GovernanceRagflowUsageRow> = [
  {
    title: "分组",
    dataIndex: "dimension_label",
    key: "dimension",
    width: 150,
    render: (label: string) => <DimensionLabel label={label} />,
  },
  {
    title: "调用",
    dataIndex: "calls",
    key: "calls",
    align: "right",
    width: 80,
    render: formatIntegerString,
  },
  {
    title: "已完成",
    dataIndex: "completed_calls",
    key: "completed_calls",
    align: "right",
    width: 90,
    render: formatIntegerString,
  },
  {
    title: "失败",
    dataIndex: "failure_calls",
    key: "failure_calls",
    align: "right",
    width: 80,
    render: (value: string) => (
      <Typography.Text type={isIntegerZero(value) ? "secondary" : "danger"}>
        {formatIntegerString(value)}
      </Typography.Text>
    ),
  },
  {
    title: "进行中",
    dataIndex: "in_progress_calls",
    key: "in_progress_calls",
    align: "right",
    width: 90,
    render: formatIntegerString,
  },
  {
    title: "已完成调用累计耗时",
    dataIndex: "total_latency_ms",
    key: "total_latency_ms",
    align: "right",
    width: 110,
    render: (value: string) => `${formatIntegerString(value)} ms`,
  },
];

export function GovernanceMetricsPanel() {
  const [dateRange, setDateRange] = useState<GovernanceDateRange>(() =>
    getDefaultGovernanceDateRange(),
  );
  const [capacityGroup, setCapacityGroup] = useState<GovernanceCapacityGroupBy>("processing_stage");
  const [physicalDimension, setPhysicalDimension] =
    useState<GovernancePhysicalDimension>("cluster");
  const [llmGroup, setLlmGroup] = useState<GovernanceLlmGroupBy>("provider");
  const [ragflowGroup, setRagflowGroup] = useState<GovernanceRagflowGroupBy>("result");
  const [capacityPage, setCapacityPage] = useState(1);
  const [llmPage, setLlmPage] = useState(1);
  const [ragflowPage, setRagflowPage] = useState(1);

  const resolvedWindow = useMemo(() => resolveGovernanceWindow(dateRange), [dateRange]);
  const baseParams = resolvedWindow.params ?? {};

  const capacityParams: GovernanceCapacityQuery = {
    ...baseParams,
    group_by: capacityGroup,
    physical_dimension: physicalDimension,
    page: capacityPage,
    page_size: PAGE_SIZE,
  };
  const llmParams: GovernanceLlmQuery = {
    ...baseParams,
    group_by: llmGroup,
    page: llmPage,
    page_size: PAGE_SIZE,
  };
  const ragflowParams: GovernanceRagflowQuery = {
    ...baseParams,
    group_by: ragflowGroup,
    page: ragflowPage,
    page_size: PAGE_SIZE,
  };

  const capacityQuery = useQuery({
    queryKey: ["dashboard", "governance", "capacity", capacityParams],
    queryFn: () => getGovernanceCapacity(capacityParams),
    enabled: resolvedWindow.params !== null,
  });
  const llmQuery = useQuery({
    queryKey: ["dashboard", "governance", "llm", llmParams],
    queryFn: () => getGovernanceLlmUsage(llmParams),
    enabled: resolvedWindow.params !== null,
  });
  const ragflowQuery = useQuery({
    queryKey: ["dashboard", "governance", "ragflow", ragflowParams],
    queryFn: () => getGovernanceRagflowUsage(ragflowParams),
    enabled: resolvedWindow.params !== null,
  });

  function handleDateRangeChange(value: GovernanceDateRange) {
    setDateRange(value);
    setCapacityPage(1);
    setLlmPage(1);
    setRagflowPage(1);
  }

  return (
    <Card
      className="dashboard-panel dashboard-governance-shell dashboard-span-12"
      title={
        <Space>
          <SafetyCertificateOutlined />
          容量与成本治理
        </Space>
      }
    >
      <div className="dashboard-governance-toolbar">
        <div>
          <Typography.Text strong>查询窗口</Typography.Text>
          <Typography.Text type="secondary">
            按 UTC 自然日查询，结束日期转换为次日 00:00 的半开区间。
          </Typography.Text>
        </div>
        <RangePicker
          aria-label="治理指标日期范围"
          allowClear={false}
          value={dateRange}
          onChange={(value) => handleDateRangeChange(value)}
        />
      </div>

      {resolvedWindow.error ? (
        <Alert type="error" showIcon message="日期范围无效" description={resolvedWindow.error} />
      ) : null}

      <Alert
        className="dashboard-governance-notice"
        type="warning"
        showIcon
        message="成本不是预算门禁"
        description="可核验估算成本、零金额记录与未知成本会分开展示；当前尚未定义月度预算周期、币种和软/硬阈值，不能用本页自动阻断调用。指标仅提供聚合结果，不展示正文、文件名或个人明细。"
      />

      <div className="dashboard-governance-grid">
        <section
          className="dashboard-governance-section dashboard-governance-section--capacity"
          aria-labelledby="governance-capacity-title"
        >
          <div className="dashboard-governance-section__header">
            <div>
              <Typography.Title id="governance-capacity-title" level={5}>
                <CloudServerOutlined /> 容量
              </Typography.Title>
              <Typography.Text type="secondary">
                数据库逻辑引用与 MinIO 集群物理容量采用不同口径。
              </Typography.Text>
            </div>
            <Space wrap>
              <Select
                aria-label="容量分组"
                value={capacityGroup}
                options={CAPACITY_GROUP_OPTIONS}
                onChange={(value) => {
                  setCapacityGroup(value);
                  setCapacityPage(1);
                }}
              />
              <Select
                aria-label="物理容量维度"
                value={physicalDimension}
                options={PHYSICAL_DIMENSION_OPTIONS}
                onChange={(value) => {
                  setPhysicalDimension(value);
                  setCapacityPage(1);
                }}
              />
            </Space>
          </div>
          <QueryBoundary
            isLoading={capacityQuery.isLoading}
            isError={capacityQuery.isError}
            error={capacityQuery.error}
            onRetry={() => void capacityQuery.refetch()}
            errorTitle="容量指标加载失败"
            skeletonRows={6}
          >
            {capacityQuery.data ? (
              <>
                <PhysicalCapacitySummary physical={capacityQuery.data.physical} />
                <Table
                  aria-label="逻辑容量分组明细"
                  columns={capacityColumns}
                  dataSource={capacityQuery.data.items}
                  rowKey={(row) => row.dimension_key}
                  size="small"
                  scroll={{ x: 690 }}
                  pagination={{
                    current: capacityQuery.data.pagination.page,
                    pageSize: capacityQuery.data.pagination.page_size,
                    total: capacityQuery.data.pagination.total,
                    showSizeChanger: false,
                    hideOnSinglePage: true,
                    onChange: setCapacityPage,
                  }}
                />
              </>
            ) : null}
          </QueryBoundary>
        </section>

        <section className="dashboard-governance-section" aria-labelledby="governance-llm-title">
          <div className="dashboard-governance-section__header">
            <div>
              <Typography.Title id="governance-llm-title" level={5}>
                <DollarOutlined /> LLM 用量与成本
              </Typography.Title>
              <Typography.Text type="secondary">
                按币种保留微单位精度，未知口径绝不折算为 0。
              </Typography.Text>
            </div>
            <Select
              aria-label="LLM 用量分组"
              value={llmGroup}
              options={LLM_GROUP_OPTIONS}
              onChange={(value) => {
                setLlmGroup(value);
                setLlmPage(1);
              }}
            />
          </div>
          <QueryBoundary
            isLoading={llmQuery.isLoading}
            isError={llmQuery.isError}
            error={llmQuery.error}
            onRetry={() => void llmQuery.refetch()}
            errorTitle="LLM 用量加载失败"
            skeletonRows={5}
          >
            {llmQuery.data ? (
              <Table
                aria-label="LLM 用量与成本明细"
                columns={llmColumns}
                dataSource={llmQuery.data.items}
                rowKey={(row) => row.dimension_key}
                size="small"
                scroll={{ x: 750 }}
                pagination={{
                  current: llmQuery.data.pagination.page,
                  pageSize: llmQuery.data.pagination.page_size,
                  total: llmQuery.data.pagination.total,
                  showSizeChanger: false,
                  hideOnSinglePage: true,
                  onChange: setLlmPage,
                }}
              />
            ) : null}
          </QueryBoundary>
        </section>

        <section
          className="dashboard-governance-section"
          aria-labelledby="governance-ragflow-title"
        >
          <div className="dashboard-governance-section__header">
            <div>
              <Typography.Title id="governance-ragflow-title" level={5}>
                <ApiOutlined /> RAGFlow 调用
              </Typography.Title>
              <Typography.Text type="secondary">
                展示调用、完成、失败、进行中和累计耗时。
              </Typography.Text>
            </div>
            <Select
              aria-label="RAGFlow 调用分组"
              value={ragflowGroup}
              options={RAGFLOW_GROUP_OPTIONS}
              onChange={(value) => {
                setRagflowGroup(value);
                setRagflowPage(1);
              }}
            />
          </div>
          <QueryBoundary
            isLoading={ragflowQuery.isLoading}
            isError={ragflowQuery.isError}
            error={ragflowQuery.error}
            onRetry={() => void ragflowQuery.refetch()}
            errorTitle="RAGFlow 调用加载失败"
            skeletonRows={5}
          >
            {ragflowQuery.data ? (
              <Table
                aria-label="RAGFlow 调用明细"
                columns={ragflowColumns}
                dataSource={ragflowQuery.data.items}
                rowKey={(row) => row.dimension_key}
                size="small"
                scroll={{ x: 600 }}
                pagination={{
                  current: ragflowQuery.data.pagination.page,
                  pageSize: ragflowQuery.data.pagination.page_size,
                  total: ragflowQuery.data.pagination.total,
                  showSizeChanger: false,
                  hideOnSinglePage: true,
                  onChange: setRagflowPage,
                }}
              />
            ) : null}
          </QueryBoundary>
        </section>
      </div>
    </Card>
  );
}
