import { useCallback, useDeferredValue, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Timeline,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  HistoryOutlined,
  ReloadOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import dayjs, { type Dayjs } from "dayjs";

import {
  type KnowledgeFile,
  type ReplacementRemoteAction,
  type SyncTask,
  type VersionSwitchStatus,
  VERSION_SWITCH_RECONCILE_REASON_MAX_LENGTH,
  getUserFacingErrorMessage,
  isApiError,
  listDocumentOwnerOptions,
  reconcileVersionSwitchTask,
  retryTask,
  updateDocumentDraft,
} from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { SessionBoundModal as Modal } from "../../components/SessionBoundActions";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import {
  type AuthSessionIdentity,
  assertCurrentAuthSessionIdentity,
  captureAuthSessionIdentity,
  isCurrentAuthSessionIdentity,
  isSessionSupersededError,
  runAuthSessionLifecycleCallback,
} from "../../sessionIdentity";
import { Roles, useAuthStore } from "../../store/auth.store";

const GOVERNANCE_EDITABLE_STATUSES = new Set([
  "uploaded",
  "analyzed",
  "analysis_failed",
  "sensitive_review_required",
  "rejected",
]);
const OWNER_OPTIONS_PAGE_SIZE = 50;

interface GovernanceFormValues {
  ownerId: string;
  expiresAt: Dayjs | null;
}

interface GovernanceMutationVariables {
  requestIdentity: AuthSessionIdentity;
  values: GovernanceFormValues;
}

interface TaskMutationVariables {
  requestIdentity: AuthSessionIdentity;
  taskId: string;
}

interface ReconcileMutationVariables extends TaskMutationVariables {
  reason: string;
}

interface SwitchPresentation {
  type: "success" | "info" | "warning" | "error";
  title: string;
  description: string;
}

export interface VersionGovernanceCardProps {
  file: KnowledgeFile;
  tasks: SyncTask[];
  isAdmin: boolean;
  onOpenVersion: (fileId: string) => void;
  onRefresh: () => Promise<void>;
}

export function canEditDocumentGovernance(file: KnowledgeFile, userId?: string): boolean {
  return (
    Boolean(userId) &&
    userId === file.uploader_id &&
    GOVERNANCE_EDITABLE_STATUSES.has(file.status) &&
    Number.isInteger(file.review_version)
  );
}

export function isVersionSwitchFailure(status: VersionSwitchStatus): boolean {
  return status === "failed_old_deactivate" || status === "failed_new_activate";
}

export function findRetryableVersionSwitchTask(tasks: SyncTask[], fileId: string): SyncTask | null {
  const candidates = tasks.filter(
    (task) =>
      task.file_id === fileId &&
      task.task_type === "ragflow_upload" &&
      task.status === "failed" &&
      task.retry_count < task.max_retry_count,
  );
  return candidates.length === 1 ? candidates[0] : null;
}

export function findReconcileableVersionSwitchTask(
  tasks: SyncTask[],
  file: Pick<KnowledgeFile, "id" | "version_switch_status">,
): SyncTask | null {
  if (file.version_switch_status === "not_required" || file.version_switch_status === "completed") {
    return null;
  }
  const candidates = tasks.filter(
    (task) =>
      task.file_id === file.id &&
      task.task_type === "ragflow_upload" &&
      (task.status === "canceled" ||
        (task.status === "failed" && task.retry_count >= task.max_retry_count)),
  );
  return candidates.length === 1 ? candidates[0] : null;
}

export function versionSwitchReconcileErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 404) {
    return "切换任务不存在、已不可访问或超出当前管理范围";
  }
  if (isApiError(error) && error.status === 409) {
    return "版本切换状态已变化，请刷新详情后重试";
  }
  return getUserFacingErrorMessage(error, "人工协调版本切换失败");
}

