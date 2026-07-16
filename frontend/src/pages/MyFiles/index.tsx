import { useDeferredValue, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import {
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  SearchOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import type { ColumnsType } from "antd/es/table";

import {
  type KnowledgeFile,
  getUserFacingErrorMessage,
  isApiError,
  deleteFile,
  getDocumentContent,
  getUploadPolicy,
  listDocuments,
  listTags,
  submitFileForReview,
} from "../../api/client";
import {
  type DashboardRecentDocument,
  type EmployeeStatusCounts,
  getEmployeeDashboard,
} from "../../api/dashboard";
import { DepartmentAssignmentAlert } from "../../components/DepartmentAssignmentAlert";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { hasAssignedDepartment, useAuthStore } from "../../store/auth.store";
import { allowUserDeleteFromPolicy, allowedExtensionsFromPolicy } from "../../utils/uploadConfig";

const STATUS_RAIL: Array<{
  key: keyof EmployeeStatusCounts;
  filterStatus?: string;
  label: string;
  hint: string;
  danger: boolean;
}> = [
  {
    key: "draft",
    label: "草稿",
    hint: "上传或分析完成；聚合项请用下方筛选",
    danger: false,
  },
  {
    key: "ai_processing",
    label: "AI 处理中",
    hint: "提取与分析进行中；聚合项请用下方筛选",
    danger: false,
  },
  {
    key: "analysis_failed",
    filterStatus: "analysis_failed",
    label: "分析失败",
    hint: "提交受系统策略控制",
    danger: true,
  },
  {
    key: "sensitive_review",
    filterStatus: "sensitive_review_required",
    label: "风险待确认",
    hint: "确认风险后提交",
    danger: true,
  },
  {
    key: "pending_review",
    filterStatus: "pending_review",
    label: "待审核",
    hint: "管理员处理中",
    danger: false,
  },
  {
    key: "approved",
    filterStatus: "approved",
    label: "已批准·未入库",
    hint: "审核决定不入库",
    danger: false,
  },
  {
    key: "sync_processing",
    label: "入库处理中",
    hint: "排队、上传或解析；聚合项请用下方筛选",
    danger: false,
  },
  {
    key: "parsed",
    filterStatus: "parsed",
    label: "已入库",
    hint: "可供下游检索",
    danger: false,
  },
  {
    key: "sync_failed",
    label: "入库失败",
    hint: "含同步与清理失败；聚合项请用下方筛选",
    danger: true,
  },
  {
    key: "rejected",
    filterStatus: "rejected",
    label: "已驳回",
    hint: "修改后重新提交",
    danger: true,
  },
  {
    key: "archived",
    filterStatus: "disabled",
    label: "已归档",
    hint: "不再参与当前流程",
    danger: false,
  },
];

const STATUS_FILTERS = [
  { value: "uploaded", label: "草稿" },
  { value: "analyzed", label: "分析完成" },
  { value: "analysis_failed", label: "分析失败" },
  { value: "sensitive_review_required", label: "风险待确认" },
  { value: "pending_review", label: "待审核" },
  { value: "approved", label: "已批准·未入库" },
  { value: "queued", label: "入库排队" },
  { value: "syncing", label: "RAGFlow 上传中" },
  { value: "uploaded_to_ragflow", label: "等待解析" },
  { value: "parsing", label: "解析中" },
  { value: "parsed", label: "已入库" },
  { value: "failed", label: "入库失败" },
  { value: "rejected", label: "已驳回" },
] as const;

const SUBMITTABLE_STATUSES = new Set([
  "uploaded",
  "analyzed",
  "analysis_failed",
  "sensitive_review_required",
  "rejected",
]);

const USER_DELETABLE_STATUSES = new Set([
  "uploaded",
  "approved",
  "rejected",
  "failed",
  "parsed",
  "analysis_failed",
  "analyzed",
  "sensitive_review_required",
  "disabled",
]);

const ANALYSIS_FAILED_SUBMISSION_DISABLED_CODE = "ANALYSIS_FAILED_SUBMISSION_DISABLED";
const SENSITIVE_RISK_ACKNOWLEDGEMENT_REQUIRED_CODE = "SENSITIVE_RISK_ACKNOWLEDGEMENT_REQUIRED";

interface SubmitReviewVariables {
  file: KnowledgeFile;
  acknowledgeSensitiveRisk?: boolean;
}

interface SubmitRecovery {
  fileId: string;
  fileName: string;
}

function positiveInteger(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
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

export function downloadBlob(blob: Blob, fileName: string): void {
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = fileName;
    document.body.appendChild(anchor);
    try {
      anchor.click();
    } finally {
      anchor.remove();
    }
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }
}

function nextStep(file: Pick<KnowledgeFile, "status">): string {
  const labels: Record<string, string> = {
    uploaded: "补充信息后提交审核",
    analyzed: "确认分析结果并提交",
    analysis_failed: "尝试提交；若策略限制请联系管理员重试分析",
    sensitive_review_required: "确认敏感风险后提交",
    pending_review: "等待部门管理员审核",
    rejected: "查看驳回原因，修改后重提",
    approved: "已批准，尚未进入知识库",
    queued: "等待同步到知识库",
    syncing: "正在同步到知识库",
    uploaded_to_ragflow: "等待 RAGFlow 解析",
    parsing: "RAGFlow 正在解析",
    parsed: "已进入知识库",
    failed: "同步失败，请联系管理员",
  };
  return labels[file.status] ?? "查看详情";
}

function isActionable(file: KnowledgeFile): boolean {
  return SUBMITTABLE_STATUSES.has(file.status);
}

function requiresSensitiveRiskAcknowledgement(file: KnowledgeFile): boolean {
  return (
    file.status === "sensitive_review_required" ||
    Boolean(file.sensitive_risk_level && file.sensitive_risk_level !== "none")
  );
}

export default function MyFilesPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const departmentBlocked = !hasAssignedDepartment(user);
  const [sensitiveSubmittingFile, setSensitiveSubmittingFile] = useState<KnowledgeFile | null>(
    null,
  );
  const [submitRecovery, setSubmitRecovery] = useState<SubmitRecovery | null>(null);

  const page = positiveInteger(searchParams.get("page"), 1);
  const pageSize = Math.min(100, positiveInteger(searchParams.get("page_size"), 20));
  const q = searchParams.get("q")?.trim() ?? "";
  const deferredQ = useDeferredValue(q);
  const status = searchParams.get("status") ?? "all";
  const extension = searchParams.get("extension") ?? "all";
  const tagId = searchParams.get("tag_id") ?? "all";

  const setQueryValue = (key: string, value?: string | number) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value === undefined || value === "" || value === "all") {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
        if (key !== "page") {
          next.set("page", "1");
        }
        return next;
      },
      { replace: true },
    );
  };

  const filesQuery = useQuery({
    queryKey: [
      "documents",
      "mine",
      {
        page,
        pageSize,
        q: deferredQ,
        status,
        extension,
        tagId,
      },
    ],
    queryFn: () =>
      listDocuments({
        page,
        page_size: pageSize,
        q: deferredQ || undefined,
        status: status === "all" ? undefined : status,
        extension: extension === "all" ? undefined : extension,
        tag_id: tagId === "all" ? undefined : tagId,
        sort: "updated_at",
        order: "desc",
      }),
    placeholderData: (previous) => previous,
  });

  const tagsQuery = useQuery({
    queryKey: ["tags", "list", "enabled"],
    queryFn: () => listTags({ enabled: true, page_size: 100 }),
  });
  const uploadPolicyQuery = useQuery({
    queryKey: ["upload-policy"],
    queryFn: getUploadPolicy,
  });
  const dashboardQuery = useQuery({
    queryKey: ["dashboard", "employee"],
    queryFn: () => getEmployeeDashboard(),
    staleTime: 30_000,
  });

  const refreshFiles = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["documents", "mine"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard", "employee"] }),
    ]);
  const deleteMutation = useMutation({
    mutationFn: deleteFile,
    onSuccess: async () => {
      await refreshFiles();
      message.success("文件已删除");
    },
    onError: (error: Error) => message.error(error.message || "删除失败"),
  });
  const submitMutation = useMutation({
    mutationFn: ({ file, acknowledgeSensitiveRisk }: SubmitReviewVariables) =>
      submitFileForReview(
        file.id,
        acknowledgeSensitiveRisk ? { acknowledge_sensitive_risk: true } : undefined,
      ),
    onSuccess: async (_file, variables) => {
      if (submitRecovery?.fileId === variables.file.id) {
        setSubmitRecovery(null);
      }
      setSensitiveSubmittingFile(null);
      await refreshFiles();
      message.success("已提交审核");
    },
    onError: (error: unknown, variables) => {
      if (isApiError(error) && error.code === ANALYSIS_FAILED_SUBMISSION_DISABLED_CODE) {
        setSubmitRecovery({ fileId: variables.file.id, fileName: variables.file.original_name });
        message.warning("当前策略禁止跳过失败的 AI 分析");
        return;
      }
      if (isApiError(error) && error.code === SENSITIVE_RISK_ACKNOWLEDGEMENT_REQUIRED_CODE) {
        setSensitiveSubmittingFile(variables.file);
        message.warning("请先确认敏感风险，再提交审核");
        return;
      }
      message.error(getUserFacingErrorMessage(error, "提交审核失败"));
    },
  });
  const downloadMutation = useMutation({
    mutationFn: async (file: KnowledgeFile) => {
      const content = await getDocumentContent(file.id, "attachment");
      downloadBlob(content.blob, file.original_name);
    },
    onError: (error: Error) => message.error(error.message || "原件下载失败"),
  });

  const employeeWorkbench = dashboardQuery.data?.employee ?? null;
  const statusCounts = employeeWorkbench?.status_counts;
  const actionCount = employeeWorkbench?.action_counts.total ?? 0;
  const continueFiles: DashboardRecentDocument[] =
    employeeWorkbench?.recent_documents.filter((file) =>
      ["submit_review", "revise_rejected", "confirm_sensitive"].includes(file.next_action) ||
      file.status === "analysis_failed",
    ) ?? [];

  const allowedExtensions = allowedExtensionsFromPolicy(uploadPolicyQuery.data);
  const tagOptions = (tagsQuery.data?.items ?? []).map((tag) => ({
    label: tag.name,
    value: tag.id,
  }));
  const allowUserDelete = allowUserDeleteFromPolicy(uploadPolicyQuery.data);
  const uploadPolicyReady = uploadPolicyQuery.isSuccess && uploadPolicyQuery.data !== undefined;
  const files = filesQuery.data?.items ?? [];
  const total = filesQuery.data?.total ?? 0;

  const requestReviewSubmission = (file: KnowledgeFile) => {
    setSubmitRecovery(null);
    if (requiresSensitiveRiskAcknowledgement(file)) {
      setSensitiveSubmittingFile(file);
      return;
    }
    submitMutation.mutate({ file });
  };

  const fileActions = (file: KnowledgeFile) => (
    <Space size={4} wrap>
      <Button
        type="text"
        icon={<EyeOutlined />}
        onClick={() => navigate(`/files/${file.id}#original`)}
        aria-label={`预览原件 ${file.original_name}`}
      >
        预览
      </Button>
      <Button
        type="text"
        icon={<DownloadOutlined />}
        loading={downloadMutation.isPending && downloadMutation.variables?.id === file.id}
        onClick={() => downloadMutation.mutate(file)}
        aria-label={`下载原件 ${file.original_name}`}
      >
        下载
      </Button>
      {isActionable(file) ? (
        <Button
          type="text"
          icon={<SendOutlined />}
          loading={submitMutation.isPending && submitMutation.variables?.file.id === file.id}
          disabled={departmentBlocked}
          onClick={() => requestReviewSubmission(file)}
          aria-label={`提交审核 ${file.original_name}`}
        >
          提交
        </Button>
      ) : null}
      {allowUserDelete && USER_DELETABLE_STATUSES.has(file.status) ? (
        <Popconfirm
          title="删除文件"
          description="仅允许删除策略放行且非运行态的文件。确认继续？"
          okText="删除"
          cancelText="取消"
          onConfirm={() => deleteMutation.mutate(file.id)}
        >
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            aria-label={`删除 ${file.original_name}`}
          />
        </Popconfirm>
      ) : null}
    </Space>
  );

  const columns: ColumnsType<KnowledgeFile> = [
    {
      title: "文件",
      dataIndex: "original_name",
      key: "original_name",
      render: (value: string, file) => (
        <Space direction="vertical" size={2}>
          <Link to={`/files/${file.id}`}>{value}</Link>
          <Typography.Text type="secondary">
            {formatFileSize(file.size)} · {file.extension.toUpperCase()}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "当前状态与下一步",
      dataIndex: "status",
      key: "status",
      width: 250,
      render: (_value: string, file) => (
        <Space direction="vertical" size={4}>
          <StatusTag kind="file" value={file.status} />
          <Typography.Text type="secondary">{nextStep(file)}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 170,
      render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      width: 280,
      render: (_value, file) => fileActions(file),
    },
  ];

  return (
    <PageContainer
      title={user?.name ? `${user.name}的知识工作台` : "我的知识工作台"}
      description="先处理草稿和驳回，再跟踪审核与入库结果。"
      actions={
        <Button
          type="primary"
          icon={<CloudUploadOutlined />}
          disabled={
            departmentBlocked ||
            !uploadPolicyReady ||
            uploadPolicyQuery.data?.upload_enabled !== true
          }
          onClick={() => navigate("/upload")}
        >
          上传文档
        </Button>
      }
    >
      {departmentBlocked ? <DepartmentAssignmentAlert className="workbench-gate-alert" /> : null}
      {uploadPolicyQuery.isError ? (
        <Alert
          className="workbench-gate-alert"
          type="error"
          showIcon
          message="上传与删除策略加载失败"
          description="上传入口和用户删除操作已安全暂停；文档浏览、下载与审核状态不受影响。"
          action={
            <Button size="small" onClick={() => void uploadPolicyQuery.refetch()}>
              重试策略
            </Button>
          }
        />
      ) : null}
      {submitRecovery ? (
        <Alert
          className="workbench-gate-alert"
          type="warning"
          showIcon
          closable
          onClose={() => setSubmitRecovery(null)}
          message={`“${submitRecovery.fileName}”暂不能提交`}
          description="系统策略禁止在 AI 分析失败后直接进入审核。请联系部门管理员重新发起分析或检查 AI 配置，分析完成后再提交。"
          action={
            <Button size="small" onClick={() => navigate(`/files/${submitRecovery.fileId}`)}>
              查看文档与处理建议
            </Button>
          }
        />
      ) : null}

      <section className="status-rail" aria-label="文档状态轨道">
        {STATUS_RAIL.map((item, index) => {
          const className = [
            "status-rail__item",
            item.danger ? "status-rail__item--danger" : "",
            item.filterStatus && status === item.filterStatus ? "status-rail__item--active" : "",
          ]
            .filter(Boolean)
            .join(" ");
          const content = (
            <>
              <span className="status-rail__step">{index + 1}</span>
              <span className="status-rail__copy">
                <strong>{item.label}</strong>
                <small>{item.hint}</small>
              </span>
              <span className="status-rail__count" aria-live="polite">
                {dashboardQuery.isPending
                  ? "…"
                  : dashboardQuery.isError
                    ? "—"
                    : (statusCounts?.[item.key] ?? 0)}
              </span>
            </>
          );
          if (!item.filterStatus) {
            return (
              <article
                className={`${className} status-rail__item--aggregate`}
                key={item.key}
                aria-label={`${item.label}（聚合状态）`}
              >
                {content}
              </article>
            );
          }
          return (
            <button
              className={className}
              key={item.key}
              type="button"
              aria-pressed={status === item.filterStatus}
              onClick={() => setQueryValue("status", item.filterStatus)}
            >
              {content}
            </button>
          );
        })}
      </section>

      <section className="continue-section" aria-labelledby="continue-title">
        <div className="workbench-section-heading">
          <div>
            <Typography.Title level={4} id="continue-title">
              继续处理
            </Typography.Title>
            <Typography.Text type="secondary">只列出你现在可以采取行动的文档</Typography.Text>
          </div>
        </div>
        {dashboardQuery.isError ? (
          <Alert
            className="workbench-gate-alert"
            type="error"
            showIcon
            message="待办汇总加载失败"
            description="当前无法确认全部待处理文档，请重试后再判断是否存在待办。"
            action={
              <Button size="small" onClick={() => void dashboardQuery.refetch()}>
                重试待办汇总
              </Button>
            }
          />
        ) : null}
        {continueFiles.length > 0 ? (
          <div className="continue-list">
            {continueFiles.map((file) => (
              <article className="continue-list__item" key={file.id}>
                <StatusTag kind="file" value={file.status} />
                <span className="continue-list__copy">
                  <Link to={`/files/${file.id}`}>{file.original_name}</Link>
                  <Typography.Text type="secondary">{nextStep(file)}</Typography.Text>
                </span>
                <Button type="link" onClick={() => navigate(`/files/${file.id}`)}>
                  {file.status === "rejected" ? "修改并重提" : "查看并处理"}
                </Button>
              </article>
            ))}
          </div>
        ) : dashboardQuery.isError ? null : dashboardQuery.isPending ? (
          <Typography.Text type="secondary" role="status">
            正在加载待办汇总…
          </Typography.Text>
        ) : actionCount > 0 ? (
          <Alert
            type="info"
            showIcon
            message={`还有 ${actionCount} 个待处理文档`}
            description="最近五条动态未包含这些文档，请在下方按状态筛选后继续处理。"
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有需要继续处理的文档" />
        )}
      </section>

      <Card className="document-panel table-card recent-files-panel">
        <div className="workbench-section-heading">
          <div>
            <Typography.Title level={4}>最近文档</Typography.Title>
            <Typography.Text type="secondary">
              服务端共 {total} 条，筛选条件会保留在地址栏
            </Typography.Text>
          </div>
        </div>

        <div className="filter-toolbar workbench-filter-toolbar">
          <Input.Search
            key={q}
            className="filter-toolbar__search"
            prefix={<SearchOutlined />}
            defaultValue={q}
            placeholder="搜索文件名或说明"
            allowClear
            enterButton="搜索"
            onSearch={(value) => setQueryValue("q", value.trim())}
            onChange={(event) => {
              if (!event.target.value) {
                setQueryValue("q");
              }
            }}
          />
          <Select
            className="filter-toolbar__control"
            placeholder="文档状态"
            value={status}
            onChange={(value) => setQueryValue("status", value)}
            options={[
              { label: "全部状态", value: "all" },
              ...STATUS_FILTERS,
            ]}
          />
          <Select
            className="filter-toolbar__control"
            placeholder="文件类型（扩展名）"
            value={extension}
            onChange={(value) => setQueryValue("extension", value)}
            options={[
              { label: "全部类型", value: "all" },
              ...allowedExtensions.map((item) => ({
                label: `.${item}`,
                value: item,
              })),
            ]}
          />
          <Select
            className="filter-toolbar__control"
            placeholder="标签筛选"
            value={tagId}
            loading={tagsQuery.isLoading}
            onChange={(value) => setQueryValue("tag_id", value)}
            options={[{ label: "全部标签", value: "all" }, ...tagOptions]}
          />
        </div>

        {filesQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message="文档列表加载失败"
            action={
              <Button size="small" onClick={() => void filesQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : null}

        <div className="recent-files-table">
          <Table<KnowledgeFile>
            rowKey="id"
            columns={columns}
            dataSource={files}
            loading={filesQuery.isLoading}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: (value) => `共 ${value} 条`,
              onChange: (nextPage, nextPageSize) => {
                setSearchParams(
                  (previous) => {
                    const next = new URLSearchParams(previous);
                    next.set("page", String(nextPage));
                    next.set("page_size", String(nextPageSize));
                    return next;
                  },
                  { replace: true },
                );
              },
            }}
            locale={{ emptyText: "暂无符合条件的文档" }}
            scroll={{ x: 900 }}
          />
        </div>

        <div className="recent-files-mobile" aria-label="移动端文档列表">
          {files.map((file) => (
            <article className="mobile-file-row" key={file.id}>
              <div className="mobile-file-row__heading">
                <Link to={`/files/${file.id}`}>{file.original_name}</Link>
                <StatusTag kind="file" value={file.status} />
              </div>
              <Typography.Text type="secondary">{nextStep(file)}</Typography.Text>
              <Typography.Text type="secondary">
                {formatFileSize(file.size)} · {dayjs(file.updated_at).format("MM-DD HH:mm")}
              </Typography.Text>
              {fileActions(file)}
            </article>
          ))}
          {files.length === 0 && !filesQuery.isLoading ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无符合条件的文档" />
          ) : null}
          {total > pageSize ? (
            <div className="mobile-pagination">
              <Button
                disabled={page <= 1}
                onClick={() => setQueryValue("page", Math.max(1, page - 1))}
              >
                上一页
              </Button>
              <Typography.Text>
                第 {page} / {Math.max(1, Math.ceil(total / pageSize))} 页
              </Typography.Text>
              <Button
                disabled={page >= Math.ceil(total / pageSize)}
                onClick={() => setQueryValue("page", page + 1)}
              >
                下一页
              </Button>
            </div>
          ) : null}
        </div>
      </Card>

      <Modal
        title="确认提交敏感风险文档"
        open={Boolean(sensitiveSubmittingFile)}
        okText="我已知悉风险，提交审核"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={submitMutation.isPending}
        onCancel={() => setSensitiveSubmittingFile(null)}
        onOk={() => {
          if (sensitiveSubmittingFile) {
            submitMutation.mutate({
              file: sensitiveSubmittingFile,
              acknowledgeSensitiveRisk: true,
            });
          }
        }}
      >
        <Alert
          type="warning"
          showIcon
          message="此文档触发了敏感内容规则"
          description="提交表示你已了解该风险并同意交由部门管理员复核；这不会自动批准文档，也不会自动同步到 RAGFlow。"
        />
        <Typography.Paragraph className="review-risk-alert" type="secondary">
          待提交文件：{sensitiveSubmittingFile?.original_name}
        </Typography.Paragraph>
      </Modal>
    </PageContainer>
  );
}
