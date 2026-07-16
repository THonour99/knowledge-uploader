import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Progress,
  Radio,
  Result,
  Select,
  Space,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloudSyncOutlined,
  CloudUploadOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileProtectOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyOutlined,
  UnlockOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate, useParams } from "react-router-dom";

import {
  type DatasetMapping,
  type FileAnalysis,
  type FileAnalysisTable,
  type DocumentContent,
  type KnowledgeFile,
  type ReviewDecisionPayload,
  type SimilarFileReference,
  type SyncTask,
  approveFile,
  claimReviewFile,
  getDocument,
  getDocumentContent,
  isApiError,
  listDatasetMappings,
  listTasks,
  rejectFile,
  releaseReviewClaim,
} from "../../api/client";
import { KpiCard, type KpiTone } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { useNow } from "../../hooks/useNow";
import { PageContainer } from "../../layouts/PageContainer";
import { Roles, useAuthStore } from "../../store/auth.store";
import { downloadDocument } from "../../utils/documentDownload";
import { documentDisplayTitle } from "../../utils/documentTitle";

const TASK_TYPE_LABELS: Record<string, string> = {
  ragflow_upload: "RAGFlow 上传",
  ragflow_parse: "RAGFlow 解析",
  ragflow_status_check: "RAGFlow 状态检查",
};

type FileLoadResultStatus = "404" | "403" | "500" | "warning" | "error";

export interface FileLoadErrorPresentation {
  status: FileLoadResultStatus;
  title: string;
  subTitle: string;
}

interface ReviewFormValues {
  sync_decision?: ReviewDecisionPayload["sync_decision"];
  dataset_mapping_id?: string;
  reason?: string;
}

export function fileLoadErrorPresentation(error: unknown): FileLoadErrorPresentation {
  if (isApiError(error)) {
    if (error.status === 404) {
      return {
        status: "404",
        title: "文件不存在",
        subTitle: "文件可能已被删除，或链接已经失效。",
      };
    }
    if (error.status === 403) {
      return {
        status: "403",
        title: "无权访问此文件",
        subTitle: "你当前的账号或部门没有查看这份文件的权限。",
      };
    }
    if (error.status !== undefined && error.status >= 500) {
      return {
        status: "500",
        title: "文件服务暂时不可用",
        subTitle: "服务端处理失败，请稍后重试；若持续失败，请联系系统管理员。",
      };
    }
    if (error.status === undefined) {
      return {
        status: "warning",
        title: "无法连接文件服务",
        subTitle: "请检查网络连接后重试。",
      };
    }
  }

  if (error instanceof TypeError) {
    return {
      status: "warning",
      title: "无法连接文件服务",
      subTitle: "请检查网络连接后重试。",
    };
  }

  return {
    status: "error",
    title: "文件加载失败",
    subTitle: error instanceof Error && error.message ? error.message : "发生未知错误，请重试。",
  };
}

export function hasValidDetailReviewClaim(
  file: Pick<KnowledgeFile, "claimed_by" | "claimed_at" | "claim_expires_at">,
  now = Date.now(),
): boolean {
  if (!file.claimed_by || !file.claimed_at || !file.claim_expires_at) {
    return false;
  }
  const claimedAt = Date.parse(file.claimed_at);
  const expiresAt = Date.parse(file.claim_expires_at);
  return (
    Number.isFinite(claimedAt) &&
    Number.isFinite(expiresAt) &&
    claimedAt <= now &&
    expiresAt > now &&
    expiresAt > claimedAt
  );
}

export function hasActiveDetailReviewClaim(
  file: Pick<KnowledgeFile, "claimed_by" | "claimed_at" | "claim_expires_at">,
  userId: string | null | undefined,
  now = Date.now(),
): boolean {
  return Boolean(userId && file.claimed_by === userId && hasValidDetailReviewClaim(file, now));
}

export function buildDetailReviewDecisionPayload(
  values: ReviewFormValues,
  file: Pick<KnowledgeFile, "category_id">,
  mappings: DatasetMapping[],
): ReviewDecisionPayload {
  if (!values.sync_decision) {
    throw new Error("必须明确选择是否进入 RAGFlow");
  }
  const reason = values.reason?.trim() || null;
  if (values.sync_decision === "approve_only") {
    return {
      sync_decision: "approve_only",
      category_id: file.category_id ?? null,
      reason,
    };
  }

  const mapping = mappings.find((item) => item.enabled && item.id === values.dataset_mapping_id);
  if (!mapping) {
    throw new Error("批准并同步时必须选择有效的 Dataset 映射");
  }
  return {
    sync_decision: "sync",
    category_id: mapping.category_id,
    dataset_mapping_id: mapping.id,
    reason,
  };
}

export function taskFailureMessage(task: SyncTask): string | null {
  const explicitMessage = task.error_message?.trim();
  if (explicitMessage) {
    return explicitMessage;
  }
  if (task.status !== "failed") {
    return null;
  }
  const logMessage = [...task.logs]
    .reverse()
    .find((log) => log.message.trim())
    ?.message.trim();
  return logMessage || `任务失败（${task.id}），服务端未提供错误详情`;
}

