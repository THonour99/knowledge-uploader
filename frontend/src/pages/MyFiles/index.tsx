import { Button, Card, Space, Table, Typography } from "antd";
import { CloudUploadOutlined, EyeOutlined } from "@ant-design/icons";
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

export default function MyFilesPage() {
  const navigate = useNavigate();
  const filesQuery = useQuery({
    queryKey: ["documents", "mine"],
    queryFn: listDocuments,
  });

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
      <Card className="document-panel">
        <Table<KnowledgeFile>
          rowKey="id"
          columns={columns}
          dataSource={filesQuery.data?.items ?? []}
          loading={filesQuery.isLoading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{ emptyText: "暂无文件" }}
        />
      </Card>
    </PageContainer>
  );
}
