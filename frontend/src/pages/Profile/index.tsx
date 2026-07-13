import { App as AntdApp, Button, Card, Descriptions, Form, Input, Typography } from "antd";
import {
  LockOutlined,
  PhoneOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";

import { type ChangePasswordRequest, changePassword, getMe } from "../../api/client";
import { KpiCard } from "../../components/KpiCard";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import { Roles } from "../../store/auth.store";

const ROLE_LABELS: Record<string, string> = {
  [Roles.EMPLOYEE]: "普通员工",
  [Roles.DEPT_ADMIN]: "部门管理员",
  [Roles.SYSTEM_ADMIN]: "系统管理员",
};

const ROLE_SCOPE_LABELS: Record<string, string> = {
  [Roles.EMPLOYEE]: "员工级",
  [Roles.DEPT_ADMIN]: "部门级",
  [Roles.SYSTEM_ADMIN]: "系统级",
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

  return (
    <PageContainer title="个人中心" description="查看当前账号资料、组织归属与登录安全设置。">
      {profile ? (
        <div className="metric-grid profile-kpi-grid">
          <KpiCard
            icon={<SafetyCertificateOutlined />}
            title="账号状态"
            value={profile.status === "active" ? "正常" : "需处理"}
            description="访问权限可用"
            tone={profile.status === "active" ? "success" : "warning"}
          />
          <KpiCard
            icon={<TeamOutlined />}
            title="权限范围"
            value={ROLE_SCOPE_LABELS[profile.role] ?? "自定义"}
            description="按当前角色授权"
            tone={profile.role === Roles.SYSTEM_ADMIN ? "purple" : "info"}
          />
          <KpiCard
            icon={<PhoneOutlined />}
            title="联系方式"
            value={profile.phone ? "已登记" : "未登记"}
            description="用于内部协同"
            tone={profile.phone ? "primary" : "warning"}
          />
        </div>
      ) : null}

      <div className="profile-workspace">
        <div className="profile-main">
          <Card title="个人资料" loading={isLoading} className="document-panel">
            {profile ? (
              <Descriptions column={1}>
                <Descriptions.Item label="姓名">
                  <Typography.Text strong>{profile.name}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="邮箱">{profile.email}</Descriptions.Item>
                <Descriptions.Item label="角色">
                  {ROLE_LABELS[profile.role] ?? profile.role}
                </Descriptions.Item>
                <Descriptions.Item label="部门">{profile.department ?? "—"}</Descriptions.Item>
                <Descriptions.Item label="手机">{profile.phone ?? "—"}</Descriptions.Item>
                <Descriptions.Item label="账号状态">
                  <StatusTag kind="user" value={profile.status} />
                </Descriptions.Item>
              </Descriptions>
            ) : null}
          </Card>

          <Card title="账号安全" className="document-panel">
            <div className="profile-security-list">
              <div className="profile-security-row">
                <span className="profile-security-copy">
                  <Typography.Text strong>账号密码登录</Typography.Text>
                  <Typography.Text type="secondary">
                    邮箱作为账号标识，不作为登录前置验证
                  </Typography.Text>
                </span>
                <StatusTag kind="user" value={profile?.status ?? "active"} />
              </div>
              <div className="profile-security-row">
                <span className="profile-security-copy">
                  <Typography.Text strong>密码策略</Typography.Text>
                  <Typography.Text type="secondary">
                    新密码至少 8 位，不能与当前密码相同
                  </Typography.Text>
                </span>
                <StatusTag kind="health" value="ok" />
              </div>
              <div className="profile-security-row">
                <span className="profile-security-copy">
                  <Typography.Text strong>访问范围</Typography.Text>
                  <Typography.Text type="secondary">权限由管理员按组织与角色维护</Typography.Text>
                </span>
                <StatusTag kind="health" value="ok" />
              </div>
            </div>
          </Card>
        </div>

        <Card title="修改密码" className="document-panel profile-password-card">
          <Form form={form} layout="vertical" onFinish={handleSubmit} autoComplete="off">
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

            <Form.Item className="profile-password-submit">
              <Button
                type="primary"
                htmlType="submit"
                loading={isPending}
                block
                icon={<LockOutlined />}
                aria-label="修改密码"
              >
                修改密码
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </div>
    </PageContainer>
  );
}
