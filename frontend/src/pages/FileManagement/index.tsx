import {
  Alert,
  App as AntdApp,
  Avatar,
  Button,
  Card,
  Checkbox,
  Dropdown,
  Form,
  Input,
  Pagination,
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
  EyeOutlined,
  FileExcelOutlined,
  FileOutlined,
  FilePdfOutlined,
  FileProtectOutlined,
  FilePptOutlined,
  FileWordOutlined,
  DownOutlined,
  FilterOutlined,
  LockOutlined,
  UpOutlined,
  ReloadOutlined,
  UnlockOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Key } from "react";
import type { ColumnsType } from "antd/es/table";
import type { FormInstance } from "antd/es/form";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  type DatasetMapping,
  type KnowledgeFile,
  type ReviewDecisionPayload,
  approveFile,
  claimReviewFile,
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
import { SavedViewManager } from "../../components/SavedViewManager";
import { StatusTag } from "../../components/StatusTag";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useNow } from "../../hooks/useNow";
import { PageContainer } from "../../layouts/PageContainer";
import {
  SessionBoundModal as Modal,
  SessionBoundPopconfirm as Popconfirm,
} from "../../components/SessionBoundActions";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import {
  captureAuthSessionIdentity,
  isCurrentAuthSessionIdentity,
  isSessionSupersededError,
  runAuthSessionCallback,
} from "../../sessionIdentity";
import { Roles, useAuthStore } from "../../store/auth.store";
import { documentDisplayTitle, originalFileNameLabel } from "../../utils/documentTitle";
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
const REVIEW_SORT_OPTIONS = [
  { value: "default", label: "智能优先级" },
  { value: "submitted_at", label: "提交时间" },
  { value: "review_due_at", label: "SLA 截止" },
  { value: "uploaded_at", label: "上传时间" },
  { value: "original_name", label: "文件名" },
  { value: "risk", label: "风险等级" },
];
const REVIEW_SORT_VALUES = new Set<string>(REVIEW_SORT_OPTIONS.map((option) => option.value));

type ReviewQueue = (typeof REVIEW_QUEUES)[number]["value"];
type RiskFilter = "all" | "none" | "low" | "medium" | "high" | "critical";

const REVIEW_QUEUE_VALUES = new Set<string>(REVIEW_QUEUES.map((queue) => queue.value));
const RISK_FILTER_VALUES = new Set<string>(["all", "none", "low", "medium", "high", "critical"]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EXTENSION_PATTERN = /^[a-z0-9][a-z0-9+._-]{0,15}$/i;

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
    reason: "批量审核通过",
  };
}

export function buildReviewDecisionPayload(values: ReviewFormValues): ReviewDecisionPayload {
  const syncDecision = values.sync_decision ?? "approve_only";
  const payload: ReviewDecisionPayload = {
    sync_decision: syncDecision,
    category_id: values.category_id ?? null,
    reason: values.reason?.trim() || null,
  };
  if (syncDecision === "sync" && values.dataset_mapping_id) {
    payload.dataset_mapping_id = values.dataset_mapping_id;
  }
  return payload;
}

export function hasActiveReviewClaim(
  file: Pick<KnowledgeFile, "claimed_by" | "claimed_at" | "claim_expires_at">,
  userId: string | null | undefined,
  now = Date.now(),
): boolean {
  return Boolean(userId && file.claimed_by === userId && hasValidReviewClaim(file, now));
}

