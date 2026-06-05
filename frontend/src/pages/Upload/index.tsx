import { App as AntdApp, Button, Card, Form, Input, Select, Space, Upload, Typography } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { UploadFile } from "antd/es/upload/interface";

import { type KnowledgeFile, uploadDocument } from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";

interface UploadFormValues {
  file?: UploadFile[];
  description?: string;
  visibility: KnowledgeFile["visibility"];
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
      title="文件上传"
      description="上传文件后进入审核队列，重复内容会自动识别并复用已有对象。"
    >
      <div className="document-workspace">
        <Card className="document-panel">
          <Form<UploadFormValues>
            form={form}
            layout="vertical"
            initialValues={{ visibility: "private" }}
            requiredMark={false}
            onFinish={(values) => mutation.mutate(values)}
          >
            <Form.Item
              label="文件"
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
                <p className="ant-upload-text">选择或拖入文件</p>
              </Upload.Dragger>
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
              <Input.TextArea rows={4} maxLength={2000} showCount />
            </Form.Item>

            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              上传文件
            </Button>
          </Form>
        </Card>

        {uploadedFile ? (
          <Card className="document-panel" title="最近上传">
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
      </div>
    </PageContainer>
  );
}
