import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Form,
  Input,
  Progress,
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
  SafetyCertificateOutlined,
  TagsOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useState, useCallback, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { UploadFile } from "antd/es/upload/interface";

import type { RcFile } from "antd/es/upload";

import { type KnowledgeFile, getUploadPolicy, uploadDocument } from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import {
  allowMultiFileFromPolicy,
  allowedExtensionsFromPolicy,
  extensionAcceptValue,
  uploadEnabledFromPolicy,
} from "../../utils/uploadConfig";

/** Maximum number of simultaneous uploads. */
const CONCURRENCY_LIMIT = 3;

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
}

function normalizeUploadFile(event: { fileList?: UploadFile[] } | UploadFile[]): UploadFile[] {
  if (Array.isArray(event)) {
    return event;
  }
  return event.fileList ?? [];
}

/** Run at most `limit` promises concurrently from a task factory array. */
async function runConcurrent<T>(
  tasks: Array<() => Promise<T>>,
  limit: number,
): Promise<PromiseSettledResult<T>[]> {
  const results: PromiseSettledResult<T>[] = new Array(tasks.length);
  let nextIndex = 0;

  async function worker(): Promise<void> {
    while (nextIndex < tasks.length) {
      const index = nextIndex;
      nextIndex += 1;
      try {
        results[index] = { status: "fulfilled", value: await tasks[index]() };
      } catch (err) {
        results[index] = {
          status: "rejected",
          reason: err instanceof Error ? err : new Error(String(err)),
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
  const [form] = Form.useForm<UploadFormValues>();

  // The upload queue is separate from the AntD Upload fileList so we can
  // track per-file progress and result independently of the form state.
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const uploadPolicyQuery = useQuery({
    queryKey: ["upload-policy"],
    queryFn: getUploadPolicy,
  });
  const allowedExtensions = useMemo(
    () => allowedExtensionsFromPolicy(uploadPolicyQuery.data),
    [uploadPolicyQuery.data],
  );
  const allowMultiFile = allowMultiFileFromPolicy(uploadPolicyQuery.data);
  const uploadEnabled = uploadEnabledFromPolicy(uploadPolicyQuery.data);
  const acceptValue = useMemo(() => extensionAcceptValue(allowedExtensions), [allowedExtensions]);
  const allowedExtensionText = allowedExtensions
    .map((extension) => extension.toUpperCase())
    .join("、");

  // Guard against stale closures when updating individual queue rows.
  const queueRef = useRef(queue);
  queueRef.current = queue;

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

  const handleSubmit = useCallback(
    async (values: UploadFormValues) => {
      const fileList = values.file ?? [];
      const submitAfterUpload = values.submitAfterUpload ?? false;
      const aiAnalysisEnabled = values.aiAnalyze ?? true;

      if (fileList.length === 0) {
        message.warning("请至少选择一个文件");
        return;
      }

      if (!uploadEnabled) {
        message.warning("当前系统已关闭员工上传");
        return;
      }

      // Re-build queue from the current form file list so UIDs match.
      const freshQueue = buildQueue(fileList);
      setQueue(freshQueue);
      setIsUploading(true);

      const tasks = freshQueue.map((item) => async () => {
        updateItem(item.uid, { status: "uploading", percent: 0 });

        const result = await uploadDocument(
          {
            file: item.file,
            description: values.description,
            visibility: "private",
            submitAfterUpload,
            aiAnalysisEnabled,
          },
          (percent) => {
            updateItem(item.uid, { percent });
          },
        );

        updateItem(item.uid, {
          status: result.duplicate ? "duplicate" : "success",
          percent: 100,
          result,
        });

        return result;
      });

      const settled = await runConcurrent(tasks, CONCURRENCY_LIMIT);

      // Mark failed items.
      settled.forEach((outcome, i) => {
        if (outcome.status === "rejected") {
          const errorMessage =
            outcome.reason instanceof Error ? outcome.reason.message : "上传失败";
          updateItem(freshQueue[i].uid, { status: "error", errorMessage });
        }
      });

      setIsUploading(false);
      form.resetFields(["file"]);

      const successCount = settled.filter((r) => r.status === "fulfilled").length;
      const failCount = settled.filter((r) => r.status === "rejected").length;
      const dupCount = settled
        .filter((r): r is PromiseFulfilledResult<KnowledgeFile> => r.status === "fulfilled")
        .filter((r) => r.value.duplicate).length;

      if (failCount === 0) {
        message.success(
          dupCount > 0
            ? `上传完成，共 ${successCount} 个文件，其中 ${dupCount} 个重复`
            : `上传完成，共 ${successCount} 个文件`,
        );
      } else {
        message.warning(`上传完成：${successCount} 成功，${failCount} 失败`);
      }
    },
    [buildQueue, updateItem, form, message, uploadEnabled],
  );

  const handleSaveDraft = useCallback(() => {
    form.setFieldsValue({ submitAfterUpload: false });
    form.submit();
  }, [form]);

  const selectedFiles: UploadFile[] = Form.useWatch("file", form) ?? [];
  const queuedCount = queue.length > 0 ? queue.length : selectedFiles.length;
  const completedCount = queue.filter(
    (item) => item.status === "success" || item.status === "duplicate",
  ).length;
  const failedCount = queue.filter((item) => item.status === "error").length;
  const supportedFormatValue =
    allowedExtensions.length > 0 ? `${allowedExtensions.length} 类` : "读取中";

  // Sync queue when the file list changes and we are not uploading.
  const handleFormValuesChange = useCallback(
    (changed: Partial<UploadFormValues>) => {
      if ("file" in changed && !isUploading) {
        handleFileListChange(changed.file ?? []);
      }
    },
    [isUploading, handleFileListChange],
  );

  return (
    <PageContainer
      title="上传知识文件"
      description="上传文件后进入校验、去重、AI 分析与管理员审核流程。"
    >
      <div className="metric-grid">
        <KpiCard
          icon={<FileTextOutlined />}
          title="支持格式"
          value={supportedFormatValue}
          description={allowedExtensionText || "读取上传配置"}
          tone="primary"
        />
        <KpiCard
          icon={<CloudUploadOutlined />}
          title="并发上传"
          value={CONCURRENCY_LIMIT}
          description="最多同时处理文件"
          tone="info"
        />
        <KpiCard
          icon={<InboxOutlined />}
          title="当前队列"
          value={queuedCount}
          description={isUploading ? "上传进行中" : "待处理文件"}
          tone="warning"
        />
        <KpiCard
          icon={<SafetyCertificateOutlined />}
          title="成功 / 失败"
          value={`${completedCount} / ${failedCount}`}
          description="本次上传结果"
          tone={failedCount > 0 ? "danger" : "success"}
        />
      </div>

      <Form<UploadFormValues>
        form={form}
        className="upload-workspace"
        layout="vertical"
        initialValues={{
          submitAfterUpload: true,
          aiAnalyze: true,
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
            {!uploadEnabled ? (
              <Alert
                type="warning"
                showIcon
                className="upload-disabled-alert"
                message="当前系统已关闭员工上传"
                description="管理员重新开启上传入口后，员工可继续选择文件并提交。"
              />
            ) : null}
            <Form.Item
              name="file"
              valuePropName="fileList"
              getValueFromEvent={normalizeUploadFile}
              rules={[{ required: true, message: "请选择文件" }]}
            >
              <Upload.Dragger
                multiple={allowMultiFile}
                maxCount={allowMultiFile ? undefined : 1}
                beforeUpload={() => false}
                accept={acceptValue}
                disabled={isUploading || !uploadEnabled}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">拖拽文件到此处，或点击选择文件</p>
                <p className="ant-upload-hint">
                  支持 {allowedExtensionText}
                  {allowMultiFile ? "，可同时选择多个文件。" : "，当前仅允许单文件上传。"}
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
            <Button disabled={isUploading || !uploadEnabled} onClick={handleSaveDraft}>
              保存草稿
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={isUploading}
              disabled={!uploadEnabled}
            >
              开始上传
            </Button>
          </Space>
        </Card>
      </Form>
    </PageContainer>
  );
}
