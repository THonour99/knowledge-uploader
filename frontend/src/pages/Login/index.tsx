import { LockOutlined, MailOutlined } from "@ant-design/icons";
import { App as AntdApp, Button, Checkbox, Form, Input, Typography } from "antd";
import type { CheckboxChangeEvent } from "antd/es/checkbox";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { login } from "../../api/client";
import { defaultRouteForRole, useAuthStore } from "../../store/auth.store";
import { AuthLayout } from "../AuthLayout";

interface LoginFormValues {
  email: string;
  password: string;
  remember?: boolean;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const setSession = useAuthStore((state) => state.setSession);
  const [form] = Form.useForm<LoginFormValues>();

  const mutation = useMutation({
    mutationFn: (values: LoginFormValues) =>
      login({
        email: values.email,
        password: values.password,
        remember_me: Boolean(values.remember),
      }),
    onSuccess: (session) => {
      setSession(session.access_token, session.user);
      navigate(defaultRouteForRole[session.user.role], { replace: true });
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const handleRememberChange = (event: CheckboxChangeEvent) => {
    form.setFieldValue("remember", event.target.checked);
  };

  return (
    <AuthLayout
      title="欢迎登录"
      description="使用公司邮箱登录，进入知识库贡献与审核工作台"
      footer={
        <Typography.Text type="secondary">
          还没有账号？ <Link to="/register">立即注册</Link>
        </Typography.Text>
      }
    >
      <Form<LoginFormValues>
        form={form}
        className="auth-form"
        layout="vertical"
        initialValues={{ remember: true }}
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
          <Input
            placeholder="name@company.com"
            autoComplete="email"
            size="large"
            prefix={<MailOutlined />}
          />
        </Form.Item>

        <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password
            placeholder="请输入密码"
            autoComplete="current-password"
            size="large"
            prefix={<LockOutlined />}
          />
        </Form.Item>

        <div className="auth-form-row">
          <Form.Item name="remember" valuePropName="checked" noStyle>
            <Checkbox onChange={handleRememberChange}>记住我</Checkbox>
          </Form.Item>
          <Link to="/forgot-password">忘记密码</Link>
        </div>

        <Button type="primary" htmlType="submit" size="large" block loading={mutation.isPending}>
          登录
        </Button>
      </Form>
    </AuthLayout>
  );
}
