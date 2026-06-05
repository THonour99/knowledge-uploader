import { Button, Card, Descriptions, Result, Space, Typography } from "antd";
import { ArrowLeftOutlined, CloudUploadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate, useParams } from "react-router-dom";

import { getDocument } from "../../api/client";
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

export default function FileDetailPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const fileQuery = useQuery({
    queryKey: ["documents", id],
    queryFn: () => getDocument(id ?? ""),
    enabled: Boolean(id),
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

  return (
    <PageContainer
      title={file?.original_name ?? "文件详情"}
      description="文件基础信息与审核同步状态。"
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
      <div className="document-workspace">
        <Card className="document-panel" loading={fileQuery.isLoading}>
          {file ? (
            <Descriptions column={1} size="middle" labelStyle={{ width: 140 }}>
              <Descriptions.Item label="文件状态">
                <Space wrap>
                  <StatusTag kind="file" value={file.status} />
                  <StatusTag kind="review" value={file.review_status} />
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="文件大小">{formatFileSize(file.size)}</Descriptions.Item>
              <Descriptions.Item label="MIME">{file.mime_type}</Descriptions.Item>
              <Descriptions.Item label="可见范围">{file.visibility}</Descriptions.Item>
              <Descriptions.Item label="上传时间">
                {dayjs(file.uploaded_at).format("YYYY-MM-DD HH:mm")}
              </Descriptions.Item>
              <Descriptions.Item label="说明">{file.description ?? "-"}</Descriptions.Item>
            </Descriptions>
          ) : null}
        </Card>

        <Card className="document-panel" title="同步信息" loading={fileQuery.isLoading}>
          {file ? (
            <Space direction="vertical" size={12} className="document-result">
              <Typography.Text>
                RAGFlow 文档：<Typography.Text code>{file.ragflow_document_id ?? "-"}</Typography.Text>
              </Typography.Text>
              <Typography.Text>
                解析状态：<Typography.Text code>{file.ragflow_parse_status ?? "-"}</Typography.Text>
              </Typography.Text>
              <Typography.Text>
                最近同步：
                {file.last_sync_at ? dayjs(file.last_sync_at).format("YYYY-MM-DD HH:mm") : "-"}
              </Typography.Text>
            </Space>
          ) : null}
        </Card>
      </div>
    </PageContainer>
  );
}
