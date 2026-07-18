import { useCallback, useEffect, useMemo, useState } from "react";
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
  Input,
  Select,
  Space,
  Table,
  Timeline,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  cancelTask,
  getTask,
  listTasks,
  retryTask,
  type SyncTask,
  type SyncTaskLog,
  type SyncTaskSort,
  type SyncTaskStatus,
  type SyncTaskType,
  type TaskListQuery,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { SavedViewManager } from "../../components/SavedViewManager";
import { QueryBoundary } from "../../components/QueryBoundary";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { SessionBoundPopconfirm as Popconfirm } from "../../components/SessionBoundActions";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import { useAuthStore } from "../../store/auth.store";
import { colors } from "../../theme/tokens";
import "./styles.css";

const TASK_TYPE_OPTIONS: Array<{ value: "" | SyncTaskType; label: string }> = [
  { value: "", label: "全部类型" },
  { value: "ragflow_upload", label: "ragflow_upload" },
  { value: "ragflow_parse", label: "ragflow_parse" },
  { value: "ragflow_status_check", label: "ragflow_status_check" },
  { value: "ragflow_delete", label: "ragflow_delete" },
];

const TASK_STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "queued", label: "队列中" },
  { value: "running", label: "运行中" },
  { value: "succeeded", label: "已成功" },
  { value: "failed", label: "已失败" },
  { value: "canceled", label: "已取消" },
];

const TASK_TYPE_VALUES = new Set<SyncTaskType>(
  TASK_TYPE_OPTIONS.flatMap((option) => (option.value ? [option.value] : [])),
);
const TASK_STATUS_VALUES = new Set<SyncTaskStatus>(
  TASK_STATUS_OPTIONS.flatMap((option) => (option.value ? [option.value as SyncTaskStatus] : [])),
);
const TASK_SORT_OPTIONS: Array<{ value: SyncTaskSort; label: string }> = [
  { value: "created_at", label: "创建时间" },
  { value: "updated_at", label: "更新时间" },
  { value: "started_at", label: "开始时间" },
  { value: "finished_at", label: "完成时间" },
];
const TASK_SORT_VALUES = new Set<SyncTaskSort>(TASK_SORT_OPTIONS.map((option) => option.value));
const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;

