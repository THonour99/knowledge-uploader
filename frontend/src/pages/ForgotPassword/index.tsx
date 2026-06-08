import { MailOutlined } from "@ant-design/icons";
import { Button, Form, Input } from "antd";
import { Link } from "react-router-dom";

import { AuthLayout } from "../AuthLayout";

interface ForgotPasswordFormValues {
  email: string;
}

export default function ForgotPasswordPage() {
  return (
    <AuthLayout
      title="找回密码"
      description="输入公司邮箱后，如账号存在会收到一次性重置链接"
      footer={<Link to="/login">返回登录</Link>}
    >
      <Form<ForgotPasswordFormValues> className="auth-form" layout="vertical" requiredMark={false}>
        <Form.Item
          label="公司邮箱"
          name="email"
          rules={[
            { required: true, message: "请输入公司邮箱" },
            { type: "email", message: "请输入有效邮箱" },
          ]}
        >
          <Input size="large" placeholder="name@company.com" prefix={<MailOutlined />} />
        </Form.Item>
        <Button type="primary" size="large" block>
          发送重置邮件
        </Button>
      </Form>
    </AuthLayout>
  );
}
