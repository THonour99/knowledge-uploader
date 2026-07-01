import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Progress,
  Result,
  Space,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  CloudSyncOutlined,
  CloudUploadOutlined,
  FileProtectOutlined,
  SafetyOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate, useParams } from "react-router-dom";

import {
  type FileAnalysis,
  type FileAnalysisTable,
  type KnowledgeFile,
  type SimilarFileReference,
  type SyncTask,
  getDocument,
  listTasks,
} from "../../api/client";
import { KpiCard, type KpiTone } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { Roles, useAuthStore } from "../../store/auth.store";

const TASK_TYPE_LABELS: Record<string, string> = {
  ragflow_upload: "RAGFlow 上传",
  ragflow_parse: "RAGFlow 解析",
  ragflow_status_check: "RAGFlow 状态检查",
};

const REVIEW_STATUS_LABELS: Record<string, string> = {
  pending: "待审核",
  in_review: "审核中",
  approved: "已通过",
  rejected: "未通过",
};

const RISK_LEVEL_LABELS: Record<string, string> = {
  none: "无风险",
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  critical: "严重风险",
};

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

function reviewStatusLabel(status: string): string {
  return REVIEW_STATUS_LABELS[status] ?? status;
}

function riskLevelLabel(level: string | null | undefined): string {
  if (!level) {
    return "暂无风险结果";
  }
  return RISK_LEVEL_LABELS[level] ?? level;
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

interface TaskTimelineCardProps {
  tasks: SyncTask[];
  loading: boolean;
}

function TaskTimelineCard({ tasks, loading }: TaskTimelineCardProps) {
  return (
    <Card className="document-panel" title="处理日志" loading={loading}>
      {tasks.length === 0 ? (
        <Empty description="暂无任务记录" />
      ) : (
        <Timeline
          items={tasks.map((task) => ({
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
                {task.error_message ? (
                  <Typography.Text type="danger">{task.error_message}</Typography.Text>
                ) : null}
              </Space>
            ),
          }))}
        />
      )}
    </Card>
  );
}

export default function FileDetailPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const role = useAuthStore((state) => state.user?.role ?? null);
  const isAdmin = role === Roles.DEPT_ADMIN || role === Roles.SYSTEM_ADMIN;
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

  if (!id) {
    return <Result status="404" title="文件不存在" />;
  }

  if (fileQuery.isError) {
    return (
      <Result
        status="404"
        title="文件不存在"
        extra={
          <Button type="primary" onClick={() => navigate("/my-files")}>
            返回我的文件
          </Button>
        }
      />
    );
  }

  const file = fileQuery.data;
  const fileTasks = (tasksQuery.data?.items ?? []).filter((task) => task.file_id === id);
  const detailQualityScore =
    file?.analysis && typeof file.analysis.quality_score === "number"
      ? clampScore(file.analysis.quality_score)
      : null;
  const detailSyncStatus = file ? syncStatus(file) : "not_synced";
  const detailAnalysisHealth =
    file?.analysis?.status === "failed" ? "error" : file?.analysis ? "ok" : "unknown";
  const detailMetadataHealth =
    file && (file.category_name || file.tags.length > 0) ? "ok" : "unknown";
  const detailRiskLevel = file?.analysis?.sensitive_risk_level ?? "none";

  return (
    <PageContainer
      title={file?.original_name ?? "文件详情"}
      description="文件基础信息、AI 分析结果与审核同步状态。"
      breadcrumb={[
        { label: "文件审核", path: "/files" },
        { label: file?.original_name ?? "加载中" },
      ]}
      actions={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/my-files")}>
            返回
          </Button>
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => navigate("/upload")}>
            上传文件
          </Button>
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
            icon={<TagsOutlined />}
            title="标签数量"
            value={file.tags.length}
            description={file.category_name ? "分类已设置" : "未分类"}
            tone="info"
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

      {file ? (
        <section className="document-status-strip" aria-label="文档运行状态">
          <div className="document-status-strip__main">
            <span className="document-status-strip__icon">
              <FileProtectOutlined />
            </span>
            <span className="document-status-strip__copy">
              <Typography.Text type="secondary">文档治理</Typography.Text>
              <Typography.Title level={4} className="document-status-strip__title">
                文档运行状态
              </Typography.Title>
              <Typography.Text type="secondary">
                {file.extension.toUpperCase()} · {formatFileSize(file.size)} ·{" "}
                {dayjs(file.uploaded_at).format("YYYY-MM-DD HH:mm")}
              </Typography.Text>
            </span>
            <StatusTag kind="file" value={file.status} variant="dot" />
          </div>

          <div className="document-status-strip__lanes">
            <div className="document-status-lane">
              <span className="document-status-lane__icon">
                <FileProtectOutlined />
              </span>
              <span className="document-status-lane__body">
                <span className="document-status-lane__topline">
                  <Typography.Text type="secondary">文件状态</Typography.Text>
                  <StatusTag kind="file" value={file.status} variant="dot" />
                </span>
                <strong>{file.original_name}</strong>
                <Typography.Text type="secondary">
                  审核：{reviewStatusLabel(file.review_status)}
                </Typography.Text>
              </span>
            </div>

            <div className="document-status-lane">
              <span className="document-status-lane__icon document-status-lane__icon--sync">
                <CloudSyncOutlined />
              </span>
              <span className="document-status-lane__body">
                <span className="document-status-lane__topline">
                  <Typography.Text type="secondary">同步健康</Typography.Text>
                  <StatusTag kind="sync" value={detailSyncStatus} variant="dot" />
                </span>
                <strong>{syncMetricLabel(detailSyncStatus)}</strong>
                <Typography.Text type="secondary">{syncMetricDescription(file)}</Typography.Text>
              </span>
            </div>

            <div className="document-status-lane">
              <span className="document-status-lane__icon document-status-lane__icon--risk">
                <SafetyOutlined />
              </span>
              <span className="document-status-lane__body">
                <span className="document-status-lane__topline">
                  <Typography.Text type="secondary">AI 治理</Typography.Text>
                  <StatusTag kind="health" value={detailAnalysisHealth} variant="dot" />
                </span>
                <strong>{file.analysis ? qualityLevel(detailQualityScore) : "未生成分析"}</strong>
                <Typography.Text type="secondary">
                  风险：{riskLevelLabel(detailRiskLevel)}
                </Typography.Text>
              </span>
            </div>

            <div className="document-status-lane">
              <span className="document-status-lane__icon document-status-lane__icon--meta">
                <TagsOutlined />
              </span>
              <span className="document-status-lane__body">
                <span className="document-status-lane__topline">
                  <Typography.Text type="secondary">元数据</Typography.Text>
                  <StatusTag kind="health" value={detailMetadataHealth} variant="dot" />
                </span>
                <strong>{file.category_name ?? "未分类"}</strong>
                <Typography.Text type="secondary">{file.tags.length} 个标签</Typography.Text>
              </span>
            </div>
          </div>
        </section>
      ) : null}
      <div className="document-workspace document-workspace--detail">
        <div className="document-workspace__main">
          <Card className="document-panel" loading={fileQuery.isLoading}>
            {file ? (
              <Descriptions column={1} size="middle" styles={{ label: { width: 140 } }}>
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

          {isAdmin ? <TaskTimelineCard tasks={fileTasks} loading={tasksQuery.isLoading} /> : null}
        </aside>
      </div>
    </PageContainer>
  );
}