const TASK_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
  const user = useAuthStore((state) => state.user);
  const [searchParams, setSearchParams] = useSearchParams();

  const [drawerTaskId, setDrawerTaskId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const linkedTaskId = searchParams.get("task_id")?.trim() ?? "";
  const rawTaskType = searchParams.get("task_type")?.trim() ?? "";
  const rawStatus = searchParams.get("status")?.trim() ?? "";
  const rawFileId = searchParams.get("file_id")?.trim() ?? "";
  const rawDepartmentId = searchParams.get("department_id")?.trim() ?? "";
  const rawSort = searchParams.get("sort")?.trim() ?? "";
  const taskType = TASK_TYPE_VALUES.has(rawTaskType as SyncTaskType)
    ? (rawTaskType as SyncTaskType)
    : undefined;
  const status = TASK_STATUS_VALUES.has(rawStatus as SyncTaskStatus)
    ? (rawStatus as SyncTaskStatus)
    : undefined;
  const fileId = TASK_ID_PATTERN.test(rawFileId) ? rawFileId : undefined;
  const departmentId = TASK_ID_PATTERN.test(rawDepartmentId) ? rawDepartmentId : undefined;
  const sort = TASK_SORT_VALUES.has(rawSort as SyncTaskSort)
    ? (rawSort as SyncTaskSort)
    : "created_at";
  const order: "asc" | "desc" = searchParams.get("order") === "asc" ? "asc" : "desc";
  const rawPage = Number.parseInt(searchParams.get("page") ?? "", 10);
  const rawPageSize = Number.parseInt(searchParams.get("page_size") ?? "", 10);
  const page = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1;
  const pageSize =
    Number.isInteger(rawPageSize) && rawPageSize > 0 && rawPageSize <= 100 ? rawPageSize : 20;
  const filters = useMemo<TaskListQuery>(
    () => ({
      page,
      page_size: pageSize,
      sort,
      order,
      ...(taskType ? { task_type: taskType } : {}),
      ...(status ? { status } : {}),
      ...(fileId ? { file_id: fileId } : {}),
      ...(departmentId ? { department_id: departmentId } : {}),
    }),
    [departmentId, fileId, order, page, pageSize, sort, status, taskType],
  );
  const savedViewDefinition = useMemo<Record<string, unknown>>(
    () => ({
      sort,
      order,
      page_size: pageSize,
      ...(taskType ? { task_type: taskType } : {}),
      ...(status ? { status } : {}),
      ...(fileId ? { file_id: fileId } : {}),
      ...(departmentId ? { department_id: departmentId } : {}),
    }),
    [departmentId, fileId, order, pageSize, sort, status, taskType],
  );
  const savedViewDepartmentOptions = useMemo(() => {
    const options: Array<{ label: string; value: string }> = [];
    if (user?.department_id) {
      options.push({
        label: user.department_name?.trim() || "账号所属部门",
        value: user.department_id,
      });
    }
    if (departmentId && !options.some((option) => option.value === departmentId)) {
      options.push({
        label: "部门 " + departmentId.slice(0, 8),
        value: departmentId,
      });
    }
    return options;
  }, [departmentId, user?.department_id, user?.department_name]);

  const updateTaskQuery = useCallback(
    (
      updates: Record<string, string | number | undefined>,
      options: { resetPage?: boolean } = {},
    ) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          for (const [key, value] of Object.entries(updates)) {
            if (value === undefined || value === "") {
              next.delete(key);
            } else {
              next.set(key, String(value));
            }
          }
          if (options.resetPage !== false) {
            next.set("page", "1");
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const applySavedView = useCallback(
    (definition: Record<string, unknown>) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          for (const key of [
            "task_type",
            "status",
            "file_id",
            "department_id",
            "sort",
            "order",
            "page",
            "page_size",
          ]) {
            next.delete(key);
          }

          if (
            typeof definition.task_type === "string" &&
            TASK_TYPE_VALUES.has(definition.task_type as SyncTaskType)
          ) {
            next.set("task_type", definition.task_type);
          }
          if (
            typeof definition.status === "string" &&
            TASK_STATUS_VALUES.has(definition.status as SyncTaskStatus)
          ) {
            next.set("status", definition.status);
          }
          if (typeof definition.file_id === "string" && TASK_ID_PATTERN.test(definition.file_id)) {
            next.set("file_id", definition.file_id);
          }
          if (
            typeof definition.department_id === "string" &&
            TASK_ID_PATTERN.test(definition.department_id)
          ) {
            next.set("department_id", definition.department_id);
          }
          if (
            typeof definition.sort === "string" &&
            TASK_SORT_VALUES.has(definition.sort as SyncTaskSort)
          ) {
            next.set("sort", definition.sort);
          }
          if (definition.order === "asc" || definition.order === "desc") {
            next.set("order", definition.order);
          }
          if (
            typeof definition.page_size === "number" &&
            Number.isInteger(definition.page_size) &&
            definition.page_size > 0 &&
            definition.page_size <= 100
          ) {
            next.set("page_size", String(definition.page_size));
          }
          next.set("page", "1");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const clearTaskParam = useCallback(() => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("task_id");
        return next;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  useEffect(() => {
    if (!linkedTaskId) {
      return;
    }
    if (!TASK_ID_PATTERN.test(linkedTaskId)) {
      setDrawerOpen(false);
      setDrawerTaskId(null);
      clearTaskParam();
      message.warning("任务链接无效，已忽略");
      return;
    }
    setDrawerTaskId(linkedTaskId);
    setDrawerOpen(true);
  }, [clearTaskParam, linkedTaskId, message]);

  const tasksQuery = useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => listTasks(filters),
  });
  const taskDataUnavailable = tasksQuery.isError;
  const tasks = taskDataUnavailable ? [] : (tasksQuery.data?.items ?? []);
  const totalTasks = tasksQuery.data?.total ?? 0;
  const statusCounts = tasksQuery.data?.status_counts;

  useEffect(() => {
    const response = tasksQuery.data;
    if (!response || tasksQuery.isFetching || tasksQuery.isPlaceholderData) {
      return;
    }
    const responseLastPage = response.total_pages ?? Math.ceil(response.total / pageSize);
    const lastPage = Math.max(1, responseLastPage);
    if (page > lastPage) {
      updateTaskQuery({ page: lastPage }, { resetPage: false });
    }
  }, [
    page,
    pageSize,
    tasksQuery.data,
    tasksQuery.isFetching,
    tasksQuery.isPlaceholderData,
    updateTaskQuery,
  ]);

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
    if (TASK_ID_PATTERN.test(taskId)) {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          next.set("task_id", taskId);
          return next;
        },
        { replace: true },
      );
    }
  }

  function closeDrawer() {
    setDrawerOpen(false);
    setDrawerTaskId(null);
    clearTaskParam();
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
      description="追踪 RAGFlow 上传、解析、状态检查与清理任务的队列状态和执行记录。"
    >
      <SavedViewManager
        pageKey="task_logs"
        queryDefinition={savedViewDefinition}
        departmentOptions={savedViewDepartmentOptions}
        onApply={applySavedView}
      />
      <div className="metric-grid">
        <KpiCard
          icon={<OrderedListOutlined />}
          title="任务总数"
          value={taskDataUnavailable ? "—" : totalTasks}
          description={taskDataUnavailable ? "任务列表加载失败" : "满足当前筛选条件"}
          tone="primary"
        />
        <KpiCard
          icon={<SyncOutlined />}
          title="运行中"
          value={taskDataUnavailable ? "—" : (statusCounts?.running ?? 0)}
          description={taskDataUnavailable ? "任务列表加载失败" : "正在执行任务"}
          tone="info"
        />
        <KpiCard
          icon={<CloseCircleOutlined />}
          title="失败任务"
          value={taskDataUnavailable ? "—" : (statusCounts?.failed ?? 0)}
          description={taskDataUnavailable ? "任务列表加载失败" : "需要重试或排查"}
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
              {taskDataUnavailable
                ? "任务数据暂不可用，请重试"
                : `当前显示 ${tasks.length} 条任务，共 ${totalTasks} 条队列记录`}
            </Typography.Text>
          </span>
          <StatusTag kind="health" value={tasksQuery.isError ? "error" : "ok"} variant="dot" />
        </div>

        <div className="filter-toolbar">
          <Select
            className="filter-toolbar__control"
            aria-label="任务类型筛选"
            placeholder="任务类型"
            style={{ width: 210 }}
            options={TASK_TYPE_OPTIONS}
            value={taskType ?? ""}
            onChange={(value: "" | SyncTaskType) =>
              updateTaskQuery({ task_type: value || undefined })
            }
          />
          <Select
            className="filter-toolbar__control"
            aria-label="任务状态筛选"
            placeholder="状态"
            style={{ width: 160 }}
            options={TASK_STATUS_OPTIONS}
            value={status ?? ""}
            onChange={(value: "" | SyncTaskStatus) =>
              updateTaskQuery({ status: value || undefined })
            }
          />
          <Input
            className="filter-toolbar__control"
            aria-label="文件 ID 筛选"
            placeholder="文件 ID"
            value={rawFileId}
            onChange={(event) =>
              updateTaskQuery({ file_id: event.target.value.trim() || undefined })
            }
          />
          <Input
            className="filter-toolbar__control"
            aria-label="部门 ID 筛选"
            placeholder="部门 ID"
            value={rawDepartmentId}
            onChange={(event) =>
              updateTaskQuery({ department_id: event.target.value.trim() || undefined })
            }
          />
          <Select
            className="filter-toolbar__control"
            aria-label="任务排序字段"
            options={TASK_SORT_OPTIONS}
            value={sort}
            onChange={(value: SyncTaskSort) => updateTaskQuery({ sort: value })}
          />
          <Select
            className="filter-toolbar__control"
            aria-label="任务排序方向"
            options={[
              { label: "降序", value: "desc" },
              { label: "升序", value: "asc" },
            ]}
            value={order}
            onChange={(value: "asc" | "desc") => updateTaskQuery({ order: value })}
          />
          <Button
            aria-label="刷新任务列表"
            icon={<ReloadOutlined />}
            loading={tasksQuery.isFetching}
            onClick={() => void tasksQuery.refetch()}
          />
        </div>

        <QueryBoundary
          isLoading={false}
          isError={tasksQuery.isError}
          error={tasksQuery.error}
          errorTitle="任务列表加载失败"
          onRetry={() => void tasksQuery.refetch()}
        >
          <Table<SyncTask>
            rowKey="id"
            loading={tasksQuery.isLoading}
            dataSource={tasks}
            columns={columns}
            pagination={{
              total: totalTasks,
              current: page,
              pageSize,
              showSizeChanger: true,
              pageSizeOptions: PAGE_SIZE_OPTIONS.map(String),
              onChange: (nextPage, nextPageSize) =>
                updateTaskQuery({ page: nextPage, page_size: nextPageSize }, { resetPage: false }),
              showTotal: (total) => `共 ${total} 条`,
            }}
            scroll={{ x: 1000 }}
          />
        </QueryBoundary>
      </Card>

      <TaskDetailDrawer taskId={drawerTaskId} open={drawerOpen} onClose={closeDrawer} />
    </PageContainer>
  );
}
