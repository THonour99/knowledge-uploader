import { Alert, App as AntdApp, Button, Form, Input, Select, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { listRegistrationDepartments, register } from "../../api/client";
import { AuthLayout } from "../AuthLayout";

interface RegisterFormValues {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
  department_id: string;
  phone?: string;
}

export default function RegisterPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();

  const departmentsQuery = useQuery({
    queryKey: ["auth", "registration-departments"],
    queryFn: listRegistrationDepartments,
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const mutation = useMutation({
    mutationFn: (values: RegisterFormValues) =>
      register({
        name: values.name,
        email: values.email,
        password: values.password,
        department_id: values.department_id,
        phone: values.phone?.trim() ? values.phone.trim() : undefined,
      }),
    onSuccess: (_response, values) => {
      message.success("注册已受理；如系统启用邮箱验证，请先完成邮箱验证");
      navigate("/login", {
        replace: true,
        state: { registeredEmail: values.email },
      });
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  return (
    <AuthLayout
      title="创建账号"
      description="选择真实归属部门并使用公司邮箱注册"
      footer={
        <Typography.Text type="secondary">
          已有账号？ <Link to="/login">返回登录</Link>
        </Typography.Text>
      }
    >
      <Form<RegisterFormValues>
        className="auth-form"
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => mutation.mutate(values)}
      >
        <Form.Item label="姓名" name="name" rules={[{ required: true, message: "请输入姓名" }]}>
          <Input size="large" placeholder="请输入姓名" autoComplete="name" />
        </Form.Item>
        <Form.Item
          label="归属部门"
          name="department_id"
          extra="部门用于审核权限和知识库归属；未分配部门的账号不能上传或提交文档。"
          rules={[{ required: true, message: "请选择归属部门" }]}
        >
          <Select
            size="large"
            showSearch
            optionFilterProp="label"
            placeholder="请选择部门"
            loading={departmentsQuery.isLoading}
            disabled={departmentsQuery.isError}
            options={(departmentsQuery.data ?? []).map((department) => ({
              label: `${department.name}（${department.code}）`,
              value: department.id,
            }))}
          />
        </Form.Item>
        {departmentsQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message="部门列表加载失败"
            description="暂时无法安全完成注册，请重试后再提交。"
            action={
              <Button size="small" onClick={() => void departmentsQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : null}
        {departmentsQuery.isSuccess && departmentsQuery.data.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="暂无可注册部门"
            description="当前没有开放注册的部门，请联系系统管理员完成部门配置。"
          />
        ) : null}
        <Form.Item
          label="公司邮箱"
          name="email"
          rules={[
            { required: true, message: "请输入公司邮箱" },
            { type: "email", message: "请输入有效邮箱" },
          ]}
        >
          <Input size="large" placeholder="name@company.com" autoComplete="email" />
        </Form.Item>
        <Form.Item label="手机号" name="phone">
          <Input size="large" placeholder="可选" autoComplete="tel" />
        </Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password size="large" placeholder="请输入密码" autoComplete="new-password" />
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
          <Input.Password size="large" placeholder="请再次输入密码" autoComplete="new-password" />
        </Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          size="large"
          block
          disabled={
            departmentsQuery.isLoading ||
            departmentsQuery.isError ||
            (departmentsQuery.data?.length ?? 0) === 0
          }
          loading={mutation.isPending}
        >
          提交注册
        </Button>
      </Form>
    </AuthLayout>
  );
}
