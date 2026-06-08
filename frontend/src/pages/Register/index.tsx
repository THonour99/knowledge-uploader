import {
  ApartmentOutlined,
  LockOutlined,
  MailOutlined,
  PhoneOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Button, Form, Input, Typography } from "antd";
import { Link } from "react-router-dom";

import { AuthLayout } from "../AuthLayout";

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
    <AuthLayout
      title="创建账号"
      description="提交公司邮箱与基础信息，管理员审核通过后即可上传知识文件"
      footer={
        <Typography.Text type="secondary">
          已有账号？ <Link to="/login">返回登录</Link>
        </Typography.Text>
      }
    >
      <Form<RegisterFormValues> className="auth-form" layout="vertical" requiredMark={false}>
        <div className="auth-form-grid">
          <Form.Item label="姓名" name="name" rules={[{ required: true, message: "请输入姓名" }]}>
            <Input size="large" placeholder="请输入姓名" prefix={<UserOutlined />} />
          </Form.Item>
          <Form.Item label="部门" name="department">
            <Input size="large" placeholder="请选择或填写部门" prefix={<ApartmentOutlined />} />
          </Form.Item>
        </div>
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
        <Form.Item label="手机号" name="phone">
          <Input size="large" placeholder="可选" prefix={<PhoneOutlined />} />
        </Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password size="large" placeholder="请输入密码" prefix={<LockOutlined />} />
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
          <Input.Password size="large" placeholder="请再次输入密码" prefix={<LockOutlined />} />
        </Form.Item>
        <Button type="primary" size="large" block>
          提交注册
        </Button>
      </Form>
    </AuthLayout>
  );
}
