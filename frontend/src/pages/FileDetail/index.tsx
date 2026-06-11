import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Result,
  Space,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { ArrowLeftOutlined, CloudUploadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate, useParams } from "react-router-dom";

import { type FileAnalysis, type SyncTask, getDocument, listTasks } from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { Roles, useAuthStore } from "../../store/auth.store";

const TASK_TYPE_LABELS: Record<string, string> = {
  ragflow_upload: "RAGFlow 上传",
  ragflow_parse: "RAGFlow 解析",
  ragflow_status_check: "RAGFlow 状态检查",
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

interface AnalysisCardProps {
  analysis: FileAnalysis;
  loading: boolean;
}

function AnalysisCard({ analysis, loading }: AnalysisCardProps) {
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
        <Descriptions column={1} size="middle" labelStyle={{ width: 140 }}>
          <Descriptions.Item label="风险等级">
            <StatusTag kind="risk" value={analysis.sensitive_risk_level} />
          </Descriptions.Item>
          <Descriptions.Item label="摘要">{analysis.summary ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="完成时间">
            {analysis.finished_at ? dayjs(analysis.finished_at).format("YYYY-MM-DD HH:mm") : "-"}
          </Descriptions.Item>
        </Descriptions>
        <Collapse
          items={[
            {
              key: "extracted-text-preview",
              label: "提取文本预览",
              children: (
                <Typography.Paragraph className="document-extracted-preview">
                  {analysis.extracted_text_preview ?? "暂无提取文本"}
                </Typography.Paragraph>
              ),
            },
          ]}
        />
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
  const isAdmin = role === Roles.KNOWLEDGE_ADMIN || role === Roles.SYSTEM_ADMIN;
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

  return (
    <PageContainer
      title={file?.original_name ?? "文件详情"}
      description="文件基础信息、AI 分析结果与审核同步状态。"
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

        <Card className="document-panel" title="分类与标签" loading={fileQuery.isLoading}>
          {file ? (
            <Descriptions column={1} size="middle" labelStyle={{ width: 140 }}>
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

        {file?.analysis ? (
          <AnalysisCard analysis={file.analysis} loading={fileQuery.isLoading} />
        ) : null}

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
                解析状态：<Typography.Text code>{file.ragflow_parse_status ?? "-"}</Typography.Text>
              </Typography.Text>
              <Typography.Text>
                最近同步：
                {file.last_sync_at ? dayjs(file.last_sync_at).format("YYYY-MM-DD HH:mm") : "-"}
              </Typography.Text>
            </Space>
          ) : null}
        </Card>

        {isAdmin ? <TaskTimelineCard tasks={fileTasks} loading={tasksQuery.isLoading} /> : null}
      </div>
    </PageContainer>
  );
}
