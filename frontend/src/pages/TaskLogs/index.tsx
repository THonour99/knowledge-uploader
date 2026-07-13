import { useState } from "react";
import {
  ClockCircleOutlined,
  CloseCircleOutlined,
  CheckCircleOutlined,
  OrderedListOutlined,
  ReloadOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Descriptions,
  Drawer,
  Popconfirm,
  Select,
  Space,
  Table,
  Timeline,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelTask,
  getTask,
  listTasks,
  retryTask,
  type SyncTask,
  type SyncTaskLog,
  type TaskListQuery,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { QueryBoundary } from "../../components/QueryBoundary";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { colors } from "../../theme/tokens";
import "./styles.css";

const TASK_TYPE_OPTIONS = [
  { value: "", label: "全部类型" },
  { value: "ragflow_upload", label: "ragflow_upload" },
  { value: "ragflow_parse", label: "ragflow_parse" },
  { value: "ai_analysis", label: "ai_analysis" },
];

const TASK_STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "queued", label: "队列中" },
  { value: "running", label: "运行中" },
  { value: "succeeded", label: "已成功" },
  { value: "failed", label: "已失败" },
  { value: "canceled", label: "已取消" },
];

/**
 * Maps a SyncTask status string to a StatusTag sync kind value.
 * StatusTag sync kind supports: not_synced / queued / syncing / synced / failed
 */
function toSyncKindValue(status: string): string {
  switch (status) {
    case "queued":
      return "queued";
    case "running":
      return "syncing";
    case "succeeded":
      return "synced";
    case "failed":
      return "failed";
    case "canceled":
      return "not_synced";
    default:
      return "not_synced";
  }
}

function formatDatetime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function logDotColor(status: string): string {
  switch (status) {
    case "running":
      return colors.primary;
    case "failed":
      return colors.danger;
    case "succeeded":
      return colors.success;
    default:
      return colors.textDisabled;
  }
}

function logIcon(status: string) {
  switch (status) {
    case "running":
      return <SyncOutlined spin style={{ color: colors.primary }} />;
    case "failed":
      return <CloseCircleOutlined style={{ color: colors.danger }} />;
    case "succeeded":
      return <CheckCircleOutlined style={{ color: colors.success }} />;
    default:
      return <ClockCircleOutlined style={{ color: colors.textDisabled }} />;
  }
}


interface TaskDetailDrawerProps {
  taskId: string | null;
  open: boolean;
  onClose: () => void;
}

function TaskDetailOverview({ task }: { task: SyncTask }) {
  return (
    <section className="task-detail-overview" role="region" aria-label="任务执行摘要">
      <span className="task-detail-overview__icon">
        <SyncOutlined />
      </span>
      <span className="task-detail-overview__copy">
        <span className="task-detail-overview__title-row">
          <Typography.Text strong>{task.task_type}</Typography.Text>
          <StatusTag kind="sync" value={toSyncKindValue(task.status)} />
        </span>
        <Typography.Text type="secondary">任务 ID：{task.id}</Typography.Text>
      </span>
      <div className="task-detail-overview__stats" aria-label="任务执行指标">
        <span className="task-detail-overview__stat">
          <Typography.Text type="secondary">关联文件</Typography.Text>
          <strong>{task.file_id}</strong>
        </span>
        <span className="task-detail-overview__stat">
          <Typography.Text type="secondary">重试次数</Typography.Text>
          <strong>
            {task.retry_count} / {task.max_retry_count}
          </strong>
        </span>
        <span className="task-detail-overview__stat">
          <Typography.Text type="secondary">开始时间</Typography.Text>
          <strong>{formatDatetime(task.started_at)}</strong>
        </span>
        <span className="task-detail-overview__stat">
          <Typography.Text type="secondary">结束时间</Typography.Text>
          <strong>{formatDatetime(task.finished_at)}</strong>
        </span>
      </div>
    </section>
  );
}

