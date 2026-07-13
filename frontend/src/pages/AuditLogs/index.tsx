import {
  Button,
  Card,
  DatePicker,
  Descriptions,
  Drawer,
  Input,
  Pagination,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import {
  AuditOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";
import type { ColumnsType } from "antd/es/table";

import { type AuditLogItem, type AuditLogQuery, listAuditLogs } from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

const { RangePicker } = DatePicker;

const ACTION_OPTIONS = [
  { label: "操作类型：全部", value: "" },
  { label: "config.update", value: "config.update" },
  { label: "file.approve", value: "file.approve" },
  { label: "file.reject", value: "file.reject" },
  { label: "file.upload", value: "file.upload" },
  { label: "file.delete", value: "file.delete" },
  { label: "user.create", value: "user.create" },
  { label: "user.disable", value: "user.disable" },
  { label: "user.enable", value: "user.enable" },
  { label: "category.create", value: "category.create" },
  { label: "category.update", value: "category.update" },
  { label: "ai.config.update", value: "ai.config.update" },
];

function MetadataViewer({ metadata }: { metadata: Record<string, unknown> | null }) {
  if (metadata === null) {
    return <Typography.Text type="secondary">无</Typography.Text>;
  }

  return <pre className="audit-metadata-viewer">{JSON.stringify(metadata, null, 2)}</pre>;
}


function AuditDetailOverview({ record }: { record: AuditLogItem }) {
  return (
    <section className="audit-detail-overview" role="region" aria-label="审计事件摘要">
      <span className="audit-detail-overview__icon">
        <AuditOutlined />
      </span>
      <span className="audit-detail-overview__copy">
        <span className="audit-detail-overview__title-row">
          <Typography.Text code>{record.action}</Typography.Text>
          <StatusTag kind="health" value="ok" variant="dot" />
        </span>
        <Typography.Text type="secondary">
          {record.actor_name ?? record.actor_id}
          {record.actor_email ? ` · ${record.actor_email}` : ""}
        </Typography.Text>
      </span>
      <div className="audit-detail-overview__stats" aria-label="审计事件指标">
        <span className="audit-detail-overview__stat">
          <Typography.Text type="secondary">操作时间</Typography.Text>
          <strong>{dayjs(record.created_at).format("YYYY-MM-DD HH:mm:ss")}</strong>
        </span>
        <span className="audit-detail-overview__stat">
          <Typography.Text type="secondary">操作对象</Typography.Text>
          <strong>
            {record.target_type} / {record.target_id ?? "-"}
          </strong>
        </span>
        <span className="audit-detail-overview__stat">
          <Typography.Text type="secondary">来源 IP</Typography.Text>
          <strong>{record.ip_address ?? "-"}</strong>
        </span>
        <span className="audit-detail-overview__stat">
          <Typography.Text type="secondary">结果摘要</Typography.Text>
          <strong>{record.reason ?? "-"}</strong>
        </span>
      </div>
    </section>
  );
}
export default function AuditLogsPage() {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [actorFilter, setActorFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [targetTypeFilter, setTargetTypeFilter] = useState("");
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [detailRecord, setDetailRecord] = useState<AuditLogItem | null>(null);

  const queryParams: AuditLogQuery = {
    page,
    page_size: pageSize,
    ...(actorFilter.trim() ? { actor_id: actorFilter.trim() } : {}),
    ...(actionFilter ? { action: actionFilter } : {}),
    ...(targetTypeFilter.trim() ? { target_type: targetTypeFilter.trim() } : {}),
    ...(dateRange
      ? {
          created_from: dateRange[0].startOf("day").toISOString(),
          created_to: dateRange[1].endOf("day").toISOString(),
        }
      : {}),
  };

  const logsQuery = useQuery({
    queryKey: ["audit-logs", queryParams],
    queryFn: () => listAuditLogs(queryParams),
  });

  const logs = logsQuery.data?.items ?? [];
  const total = logsQuery.data?.total ?? 0;
  const pageActorCount = new Set(logs.map((log) => log.actor_id)).size;
  const configActionCount = logs.filter((log) => log.action.includes("config")).length;
  const fileActionCount = logs.filter((log) => log.action.startsWith("file.")).length;

  const handleActorSearch = (value: string) => {
    setActorFilter(value);
    setPage(1);
  };

  const handleActionChange = (value: string) => {
    setActionFilter(value);
    setPage(1);
  };

  const handleTargetTypeSearch = (value: string) => {
    setTargetTypeFilter(value);
    setPage(1);
  };

  const handleDateRangeChange = (values: unknown) => {
    setDateRange(values as [Dayjs, Dayjs] | null);
    setPage(1);
  };

  const handlePageChange = (newPage: number) => {
    setPage(newPage);
  };

  const resetFilters = () => {
    setActorFilter("");
    setActionFilter("");
    setTargetTypeFilter("");
    setDateRange(null);
    setPage(1);
  };

  const columns: ColumnsType<AuditLogItem> = [
    {
      title: "操作时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 150,
      render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作人",
      key: "actor",
      width: 160,
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong style={{ fontSize: 13 }}>
            {record.actor_name ?? record.actor_id.slice(0, 8)}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.actor_email ?? ""}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "操作类型",
      dataIndex: "action",
      key: "action",
      width: 140,
      ellipsis: true,
      render: (value: string) => (
        <Typography.Text code style={{ fontSize: 12 }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "对象类型",
      dataIndex: "target_type",
      key: "target_type",
      width: 120,
      ellipsis: true,
    },
    {
      title: "IP 地址",
      dataIndex: "ip_address",
      key: "ip_address",
      width: 130,
      render: (value: string | null) => value ?? "-",
    },
    {
      title: "结果摘要",
      dataIndex: "reason",
      key: "reason",
      ellipsis: true,
      render: (value: string | null) => (
        <Typography.Text type="secondary">{value ?? "-"}</Typography.Text>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 72,
      fixed: "right" as const,
      render: (_, record) => (
        <Button
          type="link"
          size="small"
          className="table-link-button"
          onClick={() => setDetailRecord(record)}
          aria-label="详情"
        >
          详情
        </Button>
      ),
    },
  ];

  return (
    <PageContainer
      title="操作日志"
      description="查询管理员与系统操作的完整审计日志，支持按操作人、类型与时间筛选。"
    >
      <div className="metric-grid">
        <KpiCard
          icon={<AuditOutlined />}
          title="审计日志总数"
          value={total}
          description="满足当前筛选条件"
          tone="primary"
        />
        <KpiCard
          icon={<UserOutlined />}
          title="当前页操作人"
          value={pageActorCount}
          description="去重管理员账号"
          tone="success"
        />
        <KpiCard
          icon={<DatabaseOutlined />}
          title="配置变更"
          value={configActionCount}
          description="当前页配置类操作"
          tone="warning"
        />
        <KpiCard
          icon={<FileSearchOutlined />}
          title="文件操作"
          value={fileActionCount}
          description="当前页文件类操作"
          tone="info"
        />
      </div>

      <Card className="audit-logs-panel table-card">
        <div className="table-section-header">
          <span className="table-section-header__copy">
            <Typography.Title level={4} className="table-section-header__title">
              审计列表
            </Typography.Title>
            <Typography.Text className="table-section-header__meta">
              当前显示 {logs.length} 条审计事件，共 {total} 条匹配记录
            </Typography.Text>
          </span>
          <StatusTag kind="health" value={logsQuery.isError ? "error" : "ok"} variant="dot" />
        </div>

        <div className="filter-toolbar filter-toolbar--audit">
          <Input.Search
            className="filter-toolbar__search"
            placeholder="操作人 ID"
            value={actorFilter}
            onSearch={handleActorSearch}
            onChange={(event) => setActorFilter(event.target.value)}
            allowClear
            aria-label="操作人"
          />
          <Select
            className="filter-toolbar__control"
            aria-label="操作类型"
            value={actionFilter || undefined}
            placeholder="操作类型：全部"
            options={ACTION_OPTIONS}
            onChange={handleActionChange}
            allowClear
            style={{ minWidth: 180 }}
          />
          <Input.Search
            className="filter-toolbar__search"
            placeholder="对象类型"
            value={targetTypeFilter}
            onSearch={handleTargetTypeSearch}
            onChange={(event) => setTargetTypeFilter(event.target.value)}
            allowClear
            aria-label="对象类型"
          />
          <RangePicker
            className="filter-toolbar__range"
            placeholder={["开始日期", "结束日期"]}
            value={dateRange}
            onChange={handleDateRangeChange}
          />
          <Button onClick={resetFilters}>重置</Button>
          <Button
            icon={<ReloadOutlined />}
            loading={logsQuery.isFetching}
            onClick={() => void logsQuery.refetch()}
          />
        </div>

        <Table<AuditLogItem>
          className="audit-logs-table"
          rowKey="id"
          columns={columns}
          dataSource={logs}
          loading={logsQuery.isLoading}
          pagination={false}
          locale={{ emptyText: "暂无操作日志" }}
          tableLayout="fixed"
          scroll={{ x: 900 }}
        />

        <div className="audit-logs-pagination">
          <Pagination
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger={false}
            showTotal={(t) => `共 ${t} 条`}
            onChange={handlePageChange}
          />
        </div>
      </Card>

      <Drawer
        title="操作日志详情"
        open={detailRecord !== null}
        onClose={() => setDetailRecord(null)}
        width={560}
        destroyOnClose
        className="audit-detail-drawer"
      >
        {detailRecord !== null ? (
          <Space direction="vertical" style={{ width: "100%" }} size="large">
            <AuditDetailOverview record={detailRecord} />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="日志 ID">{detailRecord.id}</Descriptions.Item>
              <Descriptions.Item label="操作时间">
                {dayjs(detailRecord.created_at).format("YYYY-MM-DD HH:mm:ss")}
              </Descriptions.Item>
              <Descriptions.Item label="操作人">
                {detailRecord.actor_name ?? detailRecord.actor_id}
                {detailRecord.actor_email ? ` (${detailRecord.actor_email})` : ""}
              </Descriptions.Item>
              <Descriptions.Item label="操作人 ID">{detailRecord.actor_id}</Descriptions.Item>
              <Descriptions.Item label="操作类型">
                <Typography.Text code>{detailRecord.action}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="对象类型">{detailRecord.target_type}</Descriptions.Item>
              <Descriptions.Item label="对象 ID">{detailRecord.target_id ?? "-"}</Descriptions.Item>
              <Descriptions.Item label="IP 地址">
                {detailRecord.ip_address ?? "-"}
              </Descriptions.Item>
              <Descriptions.Item label="User Agent">
                <Typography.Text className="audit-detail-user-agent">
                  {detailRecord.user_agent ?? "-"}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="结果摘要">{detailRecord.reason ?? "-"}</Descriptions.Item>
              <Descriptions.Item label="元数据">
                <MetadataViewer metadata={detailRecord.metadata} />
              </Descriptions.Item>
            </Descriptions>
          </Space>
        ) : null}
      </Drawer>
    </PageContainer>
  );
}
