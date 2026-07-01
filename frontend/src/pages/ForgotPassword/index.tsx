import { App as AntdApp, Button, Form, Input } from "antd";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { forgotPassword } from "../../api/client";
import { AuthLayout } from "../AuthLayout";

interface ForgotPasswordFormValues {
  email: string;
}

export default function ForgotPasswordPage() {
  const { message } = AntdApp.useApp();

  const mutation = useMutation({
    mutationFn: (values: ForgotPasswordFormValues) => forgotPassword({ email: values.email }),
    onSuccess: () => {
      message.success("重置邮件已发送，如账号存在请查收邮箱");
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  return (
    <AuthLayout
      title="找回密码"
      description="输入公司邮箱后，如账号存在会收到一次性重置链接"
      footer={<Link to="/login">返回登录</Link>}
    >
      <Form<ForgotPasswordFormValues>
        className="auth-form"
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => mutation.mutate(values)}
      >
        <Form.Item
          label="公司邮箱"
          name="email"
          rules={[
            { required: true, message: "请输入公司邮箱" },
            { type: "email", message: "请输入有效邮箱" },
          ]}
        >
          <Input size="large" placeholder="name@company.com" />
        </Form.Item>
        <Button type="primary" htmlType="submit" size="large" block loading={mutation.isPending}>
          发送重置邮件
        </Button>
      </Form>
    </AuthLayout>
  );
}
