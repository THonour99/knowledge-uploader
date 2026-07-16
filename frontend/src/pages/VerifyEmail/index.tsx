import { useEffect, useState } from "react";
import { App as AntdApp, Button, Form, Input, Result } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { resendVerification, verifyEmail } from "../../api/client";
import { AuthLayout } from "../AuthLayout";

interface ResendFormValues {
  email: string;
}

export default function VerifyEmailPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token")?.trim() ?? "";
  const verificationComplete = Boolean(
    (location.state as { emailVerified?: boolean } | null)?.emailVerified,
  );
  const { message } = AntdApp.useApp();
  const [resent, setResent] = useState(false);

  const verifyQuery = useQuery({
    queryKey: ["auth", "verify-email", token],
    queryFn: () => verifyEmail({ token }),
    enabled: token.length > 0,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  useEffect(() => {
    if (verifyQuery.isSuccess && token) {
      navigate("/verify-email", { replace: true, state: { emailVerified: true } });
    }
  }, [navigate, token, verifyQuery.isSuccess]);

  const resendMutation = useMutation({
    mutationFn: (values: ResendFormValues) => resendVerification(values),
    onSuccess: () => {
      setResent(true);
      message.success("如账号存在且仍待验证，验证邮件已重新发送");
    },
    onError: (error: Error) => {
      message.error(error.message || "验证邮件发送失败");
    },
  });

  const content = (() => {
    if (verificationComplete || verifyQuery.isSuccess) {
      return (
        <Result
          status="success"
          title="邮箱验证成功"
          subTitle="账号验证已完成，现在可以登录知识工作台。"
          extra={
            <Button type="primary" onClick={() => navigate("/login", { replace: true })}>
              前往登录
            </Button>
          }
        />
      );
    }

    if (!token) {
      return (
        <Result
          status="warning"
          title="验证链接缺少令牌"
          subTitle="请使用验证邮件中的完整链接，或重新发送验证邮件。"
        />
      );
    }

    if (verifyQuery.isPending) {
      return <Result status="info" title="正在验证邮箱" subTitle="请稍候，不要重复打开链接。" />;
    }

    return (
      <Result
        status="error"
        title="验证链接无效或已失效"
        subTitle="验证令牌只能使用一次。若仍无法登录，请重新发送验证邮件。"
      />
    );
  })();

  return (
    <AuthLayout
      title="验证邮箱"
      description="完成邮箱验证后才能登录并使用知识库工作台"
      footer={<Link to="/login">返回登录</Link>}
    >
      {content}
      {verifyQuery.isError || (!token && !verificationComplete) ? (
        <Form<ResendFormValues>
          className="auth-form auth-form--resend"
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => resendMutation.mutate(values)}
        >
          <Form.Item
            label="公司邮箱"
            name="email"
            rules={[
              { required: true, message: "请输入公司邮箱" },
              { type: "email", message: "请输入有效邮箱" },
            ]}
          >
            <Input autoComplete="email" placeholder="name@company.com" size="large" />
          </Form.Item>
          <Button block htmlType="submit" loading={resendMutation.isPending} disabled={resent}>
            {resent ? "验证邮件已发送" : "重新发送验证邮件"}
          </Button>
        </Form>
      ) : null}
    </AuthLayout>
  );
}
