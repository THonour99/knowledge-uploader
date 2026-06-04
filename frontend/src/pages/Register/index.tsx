import { Button, Card, Form, Input, Space, Typography } from "antd";
import { Link } from "react-router-dom";

interface RegisterFormValues {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
  department?: string;
  phone?: string;
}

export default function RegisterPage() {
  return (
    <div className="auth-page">
      <Card className="auth-card">
        <Space direction="vertical" size={24} className="auth-card__content">
          <div>
            <Typography.Title level={2} className="auth-title">
              注册账号
            </Typography.Title>
            <Typography.Paragraph className="auth-description">
              仅支持公司邮箱注册，例如 name@company.com
            </Typography.Paragraph>
          </div>

          <Form<RegisterFormValues> layout="vertical" requiredMark={false}>
            <Form.Item label="姓名" name="name" rules={[{ required: true, message: "请输入姓名" }]}>
              <Input size="large" placeholder="请输入姓名" />
            </Form.Item>
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
            <Form.Item
              label="密码"
              name="password"
              rules={[{ required: true, message: "请输入密码" }]}
            >
              <Input.Password size="large" placeholder="请输入密码" />
            </Form.Item>
            <Form.Item
              label="确认密码"
              name="confirmPassword"
              dependencies={["password"]}
              rules={[
                { required: true, message: "请再次输入密码" },
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
              <Input.Password size="large" placeholder="请再次输入密码" />
            </Form.Item>
            <Form.Item label="部门" name="department">
              <Input size="large" placeholder="可选" />
            </Form.Item>
            <Button type="primary" size="large" block>
              注册
            </Button>
          </Form>

          <Typography.Text type="secondary">
            已有账号？ <Link to="/login">返回登录</Link>
          </Typography.Text>
        </Space>
      </Card>
    </div>
  );
}
