import { useMemo, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  DatePicker,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloudSyncOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileTextOutlined,
  SearchOutlined,
  SendOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Dayjs } from "dayjs";
import dayjs from "dayjs";
import { Link, useNavigate } from "react-router-dom";
import type { ColumnsType } from "antd/es/table";

import {
  type KnowledgeFile,
  deleteFile,
  getConfigs,
  listDocuments,
  listTags,
  submitFileForReview,
} from "../../api/client";
import { KpiCard, type KpiTone } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { allowedExtensionsFromConfig } from "../../utils/uploadConfig";

const { RangePicker } = DatePicker;

function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function syncStatus(file: KnowledgeFile): string {
  if (file.ragflow_parse_status === "parsed") {
    return "synced";
  }
  if (file.ragflow_parse_status === "failed") {
    return "failed";
  }
  if (file.ragflow_parse_status === "parsing") {
    return "syncing";
  }
  return file.ragflow_document_id ? "queued" : "not_synced";
}

function canSubmitForReview(file: KnowledgeFile): boolean {
  return file.status === "uploaded" || file.status === "rejected";
}

interface MyFilesMetric {
  title: string;
  value: number;
  description: string;
  icon: ReactNode;
  tone: KpiTone;
}

export default function MyFilesPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { message } = App.useApp();

  // ── filter state ────────────────────────────────────────────────────────────
  const [keyword, setKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [reviewFilter, setReviewFilter] = useState("all");
  // server-side filters forwarded to the API
  const [extensionFilter, setExtensionFilter] = useState<string | undefined>(undefined);
  const [tagIdFilter, setTagIdFilter] = useState<string | undefined>(undefined);
  // local time range filter (listDocuments doesn't support date params)
  const [timeRange, setTimeRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);

  // ── queries ─────────────────────────────────────────────────────────────────
  const filesQuery = useQuery({
    queryKey: ["documents", "mine", extensionFilter, tagIdFilter],
    queryFn: () =>
      listDocuments({
        extension: extensionFilter,
        tag_id: tagIdFilter,
      }),
  });

  const tagsQuery = useQuery({
    queryKey: ["tags", "list"],
    queryFn: () => listTags({ enabled: true, page_size: 100 }),
  });
  const uploadConfigQuery = useQuery({
    queryKey: ["configs", "upload", "my-files"],
    queryFn: () => getConfigs("upload"),
  });

  // ── delete mutation ──────────────────────────────────────────────────────────
  const deleteMutation = useMutation({
    mutationFn: (fileId: string) => deleteFile(fileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents", "mine"] });
      void message.success("文件已删除");
    },
    onError: (error: Error) => {
      const isPermissionDenied =
        error.message.includes("403") ||
        error.message.includes("管理员") ||
        error.message.toLowerCase().includes("forbidden");

      if (isPermissionDenied) {
        void message.error("管理员未开放删除权限");
      } else {
        void message.error(error.message || "删除失败");
      }
    },
  });

  const submitReviewMutation = useMutation({
    mutationFn: (fileId: string) => submitFileForReview(fileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents", "mine"] });
      void message.success("已提交审核");
    },
    onError: (error: Error) => {
      void message.error(error.message || "提交审核失败");
    },
  });

  // ── derived data ─────────────────────────────────────────────────────────────
  const allowedExtensions = useMemo(
    () => allowedExtensionsFromConfig(uploadConfigQuery.data?.items),
    [uploadConfigQuery.data?.items],
  );
  const files = filesQuery.data?.items ?? [];

  const filteredFiles = useMemo(() => {
    return files.filter((file) => {
      const normalizedKeyword = keyword.trim().toLowerCase();
      const keywordMatched =
        normalizedKeyword.length === 0 ||
        [file.original_name, file.description ?? "", file.mime_type].some((value) =>
          value.toLowerCase().includes(normalizedKeyword),
        );
      const statusMatched = statusFilter === "all" || file.status === statusFilter;
      const reviewMatched = reviewFilter === "all" || file.review_status === reviewFilter;

      // Local time range filter (server API does not support date params)
      let timeMatched = true;
      if (timeRange !== null && timeRange[0] !== null && timeRange[1] !== null) {
        const uploadedAt = dayjs(file.uploaded_at);
        timeMatched =
          uploadedAt.isAfter(timeRange[0].startOf("day")) &&
          uploadedAt.isBefore(timeRange[1].endOf("day"));
      }

      return keywordMatched && statusMatched && reviewMatched && timeMatched;
    });
  }, [files, keyword, reviewFilter, statusFilter, timeRange]);

  const metrics = useMemo<MyFilesMetric[]>(
    () => [
      {
        title: "我的文件",
        value: files.length,
        description: "已上传文件总数",
        icon: <FileTextOutlined />,
        tone: "primary",
      },
      {
        title: "审核通过",
        value: files.filter((file) => file.review_status === "approved").length,
        description: "可进入同步流程",
        icon: <CheckCircleOutlined />,
        tone: "success",
      },
      {
        title: "待审核",
        value: files.filter((file) => file.review_status === "pending").length,
        description: "等待管理员处理",
        icon: <WarningOutlined />,
        tone: "warning",
      },
      {
        title: "同步失败",
        value: files.filter((file) => syncStatus(file) === "failed").length,
        description: "需要重新处理",
        icon: <CloudSyncOutlined />,
        tone: "danger",
      },
    ],
    [files],
  );

  const tagOptions = useMemo(
    () =>
      (tagsQuery.data?.items ?? []).map((tag) => ({
        label: tag.name,
        value: tag.id,
      })),
    [tagsQuery.data],
  );

  // ── table columns ─────────────────────────────────────────────────────────────
  const columns: ColumnsType<KnowledgeFile> = [
    {
      title: "文件名",
      dataIndex: "original_name",
      key: "original_name",
      render: (value: string, record) => (
        <Space direction="vertical" size={2}>
          <Link to={`/files/${record.id}`}>{value}</Link>
          <Typography.Text type="secondary">{record.mime_type}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "文件状态",
      dataIndex: "status",
      key: "status",
      width: 140,
      render: (value: string) => <StatusTag kind="file" value={value} />,
    },
    {
      title: "审核状态",
      dataIndex: "review_status",
      key: "review_status",
      width: 120,
      render: (value: string) => <StatusTag kind="review" value={value} />,
    },
    {
      title: "同步状态",
      key: "sync_status",
      width: 120,
      render: (_, record) => <StatusTag kind="sync" value={syncStatus(record)} />,
    },
    {
      title: "大小",
      dataIndex: "size",
      key: "size",
      width: 120,
      render: (value: number) => formatFileSize(value),
    },
    {
      title: "上传时间",
      dataIndex: "uploaded_at",
      key: "uploaded_at",
      width: 180,
      render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      fixed: "right",
      render: (_, record) => (
        <Space size={4}>
          <Button
            type="text"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/files/${record.id}`)}
            aria-label="查看详情"
          />
          {canSubmitForReview(record) && (
            <Button
              type="text"
              icon={<SendOutlined />}
              loading={
                submitReviewMutation.isPending && submitReviewMutation.variables === record.id
              }
              onClick={() => submitReviewMutation.mutate(record.id)}
              aria-label={`提交审核 ${record.original_name}`}
            >
              提交审核
            </Button>
          )}
          <Popconfirm
            title="删除文件"
            description="确认删除该文件？此操作不可撤销。"
            okText="确定"
            cancelText="取消"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button
              type="text"
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending && deleteMutation.variables === record.id}
              aria-label={`删除 ${record.original_name}`}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── render ───────────────────────────────────────────────────────────────────
  return (
    <PageContainer
      title="我的文件"
      description="查看自己上传的文件、审核状态和存储元数据。"
      actions={
        <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => navigate("/upload")}>
          上传文件
        </Button>
      }
    >
      <div className="my-files-kpi-grid">
        {metrics.map((metric) => (
          <KpiCard
            key={metric.title}
            icon={metric.icon}
            title={metric.title}
            value={metric.value}
            description={metric.description}
            tone={metric.tone}
          />
        ))}
      </div>

      <Card className="document-panel table-card">
        <div className="table-section-header">
          <span className="table-section-header__copy">
            <Typography.Title level={4} className="table-section-header__title">
              文件列表
            </Typography.Title>
            <Typography.Text className="table-section-header__meta">
              当前显示 {filteredFiles.length} 个文件，共 {files.length} 个上传记录
            </Typography.Text>
          </span>
        </div>

        <div className="filter-toolbar filter-toolbar--management">
          <Input
            className="filter-toolbar__search"
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索文件名、说明或类型"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Select
            className="filter-toolbar__control"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { label: "文件状态：全部", value: "all" },
              { label: "待审核", value: "pending_review" },
              { label: "已审核", value: "approved" },
              { label: "分析完成", value: "analyzed" },
              { label: "失败", value: "failed" },
            ]}
          />
          <Select
            className="filter-toolbar__control"
            value={reviewFilter}
            onChange={setReviewFilter}
            options={[
              { label: "审核状态：全部", value: "all" },
              { label: "待审核", value: "pending" },
              { label: "审核中", value: "in_review" },
              { label: "已通过", value: "approved" },
              { label: "未通过", value: "rejected" },
            ]}
          />
          <Select
            className="filter-toolbar__control"
            allowClear
            placeholder="文件类型（扩展名）"
            value={extensionFilter}
            onChange={(value: string | undefined) => setExtensionFilter(value)}
            options={allowedExtensions.map((ext) => ({ label: ext, value: ext }))}
          />
          <Select
            className="filter-toolbar__control"
            allowClear
            placeholder="标签筛选"
            value={tagIdFilter}
            onChange={(value: string | undefined) => setTagIdFilter(value)}
            loading={tagsQuery.isLoading}
            options={tagOptions}
          />
          <RangePicker
            className="filter-toolbar__control filter-toolbar__control--wide"
            placeholder={["上传开始日期", "上传结束日期"]}
            value={timeRange}
            onChange={(dates) => setTimeRange(dates as [Dayjs | null, Dayjs | null] | null)}
          />
        </div>

        <Table<KnowledgeFile>
          rowKey="id"
          columns={columns}
          dataSource={filteredFiles}
          loading={filesQuery.isLoading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <span className="my-files-empty">
                    <Typography.Text strong>还没有符合条件的文件</Typography.Text>
                    <Typography.Text type="secondary">
                      上传知识文件后，可以在这里查看解析、审核和同步进度。
                    </Typography.Text>
                    <Button
                      type="primary"
                      icon={<CloudUploadOutlined />}
                      onClick={() => navigate("/upload")}
                    >
                      上传文件
                    </Button>
                  </span>
                }
              />
            ),
          }}
        />
      </Card>
    </PageContainer>
  );
}
