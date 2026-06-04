import { Button, Card, Form, Input, Space, Typography } from "antd";
import { Link, useParams } from "react-router-dom";

interface ResetPasswordFormValues {
  password: string;
  confirmPassword: string;
}

export default function ResetPasswordPage() {
  const { token } = useParams();
  const hasToken = Boolean(token);

  return (
    <div className="auth-page">
      <Card className="auth-card">
        <Space direction="vertical" size={24} className="auth-card__content">
          <div>
            <Typography.Title level={2} className="auth-title">
              重置密码
            </Typography.Title>
            <Typography.Paragraph className="auth-description">
              设置新的登录密码
            </Typography.Paragraph>
          </div>

          <Form<ResetPasswordFormValues> layout="vertical" requiredMark={false} disabled={!hasToken}>
            <Form.Item
              label="新密码"
              name="password"
              rules={[{ required: true, message: "请输入新密码" }]}
            >
              <Input.Password size="large" placeholder="请输入新密码" />
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
              <Input.Password size="large" placeholder="请再次输入新密码" />
            </Form.Item>
            <Button type="primary" size="large" block disabled={!hasToken}>
              重置密码
            </Button>
          </Form>

          <Link to="/login">返回登录</Link>
        </Space>
      </Card>
    </div>
  );
}
