import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Form,
  Input,
  Progress,
  Select,
  Space,
  Switch,
  Upload,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  FileTextOutlined,
  InboxOutlined,
  InfoCircleOutlined,
  SwapOutlined,
  TagsOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { UploadFile } from "antd/es/upload/interface";

import type { RcFile } from "antd/es/upload";

import {
  DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE,
  type KnowledgeFile,
  getUploadPolicy,
  getUserFacingErrorMessage,
  listDocuments,
  uploadDocument,
} from "../../api/client";
import { DepartmentAssignmentAlert } from "../../components/DepartmentAssignmentAlert";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import {
  type AuthSessionIdentity,
  SessionSupersededError,
  captureAuthSessionIdentity,
  isCurrentAuthSessionIdentity,
  isSessionSupersededError,
  runAuthSessionCallback,
} from "../../sessionIdentity";
import { hasAssignedDepartment, useAuthStore } from "../../store/auth.store";
import {
  allowMultiFileFromPolicy,
  allowedExtensionsFromPolicy,
  extensionAcceptValue,
  uploadEnabledFromPolicy,
} from "../../utils/uploadConfig";
import { documentDisplayTitle } from "../../utils/documentTitle";

/** Maximum number of simultaneous uploads. */
const CONCURRENCY_LIMIT = 3;
const BYTES_PER_MEBIBYTE = 1024 * 1024;