function versionSwitchPresentation(file: KnowledgeFile): SwitchPresentation {
  const status = file.version_switch_status;
  if (status === "not_required" && file.remote_visibility === "current") {
    return {
      type: "success",
      title: "首个版本已生效",
      description: "该文档已成为本地与 RAGFlow 当前版本。",
    };
  }
  if (
    status === "not_required" &&
    !file.is_current_version &&
    file.remote_visibility === "not_current"
  ) {
    return {
      type: "info",
      title: "已被后续版本替代",
      description: "该首版本已成为可追溯的历史版本，当前版本由后续版本承接。",
    };
  }
  const presentations: Record<VersionSwitchStatus, SwitchPresentation> = {
    not_required: {
      type: "info",
      title: "首个版本无需执行替代切换",
      description: "文档完成审核和解析后，将直接成为该版本链的当前版本。",
    },
    pending: {
      type: "info",
      title: "候选版本等待审核与切换",
      description: "旧版本仍是当前远端版本；候选版本完成审核后才会开始 RAGFlow 切换。",
    },
    old_remote_deactivated: {
      type: "warning",
      title: "旧远端版本已停用",
      description: "系统正在切换本地当前版本，操作完成前请勿重复上传替代文件。",
    },
    local_switched: {
      type: "warning",
      title: "本地当前版本已切换",
      description: "系统正在激活 RAGFlow 新版本；完成前远端可见性可能处于过渡状态。",
    },
    completed: {
      type: "success",
      title: "版本切换已完成",
      description:
        "新版本已成为本地与 RAGFlow 当前版本，旧本地版本保留为可追溯历史；旧远端按冻结策略处理。",
    },
    failed_old_deactivate: {
      type: "error",
      title: "旧版本停用结果未确认",
      description:
        "系统未确认旧远端版本已停用，应按仍可能可见处理。请先核对处理日志，再决定是否重试。",
    },
    failed_new_activate: {
      type: "error",
      title: "新版本远端激活失败",
      description: "系统已保留切换阶段和恢复信息。管理员应重试失败任务，避免创建重复版本。",
    },
  };
  return presentations[status];
}

function expiryLabel(expiresAt?: string | null): string {
  return expiresAt ? dayjs(expiresAt).format("YYYY-MM-DD HH:mm") : "长期有效";
}

function replacementRemoteActionLabel(
  action: ReplacementRemoteAction | null | undefined,
  hasPredecessor: boolean,
): string {
  if (action === "delete") {
    return "旧远端删除";
  }
  if (action === "archive") {
    return "旧远端保留并标记非当前";
  }
  return hasPredecessor ? "策略数据不可用" : "不适用（首个版本）";
}

function switchTimeLabel(value: string | null): string {
  return value ? dayjs(value).format("YYYY-MM-DD HH:mm:ss") : "尚未到达";
}

function taskRecoveryAction(
  isAdmin: boolean,
  retryableTask: SyncTask | null,
  reconcileableTask: SyncTask | null,
  retryPending: boolean,
  reconcilePending: boolean,
  onRetry: () => void,
  onReconcile: () => void,
): ReactNode {
  if (!isAdmin) {
    return null;
  }
  if (retryableTask) {
    return (
      <Button size="small" icon={<ReloadOutlined />} loading={retryPending} onClick={onRetry}>
        重试切换任务
      </Button>
    );
  }
  if (reconcileableTask) {
    return (
      <Button size="small" danger loading={reconcilePending} onClick={onReconcile}>
        人工协调版本切换
      </Button>
    );
  }
  return (
    <Button size="small" href="#file-task-timeline">
      查看处理日志
    </Button>
  );
}

