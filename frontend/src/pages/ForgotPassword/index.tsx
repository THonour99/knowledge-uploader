import { Button, Card, Form, Input, Space, Typography } from "antd";
import { Link } from "react-router-dom";

interface ForgotPasswordFormValues {
  email: string;
}

export default function ForgotPasswordPage() {
  return (
    <div className="auth-page">
      <Card className="auth-card">
        <Space direction="vertical" size={24} className="auth-card__content">
          <div>
            <Typography.Title level={2} className="auth-title">
              忘记密码
            </Typography.Title>
            <Typography.Paragraph className="auth-description">
              如邮箱已注册，会收到密码重置邮件
            </Typography.Paragraph>
          </div>

          <Form<ForgotPasswordFormValues> layout="vertical" requiredMark={false}>
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
            <Button type="primary" size="large" block>
              发送邮件
            </Button>
          </Form>

          <Link to="/login">返回登录</Link>
        </Space>
      </Card>
    </div>
  );
}
