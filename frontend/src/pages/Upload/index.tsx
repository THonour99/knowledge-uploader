import {
  App as AntdApp,
  Button,
  Card,
  Form,
  Input,
  Progress,
  Select,
  Space,
  Switch,
  Upload,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  FileTextOutlined,
  InboxOutlined,
  InfoCircleOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { UploadFile } from "antd/es/upload/interface";

import { type KnowledgeFile, uploadDocument } from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";

interface UploadFormValues {
  file?: UploadFile[];
  title?: string;
  category?: string;
  dataset?: string;
  tags?: string[];
  description?: string;
  visibility: KnowledgeFile["visibility"];
  submitAfterUpload?: boolean;
  aiAnalyze?: boolean;
}

function normalizeUploadFile(event: { fileList?: UploadFile[] } | UploadFile[]): UploadFile[] {
  if (Array.isArray(event)) {
    return event;
  }

  return event.fileList ?? [];
}

export default function UploadPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm<UploadFormValues>();
  const [uploadedFile, setUploadedFile] = useState<KnowledgeFile | null>(null);
  const selectedFiles = Form.useWatch("file", form) ?? [];

  const mutation = useMutation({
    mutationFn: (values: UploadFormValues) => {
      const selectedFile = values.file?.[0]?.originFileObj;

      if (!selectedFile) {
        throw new Error("请选择文件");
      }

      return uploadDocument({
        file: selectedFile,
        description: values.description,
        visibility: values.visibility,
      });
    },
    onSuccess: (file) => {
      setUploadedFile(file);
      message.success(file.duplicate ? "已识别重复文件" : "上传成功");
      form.resetFields();
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  return (
    <PageContainer
      title="上传知识文件"
      description="上传文件后进入校验、去重、AI 分析与管理员审核流程。"
    >
      <Form<UploadFormValues>
        form={form}
        className="upload-workspace"
        layout="vertical"
        initialValues={{
          visibility: "private",
          submitAfterUpload: true,
          aiAnalyze: true,
        }}
        requiredMark={false}
        onFinish={(values) => mutation.mutate(values)}
      >
        <div className="upload-main">
          <Card
            className="document-panel upload-drop-card"
            title={
              <Space>
                <CloudUploadOutlined />
                选择文件
              </Space>
            }
          >
            <Form.Item
              name="file"
              valuePropName="fileList"
              getValueFromEvent={normalizeUploadFile}
              rules={[{ required: true, message: "请选择文件" }]}
            >
              <Upload.Dragger
                maxCount={1}
                multiple={false}
                beforeUpload={() => false}
                accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv"
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">拖拽文件到此处，或点击选择文件</p>
                <p className="ant-upload-hint">
                  支持 PDF、Word、Excel、PPT、TXT、Markdown、CSV，上传后自动校验与去重。
                </p>
              </Upload.Dragger>
            </Form.Item>
          </Card>

          <Card className="document-panel upload-queue-card" title="上传队列">
            {selectedFiles.length > 0 ? (
              selectedFiles.map((file) => (
                <div className="upload-queue-row" key={file.uid}>
                  <span className="upload-queue-row__icon">
                    <FileTextOutlined />
                  </span>
                  <span className="upload-queue-row__copy">
                    <Typography.Text strong>{file.name}</Typography.Text>
                    <Typography.Text type="secondary">等待开始上传</Typography.Text>
                    <Progress percent={mutation.isPending ? 62 : 0} size="small" status="active" />
                  </span>
                  <StatusTag kind="sync" value={mutation.isPending ? "syncing" : "queued"} />
                </div>
              ))
            ) : (
              <div className="upload-empty-queue">
                <InfoCircleOutlined />
                <Typography.Text type="secondary">选择文件后会显示待上传队列。</Typography.Text>
              </div>
            )}
          </Card>

          <Card className="document-panel upload-tips-card" title="上传提示">
            <div className="upload-tip-list">
              <Space>
                <CheckCircleOutlined className="upload-tip-list__icon" />
                <Typography.Text>重复文件会复用已有对象，避免重复占用存储。</Typography.Text>
              </Space>
              <Space>
                <CheckCircleOutlined className="upload-tip-list__icon" />
                <Typography.Text>严重敏感内容默认不会同步到 RAGFlow。</Typography.Text>
              </Space>
              <Space>
                <CheckCircleOutlined className="upload-tip-list__icon" />
                <Typography.Text>审核通过后才会进入 RAGFlow 同步队列。</Typography.Text>
              </Space>
            </div>
          </Card>
        </div>

        <Card
          className="document-panel upload-meta-card"
          title={
            <Space>
              <TagsOutlined />
              文件信息
            </Space>
          }
        >
          <Form.Item label="知识标题" name="title">
            <Input placeholder="默认使用文件名，可在此补充业务标题" />
          </Form.Item>
          <Form.Item label="知识分类" name="category">
            <Select
              placeholder="请选择分类"
              options={[
                { label: "产品资料", value: "product" },
                { label: "技术支持", value: "support" },
                { label: "制度流程", value: "process" },
                { label: "市场素材", value: "marketing" },
              ]}
            />
          </Form.Item>
          <Form.Item label="目标 Dataset" name="dataset">
            <Select
              placeholder="审核后同步的 RAGFlow Dataset"
              options={[
                { label: "客服机器人知识库", value: "customer-service" },
                { label: "技术支持知识库", value: "support" },
                { label: "员工制度知识库", value: "employee" },
              ]}
            />
          </Form.Item>
          <Form.Item label="标签" name="tags">
            <Select
              mode="tags"
              placeholder="输入标签后回车"
              options={[
                { label: "FAQ", value: "FAQ" },
                { label: "产品", value: "产品" },
                { label: "流程", value: "流程" },
              ]}
            />
          </Form.Item>
          <Form.Item label="可见范围" name="visibility">
            <Select
              options={[
                { label: "仅自己", value: "private" },
                { label: "同部门", value: "department" },
                { label: "全公司", value: "company" },
              ]}
            />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input.TextArea rows={5} maxLength={2000} showCount placeholder="补充用途、来源或审核备注" />
          </Form.Item>
          <div className="upload-switch-grid">
            <Form.Item name="submitAfterUpload" valuePropName="checked">
              <Switch checkedChildren="上传后提交审核" unCheckedChildren="保存草稿" />
            </Form.Item>
            <Form.Item name="aiAnalyze" valuePropName="checked">
              <Switch checkedChildren="启用 AI 分析" unCheckedChildren="跳过 AI" />
            </Form.Item>
          </div>
          <Space className="upload-actions">
            <Button>保存草稿</Button>
            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              开始上传
            </Button>
          </Space>
        </Card>
      </Form>

      {uploadedFile ? (
        <Card className="document-panel upload-result-card" title="最近上传">
          <Space direction="vertical" size={12} className="document-result">
            <Typography.Text strong>{uploadedFile.original_name}</Typography.Text>
            <Space wrap>
              <StatusTag kind="file" value={uploadedFile.status} />
              <StatusTag kind="review" value={uploadedFile.review_status} />
              {uploadedFile.duplicate ? <StatusTag kind="sync" value="not_synced" /> : null}
            </Space>
            <Typography.Text type="secondary">
              {uploadedFile.duplicate ? "已识别为本人重复上传" : "已保存并等待后续审核"}
            </Typography.Text>
            <Button
              type="link"
              className="document-link-button"
              onClick={() => navigate(`/files/${uploadedFile.id}`)}
            >
              查看详情
            </Button>
          </Space>
        </Card>
      ) : null}
    </PageContainer>
  );
}
