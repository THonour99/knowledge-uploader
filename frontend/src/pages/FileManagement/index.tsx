import {
  Alert,
  App as AntdApp,
  Avatar,
  Button,
  Card,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import type { MenuProps } from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileExcelOutlined,
  FileOutlined,
  FilePdfOutlined,
  FileProtectOutlined,
  FilePptOutlined,
  FileWordOutlined,
  DownOutlined,
  FilterOutlined,
  InboxOutlined,
  LockOutlined,
  UpOutlined,
  ReloadOutlined,
  StarOutlined,
  UnlockOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useMemo, useState } from "react";
import type { Key } from "react";
import type { ColumnsType } from "antd/es/table";
import type { FormInstance } from "antd/es/form";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  type DatasetMapping,
  type KnowledgeFile,
  type ReviewDecisionPayload,
  approveFile,
  archiveFile,
  claimReviewFile,
  deleteFile,
  getUploadPolicy,
  isApiError,
  listCategories,
  listDatasetMappings,
  listReviewFiles,
  listTags,
  rejectFile,
  releaseReviewClaim,
  updateFileClassification,
} from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { Roles, useAuthStore } from "../../store/auth.store";
import { allowedExtensionsFromPolicy } from "../../utils/uploadConfig";

// ── 常量 ──────────────────────────────────────────────────────────────────────

// ── 类型 ──────────────────────────────────────────────────────────────────────

