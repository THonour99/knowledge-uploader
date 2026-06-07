import { useMemo, useState, type ReactNode } from "react";
import { Button, Card, Input, Select, Space, Table, Typography } from "antd";
import {
  CheckCircleOutlined,
  CloudSyncOutlined,
  CloudUploadOutlined,
  EyeOutlined,
  FileTextOutlined,
  SearchOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { Link, useNavigate } from "react-router-dom";
import type { ColumnsType } from "antd/es/table";

import { type KnowledgeFile, listDocuments } from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";

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

interface MyFilesMetric {
  title: string;
  value: string;
  description: string;
  icon: ReactNode;
  tone: "primary" | "success" | "warning" | "danger";
}

export default function MyFilesPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [reviewFilter, setReviewFilter] = useState("all");
  const filesQuery = useQuery({
    queryKey: ["documents", "mine"],
    queryFn: listDocuments,
  });

  const files = filesQuery.data?.items ?? [];
  const filteredFiles = useMemo(
    () =>
      files.filter((file) => {
        const normalizedKeyword = keyword.trim().toLowerCase();
        const keywordMatched =
          normalizedKeyword.length === 0 ||
          [file.original_name, file.description ?? "", file.mime_type].some((value) =>
            value.toLowerCase().includes(normalizedKeyword),
          );
        const statusMatched = statusFilter === "all" || file.status === statusFilter;
        const reviewMatched = reviewFilter === "all" || file.review_status === reviewFilter;
        return keywordMatched && statusMatched && reviewMatched;
      }),
    [files, keyword, reviewFilter, statusFilter],
  );

  const metrics = useMemo<MyFilesMetric[]>(
    () => [
      {
        title: "我的文件",
        value: String(files.length),
        description: "已上传文件总数",
        icon: <FileTextOutlined />,
        tone: "primary",
      },
      {
        title: "审核通过",
        value: String(files.filter((file) => file.review_status === "approved").length),
        description: "可进入同步流程",
        icon: <CheckCircleOutlined />,
        tone: "success",
      },
      {
        title: "待审核",
        value: String(files.filter((file) => file.review_status === "pending").length),
        description: "等待管理员处理",
        icon: <WarningOutlined />,
        tone: "warning",
      },
      {
        title: "同步失败",
        value: String(files.filter((file) => syncStatus(file) === "failed").length),
        description: "需要重新处理",
        icon: <CloudSyncOutlined />,
        tone: "danger",
      },
    ],
    [files],
  );

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
      width: 96,
      render: (_, record) => (
        <Button
          type="text"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/files/${record.id}`)}
          aria-label="查看详情"
        />
      ),
    },
  ];

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
          <Card className="my-files-kpi-card" key={metric.title}>
            <div className="my-files-kpi-card__body">
              <span className={`my-files-kpi-card__icon my-files-kpi-card__icon--${metric.tone}`}>
                {metric.icon}
              </span>
              <span className="my-files-kpi-card__copy">
                <Typography.Text type="secondary">{metric.title}</Typography.Text>
                <Typography.Title level={3}>{metric.value}</Typography.Title>
                <Typography.Text type="secondary">{metric.description}</Typography.Text>
              </span>
            </div>
          </Card>
        ))}
      </div>

      <Card className="document-panel table-card">
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
        </div>
        <Table<KnowledgeFile>
          rowKey="id"
          columns={columns}
          dataSource={filteredFiles}
          loading={filesQuery.isLoading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{ emptyText: "暂无文件" }}
        />
      </Card>
    </PageContainer>
  );
}