function TaskDetailDrawer({ taskId, open, onClose }: TaskDetailDrawerProps) {
  const {
    data: task,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => getTask(taskId as string),
    enabled: open && taskId !== null,
  });

  return (
    <Drawer
      title="任务详情"
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
      className="task-detail-drawer"
    >
      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        isEmpty={!task}
        error={error}
        onRetry={() => void refetch()}
        skeletonRows={5}
        emptyDescription="暂无任务详情"
      >
        {task ? (
          <Space direction="vertical" style={{ width: "100%" }} size="large">
            <TaskDetailOverview task={task} />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="任务 ID">{task.id}</Descriptions.Item>
              <Descriptions.Item label="关联文件">{task.file_id}</Descriptions.Item>
              <Descriptions.Item label="任务类型">{task.task_type}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <StatusTag kind="sync" value={toSyncKindValue(task.status)} />
              </Descriptions.Item>
              <Descriptions.Item label="重试次数">
                {task.retry_count} / {task.max_retry_count}
              </Descriptions.Item>
              <Descriptions.Item label="开始时间">
                {formatDatetime(task.started_at)}
              </Descriptions.Item>
              <Descriptions.Item label="结束时间">
                {formatDatetime(task.finished_at)}
              </Descriptions.Item>
            </Descriptions>

            {task.error_message ? (
              <Alert
                className="task-detail-error"
                type="error"
                message="失败原因"
                description={task.error_message}
                showIcon
              />
            ) : null}

            {task.logs.length > 0 ? (
              <Card
                title={
                  <span className="task-detail-log-panel__title">
                    <OrderedListOutlined />
                    执行日志
                  </span>
                }
                size="small"
                className="task-detail-log-panel"
              >
                <Timeline
                  className="task-detail-timeline"
                  items={task.logs.map((log: SyncTaskLog) => ({
                    dot: logIcon(log.status),
                    color: logDotColor(log.status),
                    children: (
                      <Space direction="vertical" size={2}>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {formatDatetime(log.created_at)}
                        </Typography.Text>
                        <Typography.Text>{log.message}</Typography.Text>
                      </Space>
                    ),
                  }))}
                />
              </Card>
            ) : (
              <Typography.Text type="secondary">暂无日志记录</Typography.Text>
            )}
          </Space>
        ) : null}
      </QueryBoundary>
    </Drawer>
  );
}
export default function TaskLogsPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();

  const [filters, setFilters] = useState<TaskListQuery>({});
  const [drawerTaskId, setDrawerTaskId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const tasksQuery = useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => listTasks(filters),
  });
  const tasks = tasksQuery.data?.items ?? [];
  const totalTasks = tasksQuery.data?.total ?? 0;
  const runningTasks = tasks.filter((task) => task.status === "running").length;
  const failedTasks = tasks.filter((task) => task.status === "failed").length;

  const retryMutation = useMutation({
    mutationFn: (id: string) => retryTask(id),
    onSuccess: () => {
      void message.success("任务已重新入队");
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (err: Error) => {
      void message.error(err.message);
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (id: string) => cancelTask(id),
    onSuccess: () => {
      void message.success("任务已取消");
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (err: Error) => {
      void message.error(err.message);
    },
  });

  function openDetail(taskId: string) {
    setDrawerTaskId(taskId);
    setDrawerOpen(true);
  }

  function closeDrawer() {
    setDrawerOpen(false);
  }

  const columns: ColumnsType<SyncTask> = [
    {
      title: "任务类型",
      dataIndex: "task_type",
      key: "task_type",
      width: 140,
    },
    {
      title: "关联文件",
      dataIndex: "file_id",
      key: "file_id",
      width: 200,
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: string) => <StatusTag kind="sync" value={toSyncKindValue(status)} />,
    },
    {
      title: "重试次数",
      dataIndex: "retry_count",
      key: "retry_count",
      width: 90,
      align: "center",
    },
    {
      title: "开始时间",
      dataIndex: "started_at",
      key: "started_at",
      width: 160,
      render: (v: string | null) => formatDatetime(v),
    },
    {
      title: "结束时间",
      dataIndex: "finished_at",
      key: "finished_at",
      width: 160,
      render: (v: string | null) => formatDatetime(v),
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right" as const,
      width: 160,
      render: (_: unknown, record: SyncTask) => (
        <Space size="small">
          <Button
            size="small"
            onClick={() => openDetail(record.id)}
            data-testid={`detail-${record.id}`}
          >
            详情
          </Button>
          {record.status === "failed" ? (
            <Popconfirm
              title="确认重试该任务？"
              okText="确认"
              cancelText="取消"
              onConfirm={() => retryMutation.mutate(record.id)}
            >
              <Button size="small" type="primary" data-testid={`retry-${record.id}`}>
                重试
              </Button>
            </Popconfirm>
          ) : null}
          {record.status === "queued" || record.status === "running" ? (
            <Popconfirm
              title="确认取消该任务？"
              okText="确认"
              cancelText="取消"
              onConfirm={() => cancelMutation.mutate(record.id)}
            >
              <Button size="small" danger data-testid={`cancel-${record.id}`}>
                取消
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <PageContainer
      title="任务日志"
      description="追踪 RAGFlow 同步、解析与 AI 分析任务的队列状态和执行记录。"
    >
      <div className="metric-grid">
        <KpiCard
          icon={<OrderedListOutlined />}
          title="任务总数"
          value={totalTasks}
          description="满足当前筛选条件"
          tone="primary"
        />
        <KpiCard
          icon={<SyncOutlined />}
          title="运行中"
          value={runningTasks}
          description="正在执行任务"
          tone="info"
        />
        <KpiCard
          icon={<CloseCircleOutlined />}
          title="失败任务"
          value={failedTasks}
          description="需要重试或排查"
          tone="danger"
        />
      </div>

      <Card className="document-panel table-card">
        <div className="table-section-header">
          <span className="table-section-header__copy">
            <Typography.Title level={4} className="table-section-header__title">
              任务列表
            </Typography.Title>
            <Typography.Text className="table-section-header__meta">
              当前显示 {tasks.length} 条任务，共 {totalTasks} 条队列记录
            </Typography.Text>
          </span>
          <StatusTag kind="health" value={tasksQuery.isError ? "error" : "ok"} variant="dot" />
        </div>

        <div className="filter-toolbar">
          <Select
            className="filter-toolbar__control"
            placeholder="任务类型"
            style={{ width: 180 }}
            options={TASK_TYPE_OPTIONS}
            value={filters.task_type ?? ""}
            onChange={(v: string) => setFilters((prev) => ({ ...prev, task_type: v || undefined }))}
          />
          <Select
            className="filter-toolbar__control"
            placeholder="状态"
            style={{ width: 160 }}
            options={TASK_STATUS_OPTIONS}
            value={filters.status ?? ""}
            onChange={(v: string) => setFilters((prev) => ({ ...prev, status: v || undefined }))}
          />
          <Button
            icon={<ReloadOutlined />}
            loading={tasksQuery.isFetching}
            onClick={() => void tasksQuery.refetch()}
          />
        </div>

        <Table<SyncTask>
          rowKey="id"
          loading={tasksQuery.isLoading}
          dataSource={tasks}
          columns={columns}
          pagination={{
            total: totalTasks,
            pageSize: 20,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
          }}
          scroll={{ x: 1000 }}
        />
      </Card>

      <TaskDetailDrawer taskId={drawerTaskId} open={drawerOpen} onClose={closeDrawer} />
    </PageContainer>
  );
}