export function hasValidReviewClaim(
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

export function eligibleReviewTargets(
  files: KnowledgeFile[],
  userId: string | null | undefined,
  now = Date.now(),
) {
  return files.filter(
    (file) => file.status === "pending_review" && hasActiveReviewClaim(file, userId, now),
  );
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

function riskLevel(
  file: KnowledgeFile,
): "unknown" | "none" | "low" | "medium" | "high" | "critical" {
  return file.sensitive_risk_level ?? "unknown";
}

function isUuid(value: string | null): value is string {
  return Boolean(value && UUID_PATTERN.test(value));
}

function uploaderText(file: KnowledgeFile): string {
  return file.uploader_name?.trim() || file.uploader_id.slice(0, 8);
}

function positiveInteger(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function reviewSla(
  file: KnowledgeFile,
  now = Date.now(),
): {
  label: string;
  detail: string;
  state: "normal" | "due_soon" | "overdue" | "unknown";
} {
  if (!file.review_due_at) {
    return { label: "未设置", detail: "等待 SLA 数据", state: "unknown" };
  }
  const dueAt = dayjs(file.review_due_at);
  if (!dueAt.isValid()) {
    return { label: "数据异常", detail: "SLA 时间不可用", state: "unknown" };
  }
  const remainingMs = dueAt.valueOf() - now;
  if (remainingMs <= 0) {
    const overdueMs = Math.abs(remainingMs);
    const overdueLabel =
      overdueMs < 60 * 60 * 1_000
        ? `${Math.max(1, Math.ceil(overdueMs / 60_000))} 分钟`
        : `${Math.ceil(overdueMs / (60 * 60 * 1_000))} 小时`;
    return {
      label: `已超时 ${overdueLabel}`,
      detail: dueAt.format("MM-DD HH:mm"),
      state: "overdue",
    };
  }
  const remainingMinutes = Math.max(1, Math.ceil(remainingMs / 60_000));
  const remainingLabel =
    remainingMinutes < 60 ? `${remainingMinutes} 分钟` : `${Math.ceil(remainingMinutes / 60)} 小时`;
  if (remainingMs <= 4 * 60 * 60 * 1_000) {
    return {
      label: `剩余 ${remainingLabel}`,
      detail: dueAt.format("MM-DD HH:mm"),
      state: "due_soon",
    };
  }
  return {
    label: `剩余 ${remainingLabel}`,
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
  const now = useNow();
  const isMobile = useMediaQuery("(max-width: 768px)");
  const refreshedExpiredClaims = useRef(new Set<string>());
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
  const rawQueue = searchParams.get("queue");
  const queue: ReviewQueue = REVIEW_QUEUE_VALUES.has(rawQueue ?? "all")
    ? ((rawQueue as ReviewQueue | null) ?? "all")
    : "all";
  const rawRiskFilter = searchParams.get("risk");
  const riskFilter: RiskFilter = RISK_FILTER_VALUES.has(rawRiskFilter ?? "all")
    ? ((rawRiskFilter as RiskFilter | null) ?? "all")
    : "all";
  const rawExtensionFilter = searchParams.get("extension")?.trim() ?? "";
  const extensionFilter = EXTENSION_PATTERN.test(rawExtensionFilter)
    ? rawExtensionFilter.toLowerCase()
    : undefined;
  const rawTagIdFilter = searchParams.get("tag_id");
  const tagIdFilter = isUuid(rawTagIdFilter) ? rawTagIdFilter : undefined;
  const rawDepartmentIdFilter = searchParams.get("department_id");
  const departmentIdFilter = isUuid(rawDepartmentIdFilter) ? rawDepartmentIdFilter : undefined;
  const rawSort = searchParams.get("sort");
  const sort = REVIEW_SORT_VALUES.has(rawSort ?? "default") ? (rawSort ?? "default") : "default";
  const rawOrder = searchParams.get("order");
  const order = rawOrder === "desc" ? "desc" : "asc";
  const savedViewDefinition: Record<string, unknown> = {
    order,
    page_size: pageSize,
    ...(serverSearch ? { q: serverSearch } : {}),
    ...(queue !== "all" ? { queue } : {}),
    ...(extensionFilter ? { extension: extensionFilter } : {}),
    ...(tagIdFilter ? { tag_id: tagIdFilter } : {}),
    ...(departmentIdFilter ? { department_id: departmentIdFilter } : {}),
    ...(riskFilter !== "all" ? { sensitive_risk_level: riskFilter } : {}),
    ...(sort !== "default" ? { sort } : {}),
  };
  const [searchText, setSearchText] = useState(serverSearch);
  const [claimFeedback, setClaimFeedback] = useState<{
    fileId: string;
    message: string;
  } | null>(null);
  const [filtersExpanded, setFiltersExpanded] = useState(
    riskFilter !== "all" ||
      sort !== "default" ||
      order !== "asc" ||
      Boolean(extensionFilter || tagIdFilter || departmentIdFilter),
  );
  const [bulkApproving, setBulkApproving] = useState(false);

  useEffect(() => {
    setSearchText(serverSearch);
  }, [serverSearch]);

  useEffect(() => {
    if (
      riskFilter !== "all" ||
      sort !== "default" ||
      order !== "asc" ||
      extensionFilter ||
      tagIdFilter ||
      departmentIdFilter
    ) {
      setFiltersExpanded(true);
    }
  }, [departmentIdFilter, extensionFilter, order, riskFilter, sort, tagIdFilter]);

  const serializedSearchParams = searchParams.toString();
  useEffect(() => {
    const next = new URLSearchParams(serializedSearchParams);
    let changed = false;
    if (rawQueue && !REVIEW_QUEUE_VALUES.has(rawQueue)) {
      next.delete("queue");
      changed = true;
    }
    if (rawRiskFilter && !RISK_FILTER_VALUES.has(rawRiskFilter)) {
      next.delete("risk");
      changed = true;
    }
    if (rawExtensionFilter && !EXTENSION_PATTERN.test(rawExtensionFilter)) {
      next.delete("extension");
      changed = true;
    } else if (rawExtensionFilter && rawExtensionFilter !== extensionFilter) {
      next.set("extension", extensionFilter ?? "");
      changed = true;
    }
    if (rawTagIdFilter && !isUuid(rawTagIdFilter)) {
      next.delete("tag_id");
      changed = true;
    }
    if (rawDepartmentIdFilter && !isUuid(rawDepartmentIdFilter)) {
      next.delete("department_id");
      changed = true;
    }
    if (rawSort && !REVIEW_SORT_VALUES.has(rawSort)) {
      next.delete("sort");
      changed = true;
    }
    if (rawOrder && rawOrder !== "asc" && rawOrder !== "desc") {
      next.delete("order");
      changed = true;
    }
    if (changed) {
      next.set("page", "1");
      setSearchParams(next, { replace: true });
    }
  }, [
    extensionFilter,
    rawDepartmentIdFilter,
    rawExtensionFilter,
    rawOrder,
    rawQueue,
    rawRiskFilter,
    rawSort,
    rawTagIdFilter,
    serializedSearchParams,
    setSearchParams,
  ]);

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
  const applySavedView = (definition: Record<string, unknown>) => {
    const next = new URLSearchParams();
    const stringFields = ["q", "extension", "tag_id", "department_id"] as const;
    for (const field of stringFields) {
      const value = definition[field];
      if (typeof value === "string" && value) {
        next.set(field, value);
      }
    }
    if (typeof definition.queue === "string" && REVIEW_QUEUE_VALUES.has(definition.queue)) {
      next.set("queue", definition.queue);
    }
    if (
      typeof definition.sensitive_risk_level === "string" &&
      RISK_FILTER_VALUES.has(definition.sensitive_risk_level)
    ) {
      next.set("risk", definition.sensitive_risk_level);
    }
    if (typeof definition.sort === "string" && REVIEW_SORT_VALUES.has(definition.sort)) {
      next.set("sort", definition.sort);
    }
    if (definition.order === "asc" || definition.order === "desc") {
      next.set("order", definition.order);
    }
    if (
      typeof definition.page_size === "number" &&
      Number.isInteger(definition.page_size) &&
      definition.page_size >= 1 &&
      definition.page_size <= 100
    ) {
      next.set("page_size", String(definition.page_size));
    }
    next.set("page", "1");
    setSearchParams(next, { replace: true });
  };

  const reviewFilesQuery = useQuery({
    queryKey: [
      "review-files",
      {
        page,
        pageSize,
        q: serverSearch,
        queue,
        extension: extensionFilter,
        department_id: departmentIdFilter,
        sort,
        order,
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
        department_id: departmentIdFilter,
        sort: sort === "default" ? undefined : sort,
        order,
        tag_id: tagIdFilter,
        sensitive_risk_level:
          riskFilter === "all"
            ? undefined
            : (riskFilter as "none" | "low" | "medium" | "high" | "critical"),
      }),
    placeholderData: (previous) => previous,
  });

  useEffect(() => {
    const response = reviewFilesQuery.data;
    if (!response || reviewFilesQuery.isFetching || reviewFilesQuery.isPlaceholderData) {
      return;
    }
    const responseLastPage = response.total_pages ?? Math.ceil(response.total / pageSize);
    const lastPage = Math.max(1, responseLastPage);
    if (page <= lastPage) {
      return;
    }
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("page", String(lastPage));
        return next;
      },
      { replace: true },
    );
  }, [
    page,
    pageSize,
    reviewFilesQuery.data,
    reviewFilesQuery.isFetching,
    reviewFilesQuery.isPlaceholderData,
    setSearchParams,
  ]);
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
  const savedViewDepartmentOptions = useMemo(() => {
    const labels = new Map<string, string>();
    if (user?.department_id) {
      labels.set(user.department_id, user.department_name?.trim() || "账号所属部门");
    }
    for (const file of files) {
      if (file.department_id) {
        labels.set(
          file.department_id,
          file.department_name?.trim() || file.department?.trim() || "授权部门",
        );
      }
    }
    if (departmentIdFilter && !labels.has(departmentIdFilter)) {
      labels.set(departmentIdFilter, `部门 ${departmentIdFilter.slice(0, 8)}`);
    }
    return [...labels].map(([value, label]) => ({ value, label }));
  }, [departmentIdFilter, files, user?.department_id, user?.department_name]);
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
  const canDecideFile = (file: KnowledgeFile) => hasActiveReviewClaim(file, user?.id, now);
  const pendingReviewCount = files.filter((file) => file.status === "pending_review").length;
  const highRiskCount = files.filter((file) =>
    ["high", "critical"].includes(riskLevel(file)),
  ).length;
  const unclaimedCount = files.filter((file) => !hasValidReviewClaim(file, now)).length;
  const mineCount = files.filter(canDecideFile).length;
  const selectedPendingCount = selectedFiles.filter(
    (file) => file.status === "pending_review" && canDecideFile(file),
  ).length;
  const selectedRatio =
    files.length > 0 ? Math.round((selectedFiles.length / files.length) * 100) : 0;
  const nextUnclaimedFile = files.find((file) => !hasValidReviewClaim(file, now));
  const dueSoonCount = files.filter((file) => reviewSla(file, now).state === "due_soon").length;
  const overdueCount = files.filter((file) => reviewSla(file, now).state === "overdue").length;

  // ── 刷新辅助 ─────────────────────────────────────────────────────────────────

  const refreshFiles = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["review-files"] }),
      queryClient.invalidateQueries({ queryKey: ["documents"] }),
    ]);
  }, [queryClient]);

  useEffect(() => {
    let shouldRefresh = false;
    for (const file of files) {
      if (!file.claimed_by || !file.claim_expires_at) {
        continue;
      }
      const expiresAt = Date.parse(file.claim_expires_at);
      if (!Number.isFinite(expiresAt) || expiresAt > now) {
        continue;
      }
      const expiryKey = `${file.id}:${file.claim_expires_at}`;
      if (!refreshedExpiredClaims.current.has(expiryKey)) {
        refreshedExpiredClaims.current.add(expiryKey);
        shouldRefresh = true;
      }
    }
    if (shouldRefresh) {
      void refreshFiles();
    }
  }, [files, now, refreshFiles]);

  // ── mutations ────────────────────────────────────────────────────────────────

  const claimMutation = useMutation({
    mutationFn: claimReviewFile,
    onSuccess: async (file) => {
      setClaimFeedback(null);
      message.success(`已领取 ${documentDisplayTitle(file)}`);
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
      message.success(`已释放 ${documentDisplayTitle(file)}`);
      await refreshFiles();
    },
    onError: (error: Error, variables) => {
      setClaimFeedback({ fileId: variables.fileId, message: error.message || "释放失败" });
      message.error(error.message || "释放失败");
    },
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, values }: { id: string; values: ReviewFormValues }) =>
      approveFile(id, buildReviewDecisionPayload(values)),
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
    setSearchParams(new URLSearchParams(), { replace: true });
  };

  const handleBulkApprove = async () => {
    const requestIdentity = captureAuthSessionIdentity();

    try {
      await runAuthSessionCallback(requestIdentity, async (context) => {
        const targets = eligibleReviewTargets(selectedFiles, user?.id, now);

        if (targets.length === 0) {
          context.run(() => message.warning("已选文件中没有可批量审核项"));
          return;
        }

        context.run(() => setBulkApproving(true));
        try {
          const results = await context.waitFor(() =>
            Promise.allSettled(
              targets.map((file) => approveFile(file.id, buildBulkApproveOnlyPayload(file))),
            ),
          );
          const failedCount = results.filter((result) => result.status === "rejected").length;
          const successCount = targets.length - failedCount;

          context.run(() => {
            if (failedCount > 0) {
              message.warning(`批量审核完成，成功 ${successCount} 项，失败 ${failedCount} 项`);
            } else {
              message.success(`已批量审核 ${successCount} 个文件`);
            }
            setSelectedRowKeys([]);
          });
          await context.waitFor(refreshFiles);
        } finally {
          context.runIfCurrent(() => setBulkApproving(false));
        }
      });
    } catch (error) {
      if (!isSessionSupersededError(error) && isCurrentAuthSessionIdentity(requestIdentity)) {
        message.error(error instanceof Error ? error.message : "批量审核失败");
      }
    }
  };

  // ── 表格列定义 ────────────────────────────────────────────────────────────────

  const columns: ColumnsType<KnowledgeFile> = [
    {
      title: "文件名称",
      dataIndex: "original_name",
      key: "original_name",
      width: 240,
      ellipsis: true,
      render: (value: string, record) => {
        const meta = fileTypeMeta(value);
        const displayTitle = documentDisplayTitle(record);

        return (
          <div className="file-title-cell">
            <span className={`file-title-cell__icon ${meta.className}`}>{meta.icon}</span>
            <span className="file-title-cell__content">
              <button
                type="button"
                className="file-title-cell__name file-title-cell__link"
                title={displayTitle}
                onClick={() => navigate(`/files/${record.id}#original`)}
                aria-label={`查看原件与审核详情 ${displayTitle}`}
              >
                {displayTitle}
              </button>
              <span className="file-title-cell__meta">
                <Typography.Text type="secondary">{originalFileNameLabel(record)}</Typography.Text>
                <Typography.Text type="secondary">{record.mime_type}</Typography.Text>
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
        const sla = reviewSla(record, now);
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
      render: (_value: string | null, record) => {
        const validClaim = hasValidReviewClaim(record, now);
        if (validClaim) {
          return (
            <Space direction="vertical" size={1}>
              <Typography.Text>
                {record.claimed_by === user?.id ? "我" : record.claimed_by_name || "其他审核人"}
              </Typography.Text>
              <Typography.Text type="secondary">
                {record.claimed_at ? dayjs(record.claimed_at).format("MM-DD HH:mm") : "已领取"}
              </Typography.Text>
            </Space>
          );
        }
        return (
          <Typography.Text type={record.claimed_by ? "warning" : "secondary"}>
            {record.claimed_by ? "领取已失效" : "待领取"}
          </Typography.Text>
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 250,
      fixed: "right" as const,
      render: (_, record) => {
        const canDecide = record.status === "pending_review" && canDecideFile(record);
        const validClaim = hasValidReviewClaim(record, now);
        const canClaim = record.status === "pending_review" && !validClaim;
        const claimedByMe = canDecide;
        const canForceRelease = user?.role === Roles.SYSTEM_ADMIN && validClaim && !claimedByMe;

        const moreItems: MenuProps["items"] = [];

        if (canDecide) {
          moreItems.push({
            key: "classify",
            label: "编辑审核草案",
            onClick: () => openClassificationModal(record),
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

  const changePage = (nextPage: number, nextPageSize: number) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("page", String(nextPage));
        next.set("page_size", String(nextPageSize));
        return next;
      },
      { replace: true },
    );
  };

  const toggleSelectedFile = (fileId: string, checked: boolean) => {
    setSelectedRowKeys((previous) => {
      const next = new Set(previous.map(String));
      if (checked) {
        next.add(fileId);
      } else {
        next.delete(fileId);
      }
      return Array.from(next);
    });
  };

  const renderMobileActions = (record: KnowledgeFile) => {
    const canDecide = record.status === "pending_review" && canDecideFile(record);
    const validClaim = hasValidReviewClaim(record, now);
    const canClaim = record.status === "pending_review" && !validClaim;
    const claimedByMe = canDecide;
    const canForceRelease = user?.role === Roles.SYSTEM_ADMIN && validClaim && !claimedByMe;

    return (
      <div
        className="review-mobile-card__actions"
        aria-label={`${documentDisplayTitle(record)} 审核操作`}
      >
        <Button onClick={() => navigate(`/files/${record.id}#original`)}>查看原件</Button>
        {canClaim ? (
          <Button
            icon={<LockOutlined />}
            loading={claimMutation.isPending && claimMutation.variables === record.id}
            onClick={() => claimMutation.mutate(record.id)}
          >
            {record.claimed_by ? "重新领取" : "领取"}
          </Button>
        ) : null}
        {canDecide ? (
          <>
            <Button type="primary" onClick={() => openApproveModal(record)}>
              批准
            </Button>
            <Button danger onClick={() => openRejectModal(record)}>
              驳回
            </Button>
            <Button onClick={() => openClassificationModal(record)}>编辑草案</Button>
          </>
        ) : null}
        {claimedByMe ? (
          <Button
            icon={<UnlockOutlined />}
            loading={
              releaseClaimMutation.isPending && releaseClaimMutation.variables?.fileId === record.id
            }
            onClick={() => releaseClaimMutation.mutate({ fileId: record.id })}
          >
            释放
          </Button>
        ) : null}
        {canForceRelease ? (
          <Button
            danger
            icon={<UnlockOutlined />}
            onClick={() => {
              setForceReleasingFile(record);
              setForceReleaseReason("");
            }}
          >
            强制释放
          </Button>
        ) : null}
      </div>
    );
  };

  const handleQueueTabKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % REVIEW_QUEUES.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + REVIEW_QUEUES.length) % REVIEW_QUEUES.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = REVIEW_QUEUES.length - 1;
    }
    if (nextIndex === null) {
      return;
    }
    event.preventDefault();
    const nextQueue = REVIEW_QUEUES[nextIndex].value;
    updateCoreQuery("queue", nextQueue);
    queueMicrotask(() => document.getElementById(`review-queue-tab-${nextQueue}`)?.focus());
  };

  // ── 渲染 ──────────────────────────────────────────────────────────────────────

  return (
    <PageContainer
      title="部门审核工作台"
      description="先领取任务，再查看原件、风险与元数据并作出明确入库决定。"
      actions={
        <Button
          type="primary"
          icon={<LockOutlined />}
          loading={claimMutation.isPending && claimMutation.variables === nextUnclaimedFile?.id}
          title={
            nextUnclaimedFile
              ? "领取当前页下一份可领取任务"
              : "当前页没有可领取项，点击打开待领取队列"
          }
          onClick={() => {
            if (nextUnclaimedFile) {
              claimMutation.mutate(nextUnclaimedFile.id);
            } else {
              message.info("当前页没有可直接领取项，已为你打开待领取队列");
              updateCoreQuery("queue", "unclaimed");
            }
          }}
        >
          领取下一份
        </Button>
      }
    >
      <SavedViewManager
        pageKey="review_files"
        queryDefinition={savedViewDefinition}
        departmentOptions={savedViewDepartmentOptions}
        onApply={applySavedView}
      />
      <nav className="review-queue-tabs" aria-label="审核队列" role="tablist">
        {REVIEW_QUEUES.map((item, index) => (
          <button
            key={item.value}
            id={`review-queue-tab-${item.value}`}
            type="button"
            role="tab"
            aria-selected={queue === item.value}
            aria-controls="review-queue-panel"
            tabIndex={queue === item.value ? 0 : -1}
            className={
              queue === item.value
                ? "review-queue-tab review-queue-tab--active"
                : "review-queue-tab"
            }
            onClick={() => updateCoreQuery("queue", item.value)}
            onKeyDown={(event) => handleQueueTabKeyDown(event, index)}
          >
            <span>{item.label}</span>
            {queue === item.value ? <strong>{reviewFilesQuery.data?.total ?? 0}</strong> : null}
          </button>
        ))}
      </nav>

      <Card
        id="review-queue-panel"
        className="document-panel table-card"
        role="tabpanel"
        aria-labelledby={`review-queue-tab-${queue}`}
        aria-busy={reviewFilesQuery.isFetching}
      >
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
                aria-label="风险等级筛选"
                value={riskFilter}
                options={[
                  { label: "风险等级：全部", value: "all" },
                  { label: "无风险", value: "none" },
                  { label: "低风险", value: "low" },
                  { label: "中风险", value: "medium" },
                  { label: "高风险", value: "high" },
                  { label: "严重风险", value: "critical" },
                ]}
                onChange={(value) => updateCoreQuery("risk", value)}
              />
              <Select
                className="filter-toolbar__control"
                aria-label="文件类型筛选"
                value={extensionFilter ?? "all"}
                options={extensionOptions}
                onChange={(value) => updateCoreQuery("extension", value)}
                placeholder="文件类型：全部"
              />
              <Select
                className="filter-toolbar__control"
                aria-label="标签筛选"
                value={tagIdFilter ?? "all"}
                options={tagOptions}
                onChange={(value) => updateCoreQuery("tag_id", value)}
                loading={tagsQuery.isLoading}
                placeholder="标签：全部"
              />
              {savedViewDepartmentOptions.length > 0 ? (
                <Select
                  className="filter-toolbar__control"
                  aria-label="部门筛选"
                  value={departmentIdFilter ?? "all"}
                  options={[{ label: "部门：全部", value: "all" }, ...savedViewDepartmentOptions]}
                  onChange={(value) => updateCoreQuery("department_id", value)}
                />
              ) : null}
              <Select
                className="filter-toolbar__control"
                aria-label="审核队列排序字段"
                value={sort}
                options={REVIEW_SORT_OPTIONS}
                onChange={(value) => updateCoreQuery("sort", value)}
              />
              <Select
                className="filter-toolbar__control"
                aria-label="审核队列排序方向"
                value={order}
                options={[
                  { label: "升序", value: "asc" },
                  { label: "降序", value: "desc" },
                ]}
                onChange={(value) => updateCoreQuery("order", value)}
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

        {isMobile ? (
          <div className="review-mobile-list" role="list" aria-label="移动端审核队列">
            {files.map((file) => {
              const sla = reviewSla(file, now);
              const validClaim = hasValidReviewClaim(file, now);
              return (
                <article className="review-mobile-card" role="listitem" key={file.id}>
                  <header className="review-mobile-card__header">
                    <Checkbox
                      className="review-mobile-card__select"
                      checked={selectedKeySet.has(file.id)}
                      onChange={(event) => toggleSelectedFile(file.id, event.target.checked)}
                      aria-label={`选择 ${documentDisplayTitle(file)}`}
                    />
                    <button
                      type="button"
                      className="review-mobile-card__title"
                      onClick={() => navigate(`/files/${file.id}#original`)}
                    >
                      {documentDisplayTitle(file)}
                    </button>
                    <StatusTag kind="risk" value={riskLevel(file)} />
                  </header>
                  <div className="review-mobile-card__meta">
                    <span>{originalFileNameLabel(file)}</span>
                    <span>{uploaderText(file)}</span>
                    <span>{file.department ?? "未分配部门"}</span>
                    <span>{formatFileSize(file.size)}</span>
                  </div>
                  <div className="review-mobile-card__state">
                    <StatusTag kind="review" value={file.review_status} />
                    <span className={`review-sla review-sla--${sla.state}`}>
                      <span>
                        <ClockCircleOutlined /> {sla.label}
                      </span>
                      <small>{sla.detail}</small>
                    </span>
                    <Typography.Text type={validClaim ? undefined : "secondary"}>
                      {validClaim
                        ? file.claimed_by === user?.id
                          ? "由我领取"
                          : `由 ${file.claimed_by_name || "其他审核人"} 领取`
                        : file.claimed_by
                          ? "领取已失效"
                          : "待领取"}
                    </Typography.Text>
                  </div>
                  {renderMobileActions(file)}
                  {claimFeedback?.fileId === file.id ? (
                    <Typography.Text type="danger" role="alert">
                      {claimFeedback.message}
                    </Typography.Text>
                  ) : null}
                </article>
              );
            })}
            {files.length === 0 && !reviewFilesQuery.isLoading ? (
              <Typography.Text type="secondary">暂无文件</Typography.Text>
            ) : null}
            <Pagination
              className="review-mobile-pagination"
              current={page}
              pageSize={pageSize}
              total={reviewFilesQuery.data?.total ?? 0}
              showSizeChanger
              pageSizeOptions={[10, 20, 50]}
              onChange={changePage}
              showTotal={(value) => `共 ${value} 条`}
            />
          </div>
        ) : (
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
              onChange: changePage,
            }}
            locale={{ emptyText: "暂无文件" }}
            tableLayout="fixed"
            rowSelection={{
              selectedRowKeys,
              onChange: setSelectedRowKeys,
            }}
            scroll={{ x: 1420 }}
          />
        )}
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
            if (!hasActiveReviewClaim(classifyingFile, user?.id, now)) {
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