function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatTaskWindow(task: SyncTask): string {
  const startedAt = dayjs(task.started_at ?? task.created_at).format("YYYY-MM-DD HH:mm");
  if (!task.finished_at) {
    return startedAt;
  }
  return `${startedAt} ~ ${dayjs(task.finished_at).format("YYYY-MM-DD HH:mm")}`;
}

function clampScore(score: number): number {
  return Math.max(0, Math.min(100, Math.round(score)));
}

function qualityLevel(score: number | null | undefined): string {
  if (typeof score !== "number") {
    return "暂无评分";
  }
  if (score >= 85) {
    return "优秀";
  }
  if (score >= 70) {
    return "良好";
  }
  if (score >= 60) {
    return "待优化";
  }
  return "低质量";
}

function qualityProgressStatus(score: number): "success" | "normal" | "exception" {
  if (score >= 85) {
    return "success";
  }
  if (score < 60) {
    return "exception";
  }
  return "normal";
}

function qualityTone(score: number | null): KpiTone {
  if (score === null) {
    return "info";
  }
  if (score >= 85) {
    return "success";
  }
  if (score < 60) {
    return "danger";
  }
  return "warning";
}

function syncStatus(file: KnowledgeFile): string {
  if (file.sync_error || file.ragflow_parse_status === "failed") {
    return "failed";
  }
  if (file.ragflow_parse_status === "parsed") {
    return "synced";
  }
  if (file.ragflow_parse_status === "parsing") {
    return "syncing";
  }
  return file.ragflow_document_id ? "queued" : "not_synced";
}

function syncMetricLabel(status: string): string {
  const labels: Record<string, string> = {
    failed: "异常",
    synced: "已入库",
    syncing: "解析中",
    queued: "待解析",
    not_synced: "未同步",
  };
  return labels[status] ?? status;
}

function syncMetricTone(status: string): KpiTone {
  if (status === "failed") {
    return "danger";
  }
  if (status === "synced") {
    return "success";
  }
  if (status === "syncing" || status === "queued") {
    return "warning";
  }
  return "purple";
}

function syncMetricDescription(file: KnowledgeFile): string {
  if (file.sync_error) {
    return "需要人工处理";
  }
  if (file.last_sync_at) {
    return `最近 ${dayjs(file.last_sync_at).format("MM-DD HH:mm")}`;
  }
  if (file.ragflow_document_id) {
    return "远端文档已创建";
  }
  return "等待审核通过";
}

function expiryMeta(expiresAt?: string | null, explicitStatus?: string | null) {
  if (!expiresAt && !explicitStatus) {
    return null;
  }

  const expiry = expiresAt ? dayjs(expiresAt) : null;
  const inferredStatus =
    expiresAt && (!explicitStatus || explicitStatus === "never")
      ? inferExpiryStatus(expiry)
      : (explicitStatus ?? inferExpiryStatus(expiry));

  const labelMap: Record<string, { label: string; color: string }> = {
    active: { label: "有效", color: "green" },
    expiring: { label: "即将过期", color: "orange" },
    expired: { label: "已过期", color: "red" },
    never: { label: "长期有效", color: "default" },
  };
  const status = labelMap[inferredStatus] ?? { label: inferredStatus, color: "default" };
  const dateText = expiry?.isValid() ? expiry.format("YYYY-MM-DD") : "未设置到期日";

  return { ...status, dateText };
}

function inferExpiryStatus(expiry: dayjs.Dayjs | null): string {
  if (expiry?.isBefore(dayjs())) {
    return "expired";
  }
  if (expiry?.diff(dayjs(), "day") !== undefined && expiry.diff(dayjs(), "day") <= 7) {
    return "expiring";
  }
  return "active";
}

function ExpiryIndicator({
  expiresAt,
  status,
}: {
  expiresAt?: string | null;
  status?: string | null;
}) {
  const meta = expiryMeta(expiresAt, status);
  if (!meta) {
    return <Typography.Text type="secondary">暂无过期规则</Typography.Text>;
  }

  return (
    <Space size={6} wrap>
      <Tag color={meta.color}>{meta.label}</Tag>
      <Typography.Text>{meta.dateText}</Typography.Text>
    </Space>
  );
}

function similarReferenceLabel(reference: SimilarFileReference): string {
  if (typeof reference === "string") {
    return reference;
  }

  return (
    reference.original_name ??
    reference.name ??
    reference.file_id ??
    reference.id ??
    "未命名相似文档"
  );
}

function similarReferenceHint(reference: SimilarFileReference): string | null {
  if (typeof reference === "string") {
    return null;
  }

  const score = reference.similarity ?? reference.score;
  if (typeof score !== "number") {
    return null;
  }

  return score <= 1 ? `${Math.round(score * 100)}% 相似` : `${Math.round(score)} 分`;
}