interface ReviewFormValues {
  sync_decision?: "sync" | "approve_only";
  category_id?: string;
  dataset_mapping_id?: string;
  reason?: string;
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────

const REVIEW_QUEUES = [
  { value: "all", label: "全部待审" },
  { value: "unclaimed", label: "待领取" },
  { value: "mine", label: "我领取的" },
  { value: "due_soon", label: "临近 SLA" },
  { value: "overdue", label: "已超时" },
] as const;

const ARCHIVABLE_STATUSES = new Set<KnowledgeFile["status"]>([
  "approved",
  "parsed",
  "failed",
  "rejected",
  "analyzed",
  "pending_review",
]);

const DELETABLE_STATUSES = new Set<KnowledgeFile["status"]>([
  "uploaded",
  "pending_review",
  "approved",
  "rejected",
  "failed",
  "parsed",
  "analysis_failed",
  "analyzed",
  "sensitive_review_required",
  "disabled",
]);

function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function buildBulkApproveOnlyPayload(
  file: Pick<KnowledgeFile, "category_id">,
): ReviewDecisionPayload {
  return {
    sync_decision: "approve_only",
    category_id: file.category_id ?? null,
    dataset_mapping_id: null,
    reason: "批量审核通过",
  };
}

export function hasActiveReviewClaim(
  file: Pick<KnowledgeFile, "claimed_by" | "claimed_at" | "claim_expires_at">,
  userId: string | null | undefined,
  now = Date.now(),
): boolean {
  if (!userId || file.claimed_by !== userId || !file.claimed_at || !file.claim_expires_at) {
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

export function eligibleReviewTargets(files: KnowledgeFile[], userId: string | null | undefined) {
  return files.filter(
    (file) => file.status === "pending_review" && hasActiveReviewClaim(file, userId),
  );
}

function reviewClaimExpired(
  file: Pick<KnowledgeFile, "claimed_by" | "claim_expires_at">,
  now = Date.now(),
): boolean {
  if (!file.claimed_by || !file.claim_expires_at) {
    return false;
  }
  const expiresAt = Date.parse(file.claim_expires_at);
  return !Number.isFinite(expiresAt) || expiresAt <= now;
}

function buildMappingOptions(mappings: DatasetMapping[], categoryId?: string) {
  return mappings
    .filter((mapping) => mapping.enabled)
    .filter((mapping) => !categoryId || mapping.category_id === categoryId)
    .map((mapping) => ({
      label: `${mapping.name} / ${mapping.ragflow_dataset_name}`,
      value: mapping.id,
    }));
}

function riskLevel(file: KnowledgeFile): "none" | "low" | "medium" | "high" | "critical" {
  if (file.sensitive_risk_level) {
    return file.sensitive_risk_level;
  }
  if (file.status === "sensitive_review_required") {
    return "high";
  }
  if (file.review_status === "rejected" || file.status === "rejected") {
    return "medium";
  }
  return "low";
}

function uploaderText(file: KnowledgeFile): string {
  return file.uploader_name?.trim() || file.uploader_id.slice(0, 8);
}

function positiveInteger(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function reviewSla(file: KnowledgeFile): {
  label: string;
  detail: string;
  state: "normal" | "due_soon" | "overdue" | "unknown";
} {
  if (!file.review_due_at) {
    return { label: "未设置", detail: "等待 SLA 数据", state: "unknown" };
  }
  const dueAt = dayjs(file.review_due_at);
  const minutes = dueAt.diff(dayjs(), "minute");
  if (minutes <= 0) {
    return {
      label: `已超时 ${Math.max(1, Math.ceil(Math.abs(minutes) / 60))} 小时`,
      detail: dueAt.format("MM-DD HH:mm"),
      state: "overdue",
    };
  }
  if (minutes <= 4 * 60) {
    return {
      label: `剩余 ${Math.max(1, Math.ceil(minutes / 60))} 小时`,
      detail: dueAt.format("MM-DD HH:mm"),
      state: "due_soon",
    };
  }
  return {
    label: `剩余 ${Math.max(1, Math.ceil(minutes / 60))} 小时`,
    detail: dueAt.format("MM-DD HH:mm"),
    state: "normal",
  };
}

function fileTypeMeta(fileName: string) {
  const lowerName = fileName.toLowerCase();
  if (lowerName.endsWith(".pdf")) {
    return { icon: <FilePdfOutlined />, className: "file-title-cell__icon--pdf" };
  }
  if (lowerName.endsWith(".doc") || lowerName.endsWith(".docx")) {
    return { icon: <FileWordOutlined />, className: "file-title-cell__icon--word" };
  }
  if (lowerName.endsWith(".xls") || lowerName.endsWith(".xlsx")) {
    return { icon: <FileExcelOutlined />, className: "file-title-cell__icon--excel" };
  }
  if (lowerName.endsWith(".ppt") || lowerName.endsWith(".pptx")) {
    return { icon: <FilePptOutlined />, className: "file-title-cell__icon--ppt" };
  }
  return { icon: <FileOutlined />, className: "file-title-cell__icon--default" };
}

// ── 主页面 ────────────────────────────────────────────────────────────────────

export default function FileManagementPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const [approveForm] = Form.useForm<ReviewFormValues>();
  const [rejectForm] = Form.useForm<ReviewFormValues>();
  const [classificationForm] = Form.useForm<ReviewFormValues>();
  const [approvingFile, setApprovingFile] = useState<KnowledgeFile | null>(null);
  const [rejectingFile, setRejectingFile] = useState<KnowledgeFile | null>(null);
  const [classifyingFile, setClassifyingFile] = useState<KnowledgeFile | null>(null);
  const [forceReleasingFile, setForceReleasingFile] = useState<KnowledgeFile | null>(null);
  const [forceReleaseReason, setForceReleaseReason] = useState("");
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const page = positiveInteger(searchParams.get("page"), 1);
  const pageSize = Math.min(100, positiveInteger(searchParams.get("page_size"), 20));
  const serverSearch = searchParams.get("q")?.trim() ?? "";
  const queue = (searchParams.get("queue") ?? "all") as
    | "all"
    | "unclaimed"
    | "mine"
    | "due_soon"
    | "overdue";
  const [searchText, setSearchText] = useState(serverSearch);
  const [claimFeedback, setClaimFeedback] = useState<{
    fileId: string;
    message: string;
  } | null>(null);
  const [riskFilter, setRiskFilter] = useState("all");
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [bulkApproving, setBulkApproving] = useState(false);
  // 新增：服务端筛选参数
  const [extensionFilter, setExtensionFilter] = useState<string | undefined>(undefined);
  const [tagIdFilter, setTagIdFilter] = useState<string | undefined>(undefined);

  const updateCoreQuery = (key: string, value?: string | number) => {
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

  // ── 数据查询 ─────────────────────────────────────────────────────────────────

  const reviewFilesQuery = useQuery({
    queryKey: [
      "review-files",
      {
        page,
        pageSize,
        q: serverSearch,
        queue,
        extension: extensionFilter,
        tag_id: tagIdFilter,
        risk: riskFilter,
      },
    ],
    queryFn: () =>
      listReviewFiles({
        page,
        page_size: pageSize,
        q: serverSearch || undefined,
        queue: queue === "all" ? undefined : queue,
        extension: extensionFilter,
        tag_id: tagIdFilter,
        sensitive_risk_level:
          riskFilter === "all"
            ? undefined
            : (riskFilter as "none" | "low" | "medium" | "high" | "critical"),
      }),
    placeholderData: (previous) => previous,
  });
  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: listCategories,
  });
  const datasetsQuery = useQuery({
    queryKey: ["dataset-mappings"],
    queryFn: listDatasetMappings,
  });
  const tagsQuery = useQuery({
    queryKey: ["tags"],
    queryFn: () => listTags({ enabled: true, page_size: 200 }),
  });
  const uploadPolicyQuery = useQuery({
    queryKey: ["upload-policy"],
    queryFn: getUploadPolicy,
  });

  const categories = categoriesQuery.data?.items ?? [];
  const datasets = datasetsQuery.data?.items ?? [];
  const files = reviewFilesQuery.data?.items ?? [];
  const tags = tagsQuery.data?.items ?? [];
  const allowedExtensions = useMemo(
    () => allowedExtensionsFromPolicy(uploadPolicyQuery.data),
    [uploadPolicyQuery.data],
  );
  const extensionOptions = useMemo(
    () => [
      { label: "文件类型：全部", value: "all" },
      ...allowedExtensions.map((ext) => ({ label: `.${ext}`, value: ext })),
    ],
    [allowedExtensions],
  );
  const categoryIdForApprove = Form.useWatch("category_id", approveForm);
  const syncDecisionForApprove = Form.useWatch("sync_decision", approveForm);
  const categoryIdForClassification = Form.useWatch("category_id", classificationForm);

  const categoryNameById = useMemo(
    () => new Map(categories.map((category) => [category.id, category.name])),
    [categories],
  );
  const mappingById = useMemo(
    () => new Map(datasets.map((mapping) => [mapping.id, mapping])),
    [datasets],
  );

  const categoryOptions = categories.map((category) => ({
    label: category.name,
    value: category.id,
  }));
  const tagOptions = useMemo(
    () => [
      { label: "标签：全部", value: "all" },
      ...tags.map((tag) => ({ label: tag.name, value: tag.id })),
    ],
    [tags],
  );
  const approveDatasetOptions = buildMappingOptions(datasets, categoryIdForApprove);
  const classificationDatasetOptions = buildMappingOptions(datasets, categoryIdForClassification);

  const selectedKeySet = useMemo(
    () => new Set(selectedRowKeys.map((key) => String(key))),
    [selectedRowKeys],
  );
  const selectedFiles = files.filter((file) => selectedKeySet.has(file.id));
  const canDecideFile = (file: KnowledgeFile) => hasActiveReviewClaim(file, user?.id);
  const pendingReviewCount = files.filter((file) => file.status === "pending_review").length;
  const highRiskCount = files.filter((file) =>
    ["high", "critical"].includes(riskLevel(file)),
  ).length;
  const unclaimedCount = files.filter((file) => !file.claimed_by).length;
  const mineCount = files.filter(canDecideFile).length;
  const selectedPendingCount = selectedFiles.filter(
    (file) => file.status === "pending_review" && canDecideFile(file),
  ).length;
  const selectedRatio =
    files.length > 0 ? Math.round((selectedFiles.length / files.length) * 100) : 0;
  const nextUnclaimedFile = files.find((file) => !file.claimed_by);
  const dueSoonCount = files.filter((file) => reviewSla(file).state === "due_soon").length;
  const overdueCount = files.filter((file) => reviewSla(file).state === "overdue").length;

  // ── 刷新辅助 ─────────────────────────────────────────────────────────────────

  const refreshFiles = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["review-files"] }),
      queryClient.invalidateQueries({ queryKey: ["documents"] }),
    ]);
  };

  // ── mutations ────────────────────────────────────────────────────────────────

  const claimMutation = useMutation({
    mutationFn: claimReviewFile,
    onSuccess: async (file) => {
      setClaimFeedback(null);
      message.success(`已领取 ${file.original_name}`);
      await refreshFiles();
    },
    onError: async (error: Error, fileId) => {
      const conflict = isApiError(error) && error.status === 409;
      setClaimFeedback({
        fileId,
        message: conflict ? "该任务刚刚被他人领取，队列已刷新" : error.message,
      });
      if (conflict) {
        message.warning("领取冲突：该任务已被他人处理");
        await refreshFiles();
      } else {
        message.error(error.message || "领取失败");
      }
    },
  });

  const releaseClaimMutation = useMutation({
    mutationFn: ({ fileId, reason }: { fileId: string; reason?: string }) =>
      reason ? releaseReviewClaim(fileId, reason) : releaseReviewClaim(fileId),
    onSuccess: async (file) => {
      setClaimFeedback(null);
      setForceReleasingFile(null);
      setForceReleaseReason("");
      message.success(`已释放 ${file.original_name}`);
      await refreshFiles();
    },
    onError: (error: Error, variables) => {
      setClaimFeedback({ fileId: variables.fileId, message: error.message || "释放失败" });
      message.error(error.message || "释放失败");
    },
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, values }: { id: string; values: ReviewFormValues }) =>
      approveFile(id, {
        sync_decision: values.sync_decision ?? "approve_only",
        category_id: values.category_id ?? null,
        dataset_mapping_id: values.dataset_mapping_id ?? null,
        reason: values.reason?.trim() || null,
      }),
    onSuccess: async (_file, variables) => {
      message.success(
        variables.values.sync_decision === "sync"
          ? "文件已批准并进入同步队列"
          : "文件已批准，本次不进入知识库",
      );
      setApprovingFile(null);
      approveForm.resetFields();
      await refreshFiles();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("审核任务状态已变化，已为你刷新队列");
        setApprovingFile(null);
        await refreshFiles();
        return;
      }
      message.error(error.message);
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => rejectFile(id, reason),
    onSuccess: async () => {
      message.success("文件已拒绝");
      setRejectingFile(null);
      rejectForm.resetFields();
      await refreshFiles();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("审核任务状态已变化，已为你刷新队列");
        setRejectingFile(null);
        await refreshFiles();
        return;
      }
      message.error(error.message);
    },
  });

  const classificationMutation = useMutation({
    mutationFn: ({ id, values }: { id: string; values: ReviewFormValues }) =>
      updateFileClassification(id, {
        category_id: values.category_id ?? null,
        dataset_mapping_id: values.dataset_mapping_id ?? null,
      }),
    onSuccess: async () => {
      message.success("审核分类草案已保存");
      setClassifyingFile(null);
      classificationForm.resetFields();
      await refreshFiles();
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        message.warning("领取状态已变化，审核草案未保存，队列已刷新");
        setClassifyingFile(null);
        await refreshFiles();
        return;
      }
      message.error(error.message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteFile(id),
    onSuccess: async () => {
      message.success("文件已删除");
      await refreshFiles();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => archiveFile(id),
    onSuccess: async () => {
      message.success("文件已归档");
      await refreshFiles();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  // ── Modal 开关 ────────────────────────────────────────────────────────────────

  const openApproveModal = (file: KnowledgeFile) => {
    setApprovingFile(file);
    approveForm.setFieldsValue({
      sync_decision: undefined,
      category_id: file.category_id ?? undefined,
      dataset_mapping_id: file.dataset_mapping_id ?? undefined,
      reason: "",
    });
  };

  const openRejectModal = (file: KnowledgeFile) => {
    setRejectingFile(file);
    rejectForm.setFieldsValue({ reason: "" });
  };

  const openClassificationModal = (file: KnowledgeFile) => {
    setClassifyingFile(file);
    classificationForm.setFieldsValue({
      category_id: file.category_id ?? undefined,
      dataset_mapping_id: file.dataset_mapping_id ?? undefined,
    });
  };

  const syncCategoryFromMapping = (form: FormInstance<ReviewFormValues>, mappingId?: string) => {
    const mapping = mappingId ? mappingById.get(mappingId) : undefined;
    if (mapping) {
      form.setFieldValue("category_id", mapping.category_id);
    }
  };

  // ── 重置筛选 ──────────────────────────────────────────────────────────────────

  const resetFilters = () => {
    setSearchText("");
    setRiskFilter("all");
    setExtensionFilter(undefined);
    setTagIdFilter(undefined);
    setSearchParams(new URLSearchParams(), { replace: true });
  };

  const handleBulkApprove = async () => {
    const targets = eligibleReviewTargets(selectedFiles, user?.id);

    if (targets.length === 0) {
      message.warning("已选文件中没有可批量审核项");
      return;
    }

    setBulkApproving(true);
    try {
      const results = await Promise.allSettled(
        targets.map((file) => approveFile(file.id, buildBulkApproveOnlyPayload(file))),
      );
      const failedCount = results.filter((result) => result.status === "rejected").length;
      const successCount = targets.length - failedCount;

      if (failedCount > 0) {
        message.warning(`批量审核完成，成功 ${successCount} 项，失败 ${failedCount} 项`);
      } else {
        message.success(`已批量审核 ${successCount} 个文件`);
      }

      setSelectedRowKeys([]);
      await refreshFiles();
    } finally {
      setBulkApproving(false);
    }
  };

  // ── 表格列定义 ────────────────────────────────────────────────────────────────

  const columns: ColumnsType<KnowledgeFile> = [
    {
      title: "文件名称",
      dataIndex: "original_name",
      key: "original_name",
      width: 188,
      ellipsis: true,
      render: (value: string, record) => {
        const meta = fileTypeMeta(value);

        return (
          <div className="file-title-cell">
            <span className={`file-title-cell__icon ${meta.className}`}>{meta.icon}</span>
            <span className="file-title-cell__content">
              <button
                type="button"
                className="file-title-cell__name file-title-cell__link"
                title={value}
                onClick={() => navigate(`/files/${record.id}#original`)}
                aria-label={`查看原件与审核详情 ${value}`}
              >
                {value}
              </button>
              <span className="file-title-cell__meta">
                <Typography.Text type="secondary">{record.mime_type}</Typography.Text>
                <StarOutlined className="file-title-cell__star" />
              </span>
            </span>
          </div>
        );
      },
    },
    {
      title: "上传人",
      dataIndex: "uploader_id",
      key: "uploader_id",
      width: 96,
      render: (_, record) => {
        const name = uploaderText(record);
        return (
          <span className="uploader-cell">
            <Avatar size={24}>{name.slice(0, 1).toUpperCase()}</Avatar>
            <span className="single-line-text" title={name}>
              {name}
            </span>
          </span>
        );
      },
    },
    {
      title: "部门",
      dataIndex: "department",
      key: "department",
      width: 88,
      render: (value: string | null) => value ?? "-",
    },
    {
      title: "分类",
      dataIndex: "category_id",
      key: "category_id",
      width: 104,
      ellipsis: true,
      render: (value: string | null) => (
        <span
          className="single-line-text"
          title={value ? (categoryNameById.get(value) ?? "未知分类") : "未分类"}
        >
          {value ? (categoryNameById.get(value) ?? "未知分类") : "未分类"}
        </span>
      ),
    },
    {
      title: "文件大小",
      dataIndex: "size",
      key: "size",
      width: 88,
      render: (value: number) => formatFileSize(value),
    },
    {
      title: "审核状态",
      dataIndex: "review_status",
      key: "review_status",
      width: 104,
      render: (value: string, record) => (
        <StatusTag kind="review" value={record.status === "pending_review" ? "pending" : value} />
      ),
    },
    {
      title: "敏感风险",
      key: "risk",
      width: 104,
      render: (_, record) => <StatusTag kind="risk" value={riskLevel(record)} />,
    },
    {
      title: "SLA / 等待",
      dataIndex: "review_due_at",
      key: "review_due_at",
      width: 140,
      render: (_value: string | null, record) => {
        const sla = reviewSla(record);
        return (
          <span className={`review-sla review-sla--${sla.state}`}>
            <span>
              <ClockCircleOutlined /> {sla.label}
            </span>
            <small>{sla.detail}</small>
          </span>
        );
      },
    },
    {
      title: "领取人",
      dataIndex: "claimed_by",
      key: "claimed_by",
      width: 120,
      render: (_value: string | null, record) =>
        record.claimed_by ? (
          <Space direction="vertical" size={1}>
            <Typography.Text>
              {record.claimed_by === user?.id ? "我" : record.claimed_by_name || "其他审核人"}
            </Typography.Text>
            <Typography.Text type="secondary">
              {record.claimed_at ? dayjs(record.claimed_at).format("MM-DD HH:mm") : "已领取"}
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="secondary">待领取</Typography.Text>
        ),
    },
    {
      title: "操作",
      key: "actions",
      width: 250,
      fixed: "right" as const,
      render: (_, record) => {
        const canDecide = record.status === "pending_review" && canDecideFile(record);
        const canClaim =
          record.status === "pending_review" && (!record.claimed_by || reviewClaimExpired(record));
        const claimedByMe = record.claimed_by === user?.id;
        const canForceRelease =
          user?.role === Roles.SYSTEM_ADMIN && Boolean(record.claimed_by) && !claimedByMe;

        const canArchive =
          ARCHIVABLE_STATUSES.has(record.status) &&
          (record.status !== "pending_review" || canDecide);
        const canDelete =
          DELETABLE_STATUSES.has(record.status) &&
          (record.status !== "pending_review" || canDecide);
        const moreItems: MenuProps["items"] = [];

        if (canDecide) {
          moreItems.push({
            key: "classify",
            label: "编辑审核草案",
            onClick: () => openClassificationModal(record),
          });
        }
        if (canArchive) {
          moreItems.push({
            key: "archive",
            icon: <InboxOutlined />,
            label: "归档",
            onClick: () => archiveMutation.mutate(record.id),
          });
        }
        if (canDelete) {
          if (moreItems.length > 0) {
            moreItems.push({ type: "divider" });
          }
          moreItems.push({
            key: "delete",
            icon: <DeleteOutlined />,
            label: "删除",
            danger: true,
            onClick: () => deleteMutation.mutate(record.id),
          });
        }

        return (
          <Space direction="vertical" size={2}>
            <Space size={4} wrap>
              <Button
                type="link"
                size="small"
                onClick={() => navigate(`/files/${record.id}#original`)}
              >
                查看原件
              </Button>
              {canClaim ? (
                <Button
                  type="link"
                  size="small"
                  icon={<LockOutlined />}
                  loading={claimMutation.isPending && claimMutation.variables === record.id}
                  onClick={() => claimMutation.mutate(record.id)}
                >
                  {record.claimed_by ? "重新领取" : "领取"}
                </Button>
              ) : null}
              {canDecide ? (
                <>
                  <Button
                    type="link"
                    size="small"
                    className="table-link-button"
                    onClick={() => openApproveModal(record)}
                  >
                    审核
                  </Button>
                  <Button
                    type="link"
                    danger
                    size="small"
                    className="table-link-button"
                    onClick={() => openRejectModal(record)}
                  >
                    驳回
                  </Button>
                </>
              ) : null}
              {claimedByMe ? (
                <Button
                  type="link"
                  size="small"
                  icon={<UnlockOutlined />}
                  loading={
                    releaseClaimMutation.isPending &&
                    releaseClaimMutation.variables?.fileId === record.id
                  }
                  onClick={() => releaseClaimMutation.mutate({ fileId: record.id })}
                >
                  释放
                </Button>
              ) : null}
              {canForceRelease ? (
                <Button
                  type="link"
                  danger
                  size="small"
                  icon={<UnlockOutlined />}
                  onClick={() => {
                    setForceReleasingFile(record);
                    setForceReleaseReason("");
                  }}
                >
                  强制释放
                </Button>
              ) : null}
              {moreItems.length > 0 ? (
                <Dropdown menu={{ items: moreItems }} trigger={["click"]}>
                  <Button type="text" size="small" aria-label="更多操作">
                    ···
                  </Button>
                </Dropdown>
              ) : null}
            </Space>
            {claimFeedback?.fileId === record.id ? (
              <Typography.Text type="danger" className="review-row-feedback">
                {claimFeedback.message}
              </Typography.Text>
            ) : null}
          </Space>
        );
      },
    },
  ];

  // ── 渲染 ──────────────────────────────────────────────────────────────────────

  return (
    <PageContainer
      title="部门审核工作台"
      description="先领取任务，再查看原件、风险与元数据并作出明确入库决定。"
      actions={
        <Button
          type="primary"
          icon={<LockOutlined />}
          disabled={!nextUnclaimedFile}
          loading={claimMutation.isPending && claimMutation.variables === nextUnclaimedFile?.id}
          onClick={() => {
            if (nextUnclaimedFile) {
              claimMutation.mutate(nextUnclaimedFile.id);
            } else {
              updateCoreQuery("queue", "unclaimed");
            }
          }}
        >
          领取下一份
        </Button>
      }
    >
      <nav className="review-queue-tabs" aria-label="审核队列" role="tablist">
        {REVIEW_QUEUES.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={queue === item.value}
            className={
              queue === item.value
                ? "review-queue-tab review-queue-tab--active"
                : "review-queue-tab"
            }
            onClick={() => updateCoreQuery("queue", item.value)}
          >
            <span>{item.label}</span>
            {queue === item.value ? <strong>{reviewFilesQuery.data?.total ?? 0}</strong> : null}
          </button>
        ))}
      </nav>

      <Card className="document-panel table-card">
        {reviewFilesQuery.isError ? (
          <Alert
            className="review-queue-error"
            type="error"
            showIcon
            message="审核队列加载失败"
            description={reviewFilesQuery.error.message}
            action={
              <Button size="small" onClick={() => void reviewFilesQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : null}
        {/* ── 筛选栏 ── */}
        <div className="filter-toolbar filter-toolbar--management">
          <Input.Search
            className="filter-toolbar__search"
            placeholder="搜索文件名称、关键词"
            value={searchText}
            onChange={(event) => {
              setSearchText(event.target.value);
              if (!event.target.value) {
                updateCoreQuery("q");
              }
            }}
            onSearch={(value) => updateCoreQuery("q", value.trim())}
            enterButton="搜索"
            allowClear
          />
          <Button
            type="text"
            icon={filtersExpanded ? <UpOutlined /> : <DownOutlined />}
            onClick={() => setFiltersExpanded((prev) => !prev)}
            aria-label="更多筛选"
          >
            更多筛选
          </Button>
          {filtersExpanded ? (
            <>
              <Select
                className="filter-toolbar__control"
                value={riskFilter}
                options={[
                  { label: "风险等级：全部", value: "all" },
                  { label: "低风险", value: "low" },
                  { label: "中风险", value: "medium" },
                  { label: "高风险", value: "high" },
                  { label: "严重风险", value: "critical" },
                ]}
                onChange={setRiskFilter}
              />
              <Select
                className="filter-toolbar__control"
                value={extensionFilter ?? "all"}
                options={extensionOptions}
                onChange={(value) => setExtensionFilter(value === "all" ? undefined : value)}
                placeholder="文件类型：全部"
              />
              <Select
                className="filter-toolbar__control"
                value={tagIdFilter ?? "all"}
                options={tagOptions}
                onChange={(value) => setTagIdFilter(value === "all" ? undefined : value)}
                loading={tagsQuery.isLoading}
                placeholder="标签：全部"
              />
            </>
          ) : null}
        </div>

        <div className="review-command-strip" role="region" aria-label="审核队列摘要">
          <div className="review-command-strip__main">
            <span className="review-command-strip__icon">
              <FileProtectOutlined />
            </span>
            <span className="review-command-strip__copy">
              <span className="review-command-strip__title-row">
                <Typography.Text strong className="review-command-strip__title">
                  审核队列
                </Typography.Text>
                <StatusTag
                  kind="review"
                  value={pendingReviewCount > 0 ? "pending" : "approved"}
                  variant="dot"
                />
              </span>
              <Typography.Text type="secondary">
                默认按超时、风险和最早提交排序；领取冲突会在原行反馈并自动刷新。
              </Typography.Text>
            </span>
          </div>
          <div className="review-command-strip__stats" aria-label="当前筛选摘要">
            <span className="review-command-strip__stat review-command-strip__stat--warning">
              <Typography.Text type="secondary">当前页待领取</Typography.Text>
              <strong>{unclaimedCount}项</strong>
            </span>
            <span className="review-command-strip__stat review-command-strip__stat--info">
              <Typography.Text type="secondary">当前页我领取</Typography.Text>
              <strong>{mineCount}项</strong>
            </span>
            <span className="review-command-strip__stat review-command-strip__stat--warning">
              <Typography.Text type="secondary">当前页临近 SLA</Typography.Text>
              <strong>{dueSoonCount}项</strong>
            </span>
            <span className="review-command-strip__stat review-command-strip__stat--danger">
              <Typography.Text type="secondary">当前页已超时</Typography.Text>
              <strong>{overdueCount}项</strong>
            </span>
            <span className="review-command-strip__stat">
              <Typography.Text type="secondary">当前页高风险</Typography.Text>
              <strong>{highRiskCount}项</strong>
            </span>
            <span className="review-command-strip__stat">
              <Typography.Text type="secondary">选中可决定</Typography.Text>
              <strong>{selectedPendingCount}项</strong>
            </span>
          </div>
          <div className="review-command-strip__action-panel">
            <div className="review-command-strip__selection" aria-label="选择范围">
              <span className="review-command-strip__selection-copy">
                <Typography.Text type="secondary">选中范围</Typography.Text>
                <strong>
                  {selectedFiles.length}/{files.length}
                </strong>
              </span>
              <Progress percent={selectedRatio} size="small" showInfo={false} />
            </div>
            <Space wrap className="review-command-strip__actions">
              <Button size="small" onClick={() => updateCoreQuery("queue", "overdue")}>
                只看超时
              </Button>
              <Button
                size="small"
                disabled={selectedFiles.length === 0}
                onClick={() => setSelectedRowKeys([])}
              >
                清空选择
              </Button>
            </Space>
          </div>
        </div>

        {/* ── 表格工具栏 ── */}
        <div className="table-actions">
          <Button icon={<FilterOutlined />} onClick={resetFilters}>
            重置筛选
          </Button>
          <Space wrap className="table-actions__right">
            <Popconfirm
              title="批量仅批准"
              description={`将 ${selectedPendingCount} 个待审核文件标记为“仅批准不入库”，确认继续？`}
              onConfirm={() => void handleBulkApprove()}
              okText="确定"
              cancelText="取消"
            >
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                disabled={selectedPendingCount === 0}
                loading={bulkApproving}
              >
                批量仅批准
              </Button>
            </Popconfirm>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void reviewFilesQuery.refetch()}
              loading={reviewFilesQuery.isFetching}
            />
          </Space>
        </div>

        <Table<KnowledgeFile>
          className="file-management-table"
          rowKey="id"
          columns={columns}
          dataSource={files}
          loading={reviewFilesQuery.isLoading}
          pagination={{
            current: page,
            pageSize,
            total: reviewFilesQuery.data?.total ?? 0,
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
          locale={{ emptyText: "暂无文件" }}
          tableLayout="fixed"
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
          }}
          scroll={{ x: 1420 }}
        />
      </Card>

      {/* ── 审核通过 Modal ── */}
      <Modal
        rootClassName="review-decision-modal"
        title="审核通过"
        open={Boolean(approvingFile)}
        onCancel={() => setApprovingFile(null)}
        onOk={() => approveForm.submit()}
        confirmLoading={approveMutation.isPending}
        width={620}
        okText="确认批准"
      >
        {approvingFile ? (
          <Button
            className="review-original-link"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/files/${approvingFile.id}#original`)}
          >
            先查看原件、AI 分析与处理时间线
          </Button>
        ) : null}
        {approvingFile?.sensitive_risk_level === "critical" ? (
          <Alert
            className="review-risk-alert"
            type="error"
            showIcon
            message="严重风险文档禁止同步"
            description="可以仅批准留存，但不能选择进入 RAGFlow。"
          />
        ) : approvingFile?.sensitive_risk_level === "high" ? (
          <Alert
            className="review-risk-alert"
            type="warning"
            showIcon
            message="高风险文档需要明确说明"
            description="若选择同步，审核说明将作为风险确认写入审计。"
          />
        ) : null}
        <Form<ReviewFormValues>
          form={approveForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => {
            if (approvingFile) {
              approveMutation.mutate({ id: approvingFile.id, values });
            }
          }}
        >
          <Form.Item
            label="批准后的处理"
            name="sync_decision"
            rules={[{ required: true, message: "请选择批准后是否同步到 RAGFlow" }]}
          >
            <Radio.Group
              onChange={(event) => {
                if (event.target.value === "approve_only") {
                  approveForm.setFieldValue("dataset_mapping_id", undefined);
                }
              }}
            >
              <Space direction="vertical">
                <Radio value="sync" disabled={approvingFile?.sensitive_risk_level === "critical"}>
                  批准并同步到 RAGFlow（必须选择 Dataset）
                </Radio>
                <Radio value="approve_only">仅批准，不进入知识库</Radio>
              </Space>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="分类" name="category_id">
            <Select
              allowClear
              options={categoryOptions}
              loading={categoriesQuery.isLoading}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item
            label="Dataset 映射"
            name="dataset_mapping_id"
            rules={[
              {
                validator: async (_, value: string | undefined) => {
                  if (syncDecisionForApprove === "sync" && !value) {
                    throw new Error("批准并同步时必须选择 Dataset");
                  }
                },
              },
            ]}
          >
            <Select
              allowClear
              disabled={syncDecisionForApprove !== "sync"}
              options={approveDatasetOptions}
              loading={datasetsQuery.isLoading}
              showSearch
              optionFilterProp="label"
              onChange={(value) => syncCategoryFromMapping(approveForm, value)}
            />
          </Form.Item>
          <Form.Item
            label="审核说明"
            name="reason"
            extra="选填；高风险文档同步时必须填写风险确认说明。"
            rules={[
              {
                validator: async (_, value: string | undefined) => {
                  const requiresReason =
                    syncDecisionForApprove === "sync" &&
                    approvingFile?.sensitive_risk_level === "high";
                  if (requiresReason && !value?.trim()) {
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

      {/* ── 拒绝文件 Modal ── */}
      <Modal
        rootClassName="review-decision-modal"
        title="拒绝文件"
        open={Boolean(rejectingFile)}
        onCancel={() => setRejectingFile(null)}
        onOk={() => rejectForm.submit()}
        confirmLoading={rejectMutation.isPending}
        width={560}
        okText="确认驳回"
        okButtonProps={{ danger: true }}
      >
        <Form<ReviewFormValues>
          form={rejectForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => {
            if (rejectingFile) {
              rejectMutation.mutate({
                id: rejectingFile.id,
                reason: values.reason?.trim() ?? "",
              });
            }
          }}
        >
          <Form.Item
            label="拒绝原因"
            name="reason"
            rules={[{ required: true, message: "请输入拒绝原因" }]}
          >
            <Input.TextArea rows={4} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="强制释放审核任务"
        open={Boolean(forceReleasingFile)}
        okText="确认强制释放"
        okButtonProps={{ danger: true }}
        confirmLoading={releaseClaimMutation.isPending}
        onCancel={() => {
          setForceReleasingFile(null);
          setForceReleaseReason("");
        }}
        onOk={() => {
          const reason = forceReleaseReason.trim();
          if (!reason) {
            message.warning("请输入强制释放原因");
            return;
          }
          if (forceReleasingFile) {
            releaseClaimMutation.mutate({ fileId: forceReleasingFile.id, reason });
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          此操作不会直接授予审核权限。释放后仍需由你重新领取，才能批准或驳回。
        </Typography.Paragraph>
        <label htmlFor="force-release-reason">强制释放原因</label>
        <Input.TextArea
          id="force-release-reason"
          value={forceReleaseReason}
          onChange={(event) => setForceReleaseReason(event.target.value)}
          rows={4}
          maxLength={500}
          showCount
        />
      </Modal>

      {/* ── 编辑审核分类草案 Modal ── */}
      <Modal
        title="编辑审核分类草案"
        open={Boolean(classifyingFile)}
        onCancel={() => setClassifyingFile(null)}
        onOk={() => classificationForm.submit()}
        confirmLoading={classificationMutation.isPending}
        width={620}
      >
        <Alert
          className="review-risk-alert"
          type="info"
          showIcon
          message="这里保存的是审核草案"
          description="此处选择不代表文件已批准或已进入知识库。最终 Dataset 必须在“审核通过”时随同步决定再次确认。"
        />
        <Form<ReviewFormValues>
          form={classificationForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => {
            if (!classifyingFile) {
              return;
            }
            if (!hasActiveReviewClaim(classifyingFile, user?.id)) {
              message.warning("领取已失效，请刷新队列并重新领取后再编辑审核草案");
              setClassifyingFile(null);
              void refreshFiles();
              return;
            }
            classificationMutation.mutate({ id: classifyingFile.id, values });
          }}
        >
          <Form.Item label="审核草案分类" name="category_id">
            <Select
              allowClear
              options={categoryOptions}
              loading={categoriesQuery.isLoading}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item label="审核草案 Dataset" name="dataset_mapping_id">
            <Select
              allowClear
              options={classificationDatasetOptions}
              loading={datasetsQuery.isLoading}
              showSearch
              optionFilterProp="label"
              onChange={(value) => syncCategoryFromMapping(classificationForm, value)}
            />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
