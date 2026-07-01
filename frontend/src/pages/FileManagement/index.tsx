import {
  App as AntdApp,
  Avatar,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloudSyncOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileExcelOutlined,
  FileAddOutlined,
  FileOutlined,
  FilePdfOutlined,
  FileProtectOutlined,
  FilePptOutlined,
  FileWordOutlined,
  FilterOutlined,
  InboxOutlined,
  ReloadOutlined,
  SafetyOutlined,
  StarOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs, { type Dayjs } from "dayjs";
import { useMemo, useState } from "react";
import type { Key } from "react";
import type { ColumnsType } from "antd/es/table";
import type { FormInstance } from "antd/es/form";

import {
  type DatasetMapping,
  type KnowledgeFile,
  approveFile,
  archiveFile,
  deleteFile,
  getUploadPolicy,
  listCategories,
  listDatasetMappings,
  listReviewFiles,
  listTags,
  reanalyzeFile,
  rejectFile,
  submitFileForReview,
  syncFile,
  updateFileClassification,
} from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { allowedExtensionsFromPolicy } from "../../utils/uploadConfig";

// ── 常量 ──────────────────────────────────────────────────────────────────────

// ── 类型 ──────────────────────────────────────────────────────────────────────

interface ReviewFormValues {
  category_id?: string;
  dataset_mapping_id?: string;
  reason?: string;
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────

const { RangePicker } = DatePicker;
const reviewableStatuses = new Set(["uploaded", "analyzed", "sensitive_review_required"]);
const reanalyzeStatuses = new Set(["analysis_failed", "analyzed"]);
const syncableStatuses = new Set(["approved", "failed"]);

function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
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

function syncStatus(file: KnowledgeFile): "not_synced" | "syncing" | "synced" | "failed" {
  if (file.ragflow_parse_status === "failed" || file.status === "failed") {
    return "failed";
  }
  if (file.ragflow_document_id || file.ragflow_parse_status === "parsed") {
    return "synced";
  }
  if (["queued", "syncing", "uploaded_to_ragflow", "parsing"].includes(file.status)) {
    return "syncing";
  }
  return "not_synced";
}

function riskLevel(file: KnowledgeFile): "low" | "medium" | "high" {
  if (file.status === "sensitive_review_required") {
    return "high";
  }
  if (file.review_status === "rejected" || file.status === "rejected") {
    return "medium";
  }
  return "low";
}

function uploaderText(file: KnowledgeFile): string {
  return file.uploader_id.slice(0, 8);
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

function FileGovernanceStrip({
  allowedExtensionCount,
  activeMappingCount,
  totalCount,
  unmappedCount,
  pendingReviewCount,
  highRiskCount,
  syncFailedCount,
  syncableCount,
}: {
  allowedExtensionCount: number;
  activeMappingCount: number;
  totalCount: number;
  unmappedCount: number;
  pendingReviewCount: number;
  highRiskCount: number;
  syncFailedCount: number;
  syncableCount: number;
}) {
  const lanes = [
    {
      key: "ingress",
      icon: <FileProtectOutlined />,
      title: "准入规则",
      primary: allowedExtensionCount > 0 ? `${allowedExtensionCount} 类文件白名单` : "读取上传配置",
      secondary: "文件类型与标签筛选已接入服务端",
      status: { kind: "health" as const, value: allowedExtensionCount > 0 ? "ok" : "unknown" },
    },
    {
      key: "mapping",
      icon: <DatabaseOutlined />,
      title: "分类映射",
      primary: `${activeMappingCount} 个启用映射`,
      secondary: unmappedCount > 0 ? `${unmappedCount} 个文件待补 Dataset` : "当前视图已完成映射",
      status: { kind: "dataset" as const, value: unmappedCount > 0 ? "pending" : "enabled" },
    },
    {
      key: "risk",
      icon: <SafetyOutlined />,
      title: "审核风险",
      primary: `${pendingReviewCount} 个待审核`,
      secondary: highRiskCount > 0 ? `${highRiskCount} 个高风险文件` : "未发现高风险文件",
      status: { kind: "risk" as const, value: highRiskCount > 0 ? "high" : "low" },
    },
    {
      key: "sync",
      icon: <CloudSyncOutlined />,
      title: "RAGFlow 同步",
      primary: `${syncableCount} 个可同步`,
      secondary: syncFailedCount > 0 ? `${syncFailedCount} 个失败重试` : "同步队列可继续处理",
      status: {
        kind: "sync" as const,
        value: syncFailedCount > 0 ? "failed" : syncableCount > 0 ? "queued" : "succeeded",
      },
    },
  ];

  return (
    <section className="file-governance-strip" role="region" aria-label="文件治理工作台状态">
      <div className="file-governance-strip__main">
        <span className="file-governance-strip__icon">
          <FileProtectOutlined />
        </span>
        <span className="file-governance-strip__copy">
          <Typography.Text strong className="file-governance-strip__title">
            文件治理工作台
          </Typography.Text>
          <Typography.Text type="secondary">
            将准入白名单、分类映射、审核风险和 RAGFlow 同步状态合并到当前视图。
          </Typography.Text>
        </span>
        <span className="file-governance-strip__count">
          <strong>{totalCount}</strong>
          <Typography.Text type="secondary">当前视图文件</Typography.Text>
        </span>
      </div>
      <div className="file-governance-strip__lanes" aria-label="文件治理指标">
        {lanes.map((lane) => (
          <div className="file-governance-lane" key={lane.key}>
            <span className="file-governance-lane__icon">{lane.icon}</span>
            <span className="file-governance-lane__body">
              <span className="file-governance-lane__topline">
                <Typography.Text strong>{lane.title}</Typography.Text>
                <StatusTag kind={lane.status.kind} value={lane.status.value} variant="dot" />
              </span>
              <strong>{lane.primary}</strong>
              <Typography.Text type="secondary">{lane.secondary}</Typography.Text>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
// ── 主页面 ────────────────────────────────────────────────────────────────────

export default function FileManagementPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [approveForm] = Form.useForm<ReviewFormValues>();
  const [rejectForm] = Form.useForm<ReviewFormValues>();
  const [classificationForm] = Form.useForm<ReviewFormValues>();
  const [approvingFile, setApprovingFile] = useState<KnowledgeFile | null>(null);
  const [rejectingFile, setRejectingFile] = useState<KnowledgeFile | null>(null);
  const [classifyingFile, setClassifyingFile] = useState<KnowledgeFile | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [searchText, setSearchText] = useState("");
  const [uploaderFilter, setUploaderFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [reviewFilter, setReviewFilter] = useState("all");
  const [syncFilter, setSyncFilter] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [uploadedRange, setUploadedRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [bulkApproving, setBulkApproving] = useState(false);
  const [bulkSyncing, setBulkSyncing] = useState(false);
  // 新增：服务端筛选参数
  const [extensionFilter, setExtensionFilter] = useState<string | undefined>(undefined);
  const [tagIdFilter, setTagIdFilter] = useState<string | undefined>(undefined);

  // ── 数据查询 ─────────────────────────────────────────────────────────────────

  const reviewFilesQuery = useQuery({
    queryKey: ["review-files", { extension: extensionFilter, tag_id: tagIdFilter }],
    queryFn: () =>
      listReviewFiles({
        extension: extensionFilter,
        tag_id: tagIdFilter,
      }),
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
  const uploaderOptions = useMemo(
    () =>
      Array.from(new Set(files.map((file) => file.uploader_id))).map((uploaderId) => ({
        label: uploaderId.slice(0, 8),
        value: uploaderId,
      })),
    [files],
  );
  const tagOptions = useMemo(
    () => [
      { label: "标签：全部", value: "all" },
      ...tags.map((tag) => ({ label: tag.name, value: tag.id })),
    ],
    [tags],
  );
  const approveDatasetOptions = buildMappingOptions(datasets, categoryIdForApprove);
  const classificationDatasetOptions = buildMappingOptions(datasets, categoryIdForClassification);

  // ── 客户端筛选（与服务端筛选叠加） ───────────────────────────────────────────

  const filteredFiles = files.filter((file) => {
    const keyword = searchText.trim().toLowerCase();
    const categoryName = file.category_id ? (categoryNameById.get(file.category_id) ?? "") : "";
    const datasetName = file.dataset_mapping_id
      ? (mappingById.get(file.dataset_mapping_id)?.name ?? "")
      : "";
    const haystack = [
      file.original_name,
      file.mime_type,
      file.department,
      categoryName,
      datasetName,
      file.description,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const uploadedAt = dayjs(file.uploaded_at);

    return (
      (!keyword || haystack.includes(keyword)) &&
      (uploaderFilter === "all" || file.uploader_id === uploaderFilter) &&
      (categoryFilter === "all" || file.category_id === categoryFilter) &&
      (reviewFilter === "all" ||
        file.review_status === reviewFilter ||
        file.status === reviewFilter) &&
      (syncFilter === "all" || syncStatus(file) === syncFilter) &&
      (riskFilter === "all" || riskLevel(file) === riskFilter) &&
      (!uploadedRange ||
        (uploadedAt.isAfter(uploadedRange[0].startOf("day")) &&
          uploadedAt.isBefore(uploadedRange[1].endOf("day"))))
    );
  });

  const selectedKeySet = useMemo(
    () => new Set(selectedRowKeys.map((key) => String(key))),
    [selectedRowKeys],
  );
  const selectedFiles = filteredFiles.filter((file) => selectedKeySet.has(file.id));
  const pendingReviewCount = filteredFiles.filter(
    (file) => file.status === "pending_review",
  ).length;
  const highRiskCount = filteredFiles.filter((file) => riskLevel(file) === "high").length;
  const syncFailedCount = filteredFiles.filter((file) => syncStatus(file) === "failed").length;
  const syncableReadyCount = filteredFiles.filter((file) =>
    syncableStatuses.has(file.status),
  ).length;
  const unmappedFileCount = filteredFiles.filter(
    (file) => !file.dataset_mapping_id && !file.ragflow_dataset_id,
  ).length;
  const activeMappingCount = datasets.filter((mapping) => mapping.enabled).length;
  const selectedPendingCount = selectedFiles.filter(
    (file) => file.status === "pending_review",
  ).length;
  const selectedSyncableCount = selectedFiles.filter((file) =>
    syncableStatuses.has(file.status),
  ).length;
  const selectedRatio =
    filteredFiles.length > 0 ? Math.round((selectedFiles.length / filteredFiles.length) * 100) : 0;

  // ── 刷新辅助 ─────────────────────────────────────────────────────────────────

  const refreshFiles = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["review-files"] }),
      queryClient.invalidateQueries({ queryKey: ["documents"] }),
    ]);
  };

  // ── mutations ────────────────────────────────────────────────────────────────

  const submitMutation = useMutation({
    mutationFn: submitFileForReview,
    onSuccess: async () => {
      message.success("文件已进入审核");
      await refreshFiles();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, values }: { id: string; values: ReviewFormValues }) =>
      approveFile(id, {
        category_id: values.category_id ?? null,
        dataset_mapping_id: values.dataset_mapping_id ?? null,
        reason: values.reason?.trim() || null,
      }),
    onSuccess: async () => {
      message.success("文件已审核通过");
      setApprovingFile(null);
      approveForm.resetFields();
      await refreshFiles();
    },
    onError: (error) => {
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
    onError: (error) => {
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
      message.success("分类与 Dataset 已更新");
      setClassifyingFile(null);
      classificationForm.resetFields();
      await refreshFiles();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const syncMutation = useMutation({
    mutationFn: (id: string) => syncFile(id),
    onSuccess: async () => {
      message.success("手动同步任务已创建");
      await refreshFiles();
    },
    onError: (error) => {
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

  const reanalyzeMutation = useMutation({
    mutationFn: (id: string) => reanalyzeFile(id),
    onSuccess: async () => {
      message.success("重新分析已触发");
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
    setUploaderFilter("all");
    setCategoryFilter("all");
    setReviewFilter("all");
    setSyncFilter("all");
    setRiskFilter("all");
    setUploadedRange(null);
    setExtensionFilter(undefined);
    setTagIdFilter(undefined);
  };

  const handleBulkApprove = async () => {
    const targets = selectedFiles.filter((file) => file.status === "pending_review");

    if (targets.length === 0) {
      message.warning("已选文件中没有可批量审核项");
      return;
    }

    setBulkApproving(true);
    try {
      const results = await Promise.allSettled(
        targets.map((file) =>
          approveFile(file.id, {
            category_id: file.category_id ?? null,
            dataset_mapping_id: file.dataset_mapping_id ?? null,
            reason: "批量审核通过",
          }),
        ),
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

  const handleBulkSync = async () => {
    const targets = selectedFiles.filter((file) => syncableStatuses.has(file.status));

    if (targets.length === 0) {
      message.warning("已选文件中没有可同步项");
      return;
    }

    setBulkSyncing(true);
    try {
      const results = await Promise.allSettled(targets.map((file) => syncFile(file.id)));
      const failedCount = results.filter((result) => result.status === "rejected").length;
      const successCount = targets.length - failedCount;

      if (failedCount > 0) {
        message.warning(`批量同步完成，成功 ${successCount} 项，失败 ${failedCount} 项`);
      } else {
        message.success(`已创建 ${successCount} 个同步任务`);
      }

      setSelectedRowKeys([]);
      await refreshFiles();
    } finally {
      setBulkSyncing(false);
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
              <span className="file-title-cell__name" title={value}>
                {value}
              </span>
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
      title: "同步状态",
      key: "sync_status",
      width: 104,
      render: (_, record) => <StatusTag kind="sync" value={syncStatus(record)} />,
    },
    {
      title: "敏感风险",
      key: "risk",
      width: 104,
      render: (_, record) => <StatusTag kind="risk" value={riskLevel(record)} />,
    },
    {
      title: "上传时间",
      dataIndex: "uploaded_at",
      key: "uploaded_at",
      width: 118,
      render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      fixed: "right" as const,
      render: (_, record) => {
        const canSubmit = reviewableStatuses.has(record.status);
        const canDecide = record.status === "pending_review";
        const canSync = syncableStatuses.has(record.status);
        const canReanalyze = reanalyzeStatuses.has(record.status);

        return (
          <Space size={4} wrap>
            {/* 审核 / 送审 */}
            {canDecide ? (
              <Button
                type="link"
                size="small"
                className="table-link-button"
                onClick={() => openApproveModal(record)}
              >
                审核
              </Button>
            ) : canSubmit ? (
              <Button
                type="link"
                size="small"
                className="table-link-button"
                loading={submitMutation.isPending}
                onClick={() => submitMutation.mutate(record.id)}
              >
                送审
              </Button>
            ) : null}

            {/* 修改分类 */}
            <Button
              type="link"
              size="small"
              className="table-link-button"
              onClick={() => openClassificationModal(record)}
            >
              修改分类
            </Button>

            {/* 手动同步（approved / failed 态） */}
            {canSync ? (
              <Popconfirm
                title="手动触发同步"
                description="将该文件重新推送到 RAGFlow 知识库，确认继续？"
                onConfirm={() => syncMutation.mutate(record.id)}
                okText="确定"
                cancelText="取消"
              >
                <Button
                  type="link"
                  size="small"
                  icon={<SyncOutlined />}
                  className="table-link-button"
                  loading={syncMutation.isPending}
                >
                  同步
                </Button>
              </Popconfirm>
            ) : null}

            {/* 重新分析（analysis_failed / analyzed 态） */}
            {canReanalyze ? (
              <Button
                type="link"
                size="small"
                className="table-link-button"
                loading={reanalyzeMutation.isPending}
                onClick={() => reanalyzeMutation.mutate(record.id)}
              >
                重新分析
              </Button>
            ) : null}

            {/* 归档 */}
            <Popconfirm
              title="归档文件"
              description="归档后文件将停止同步，确认继续？"
              onConfirm={() => archiveMutation.mutate(record.id)}
              okText="确定"
              cancelText="取消"
            >
              <Button
                type="link"
                size="small"
                icon={<InboxOutlined />}
                className="table-link-button"
                loading={archiveMutation.isPending}
              >
                归档
              </Button>
            </Popconfirm>

            {/* 驳回（待审核态） */}
            {canDecide ? (
              <Button
                type="link"
                danger
                size="small"
                className="table-link-button"
                onClick={() => openRejectModal(record)}
              >
                驳回
              </Button>
            ) : null}

            {/* 删除 */}
            <Popconfirm
              title="删除文件"
              description="此操作不可撤销，文件将被软删除并触发 RAGFlow 联动清理，确认删除？"
              onConfirm={() => deleteMutation.mutate(record.id)}
              okText="确定"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                type="link"
                danger
                size="small"
                icon={<DeleteOutlined />}
                className="table-link-button"
                loading={deleteMutation.isPending}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  // ── 渲染 ──────────────────────────────────────────────────────────────────────

  return (
    <PageContainer
      title="文件管理"
      description="管理平台内所有文件的审核与同步状态，保障数据质量与合规安全。"
    >
      <div className="metric-grid">
        <KpiCard
          icon={<FileProtectOutlined />}
          title="待审核"
          value={files.filter((file) => file.status === "pending_review").length}
          description="较昨日保持稳定"
          tone="warning"
        />
        <KpiCard
          icon={<SafetyOutlined />}
          title="高风险文件"
          value={files.filter((file) => riskLevel(file) === "high").length}
          description="敏感审核队列"
          tone="danger"
        />
        <KpiCard
          icon={<CloudSyncOutlined />}
          title="同步失败"
          value={files.filter((file) => syncStatus(file) === "failed").length}
          description="需人工处理"
          tone="purple"
        />
        <KpiCard
          icon={<FileAddOutlined />}
          title="今日新增"
          value={files.filter((file) => dayjs(file.uploaded_at).isSame(dayjs(), "day")).length}
          description="当日上传"
          tone="info"
        />
      </div>

      <FileGovernanceStrip
        activeMappingCount={activeMappingCount}
        allowedExtensionCount={allowedExtensions.length}
        highRiskCount={highRiskCount}
        pendingReviewCount={pendingReviewCount}
        syncableCount={syncableReadyCount}
        syncFailedCount={syncFailedCount}
        totalCount={filteredFiles.length}
        unmappedCount={unmappedFileCount}
      />

      <Card className="document-panel table-card">
        {/* ── 筛选栏 ── */}
        <div className="filter-toolbar filter-toolbar--management">
          <Input.Search
            className="filter-toolbar__search"
            placeholder="搜索文件名称、关键词"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            allowClear
          />
          <Select
            className="filter-toolbar__control"
            value={uploaderFilter}
            options={[{ label: "上传人：全部", value: "all" }, ...uploaderOptions]}
            onChange={setUploaderFilter}
          />
          <Select
            className="filter-toolbar__control"
            value={categoryFilter}
            options={[{ label: "分类：全部", value: "all" }, ...categoryOptions]}
            onChange={setCategoryFilter}
          />
          <Select
            className="filter-toolbar__control"
            value={reviewFilter}
            options={[
              { label: "审核状态：全部", value: "all" },
              { label: "待审核", value: "pending_review" },
              { label: "已通过", value: "approved" },
              { label: "未通过", value: "rejected" },
            ]}
            onChange={setReviewFilter}
          />
          <Select
            className="filter-toolbar__control"
            value={syncFilter}
            options={[
              { label: "同步状态：全部", value: "all" },
              { label: "未同步", value: "not_synced" },
              { label: "同步中", value: "syncing" },
              { label: "已同步", value: "synced" },
              { label: "同步失败", value: "failed" },
            ]}
            onChange={setSyncFilter}
          />
          <Select
            className="filter-toolbar__control"
            value={riskFilter}
            options={[
              { label: "风险等级：全部", value: "all" },
              { label: "低风险", value: "low" },
              { label: "中风险", value: "medium" },
              { label: "高风险", value: "high" },
            ]}
            onChange={setRiskFilter}
          />
          {/* 新增：文件类型筛选（服务端过滤） */}
          <Select
            className="filter-toolbar__control"
            value={extensionFilter ?? "all"}
            options={extensionOptions}
            onChange={(value) => setExtensionFilter(value === "all" ? undefined : value)}
            placeholder="文件类型：全部"
          />
          {/* 新增：标签筛选（服务端过滤） */}
          <Select
            className="filter-toolbar__control"
            value={tagIdFilter ?? "all"}
            options={tagOptions}
            onChange={(value) => setTagIdFilter(value === "all" ? undefined : value)}
            loading={tagsQuery.isLoading}
            placeholder="标签：全部"
          />
          <RangePicker
            className="filter-toolbar__range"
            placeholder={["开始日期", "结束日期"]}
            value={uploadedRange}
            onChange={(value) => setUploadedRange(value as [Dayjs, Dayjs] | null)}
          />
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
                基于当前筛选结果汇总待处理文件，选中后可快速判断可审核与可同步范围。
              </Typography.Text>
            </span>
          </div>
          <div className="review-command-strip__stats" aria-label="当前筛选摘要">
            <span className="review-command-strip__stat review-command-strip__stat--warning">
              <Typography.Text type="secondary">待审核</Typography.Text>
              <strong>{pendingReviewCount}项</strong>
            </span>
            <span className="review-command-strip__stat review-command-strip__stat--danger">
              <Typography.Text type="secondary">高风险</Typography.Text>
              <strong>{highRiskCount}项</strong>
            </span>
            <span className="review-command-strip__stat review-command-strip__stat--purple">
              <Typography.Text type="secondary">同步失败</Typography.Text>
              <strong>{syncFailedCount}项</strong>
            </span>
            <span className="review-command-strip__stat review-command-strip__stat--info">
              <Typography.Text type="secondary">已选</Typography.Text>
              <strong>{selectedFiles.length}项</strong>
            </span>
            <span className="review-command-strip__stat">
              <Typography.Text type="secondary">可审核</Typography.Text>
              <strong>{selectedPendingCount}项</strong>
            </span>
            <span className="review-command-strip__stat">
              <Typography.Text type="secondary">可同步</Typography.Text>
              <strong>{selectedSyncableCount}项</strong>
            </span>
          </div>
          <div className="review-command-strip__action-panel">
            <div className="review-command-strip__selection" aria-label="选择范围">
              <span className="review-command-strip__selection-copy">
                <Typography.Text type="secondary">选中范围</Typography.Text>
                <strong>
                  {selectedFiles.length}/{filteredFiles.length}
                </strong>
              </span>
              <Progress percent={selectedRatio} size="small" showInfo={false} />
            </div>
            <Space wrap className="review-command-strip__actions">
              <Button size="small" onClick={() => setReviewFilter("pending_review")}>
                只看待审核
              </Button>
              <Button size="small" onClick={() => setSyncFilter("failed")}>
                只看同步失败
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
              title="批量审核通过"
              description={`将 ${selectedPendingCount} 个待审核文件标记为通过，确认继续？`}
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
                批量审核
              </Button>
            </Popconfirm>
            <Popconfirm
              title="批量同步文件"
              description={`为 ${selectedSyncableCount} 个可同步文件创建 RAGFlow 同步任务，确认继续？`}
              onConfirm={() => void handleBulkSync()}
              okText="确定"
              cancelText="取消"
            >
              <Button
                icon={<CloudSyncOutlined />}
                disabled={selectedSyncableCount === 0}
                loading={bulkSyncing}
              >
                批量同步
              </Button>
            </Popconfirm>
            <Button icon={<DownloadOutlined />}>导出</Button>
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
          dataSource={filteredFiles}
          loading={reviewFilesQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{ emptyText: "暂无文件" }}
          tableLayout="fixed"
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
          }}
          scroll={{ x: 1200 }}
        />
      </Card>

      {/* ── 审核通过 Modal ── */}
      <Modal
        title="审核通过"
        open={Boolean(approvingFile)}
        onCancel={() => setApprovingFile(null)}
        onOk={() => approveForm.submit()}
        confirmLoading={approveMutation.isPending}
        width={620}
      >
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
          <Form.Item label="分类" name="category_id">
            <Select
              allowClear
              options={categoryOptions}
              loading={categoriesQuery.isLoading}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item label="Dataset 映射" name="dataset_mapping_id">
            <Select
              allowClear
              options={approveDatasetOptions}
              loading={datasetsQuery.isLoading}
              showSearch
              optionFilterProp="label"
              onChange={(value) => syncCategoryFromMapping(approveForm, value)}
            />
          </Form.Item>
          <Form.Item label="审核说明" name="reason">
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── 拒绝文件 Modal ── */}
      <Modal
        title="拒绝文件"
        open={Boolean(rejectingFile)}
        onCancel={() => setRejectingFile(null)}
        onOk={() => rejectForm.submit()}
        confirmLoading={rejectMutation.isPending}
        width={560}
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

      {/* ── 调整分类 Modal ── */}
      <Modal
        title="调整分类与 Dataset"
        open={Boolean(classifyingFile)}
        onCancel={() => setClassifyingFile(null)}
        onOk={() => classificationForm.submit()}
        confirmLoading={classificationMutation.isPending}
        width={620}
      >
        <Form<ReviewFormValues>
          form={classificationForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => {
            if (classifyingFile) {
              classificationMutation.mutate({ id: classifyingFile.id, values });
            }
          }}
        >
          <Form.Item label="分类" name="category_id">
            <Select
              allowClear
              options={categoryOptions}
              loading={categoriesQuery.isLoading}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item label="Dataset 映射" name="dataset_mapping_id">
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
