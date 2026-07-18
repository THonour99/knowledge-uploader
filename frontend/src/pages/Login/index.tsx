import { useState } from "react";
import { Alert, App as AntdApp, Button, Form, Input, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";

import { isApiError, login, resendVerification } from "../../api/client";
import { useAuthStore } from "../../store/auth.store";
import { AuthLayout } from "../AuthLayout";

interface LoginFormValues {
  email: string;
  password: string;
}

export default function LoginPage() {
  const location = useLocation();
  const { message } = AntdApp.useApp();
  const setSession = useAuthStore((state) => state.setSession);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);
  const registeredEmail =
    typeof location.state === "object" &&
    location.state !== null &&
    "registeredEmail" in location.state &&
    typeof location.state.registeredEmail === "string"
      ? location.state.registeredEmail
      : undefined;

  const resendMutation = useMutation({
    mutationFn: (email: string) => resendVerification({ email }),
    onSuccess: () => {
      message.success("如账号存在且仍待验证，验证邮件已重新发送");
    },
    onError: (error: Error) => {
      message.error(error.message || "验证邮件发送失败");
    },
  });

  const mutation = useMutation({
    mutationFn: (values: LoginFormValues) =>
      login({
        email: values.email,
        password: values.password,
      }),
    onSuccess: (session) => {
      setUnverifiedEmail(null);
      setSession(session.access_token, session.user);
    },
    onError: (error, values) => {
      if (isApiError(error) && error.code === "EMAIL_NOT_VERIFIED") {
        setUnverifiedEmail(values.email);
        return;
      }
      setUnverifiedEmail(null);
      message.error(error instanceof Error ? error.message : "登录失败");
    },
  });

  return (
    <AuthLayout
      title="欢迎回来"
      description="使用公司邮箱登录知识库工作台"
      footer={
        <Typography.Text type="secondary">
          还没有账号？ <Link to="/register">立即注册</Link>
        </Typography.Text>
      }
    >
      <Form<LoginFormValues>
        className="auth-form"
        layout="vertical"
        initialValues={{ email: registeredEmail }}
        onFinish={(values) => mutation.mutate(values)}
        requiredMark={false}
      >
        <Form.Item
          label="公司邮箱"
          name="email"
          rules={[
            { required: true, message: "请输入公司邮箱" },
            { type: "email", message: "请输入有效邮箱" },
          ]}
        >
          <Input placeholder="name@company.com" autoComplete="email" size="large" />
        </Form.Item>

        <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password placeholder="请输入密码" autoComplete="current-password" size="large" />
        </Form.Item>

        {unverifiedEmail ? (
          <Alert
            type="warning"
            showIcon
            message="邮箱尚未验证"
            description="完成邮箱验证后才能登录。验证邮件失效或未收到时，可以重新发送。"
            action={
              <Button
                size="small"
                onClick={() => resendMutation.mutate(unverifiedEmail)}
                loading={resendMutation.isPending}
              >
                重新发送
              </Button>
            }
          />
        ) : null}

        <div className="auth-form-row">
          <Link to="/forgot-password">忘记密码</Link>
        </div>

        <Button type="primary" htmlType="submit" size="large" block loading={mutation.isPending}>
          登录
        </Button>
      </Form>
    </AuthLayout>
  );
}