export function VersionGovernanceCard({
  file,
  tasks,
  isAdmin,
  onOpenVersion,
  onRefresh,
}: VersionGovernanceCardProps) {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const userId = user?.id;
  const hasAdminRole = user?.role === Roles.SYSTEM_ADMIN || user?.role === Roles.DEPT_ADMIN;
  const [form] = Form.useForm<GovernanceFormValues>();
  const [reconcileOpen, setReconcileOpen] = useState(false);
  const [reconcileReason, setReconcileReason] = useState("");
  const selectedOwnerId = Form.useWatch("ownerId", form);
  const [ownerSearch, setOwnerSearch] = useState("");
  const deferredOwnerSearch = useDeferredValue(ownerSearch.trim());
  const canEdit = canEditDocumentGovernance(file, userId);
  const ownerOptionsQuery = useInfiniteQuery({
    queryKey: [
      "document-owner-options",
      {
        department_id: file.department_id ?? null,
        q: deferredOwnerSearch,
        user_id: userId ?? null,
        role: user?.role ?? null,
        user_department_id: user?.department_id ?? null,
      },
    ],
    queryFn: ({ pageParam }) =>
      listDocumentOwnerOptions({
        q: deferredOwnerSearch || undefined,
        page: pageParam,
        page_size: OWNER_OPTIONS_PAGE_SIZE,
      }),
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage.page < lastPage.total_pages ? lastPage.page + 1 : undefined,
    enabled: canEdit,
  });
  const ownerOptions = useMemo(() => {
    const options = new Map<string, string>();
    for (const page of ownerOptionsQuery.data?.pages ?? []) {
      for (const owner of page.items) {
        options.set(owner.id, owner.name);
      }
    }
    return Array.from(options, ([value, label]) => ({ value, label }));
  }, [ownerOptionsQuery.data?.pages]);
  const ownerOptionsFailed = ownerOptionsQuery.isError || ownerOptionsQuery.isFetchNextPageError;
  const ownerQueryDomainReady = ownerSearch.trim() === deferredOwnerSearch;
  const ownerOptionsLoading =
    ownerOptionsQuery.isPending || ownerOptionsQuery.isFetching || !ownerQueryDomainReady;
  const ownerSelectionVerified =
    Boolean(selectedOwnerId) && ownerOptions.some((owner) => owner.value === selectedOwnerId);
  const ownerFormReady =
    canEdit &&
    ownerOptionsQuery.isSuccess &&
    !ownerOptionsFailed &&
    !ownerOptionsLoading &&
    ownerQueryDomainReady &&
    ownerSelectionVerified;
  const ownerSelectionUnavailable =
    Boolean(selectedOwnerId) &&
    ownerOptionsQuery.isSuccess &&
    !ownerOptionsFailed &&
    !ownerOptionsLoading &&
    !ownerSelectionVerified;
  const ownerMutation = useMutation({
    mutationFn: ({ values, requestIdentity }: GovernanceMutationVariables) => {
      assertCurrentAuthSessionIdentity(requestIdentity);
      return updateDocumentDraft(file.id, {
        expected_version: file.review_version ?? 0,
        owner_id: values.ownerId,
        expires_at: values.expiresAt ? values.expiresAt.toISOString() : null,
      });
    },
    onSuccess: (_file, variables) =>
      runAuthSessionLifecycleCallback(variables.requestIdentity, async (context) => {
        await context.waitFor(onRefresh);
        context.run(() => message.success("负责人和到期时间已更新"));
      }),
    onError: (error, variables) => {
      if (
        isSessionSupersededError(error) ||
        !isCurrentAuthSessionIdentity(variables.requestIdentity)
      ) {
        return;
      }
      message.error(getUserFacingErrorMessage(error, "治理信息更新失败"));
    },
  });
  const handleOwnerSearch = useCallback(
    (value: string) => {
      if (value !== ownerSearch) {
        form.setFieldValue("ownerId", undefined);
        setOwnerSearch(value);
      }
    },
    [form, ownerSearch],
  );
  const handleOwnerSubmit = useCallback(
    (values: GovernanceFormValues) => {
      if (!ownerFormReady) {
        message.error("负责人候选尚未验证完成，请等待加载后重试");
        return;
      }
      ownerMutation.mutate({
        values,
        requestIdentity: captureAuthSessionIdentity(),
      });
    },
    [message, ownerFormReady, ownerMutation],
  );
  const canRecoverVersionSwitch = isAdmin && hasAdminRole;
  const retryableTask = canRecoverVersionSwitch
    ? findRetryableVersionSwitchTask(tasks, file.id)
    : null;
  const reconcileableTask = canRecoverVersionSwitch
    ? findReconcileableVersionSwitchTask(tasks, file)
    : null;
  const retryMutation = useMutation({
    mutationFn: ({ taskId, requestIdentity }: TaskMutationVariables) => {
      assertCurrentAuthSessionIdentity(requestIdentity);
      return retryTask(taskId);
    },
    onSuccess: (_task, variables) =>
      runAuthSessionLifecycleCallback(variables.requestIdentity, async (context) => {
        await context.waitFor(onRefresh);
        context.run(() => message.success("版本切换任务已重新入队"));
      }),
    onError: (error, variables) => {
      if (
        isSessionSupersededError(error) ||
        !isCurrentAuthSessionIdentity(variables.requestIdentity)
      ) {
        return;
      }
      message.error(getUserFacingErrorMessage(error, "版本切换任务重试失败"));
    },
  });
  const reconcileMutation = useMutation({
    mutationFn: ({ taskId, reason, requestIdentity }: ReconcileMutationVariables) => {
      assertCurrentAuthSessionIdentity(requestIdentity);
      return reconcileVersionSwitchTask(taskId, { reason });
    },
    onSuccess: (_task, variables) =>
      runAuthSessionLifecycleCallback(variables.requestIdentity, async (context) => {
        await context.waitFor(() =>
          Promise.all([
            queryClient.invalidateQueries({ queryKey: ["tasks", { file_id: file.id }] }),
            queryClient.invalidateQueries({ queryKey: ["documents", { file_id: file.id }] }),
            queryClient.invalidateQueries({ queryKey: ["documents", "uploaded"] }),
            queryClient.invalidateQueries({ queryKey: ["documents", "responsible"] }),
          ]),
        );
        context.run(() => setReconcileOpen(false));
        context.run(() => setReconcileReason(""));
        context.run(() => message.success("版本切换已按当前远端事实完成人工协调"));
      }),
    onError: (error, variables) => {
      if (
        isSessionSupersededError(error) ||
        !isCurrentAuthSessionIdentity(variables.requestIdentity)
      ) {
        return;
      }
      message.error(versionSwitchReconcileErrorMessage(error));
    },
  });

  const switchPresentation = versionSwitchPresentation(file);
  const switchFailed = isVersionSwitchFailure(file.version_switch_status);
  const normalizedReconcileReason = reconcileReason.trim();
  const reconcileReasonInvalid =
    normalizedReconcileReason.length === 0 ||
    normalizedReconcileReason.length > VERSION_SWITCH_RECONCILE_REASON_MAX_LENGTH;
  const chain = file.version_chain ?? [];
  const ownerInitialValue = file.owner_id ?? undefined;
  const expiryInitialValue = file.expires_at ? dayjs(file.expires_at) : null;

  return (
    <Card
      className="document-panel version-governance-card"
      title={
        <Space>
          <HistoryOutlined />
          版本与责任治理
        </Space>
      }
      extra={
        <Space wrap>
          <StatusTag kind="version" value={file.remote_visibility} />
          <StatusTag kind="version" value={file.version_switch_status} />
        </Space>
      }
    >
      <div className="version-governance-stack">
        <div aria-live="polite">
          <Alert
            type={switchPresentation.type}
            showIcon
            message={switchPresentation.title}
            description={
              <Space direction="vertical" size={4}>
                <span>{switchPresentation.description}</span>
                {file.version_switch_error ? (
                  <Typography.Text type="danger">
                    异常类型：{file.version_switch_error}
                  </Typography.Text>
                ) : null}
              </Space>
            }
            action={
              switchFailed || reconcileableTask
                ? taskRecoveryAction(
                    canRecoverVersionSwitch,
                    retryableTask,
                    reconcileableTask,
                    retryMutation.isPending,
                    reconcileMutation.isPending,
                    () =>
                      retryableTask &&
                      retryMutation.mutate({
                        taskId: retryableTask.id,
                        requestIdentity: captureAuthSessionIdentity(),
                      }),
                    () => {
                      if (reconcileableTask) {
                        setReconcileReason("");
                        setReconcileOpen(true);
                      }
                    },
                  )
                : undefined
            }
          />
        </div>

        <Descriptions column={1} size="small" styles={{ label: { width: 132 } }}>
          <Descriptions.Item label="当前查看版本">
            <Space wrap>
              <Typography.Text strong>v{file.version_number}</Typography.Text>
              <StatusTag
                kind="version"
                value={
                  file.is_current_version
                    ? "current"
                    : file.remote_visibility === "candidate"
                      ? "candidate"
                      : "not_current"
                }
              />
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="冻结的旧远端策略">
            {replacementRemoteActionLabel(
              file.replacement_remote_action,
              Boolean(file.replaces_file_id),
            )}
          </Descriptions.Item>
          <Descriptions.Item label="到期负责人">
            <Space wrap>
              <UserOutlined />
              <span>{file.owner_name ?? (file.owner_id ? "负责人信息不可用" : "未设置")}</span>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="到期时间">{expiryLabel(file.expires_at)}</Descriptions.Item>
          <Descriptions.Item label="远端步骤尝试次数">
            {file.version_switch_attempt_count}
          </Descriptions.Item>
          <Descriptions.Item label="旧远端停用时间">
            {switchTimeLabel(file.predecessor_remote_deactivated_at)}
          </Descriptions.Item>
          <Descriptions.Item label="本地版本切换时间">
            {switchTimeLabel(file.local_version_activated_at)}
          </Descriptions.Item>
          <Descriptions.Item label="新远端激活时间">
            {switchTimeLabel(file.remote_version_activated_at)}
          </Descriptions.Item>
        </Descriptions>

        {canEdit ? (
          <Form<GovernanceFormValues>
            key={`${file.id}:${file.review_version}`}
            form={form}
            layout="vertical"
            className="version-governance-form"
            initialValues={{ ownerId: ownerInitialValue, expiresAt: expiryInitialValue }}
            onFinish={handleOwnerSubmit}
          >
            {ownerOptionsFailed ? (
              <Alert
                type="error"
                showIcon
                message="负责人候选加载失败"
                description="候选列表恢复前不会提交治理信息。"
                action={
                  <Button
                    size="small"
                    onClick={() =>
                      void (ownerOptionsQuery.isFetchNextPageError
                        ? ownerOptionsQuery.fetchNextPage()
                        : ownerOptionsQuery.refetch())
                    }
                  >
                    重试
                  </Button>
                }
              />
            ) : null}
            {ownerSelectionUnavailable ? (
              <Alert
                type="warning"
                showIcon
                message="当前负责人不在有效候选中"
                description="该成员可能已停用、未完成验证或不在当前候选页；请搜索并重新选择后保存。"
              />
            ) : null}
            <div className="version-governance-form__grid">
              <Form.Item
                label="到期负责人"
                name="ownerId"
                rules={[{ required: true, message: "请选择到期负责人" }]}
                extra="被指定负责人可查看此文件详情与原件，不获得修改/删除权限"
              >
                <Select
                  showSearch
                  filterOption={false}
                  onSearch={handleOwnerSearch}
                  loading={ownerOptionsLoading}
                  disabled={!ownerOptionsQuery.isSuccess || ownerOptionsFailed}
                  placeholder="选择本部门已激活并完成验证的成员"
                  notFoundContent={ownerOptionsLoading ? "正在加载" : "暂无可选负责人"}
                  options={ownerOptions}
                />
              </Form.Item>
              <Form.Item label="到期时间" name="expiresAt">
                <DatePicker
                  allowClear
                  showTime={{ format: "HH:mm" }}
                  format="YYYY-MM-DD HH:mm"
                  placeholder="留空表示长期有效"
                  disabledDate={(date) => date.endOf("day").isBefore(dayjs().startOf("day"))}
                />
              </Form.Item>
            </div>
            {ownerOptionsQuery.hasNextPage ? (
              <Button
                type="link"
                loading={ownerOptionsQuery.isFetchingNextPage}
                onClick={() => void ownerOptionsQuery.fetchNextPage()}
              >
                加载更多负责人
              </Button>
            ) : null}
            {ownerMutation.isError ? (
              <Alert type="error" showIcon message="治理信息未保存，请检查后重试" />
            ) : null}
            <Button
              type="primary"
              htmlType="submit"
              icon={<CheckCircleOutlined />}
              loading={ownerMutation.isPending}
              disabled={!ownerFormReady}
            >
              保存治理信息
            </Button>
          </Form>
        ) : (
          <Typography.Text type="secondary">
            {file.uploader_id === userId
              ? "当前状态不可修改负责人或到期时间；请在草稿、分析完成、分析失败、敏感信息待复核或驳回状态调整。"
              : "仅上传者可在可编辑状态调整负责人和到期时间。"}
          </Typography.Text>
        )}

        <section className="version-chain" aria-labelledby="version-chain-title">
          <Typography.Title id="version-chain-title" level={5} className="version-chain__title">
            版本链
          </Typography.Title>
          {chain.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="版本链暂不可用" />
          ) : (
            <Timeline
              items={chain.map((item) => ({
                key: item.id,
                color: item.is_current_version ? "green" : "gray",
                children: (
                  <div className="version-chain__item">
                    <button
                      type="button"
                      className="version-chain__link"
                      aria-current={item.id === file.id ? "page" : undefined}
                      onClick={() => onOpenVersion(item.id)}
                    >
                      v{item.version_number} · {item.title}
                    </button>
                    <Space wrap size={6}>
                      <StatusTag kind="file" value={item.status} />
                      <StatusTag kind="version" value={item.remote_visibility} />
                      {item.is_current_version ? (
                        <StatusTag kind="version" value="current" />
                      ) : null}
                    </Space>
                    <Typography.Text type="secondary">
                      {dayjs(item.created_at).format("YYYY-MM-DD HH:mm")}
                    </Typography.Text>
                    {item.replaces_file_id ? (
                      <Typography.Text type="secondary">
                        冻结策略：
                        {replacementRemoteActionLabel(
                          item.replacement_remote_action,
                          Boolean(item.replaces_file_id),
                        )}
                      </Typography.Text>
                    ) : null}
                    {item.version_switch_error ? (
                      <Typography.Text type="danger">
                        异常类型：{item.version_switch_error}
                      </Typography.Text>
                    ) : null}
                  </div>
                ),
              }))}
            />
          )}
        </section>
      </div>
      <Modal
        title="人工协调版本切换"
        open={canRecoverVersionSwitch && Boolean(reconcileableTask) && reconcileOpen}
        okText="确认按远端事实协调"
        cancelText="取消"
        confirmLoading={reconcileMutation.isPending}
        okButtonProps={{ danger: true, disabled: reconcileReasonInvalid }}
        cancelButtonProps={{ disabled: reconcileMutation.isPending }}
        maskClosable={!reconcileMutation.isPending}
        destroyOnHidden
        onCancel={() => {
          if (reconcileMutation.isPending) {
            return;
          }
          setReconcileOpen(false);
          setReconcileReason("");
        }}
        onOk={() => {
          if (!reconcileableTask || reconcileReasonInvalid) {
            message.warning("请填写 1 至 1000 个字符的人工协调原因");
            return;
          }
          reconcileMutation.mutate({
            taskId: reconcileableTask.id,
            reason: normalizedReconcileReason,
            requestIdentity: captureAuthSessionIdentity(),
          });
        }}
      >
        <Space direction="vertical" size={12} className="version-governance-stack">
          <Alert
            type="warning"
            showIcon
            message="仅在自动重试已耗尽或任务已取消后使用"
            description="确认前必须核对 RAGFlow 当前可见版本；系统会依据已持久化的切换阶段完成补偿并写入审计。"
          />
          <Input.TextArea
            aria-label="人工协调原因"
            value={reconcileReason}
            onChange={(event) => setReconcileReason(event.target.value)}
            maxLength={VERSION_SWITCH_RECONCILE_REASON_MAX_LENGTH}
            showCount
            autoSize={{ minRows: 4, maxRows: 8 }}
            placeholder="说明已核对的远端事实、处理依据和责任人"
          />
        </Space>
      </Modal>
    </Card>
  );
}
