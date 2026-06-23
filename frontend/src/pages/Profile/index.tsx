import { App as AntdApp, Button, Card, Col, Descriptions, Form, Input, Row, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";

import { type ChangePasswordRequest, changePassword, getMe } from "../../api/client";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { Roles } from "../../store/auth.store";
import { colors, spacing } from "../../theme/tokens";

const ROLE_LABELS: Record<string, string> = {
  [Roles.EMPLOYEE]: "普通员工",
  [Roles.DEPT_ADMIN]: "部门管理员",
  [Roles.SYSTEM_ADMIN]: "系统管理员",
};

interface PasswordFormValues {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

export default function ProfilePage() {
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm<PasswordFormValues>();

  const { data: profile, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
  });

  const { mutate: doChangePassword, isPending } = useMutation({
    mutationFn: (payload: ChangePasswordRequest) => changePassword(payload),
    onSuccess: () => {
      void message.success("下次登录请使用新密码");
      form.resetFields();
    },
    onError: () => {
      void message.error("修改密码失败，请检查当前密码是否正确");
    },
  });

  function handleSubmit(values: PasswordFormValues) {
    doChangePassword({
      current_password: values.current_password,
      new_password: values.new_password,
    });
  }

  const emailVerifiedValue = profile?.email_verified ? "active" : "pending_email_verification";

  return (
    <PageContainer title="个人中心">
      <Row gutter={[spacing.sectionGap, spacing.sectionGap]}>
        <Col xs={24} lg={12}>
          <Card
            title="个人资料"
            loading={isLoading}
            style={{ borderRadius: 12, borderColor: colors.border }}
          >
            {profile ? (
              <Descriptions column={1} styles={{ label: { color: colors.textSecondary } }}>
                <Descriptions.Item label="姓名">
                  <Typography.Text strong>{profile.name}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="邮箱">{profile.email}</Descriptions.Item>
                <Descriptions.Item label="角色">
                  {ROLE_LABELS[profile.role] ?? profile.role}
                </Descriptions.Item>
                <Descriptions.Item label="部门">
                  {profile.department ?? "—"}
                </Descriptions.Item>
                <Descriptions.Item label="手机">
                  {profile.phone ?? "—"}
                </Descriptions.Item>
                <Descriptions.Item label="邮箱状态">
                  <StatusTag kind="user" value={emailVerifiedValue} />
                </Descriptions.Item>
              </Descriptions>
            ) : null}
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            title="修改密码"
            style={{ borderRadius: 12, borderColor: colors.border }}
          >
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSubmit}
              autoComplete="off"
            >
              <Form.Item
                label="当前密码"
                name="current_password"
                rules={[{ required: true, message: "请输入当前密码" }]}
              >
                <Input.Password placeholder="请输入当前密码" autoComplete="current-password" />
              </Form.Item>

              <Form.Item
                label="新密码"
                name="new_password"
                rules={[
                  { required: true, message: "请输入新密码" },
                  { min: 8, message: "密码长度不能少于 8 位" },
                  ({ getFieldValue }) => ({
                    validator(_, value: string) {
                      if (!value || getFieldValue("current_password") !== value) {
                        return Promise.resolve();
                      }
                      return Promise.reject(new Error("新密码不能与当前密码相同"));
                    },
                  }),
                ]}
              >
                <Input.Password placeholder="请输入新密码（至少 8 位）" autoComplete="new-password" />
              </Form.Item>

              <Form.Item
                label="确认新密码"
                name="confirm_password"
                dependencies={["new_password"]}
                rules={[
                  { required: true, message: "请确认新密码" },
                  ({ getFieldValue }) => ({
                    validator(_, value: string) {
                      if (!value || getFieldValue("new_password") === value) {
                        return Promise.resolve();
                      }
                      return Promise.reject(new Error("两次密码不一致"));
                    },
                  }),
                ]}
              >
                <Input.Password placeholder="请再次输入新密码" autoComplete="new-password" />
              </Form.Item>

              <Form.Item style={{ marginBottom: 0 }}>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={isPending}
                  block
                >
                  修改密码
                </Button>
              </Form.Item>
            </Form>
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
}