function similarReferences(analysis: FileAnalysis): SimilarFileReference[] {
  if (analysis.similar_files && analysis.similar_files.length > 0) {
    return analysis.similar_files;
  }

  return analysis.similar_file_ids ?? [];
}

function tableTitle(table: FileAnalysisTable, index: number): string {
  return table.title ?? table.name ?? `表格 ${index + 1}`;
}

function tablePreview(table: FileAnalysisTable): string {
  if (table.markdown?.trim()) {
    return table.markdown.trim();
  }
  if (table.text?.trim()) {
    return table.text.trim();
  }
  return JSON.stringify(table, null, 2);
}

export const INLINE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024;

const SAFE_INLINE_PREVIEW_MIME_TYPES = new Set([
  "application/pdf",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
  "text/csv",
  "text/markdown",
  "text/plain",
]);

function normalizeMimeType(value: string): string {
  return value.split(";", 1)[0].trim().toLowerCase();
}

export function canPreviewInline(file: KnowledgeFile): boolean {
  return (
    SAFE_INLINE_PREVIEW_MIME_TYPES.has(normalizeMimeType(file.mime_type)) &&
    file.size <= INLINE_PREVIEW_MAX_BYTES
  );
}

export function validateInlinePreviewContent(content: DocumentContent): string {
  if (/^\s*attachment\b/i.test(content.contentDisposition ?? "")) {
    throw new Error("服务端要求以附件方式下载，已阻止内联展示");
  }

  const responseMimeType = normalizeMimeType(content.contentType);
  const blobMimeType = normalizeMimeType(content.blob.type);
  if (!SAFE_INLINE_PREVIEW_MIME_TYPES.has(responseMimeType)) {
    throw new Error("服务端返回的文件类型不在安全预览白名单中");
  }
  if (blobMimeType && !SAFE_INLINE_PREVIEW_MIME_TYPES.has(blobMimeType)) {
    throw new Error("文件内容类型与安全预览策略不兼容");
  }
  if (
    content.blob.size > INLINE_PREVIEW_MAX_BYTES ||
    (content.contentLength !== null && content.contentLength > INLINE_PREVIEW_MAX_BYTES)
  ) {
    throw new Error("原件超过 20 MiB 安全预览上限，请流式下载后查看");
  }

  return responseMimeType;
}

function OriginalDocumentCard({ file }: { file: KnowledgeFile }) {
  const { message } = AntdApp.useApp();
  const [previewRequested, setPreviewRequested] = useState(false);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const [previewMimeType, setPreviewMimeType] = useState<string | null>(null);
  const displayTitle = documentDisplayTitle(file);
  const previewMutation = useMutation({
    mutationKey: ["documents", file.id, "content", "inline"],
    mutationFn: async () => {
      const content = await getDocumentContent(file.id, "inline", {
        maxBytes: INLINE_PREVIEW_MAX_BYTES,
      });
      return {
        content,
        mimeType: validateInlinePreviewContent(content),
      };
    },
    gcTime: 0,
    retry: false,
    onSuccess: ({ content, mimeType }) => {
      const nextObjectUrl = URL.createObjectURL(content.blob);
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
      }
      objectUrlRef.current = nextObjectUrl;
      setObjectUrl(nextObjectUrl);
      setPreviewMimeType(mimeType);
    },
    onError: () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
      setObjectUrl(null);
      setPreviewMimeType(null);
    },
  });
  const downloadMutation = useMutation({
    mutationFn: () =>
      downloadDocument({
        id: file.id,
        fileName: file.original_name,
        sizeBytes: file.size,
      }),
    onSuccess: (mode) => {
      if (mode !== "cancelled") {
        message.success(mode === "streamed" ? "原件已安全保存" : "原件下载已开始");
      }
    },
  });

  useEffect(() => {
    return () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, []);

  const previewSupported = canPreviewInline(file);
  const previewBlockReason =
    file.size > INLINE_PREVIEW_MAX_BYTES
      ? "原件超过 20 MiB 安全预览上限，请流式下载后查看。"
      : "请使用鉴权下载打开原件；平台不会暴露 MinIO 永久地址或对象凭据。";

  return (
    <Card
      id="original"
      className="document-panel original-document-card"
      title="原件预览"
      extra={
        <Space>
          {previewSupported && !previewRequested ? (
            <Button
              icon={<EyeOutlined />}
              onClick={() => {
                setPreviewRequested(true);
                previewMutation.mutate();
              }}
            >
              加载预览
            </Button>
          ) : null}
          {previewSupported && objectUrl && !previewMutation.isPending ? (
            <Button icon={<ReloadOutlined />} onClick={() => previewMutation.mutate()}>
              重新加载预览
            </Button>
          ) : null}
          <Button
            icon={<DownloadOutlined />}
            loading={downloadMutation.isPending}
            onClick={() => downloadMutation.mutate()}
          >
            下载原件
          </Button>
        </Space>
      }
    >
      {!previewSupported ? (
        <Alert
          type="info"
          showIcon
          message="此格式不支持浏览器安全预览"
          description={previewBlockReason}
        />
      ) : null}
      {previewSupported && !previewRequested ? (
        <div className="original-document-placeholder">
          <EyeOutlined />
          <Typography.Text strong>按需加载受鉴权原件</Typography.Text>
          <Typography.Text type="secondary">
            管理员查看他人原件会写入审计；大文件不会在进入详情时自动下载。
          </Typography.Text>
        </div>
      ) : null}
      {previewMutation.isPending ? (
        <div className="original-document-placeholder">
          <Typography.Text>正在安全加载原件…</Typography.Text>
        </div>
      ) : null}
      {previewMutation.isError ? (
        <Alert
          type="error"
          showIcon
          message="原件预览加载失败"
          description={previewMutation.error.message}
          action={
            <Button size="small" onClick={() => previewMutation.mutate()}>
              重试
            </Button>
          }
        />
      ) : null}
      {objectUrl && previewMimeType ? (
        previewMimeType.startsWith("image/") ? (
          <img
            className="original-document-image"
            src={objectUrl}
            alt={`${displayTitle} 原件预览`}
          />
        ) : (
          <iframe
            className="original-document-frame"
            src={objectUrl}
            title={`${displayTitle} 原件预览`}
            sandbox=""
            referrerPolicy="no-referrer"
          />
        )
      ) : null}
      {downloadMutation.isError ? (
        <Alert
          className="original-document-download-error"
          type="error"
          showIcon
          message="原件下载失败"
          description={downloadMutation.error.message}
        />
      ) : null}
    </Card>
  );
}

