import { LockOutlined } from "@ant-design/icons";
import { App as AntdApp, Button, Form, Input, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { resetPassword } from "../../api/client";
import { AuthLayout } from "../AuthLayout";

interface ResetPasswordFormValues {
  password: string;
  confirmPassword: string;
}

export default function ResetPasswordPage() {
  const { token } = useParams();
  const hasToken = Boolean(token);
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();

  const mutation = useMutation({
    mutationFn: (values: ResetPasswordFormValues) =>
      resetPassword({ token: token ?? "", new_password: values.password }),
    onSuccess: () => {
      message.success("密码重置成功，请使用新密码登录");
      navigate("/login", { replace: true });
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  return (
    <AuthLayout
      title="重置密码"
      description="设置新的登录密码，完成后返回登录页继续访问平台"
      footer={
        <>
          {!hasToken ? (
            <Typography.Text type="secondary">当前链接缺少重置令牌，请重新发起找回密码。</Typography.Text>
          ) : null}
          <Link to="/login">返回登录</Link>
        </>
      }
    >
      <Form<ResetPasswordFormValues>
        className="auth-form"
        layout="vertical"
        requiredMark={false}
        disabled={!hasToken}
        onFinish={(values) => mutation.mutate(values)}
      >
        <Form.Item label="新密码" name="password" rules={[{ required: true, message: "请输入新密码" }]}>
          <Input.Password size="large" placeholder="请输入新密码" prefix={<LockOutlined />} />
        </Form.Item>
        <Form.Item
          label="确认密码"
          name="confirmPassword"
          dependencies={["password"]}
          rules={[
            { required: true, message: "请再次输入新密码" },
            ({ getFieldValue }) => ({
              validator(_, value: string | undefined) {
                if (!value || getFieldValue("password") === value) {
                  return Promise.resolve();
                }

                return Promise.reject(new Error("两次输入的密码不一致"));
              },
            }),
          ]}
        >
          <Input.Password size="large" placeholder="请再次输入新密码" prefix={<LockOutlined />} />
        </Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          size="large"
          block
          disabled={!hasToken}
          loading={mutation.isPending}
        >
          重置密码
        </Button>
      </Form>
    </AuthLayout>
  );
}