function formatFileSizeLimit(maxFileSizeMb: number): string {
  return Number.isInteger(maxFileSizeMb)
    ? String(maxFileSizeMb)
    : maxFileSizeMb.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

export function fileSizeValidationMessage(
  file: Pick<File, "name" | "size">,
  maxFileSizeMb: number | null | undefined,
): string | null {
  if (typeof maxFileSizeMb !== "number" || !Number.isFinite(maxFileSizeMb) || maxFileSizeMb <= 0) {
    return "上传大小策略不可用，已停止上传；请联系系统管理员检查配置";
  }
  const maxBytes = Math.floor(maxFileSizeMb * BYTES_PER_MEBIBYTE);
  if (file.size <= maxBytes) {
    return null;
  }
  return `${file.name} 超过单文件最大 ${formatFileSizeLimit(maxFileSizeMb)} MB，请压缩或拆分后重试`;
}

type QueueItemStatus = "pending" | "uploading" | "success" | "duplicate" | "error";

interface QueueItem {
  uid: string;
  name: string;
  file: RcFile;
  status: QueueItemStatus;
  percent: number;
  result: KnowledgeFile | null;
  errorMessage: string | null;
}

interface UploadFormValues {
  file?: UploadFile[];
  description?: string;
  submitAfterUpload: boolean;
  aiAnalyze: boolean;
  replaceExisting: boolean;
  replacesFileId?: string;
}

interface ConcurrentRunOptions {
  signal: AbortSignal;
  assertCanStart: () => void;
}

interface ActiveUploadBatch {
  identity: AuthSessionIdentity;
  controller: AbortController;
}
function normalizeUploadFile(event: { fileList?: UploadFile[] } | UploadFile[]): UploadFile[] {
  if (Array.isArray(event)) {
    return event;
  }
  return event.fileList ?? [];
}

function throwIfUploadQueueCancelled(signal: AbortSignal): void {
  if (!signal.aborted) {
    return;
  }
  if (signal.reason instanceof Error) {
    throw signal.reason;
  }
  throw new DOMException("上传队列已取消", "AbortError");
}

/** Run at most `limit` promises concurrently without starting stale queued work. */
async function runConcurrent<T>(
  tasks: Array<() => Promise<T>>,
  limit: number,
  options: ConcurrentRunOptions,
): Promise<PromiseSettledResult<T>[]> {
  const results: PromiseSettledResult<T>[] = new Array(tasks.length);
  let nextIndex = 0;

  async function worker(): Promise<void> {
    for (;;) {
      options.assertCanStart();
      throwIfUploadQueueCancelled(options.signal);
      if (nextIndex >= tasks.length) {
        return;
      }
      const index = nextIndex;
      nextIndex += 1;
      try {
        const value = await tasks[index]();
        options.assertCanStart();
        throwIfUploadQueueCancelled(options.signal);
        results[index] = { status: "fulfilled", value };
      } catch (error) {
        options.assertCanStart();
        throwIfUploadQueueCancelled(options.signal);
        results[index] = {
          status: "rejected",
          reason: error instanceof Error ? error : new Error(String(error)),
        };
      }
    }
  }

  const workers = Array.from({ length: Math.min(limit, tasks.length) }, () => worker());
  await Promise.all(workers);
  return results;
}

export default function UploadPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const currentUser = useAuthStore((state) => state.user);
  const departmentBlocked = !hasAssignedDepartment(currentUser);
  const currentUserId = currentUser?.id;
  const [form] = Form.useForm<UploadFormValues>();
  const replaceExisting = Form.useWatch("replaceExisting", form) ?? false;
  const selectedReplacementId = Form.useWatch("replacesFileId", form);

  // The upload queue is separate from the AntD Upload fileList so we can
  // track per-file progress and result independently of the form state.
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [replacementSearch, setReplacementSearch] = useState("");
  const deferredReplacementSearch = useDeferredValue(replacementSearch.trim());
  const activeUploadBatch = useRef<ActiveUploadBatch | null>(null);
  const pendingUploadIdentity = useRef<AuthSessionIdentity | null>(null);

  useEffect(() => {
    const removeAuthSubscription = useAuthStore.subscribe(() => {
      const activeBatch = activeUploadBatch.current;
      let shouldResetIntent = false;
      if (activeBatch && !isCurrentAuthSessionIdentity(activeBatch.identity)) {
        activeBatch.controller.abort(new SessionSupersededError());
        activeUploadBatch.current = null;
        shouldResetIntent = true;
      }
      const pendingIdentity = pendingUploadIdentity.current;
      if (pendingIdentity && !isCurrentAuthSessionIdentity(pendingIdentity)) {
        pendingUploadIdentity.current = null;
        shouldResetIntent = true;
      }
      if (!shouldResetIntent) {
        return;
      }
      setQueue([]);
      setIsUploading(false);
      setReplacementSearch("");
      form.resetFields();
    });

    return () => {
      removeAuthSubscription();
      const activeBatch = activeUploadBatch.current;
      if (activeBatch && !activeBatch.controller.signal.aborted) {
        activeBatch.controller.abort(new DOMException("上传页面已卸载", "AbortError"));
      }
      activeUploadBatch.current = null;
      pendingUploadIdentity.current = null;
    };
  }, [form]);
  const uploadPolicyQuery = useQuery({
    queryKey: ["upload-policy"],
    queryFn: getUploadPolicy,
  });
  const allowedExtensions = useMemo(
    () => allowedExtensionsFromPolicy(uploadPolicyQuery.data),
    [uploadPolicyQuery.data],
  );
  const uploadPolicyReady = uploadPolicyQuery.isSuccess && uploadPolicyQuery.data !== undefined;
  const allowMultiFile = allowMultiFileFromPolicy(uploadPolicyQuery.data);
  const effectiveAllowMultiFile = allowMultiFile && !replaceExisting;
  const uploadEnabled = uploadEnabledFromPolicy(uploadPolicyQuery.data);
  const maxFileSizeMb = uploadPolicyQuery.data?.max_file_size_mb;
  const maxFileSizeValid =
    typeof maxFileSizeMb === "number" && Number.isFinite(maxFileSizeMb) && maxFileSizeMb > 0;
  const canUpload = uploadPolicyReady && uploadEnabled && maxFileSizeValid && !departmentBlocked;
  const replacementCandidatesQuery = useInfiniteQuery({
    queryKey: [
      "replacement-candidates",
      {
        q: deferredReplacementSearch,
        user_id: currentUserId ?? null,
        role: currentUser?.role ?? null,
        department_id: currentUser?.department_id ?? null,
      },
    ],
    queryFn: ({ pageParam }) =>
      listDocuments({
        page: pageParam,
        page_size: 100,
        q: deferredReplacementSearch || undefined,
        status: "parsed",
        sort: "updated_at",
        order: "desc",
      }),
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      const page = lastPage.page ?? 1;
      const pageSize = lastPage.page_size ?? 100;
      return page * pageSize < lastPage.total ? page + 1 : undefined;
    },
    enabled: replaceExisting && canUpload,
  });
  const replacementCandidates = useMemo(
    () =>
      (replacementCandidatesQuery.data?.pages ?? [])
        .flatMap((page) => page.items)
        .filter(
          (file) =>
            file.uploader_id === currentUserId &&
            file.status === "parsed" &&
            file.is_current_version &&
            file.remote_visibility === "current",
        ),
    [currentUserId, replacementCandidatesQuery.data],
  );
  const replacementCandidatesFailed =
    replacementCandidatesQuery.isError || replacementCandidatesQuery.isFetchNextPageError;
  const replacementQueryDomainReady = replacementSearch.trim() === deferredReplacementSearch;
  const replacementCandidatesLoading =
    replacementCandidatesQuery.isPending ||
    replacementCandidatesQuery.isFetching ||
    !replacementQueryDomainReady;
  const replacementSelectionVerified =
    !replaceExisting ||
    (Boolean(selectedReplacementId) &&
      replacementCandidates.some((candidate) => candidate.id === selectedReplacementId));
  const replacementReady =
    !replaceExisting ||
    (replacementCandidatesQuery.isSuccess &&
      !replacementCandidatesFailed &&
      !replacementCandidatesLoading &&
      replacementQueryDomainReady &&
      replacementSelectionVerified);
  const canStartUpload = canUpload && replacementReady;
  const acceptValue = useMemo(() => extensionAcceptValue(allowedExtensions), [allowedExtensions]);
  const allowedExtensionText = uploadPolicyReady
    ? allowedExtensions.map((extension) => extension.toUpperCase()).join("、")
    : "正在读取上传策略";
  const fileSizeLimitText = maxFileSizeValid
    ? `单文件最大 ${formatFileSizeLimit(maxFileSizeMb)} MB`
    : "大小上限配置不可用";

  const updateItem = useCallback(
    (uid: string, patch: Partial<Omit<QueueItem, "uid" | "name" | "file">>) => {
      setQueue((prev) => prev.map((item) => (item.uid === uid ? { ...item, ...patch } : item)));
    },
    [],
  );

  /** Build a fresh queue from the current AntD fileList. */
  const buildQueue = useCallback((fileList: UploadFile[]): QueueItem[] => {
    const items: QueueItem[] = [];
    for (const uploadFile of fileList) {
      const rc = uploadFile.originFileObj;
      if (rc instanceof File) {
        items.push({
          uid: uploadFile.uid,
          name: uploadFile.name,
          file: rc as RcFile,
          status: "pending",
          percent: 0,
          result: null,
          errorMessage: null,
        });
      }
    }
    return items;
  }, []);

  const handleFileListChange = useCallback(
    (fileList: UploadFile[]) => {
      if (!isUploading) {
        setQueue(buildQueue(fileList));
      }
    },
    [isUploading, buildQueue],
  );

  const handleReplacementSearch = useCallback(
    (value: string) => {
      if (value !== replacementSearch) {
        form.setFieldValue("replacesFileId", undefined);
      }
      setReplacementSearch(value);
    },
    [form, replacementSearch],
  );

  const handleReplacementToggle = useCallback(
    (checked: boolean) => {
      if (!checked) {
        form.setFieldValue("replacesFileId", undefined);
        setReplacementSearch("");
        return;
      }
      const fileList = form.getFieldValue("file") ?? [];
      if (fileList.length > 1) {
        const singleFileList = fileList.slice(0, 1);
        form.setFieldValue("file", singleFileList);
        handleFileListChange(singleFileList);
        message.info("替代现有文档仅支持单文件，已保留第一个待上传文件");
      }
    },
    [form, handleFileListChange, message],
  );

  const handleBeforeUpload = useCallback(
    (file: RcFile) => {
      const validationMessage = fileSizeValidationMessage(file, maxFileSizeMb);
      if (validationMessage) {
        message.error(validationMessage);
        return Upload.LIST_IGNORE;
      }
      if (!pendingUploadIdentity.current) {
        pendingUploadIdentity.current = captureAuthSessionIdentity();
      }
      return false;
    },
    [maxFileSizeMb, message],
  );

  const handleSubmit = useCallback(
    async (values: UploadFormValues) => {
      const requestIdentity = pendingUploadIdentity.current;
      pendingUploadIdentity.current = null;
      if (!requestIdentity || !isCurrentAuthSessionIdentity(requestIdentity)) {
        setQueue([]);
        form.resetFields(["file"]);
        message.warning("登录会话已变化，请重新选择文件");
        return;
      }
      const controller = new AbortController();
      const uploadBatch: ActiveUploadBatch = { identity: requestIdentity, controller };
      const previousBatch = activeUploadBatch.current;
      if (previousBatch && !previousBatch.controller.signal.aborted) {
        previousBatch.controller.abort(new DOMException("已开始新的上传批次", "AbortError"));
      }
      activeUploadBatch.current = uploadBatch;

      try {
        await runAuthSessionCallback(
          requestIdentity,
          async (context) => {
            const fileList = values.file ?? [];
            const submitAfterUpload = values.submitAfterUpload ?? false;
            const aiAnalysisEnabled = values.aiAnalyze ?? true;
            const replacesFileId = values.replaceExisting ? values.replacesFileId : undefined;

            if (fileList.length === 0) {
              context.run(() => message.warning("请至少选择一个文件"));
              return;
            }

            if (values.replaceExisting && (fileList.length !== 1 || !replacesFileId)) {
              context.run(() =>
                message.warning("替代现有文档时，请选择一个旧版本并仅上传一个新文件"),
              );
              return;
            }

            if (!uploadPolicyReady) {
              context.run(() => message.warning("上传策略尚未就绪，请重试"));
              return;
            }
            if (!uploadEnabled) {
              context.run(() => message.warning("当前系统已关闭员工上传"));
              return;
            }
            if (departmentBlocked) {
              context.run(() => message.warning(DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE));
              return;
            }

            // Re-build queue from the current form file list so UIDs match.
            const freshQueue = buildQueue(fileList);
            const sizeError = freshQueue
              .map((item) => fileSizeValidationMessage(item.file, maxFileSizeMb))
              .find((error): error is string => Boolean(error));
            if (sizeError) {
              context.run(() => message.error(sizeError));
              return;
            }
            context.run(() => setQueue(freshQueue));
            context.run(() => setIsUploading(true));

            const tasks = freshQueue.map((item) => async () => {
              context.run(() => updateItem(item.uid, { status: "uploading", percent: 0 }));

              const result = await context.waitFor(() =>
                uploadDocument(
                  {
                    file: item.file,
                    description: values.description,
                    visibility: "private",
                    submitAfterUpload,
                    aiAnalysisEnabled,
                    replacesFileId,
                  },
                  (percent) => {
                    context.runIfCurrent(() => updateItem(item.uid, { percent }));
                  },
                  {
                    signal: context.signal,
                    requestIdentity,
                  },
                ),
              );

              context.run(() =>
                updateItem(item.uid, {
                  status: result.duplicate ? "duplicate" : "success",
                  percent: 100,
                  result,
                }),
              );

              return result;
            });

            const settled = await context.waitFor(() =>
              runConcurrent(tasks, CONCURRENCY_LIMIT, {
                signal: context.signal,
                assertCanStart: context.assertCurrent,
              }),
            );

            settled.forEach((outcome, index) => {
              if (outcome.status === "rejected") {
                const errorMessage = getUserFacingErrorMessage(outcome.reason, "上传失败");
                context.run(() =>
                  updateItem(freshQueue[index].uid, { status: "error", errorMessage }),
                );
              }
            });

            context.run(() => setIsUploading(false));
            context.run(() => form.resetFields(["file"]));

            const successCount = settled.filter((result) => result.status === "fulfilled").length;
            const failCount = settled.filter((result) => result.status === "rejected").length;
            const duplicateCount = settled
              .filter(
                (result): result is PromiseFulfilledResult<KnowledgeFile> =>
                  result.status === "fulfilled",
              )
              .filter((result) => result.value.duplicate).length;

            if (failCount === 0) {
              context.run(() =>
                message.success(
                  duplicateCount > 0
                    ? `上传完成，共 ${successCount} 个文件，其中 ${duplicateCount} 个重复`
                    : `上传完成，共 ${successCount} 个文件`,
                ),
              );
            } else {
              context.run(() =>
                message.warning(`上传完成：${successCount} 成功，${failCount} 失败`),
              );
            }
          },
          controller.signal,
        );
      } catch (error) {
        if (
          isSessionSupersededError(error) ||
          controller.signal.aborted ||
          !isCurrentAuthSessionIdentity(requestIdentity)
        ) {
          return;
        }
        setIsUploading(false);
        if (!isCurrentAuthSessionIdentity(requestIdentity)) {
          return;
        }
        message.error(getUserFacingErrorMessage(error, "上传队列执行失败"));
      } finally {
        if (activeUploadBatch.current === uploadBatch) {
          activeUploadBatch.current = null;
        }
      }
    },
    [
      buildQueue,
      updateItem,
      form,
      message,
      maxFileSizeMb,
      uploadPolicyReady,
      uploadEnabled,
      departmentBlocked,
    ],
  );

  const handleSaveDraft = useCallback(() => {
    form.setFieldsValue({ submitAfterUpload: false });
    form.submit();
  }, [form]);

  const selectedFiles: UploadFile[] = Form.useWatch("file", form) ?? [];

  // Sync queue when the file list changes and we are not uploading.
  const handleFormValuesChange = useCallback(
    (changed: Partial<UploadFormValues>) => {
      if ("file" in changed && !isUploading) {
        const fileList = changed.file ?? [];
        if (fileList.length === 0) {
          pendingUploadIdentity.current = null;
        }
        handleFileListChange(fileList);
      }
    },
    [isUploading, handleFileListChange],
  );

  return (
    <PageContainer
      title="上传知识文件"
      description="上传文件后进入校验、去重、AI 分析与管理员审核流程。"
    >
      <Form<UploadFormValues>
        form={form}
        className="upload-workspace"
        layout="vertical"
        initialValues={{
          submitAfterUpload: true,
          aiAnalyze: true,
          replaceExisting: false,
        }}
        requiredMark={false}
        onValuesChange={handleFormValuesChange}
        onFinish={handleSubmit}
      >
        <div className="upload-main">
          <Card
            className="document-panel upload-drop-card"
            title={
              <Space>
                <CloudUploadOutlined />
                选择文件
              </Space>
            }
          >
            {departmentBlocked ? (
              <DepartmentAssignmentAlert className="upload-disabled-alert" />
            ) : null}
            {uploadPolicyQuery.isError ? (
              <Alert
                type="error"
                showIcon
                className="upload-disabled-alert"
                message="上传策略加载失败，上传入口已暂停"
                description="为避免使用过期的文件类型、大小或开关配置，策略恢复前不会发送文件。"
                action={
                  <Button size="small" onClick={() => void uploadPolicyQuery.refetch()}>
                    重试
                  </Button>
                }
              />
            ) : null}
            {uploadPolicyReady && !uploadEnabled ? (
              <Alert
                type="warning"
                showIcon
                className="upload-disabled-alert"
                message="当前系统已关闭员工上传"
                description="管理员重新开启上传入口后，员工可继续选择文件并提交。"
              />
            ) : null}
            {uploadPolicyReady && uploadEnabled && !maxFileSizeValid ? (
              <Alert
                type="error"
                showIcon
                className="upload-disabled-alert"
                message="上传大小策略无效，上传入口已暂停"
                description="管理员修复单文件大小上限后即可继续上传。"
              />
            ) : null}
            <Form.Item
              name="file"
              valuePropName="fileList"
              getValueFromEvent={normalizeUploadFile}
              rules={[{ required: true, message: "请选择文件" }]}
            >
              <Upload.Dragger
                multiple={effectiveAllowMultiFile}
                maxCount={effectiveAllowMultiFile ? undefined : 1}
                beforeUpload={handleBeforeUpload}
                accept={acceptValue}
                disabled={isUploading || !canUpload}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">拖拽文件到此处，或点击选择文件</p>
                <p className="ant-upload-hint">
                  支持 {allowedExtensionText}
                  {effectiveAllowMultiFile ? "，可同时选择多个文件" : "，当前仅允许单文件上传"}，
                  {fileSizeLimitText}，最多 {CONCURRENCY_LIMIT} 个并发上传。
                </p>
              </Upload.Dragger>
            </Form.Item>
          </Card>

          <Card className="document-panel upload-queue-card" title="上传队列">
            {queue.length > 0 ? (
              <div data-testid="upload-queue">
                {queue.map((item) => (
                  <div
                    className="upload-queue-row"
                    key={item.uid}
                    data-testid={`queue-row-${item.uid}`}
                  >
                    <span className="upload-queue-row__icon">
                      {item.status === "error" ? (
                        <WarningOutlined style={{ color: "var(--ku-color-danger)" }} />
                      ) : (
                        <FileTextOutlined />
                      )}
                    </span>
                    <span className="upload-queue-row__copy">
                      <Typography.Text strong>{item.name}</Typography.Text>
                      {item.status === "pending" && (
                        <Typography.Text type="secondary">等待上传</Typography.Text>
                      )}
                      {item.status === "uploading" && (
                        <Progress percent={item.percent} size="small" status="active" />
                      )}
                      {(item.status === "success" || item.status === "duplicate") && (
                        <Progress percent={100} size="small" status="success" />
                      )}
                      {item.status === "error" && (
                        <>
                          <Progress percent={item.percent} size="small" status="exception" />
                          <Typography.Text type="danger" className="upload-queue-row__error">
                            {item.errorMessage}
                          </Typography.Text>
                        </>
                      )}
                    </span>
                    <span className="upload-queue-row__status">
                      {item.status === "pending" && <StatusTag kind="sync" value="queued" />}
                      {item.status === "uploading" && <StatusTag kind="sync" value="syncing" />}
                      {(item.status === "success" || item.status === "duplicate") &&
                        item.result && <StatusTag kind="file" value={item.result.status} />}
                      {item.status === "error" && <StatusTag kind="file" value="failed" />}
                    </span>
                    {item.status === "duplicate" && (
                      <Typography.Text
                        type="warning"
                        className="upload-queue-row__dup"
                        data-testid={`dup-indicator-${item.uid}`}
                      >
                        重复文件（已复用）
                      </Typography.Text>
                    )}
                    {item.status === "success" && item.result && (
                      <Button
                        type="link"
                        size="small"
                        className="upload-queue-row__link"
                        onClick={() => navigate(`/files/${item.result!.id}`)}
                      >
                        查看详情
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            ) : selectedFiles.length > 0 ? (
              <div data-testid="upload-queue">
                {selectedFiles.map((f) => (
                  <div className="upload-queue-row" key={f.uid}>
                    <span className="upload-queue-row__icon">
                      <FileTextOutlined />
                    </span>
                    <span className="upload-queue-row__copy">
                      <Typography.Text strong>{f.name}</Typography.Text>
                      <Typography.Text type="secondary">等待开始上传</Typography.Text>
                    </span>
                    <StatusTag kind="sync" value="queued" />
                  </div>
                ))}
              </div>
            ) : (
              <div className="upload-empty-queue">
                <InfoCircleOutlined />
                <Typography.Text type="secondary">选择文件后会显示待上传队列。</Typography.Text>
              </div>
            )}
          </Card>

          <Card className="document-panel upload-tips-card" title="上传提示">
            <div className="upload-tip-list">
              <Space>
                <CheckCircleOutlined className="upload-tip-list__icon" />
                <Typography.Text>重复文件会复用已有对象，避免重复占用存储。</Typography.Text>
              </Space>
              <Space>
                <CheckCircleOutlined className="upload-tip-list__icon" />
                <Typography.Text>严重敏感内容默认不会同步到 RAGFlow。</Typography.Text>
              </Space>
              <Space>
                <CheckCircleOutlined className="upload-tip-list__icon" />
                <Typography.Text>审核通过后才会进入 RAGFlow 同步队列。</Typography.Text>
              </Space>
            </div>
          </Card>
        </div>

        <Card
          className="document-panel upload-meta-card"
          title={
            <Space>
              <TagsOutlined />
              文件信息
            </Space>
          }
        >
          <div className="upload-replacement-control">
            <Form.Item name="replaceExisting" valuePropName="checked" noStyle>
              <Switch
                aria-label="替代现有文档"
                checkedChildren="替代现有文档"
                unCheckedChildren="上传新文档"
                onChange={handleReplacementToggle}
              />
            </Form.Item>
            <Typography.Text type="secondary">
              仅已入库且仍为当前版本的本人文档可被替代。
            </Typography.Text>
          </div>
          {replaceExisting ? (
            <div className="upload-replacement-panel" aria-live="polite">
              <Alert
                type="warning"
                showIcon
                icon={<SwapOutlined />}
                message="将创建候选版本，不会覆盖旧原件"
                description="新版本完成审核并开始 RAGFlow 切换前，旧版本仍按当前状态服务；生效后的旧远端处理方式由上传时冻结的系统策略决定。若切换失败，管理员可在处理日志中安全重试。"
              />
              {replacementCandidatesFailed ? (
                <Alert
                  type="error"
                  showIcon
                  message="可替代文档加载失败"
                  description="为避免替代错误版本，候选列表恢复前不会提交上传。"
                  action={
                    <Button
                      size="small"
                      onClick={() =>
                        void (replacementCandidatesQuery.isFetchNextPageError
                          ? replacementCandidatesQuery.fetchNextPage()
                          : replacementCandidatesQuery.refetch())
                      }
                    >
                      重试
                    </Button>
                  }
                />
              ) : null}
              <Form.Item
                label="要替代的当前文档"
                name="replacesFileId"
                rules={[{ required: true, message: "请选择要替代的当前文档" }]}
              >
                <Select
                  showSearch
                  filterOption={false}
                  onSearch={handleReplacementSearch}
                  loading={replacementCandidatesLoading}
                  disabled={
                    !replacementCandidatesQuery.isSuccess ||
                    replacementCandidatesFailed ||
                    replacementCandidatesLoading
                  }
                  placeholder="搜索并选择已入库文档"
                  notFoundContent={
                    replacementCandidatesLoading ? "正在加载" : "没有可替代的当前文档"
                  }
                  options={replacementCandidates.map((file) => ({
                    value: file.id,
                    label: `${documentDisplayTitle(file)} · v${file.version_number}`,
                  }))}
                />
              </Form.Item>
              {replacementCandidatesQuery.hasNextPage ? (
                <Button
                  block
                  loading={replacementCandidatesQuery.isFetchingNextPage}
                  disabled={
                    replacementCandidatesQuery.isFetchingNextPage || replacementCandidatesFailed
                  }
                  onClick={() => void replacementCandidatesQuery.fetchNextPage()}
                >
                  加载更多候选
                </Button>
              ) : replacementCandidatesQuery.isSuccess && replacementCandidates.length > 0 ? (
                <Typography.Text type="secondary">已加载全部可替代文档</Typography.Text>
              ) : null}
            </div>
          ) : null}
          <Form.Item label="说明" name="description">
            <Input.TextArea
              rows={5}
              maxLength={2000}
              showCount
              placeholder="补充用途、来源或审核备注"
            />
          </Form.Item>
          <div className="upload-switch-grid">
            <Form.Item name="submitAfterUpload" valuePropName="checked">
              <Switch
                checkedChildren="上传后提交审核"
                unCheckedChildren="保存草稿"
                defaultChecked
              />
            </Form.Item>
            <Form.Item name="aiAnalyze" valuePropName="checked">
              <Switch checkedChildren="启用 AI 分析" unCheckedChildren="跳过 AI" defaultChecked />
            </Form.Item>
          </div>
          <Space className="upload-actions">
            <Button disabled={isUploading || !canStartUpload} onClick={handleSaveDraft}>
              保存草稿
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={isUploading}
              disabled={!canStartUpload}
            >
              开始上传
            </Button>
          </Space>
        </Card>
      </Form>
    </PageContainer>
  );
}