interface AnalysisCardProps {
  analysis: FileAnalysis;
  file: KnowledgeFile;
  loading: boolean;
}

function AnalysisCard({ analysis, file, loading }: AnalysisCardProps) {
  const qualityScore =
    typeof analysis.quality_score === "number" ? clampScore(analysis.quality_score) : null;
  const tables = analysis.tables_json ?? [];
  const tableCount = analysis.table_count ?? tables.length;
  const similarItems = similarReferences(analysis);
  const expiresAt = file.expires_at ?? analysis.expires_at ?? analysis.detected_expire_at ?? null;
  const expiryStatus = file.expiry_status ?? analysis.expiry_status ?? null;
  const collapseItems = [
    {
      key: "extracted-text-preview",
      label: "提取文本预览",
      children: (
        <Typography.Paragraph className="document-extracted-preview">
          {analysis.extracted_text_preview ?? "暂无提取文本"}
        </Typography.Paragraph>
      ),
    },
    ...tables.map((table, index) => ({
      key: `table-${index}`,
      label: tableTitle(table, index),
      children: (
        <div className="document-table-preview">
          <Typography.Text type="secondary">表格结构预览</Typography.Text>
          <pre className="document-table-preview__body">{tablePreview(table)}</pre>
        </div>
      ),
    })),
  ];

  return (
    <Card className="document-panel" title="AI 分析" loading={loading}>
      <Space direction="vertical" size={12} className="document-result">
        {analysis.status === "failed" ? (
          <Alert
            type="error"
            showIcon
            message="AI 分析失败"
            description={analysis.error_message ?? "分析任务执行失败"}
          />
        ) : null}
        <div className="document-analysis-metrics">
          <div className="document-analysis-metric">
            <span className="document-analysis-metric__label">质量评分</span>
            <span className="document-analysis-metric__value">
              {qualityScore === null ? "暂无" : `${qualityScore} 分`}
            </span>
            {qualityScore === null ? (
              <span className="document-analysis-metric__hint">等待 R5 质量评分结果</span>
            ) : (
              <Progress
                percent={qualityScore}
                size="small"
                status={qualityProgressStatus(qualityScore)}
                showInfo={false}
              />
            )}
          </div>
          <div className="document-analysis-metric">
            <span className="document-analysis-metric__label">相似文档</span>
            <span className="document-analysis-metric__value">{similarItems.length}</span>
            <span className="document-analysis-metric__hint">近重复检测结果</span>
          </div>
          <div className="document-analysis-metric">
            <span className="document-analysis-metric__label">表格结构</span>
            <span className="document-analysis-metric__value">{tableCount}</span>
            <span className="document-analysis-metric__hint">已识别表格数量</span>
          </div>
        </div>
        <Descriptions column={1} size="middle" styles={{ label: { width: 140 } }}>
          <Descriptions.Item label="风险等级">
            <StatusTag kind="risk" value={analysis.sensitive_risk_level} />
          </Descriptions.Item>
          <Descriptions.Item label="质量等级">{qualityLevel(qualityScore)}</Descriptions.Item>
          <Descriptions.Item label="过期状态">
            <ExpiryIndicator expiresAt={expiresAt} status={expiryStatus} />
          </Descriptions.Item>
          <Descriptions.Item label="摘要">{analysis.summary ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="完成时间">
            {analysis.finished_at ? dayjs(analysis.finished_at).format("YYYY-MM-DD HH:mm") : "-"}
          </Descriptions.Item>
        </Descriptions>
        {similarItems.length > 0 ? (
          <Alert
            type="warning"
            showIcon
            message={`检测到 ${similarItems.length} 个相似文档`}
            description={
              <div className="document-analysis-list">
                {similarItems.map((reference) => (
                  <span
                    className="document-analysis-list__item"
                    key={similarReferenceLabel(reference)}
                  >
                    <Typography.Text>{similarReferenceLabel(reference)}</Typography.Text>
                    {similarReferenceHint(reference) ? (
                      <Tag color="orange">{similarReferenceHint(reference)}</Tag>
                    ) : null}
                  </span>
                ))}
              </div>
            }
          />
        ) : null}
        <Collapse items={collapseItems} />
      </Space>
    </Card>
  );
}

interface ReviewActionCardProps {
  file: KnowledgeFile;
  userId: string | null | undefined;
  now: number;
  mappings: DatasetMapping[];
  mappingsLoading: boolean;
  mappingsError: Error | null;
  onRetryMappings: () => void;
  onRefresh: () => Promise<void>;
}

function ReviewActionCard({
  file,
  userId,
  now,
  mappings,
  mappingsLoading,
  mappingsError,
  onRetryMappings,
  onRefresh,
}: ReviewActionCardProps) {
  const { message } = AntdApp.useApp();
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [approveForm] = Form.useForm<ReviewFormValues>();
  const [rejectForm] = Form.useForm<ReviewFormValues>();
  const syncDecision = Form.useWatch("sync_decision", approveForm);
  const pendingReview = file.status === "pending_review";
  const validClaim = hasValidDetailReviewClaim(file, now);
  const activeClaim = hasActiveDetailReviewClaim(file, userId, now);
  const claimedByOther = validClaim && file.claimed_by !== userId;

  const claimMutation = useMutation({
    mutationFn: () => claimReviewFile(file.id),
    onSuccess: async () => {
      message.success("审核任务已领取，请在租约到期前完成决定");
      await onRefresh();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("该任务刚刚被他人领取，详情已刷新");
        await onRefresh();
        return;
      }
      message.error(error.message || "领取审核任务失败");
    },
  });

  const releaseMutation = useMutation({
    mutationFn: () => releaseReviewClaim(file.id),
    onSuccess: async () => {
      message.success("审核任务已释放");
      await onRefresh();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("审核租约已变化，详情已刷新");
        await onRefresh();
        return;
      }
      message.error(error.message || "释放审核任务失败");
    },
  });

  const approveMutation = useMutation({
    mutationFn: (values: ReviewFormValues) =>
      approveFile(file.id, buildDetailReviewDecisionPayload(values, file, mappings)),
    onSuccess: async (_approvedFile, values) => {
      message.success(
        values.sync_decision === "sync"
          ? "文件已批准并进入 RAGFlow 同步队列"
          : "文件已批准，本次明确不进入 RAGFlow",
      );
      setApproveOpen(false);
      approveForm.resetFields();
      await onRefresh();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("审核任务状态已变化，详情已刷新");
        setApproveOpen(false);
        await onRefresh();
        return;
      }
      message.error(error.message || "审核批准失败");
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (reason: string) => rejectFile(file.id, reason),
    onSuccess: async () => {
      message.success("文件已驳回");
      setRejectOpen(false);
      rejectForm.resetFields();
      await onRefresh();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("审核任务状态已变化，详情已刷新");
        setRejectOpen(false);
        await onRefresh();
        return;
      }
      message.error(error.message || "驳回文件失败");
    },
  });

  const scrollToOriginal = () => {
    const originalPanel = document.getElementById("original");
    if (originalPanel && "scrollIntoView" in originalPanel) {
      originalPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <>
      <Card className="document-panel review-detail-actions" title="审核操作">
        <Space direction="vertical" size={12} className="document-result">
          <Typography.Text type="secondary">
            按照“查看原件与分析 → 领取任务 → 明确审核决定”的顺序处理。
          </Typography.Text>
          <Button block icon={<EyeOutlined />} onClick={scrollToOriginal}>
            1. 查看原件与分析
          </Button>
          {!pendingReview ? (
            <Alert
              type="info"
              showIcon
              message="当前文件不在待审核状态"
              description={
                <Space wrap>
                  <StatusTag kind="file" value={file.status} />
                  <StatusTag kind="review" value={file.review_status} />
                </Space>
              }
            />
          ) : null}
          {pendingReview && activeClaim ? (
            <Alert
              type="success"
              showIcon
              message="你已领取此审核任务"
              description={
                file.claim_expires_at
                  ? `租约到期：${dayjs(file.claim_expires_at).format("YYYY-MM-DD HH:mm:ss")}`
                  : "租约到期时间不可用，请刷新后再决定"
              }
            />
          ) : null}
          {pendingReview && claimedByOther ? (
            <Alert
              type="warning"
              showIcon
              message={`任务由 ${file.claimed_by_name || "其他管理员"} 处理中`}
              description={
                file.claim_expires_at
                  ? `租约到期：${dayjs(file.claim_expires_at).format("YYYY-MM-DD HH:mm:ss")}`
                  : "请刷新确认任务归属"
              }
            />
          ) : null}
          {pendingReview && !validClaim && file.claimed_by ? (
            <Alert
              type="warning"
              showIcon
              message="原审核租约已失效"
              description="请重新领取后再提交审核决定。"
            />
          ) : null}
          {pendingReview ? (
            activeClaim ? (
              <Space wrap>
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  onClick={() => {
                    setApproveOpen(true);
                  }}
                >
                  3. 审核通过
                </Button>
                <Button danger onClick={() => setRejectOpen(true)}>
                  3. 驳回文件
                </Button>
                <Button
                  icon={<UnlockOutlined />}
                  loading={releaseMutation.isPending}
                  onClick={() => releaseMutation.mutate()}
                >
                  释放任务
                </Button>
              </Space>
            ) : (
              <Button
                type="primary"
                block
                icon={<LockOutlined />}
                disabled={claimedByOther}
                loading={claimMutation.isPending}
                onClick={() => claimMutation.mutate()}
              >
                2. 领取审核任务
              </Button>
            )
          ) : null}
        </Space>
      </Card>

      <Modal
        title="审核通过"
        open={approveOpen}
        width={620}
        okText="确认批准"
        confirmLoading={approveMutation.isPending}
        onCancel={() => {
          setApproveOpen(false);
          approveForm.resetFields();
        }}
        onOk={() => approveForm.submit()}
      >
        {file.sensitive_risk_level === "critical" ? (
          <Alert
            type="error"
            showIcon
            message="严重风险文档禁止同步"
            description="可以仅批准留存，但不能选择进入 RAGFlow。"
          />
        ) : file.sensitive_risk_level === "high" ? (
          <Alert
            type="warning"
            showIcon
            message="高风险文档同步需要风险确认"
            description="选择同步时必须填写审核说明。"
          />
        ) : null}
        {mappingsError ? (
          <Alert
            type="warning"
            showIcon
            message="Dataset 映射加载失败"
            description="仍可选择仅批准；如需同步，请先重试加载映射。"
            action={
              <Button size="small" onClick={onRetryMappings}>
                重试
              </Button>
            }
          />
        ) : null}
        <Form<ReviewFormValues>
          name="file-detail-approve"
          form={approveForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => approveMutation.mutate(values)}
        >
          <Form.Item
            label="批准后的处理"
            name="sync_decision"
            rules={[{ required: true, message: "请明确选择是否进入 RAGFlow" }]}
          >
            <Radio.Group
              onChange={(event) => {
                if (event.target.value === "approve_only") {
                  approveForm.setFieldValue("dataset_mapping_id", undefined);
                }
              }}
            >
              <Space direction="vertical">
                <Radio value="sync" disabled={file.sensitive_risk_level === "critical"}>
                  批准并同步到 RAGFlow
                </Radio>
                <Radio value="approve_only">仅批准，本次不进入 RAGFlow</Radio>
              </Space>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            label="Dataset 映射"
            name="dataset_mapping_id"
            rules={[
              {
                validator: async (_, value: string | undefined) => {
                  if (syncDecision === "sync" && !value) {
                    throw new Error("批准并同步时必须选择 Dataset 映射");
                  }
                },
              },
            ]}
          >
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              disabled={syncDecision !== "sync" || Boolean(mappingsError)}
              loading={mappingsLoading}
              options={mappings
                .filter((mapping) => mapping.enabled)
                .map((mapping) => ({
                  value: mapping.id,
                  label: `${mapping.name} → ${mapping.ragflow_dataset_name}`,
                }))}
            />
          </Form.Item>
          <Form.Item
            label="审核说明"
            name="reason"
            extra="选填；高风险文档同步时必须填写风险确认说明。"
            rules={[
              {
                validator: async (_, value: string | undefined) => {
                  if (
                    syncDecision === "sync" &&
                    file.sensitive_risk_level === "high" &&
                    !value?.trim()
                  ) {
                    throw new Error("高风险文档同步时必须填写风险确认说明");
                  }
                },
              },
            ]}
          >
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="驳回文件"
        open={rejectOpen}
        okText="确认驳回"
        okButtonProps={{ danger: true }}
        confirmLoading={rejectMutation.isPending}
        onCancel={() => {
          setRejectOpen(false);
          rejectForm.resetFields();
        }}
        onOk={() => rejectForm.submit()}
      >
        <Form<ReviewFormValues>
          name="file-detail-reject"
          form={rejectForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => rejectMutation.mutate(values.reason?.trim() ?? "")}
        >
          <Form.Item
            label="驳回原因"
            name="reason"
            rules={[{ required: true, whitespace: true, message: "请输入驳回原因" }]}
          >
            <Input.TextArea rows={4} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
interface TaskTimelineCardProps {
  tasks: SyncTask[];
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}

function TaskTimelineCard({ tasks, loading, error, onRetry }: TaskTimelineCardProps) {
  return (
    <Card className="document-panel" title="处理日志" loading={loading && !error}>
      {error ? (
        <Alert
          type="error"
          showIcon
          message="处理日志加载失败"
          description={error.message || "无法读取任务记录"}
          action={
            <Button size="small" onClick={onRetry}>
              重试
            </Button>
          }
        />
      ) : tasks.length === 0 ? (
        <Empty description="暂无任务记录" />
      ) : (
        <Timeline
          items={tasks.map((task) => {
            const failureMessage = taskFailureMessage(task);
            return {
              key: task.id,
              children: (
                <Space direction="vertical" size={4}>
                  <Space wrap>
                    <Typography.Text strong>
                      {TASK_TYPE_LABELS[task.task_type] ?? task.task_type}
                    </Typography.Text>
                    <StatusTag kind="sync" value={task.status} />
                  </Space>
                  <Typography.Text type="secondary">{formatTaskWindow(task)}</Typography.Text>
                  {failureMessage ? (
                    <Typography.Text type="danger">{failureMessage}</Typography.Text>
                  ) : null}
                </Space>
              ),
            };
          })}
        />
      )}
    </Card>
  );
}

export default function FileDetailPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const user = useAuthStore((state) => state.user);
  const role = user?.role ?? null;
  const isAdmin = role === Roles.DEPT_ADMIN || role === Roles.SYSTEM_ADMIN;
  const backPath = isAdmin ? "/files" : "/my-files";
  const backLabel = isAdmin ? "返回审核工作台" : "返回我的文件";
  const now = useNow();
  const refreshedExpiredClaims = useRef(new Set<string>());
  const fileQuery = useQuery({
    queryKey: ["documents", id],
    queryFn: () => getDocument(id ?? ""),
    enabled: Boolean(id),
  });
  const tasksQuery = useQuery({
    queryKey: ["tasks", { file_id: id }],
    queryFn: () => listTasks({ file_id: id ?? "" }),
    enabled: Boolean(id) && isAdmin,
  });
  const datasetMappingsQuery = useQuery({
    queryKey: ["dataset-mappings"],
    queryFn: listDatasetMappings,
    enabled: Boolean(id) && isAdmin,
  });
  const { refetch: refetchFile } = fileQuery;
  const { refetch: refetchTasks } = tasksQuery;
  const refreshDetail = useCallback(async () => {
    const refreshes: Array<Promise<unknown>> = [refetchFile()];
    if (isAdmin) {
      refreshes.push(refetchTasks());
    }
    await Promise.all(refreshes);
  }, [isAdmin, refetchFile, refetchTasks]);

  const file = fileQuery.data;
  useEffect(() => {
    if (!file?.claimed_by || !file.claim_expires_at) {
      return;
    }
    const expiresAt = Date.parse(file.claim_expires_at);
    if (!Number.isFinite(expiresAt) || expiresAt > now) {
      return;
    }
    const expiryKey = `${file.id}:${file.claim_expires_at}`;
    if (refreshedExpiredClaims.current.has(expiryKey)) {
      return;
    }
    refreshedExpiredClaims.current.add(expiryKey);
    void refreshDetail();
  }, [file, now, refreshDetail]);

  if (!id) {
    return (
      <Result
        status="404"
        title="文件不存在"
        subTitle="链接中缺少文件标识。"
        extra={<Button onClick={() => navigate(backPath)}>{backLabel}</Button>}
      />
    );
  }

  if (fileQuery.isError) {
    const presentation = fileLoadErrorPresentation(fileQuery.error);
    return (
      <Result
        status={presentation.status}
        title={presentation.title}
        subTitle={presentation.subTitle}
        extra={
          <Space wrap>
            <Button type="primary" icon={<ReloadOutlined />} onClick={() => fileQuery.refetch()}>
              重试
            </Button>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(backPath)}>
              {backLabel}
            </Button>
          </Space>
        }
      />
    );
  }

  const fileTasks = (tasksQuery.data?.items ?? []).filter((task) => task.file_id === id);
  const detailQualityScore =
    file?.analysis && typeof file.analysis.quality_score === "number"
      ? clampScore(file.analysis.quality_score)
      : null;
  const detailSyncStatus = file ? syncStatus(file) : "not_synced";
  const displayTitle = file ? documentDisplayTitle(file) : null;

  return (
    <PageContainer
      title={displayTitle ?? "文件详情"}
      description="文件基础信息、AI 分析结果与审核同步状态。"
      breadcrumb={[
        {
          label: isAdmin ? "审核工作台" : "我的知识工作台",
          path: backPath,
        },
        { label: displayTitle ?? "加载中" },
      ]}
      actions={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(backPath)}>
            返回
          </Button>
          {!isAdmin ? (
            <Button
              type="primary"
              icon={<CloudUploadOutlined />}
              onClick={() => navigate("/upload")}
            >
              上传文件
            </Button>
          ) : null}
        </Space>
      }
    >
      {file ? (
        <div className="metric-grid file-detail-kpi-grid">
          <KpiCard
            icon={<FileProtectOutlined />}
            title="文件规格"
            value={file.extension.toUpperCase()}
            description={formatFileSize(file.size)}
            tone="primary"
          />
          <KpiCard
            icon={<SafetyOutlined />}
            title="分析质量"
            value={detailQualityScore === null ? "待评分" : `${detailQualityScore}%`}
            description={file.analysis ? "R5 质量评分" : "未生成分析"}
            tone={qualityTone(detailQualityScore)}
          />
          <KpiCard
            icon={<CloudSyncOutlined />}
            title="RAGFlow"
            value={syncMetricLabel(detailSyncStatus)}
            description={syncMetricDescription(file)}
            tone={syncMetricTone(detailSyncStatus)}
          />
        </div>
      ) : null}
      <div className="document-workspace document-workspace--detail">
        <div className="document-workspace__main">
          {file ? <OriginalDocumentCard key={file.id} file={file} /> : null}
          <Card className="document-panel" loading={fileQuery.isLoading}>
            {file ? (
              <Descriptions column={1} size="middle" styles={{ label: { width: 140 } }}>
                <Descriptions.Item label="原始文件名">{file.original_name}</Descriptions.Item>
                <Descriptions.Item label="文件状态">
                  <Space wrap>
                    <StatusTag kind="file" value={file.status} />
                    <StatusTag kind="review" value={file.review_status} />
                  </Space>
                </Descriptions.Item>
                <Descriptions.Item label="文件大小">{formatFileSize(file.size)}</Descriptions.Item>
                <Descriptions.Item label="MIME">{file.mime_type}</Descriptions.Item>
                <Descriptions.Item label="AI 分析">
                  {file.ai_analysis_enabled_at_upload ? "已启用" : "上传时跳过"}
                </Descriptions.Item>
                <Descriptions.Item label="过期指标">
                  <ExpiryIndicator expiresAt={file.expires_at} status={file.expiry_status} />
                </Descriptions.Item>
                <Descriptions.Item label="上传时间">
                  {dayjs(file.uploaded_at).format("YYYY-MM-DD HH:mm")}
                </Descriptions.Item>
                <Descriptions.Item label="说明">{file.description ?? "-"}</Descriptions.Item>
              </Descriptions>
            ) : null}
          </Card>

          {file?.analysis ? (
            <AnalysisCard analysis={file.analysis} file={file} loading={fileQuery.isLoading} />
          ) : null}
        </div>

        <aside className="document-workspace__side" aria-label="文件处理侧栏">
          {isAdmin && file ? (
            <ReviewActionCard
              file={file}
              userId={user?.id}
              now={now}
              mappings={datasetMappingsQuery.data?.items ?? []}
              mappingsLoading={datasetMappingsQuery.isLoading}
              mappingsError={datasetMappingsQuery.isError ? datasetMappingsQuery.error : null}
              onRetryMappings={() => {
                void datasetMappingsQuery.refetch();
              }}
              onRefresh={refreshDetail}
            />
          ) : null}

          <Card className="document-panel" title="分类与标签" loading={fileQuery.isLoading}>
            {file ? (
              <Descriptions column={1} size="middle" styles={{ label: { width: 140 } }}>
                <Descriptions.Item label="分类">{file.category_name ?? "-"}</Descriptions.Item>
                <Descriptions.Item label="标签">
                  {file.tags.length > 0 ? (
                    <Space wrap>
                      {file.tags.map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </Space>
                  ) : (
                    "暂无标签"
                  )}
                </Descriptions.Item>
              </Descriptions>
            ) : null}
          </Card>

          <Card className="document-panel" title="同步信息" loading={fileQuery.isLoading}>
            {file ? (
              <Space direction="vertical" size={12} className="document-result">
                {file.sync_error ? (
                  <Alert
                    type="error"
                    showIcon
                    message="同步失败原因"
                    description={file.sync_error}
                  />
                ) : null}
                <Typography.Text>
                  RAGFlow 文档：
                  <Typography.Text code>{file.ragflow_document_id ?? "-"}</Typography.Text>
                </Typography.Text>
                <Typography.Text>
                  解析状态：
                  <Typography.Text code>{file.ragflow_parse_status ?? "-"}</Typography.Text>
                </Typography.Text>
                <Typography.Text>
                  最近同步：
                  {file.last_sync_at ? dayjs(file.last_sync_at).format("YYYY-MM-DD HH:mm") : "-"}
                </Typography.Text>
              </Space>
            ) : null}
          </Card>

          {isAdmin ? (
            <TaskTimelineCard
              tasks={fileTasks}
              loading={tasksQuery.isLoading}
              error={tasksQuery.isError ? tasksQuery.error : null}
              onRetry={() => {
                void tasksQuery.refetch();
              }}
            />
          ) : null}
        </aside>
      </div>
    </PageContainer>
  );
}
