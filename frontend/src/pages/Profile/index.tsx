import { App as AntdApp, Button, Card, Descriptions, Form, Input, Typography } from "antd";
import {
  LockOutlined,
  MailOutlined,
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

  const emailVerifiedValue = profile?.email_verified ? "active" : "pending_email_verification";
  const profileStatusValue = profile?.status ?? "pending_email_verification";
  const roleLabel = profile ? (ROLE_LABELS[profile.role] ?? profile.role) : "—";
  const roleScopeLabel = profile ? (ROLE_SCOPE_LABELS[profile.role] ?? "自定义") : "—";
  const contactHealthValue = profile?.phone ? "ok" : "unknown";

  return (
    <PageContainer title="个人中心" description="查看当前账号资料、认证状态与登录安全设置。">
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
            icon={<MailOutlined />}
            title="邮箱认证"
            value={profile.email_verified ? "已认证" : "待激活"}
            description="公司邮箱校验"
            tone={profile.email_verified ? "success" : "warning"}
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

      {profile ? (
        <section className="profile-status-strip" aria-label="账号运行状态">
          <div className="profile-status-strip__main">
            <span className="profile-status-strip__icon">
              <SafetyCertificateOutlined />
            </span>
            <span className="profile-status-strip__copy">
              <Typography.Text type="secondary">账号治理</Typography.Text>
              <Typography.Title level={4} className="profile-status-strip__title">
                账号运行状态
              </Typography.Title>
              <Typography.Text type="secondary">
                {roleLabel} · {profile.department ?? "未登记部门"}
              </Typography.Text>
            </span>
            <StatusTag kind="user" value={profileStatusValue} variant="dot" />
          </div>

          <div className="profile-status-strip__lanes">
            <div className="profile-status-lane">
              <span className="profile-status-lane__icon">
                <SafetyCertificateOutlined />
              </span>
              <span className="profile-status-lane__body">
                <span className="profile-status-lane__topline">
                  <Typography.Text type="secondary">账号状态</Typography.Text>
                  <StatusTag kind="user" value={profileStatusValue} variant="dot" />
                </span>
                <strong>{profile.status === "active" ? "访问正常" : "需要处理"}</strong>
                <Typography.Text type="secondary">当前登录身份与平台访问权限</Typography.Text>
              </span>
            </div>

            <div className="profile-status-lane">
              <span className="profile-status-lane__icon profile-status-lane__icon--mail">
                <MailOutlined />
              </span>
              <span className="profile-status-lane__body">
                <span className="profile-status-lane__topline">
                  <Typography.Text type="secondary">邮箱认证</Typography.Text>
                  <StatusTag kind="user" value={emailVerifiedValue} variant="dot" />
                </span>
                <strong>{profile.email_verified ? "公司邮箱已认证" : "等待激活"}</strong>
                <Typography.Text type="secondary">{profile.email}</Typography.Text>
              </span>
            </div>

            <div className="profile-status-lane">
              <span className="profile-status-lane__icon profile-status-lane__icon--role">
                <TeamOutlined />
              </span>
              <span className="profile-status-lane__body">
                <span className="profile-status-lane__topline">
                  <Typography.Text type="secondary">权限范围</Typography.Text>
                  <StatusTag kind="health" value="ok" variant="dot" />
                </span>
                <strong>{roleScopeLabel}</strong>
                <Typography.Text type="secondary">{roleLabel}</Typography.Text>
              </span>
            </div>

            <div className="profile-status-lane">
              <span className="profile-status-lane__icon profile-status-lane__icon--phone">
                <PhoneOutlined />
              </span>
              <span className="profile-status-lane__body">
                <span className="profile-status-lane__topline">
                  <Typography.Text type="secondary">联系方式</Typography.Text>
                  <StatusTag kind="health" value={contactHealthValue} variant="dot" />
                </span>
                <strong>{profile.phone ? "已登记" : "未登记"}</strong>
                <Typography.Text type="secondary">{profile.phone ?? "用于内部协同通知"}</Typography.Text>
              </span>
            </div>
          </div>
        </section>
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
                <Descriptions.Item label="邮箱状态">
                  <StatusTag kind="user" value={emailVerifiedValue} />
                </Descriptions.Item>
              </Descriptions>
            ) : null}
          </Card>

          <Card title="账号安全" className="document-panel">
            <div className="profile-security-list">
              <div className="profile-security-row">
                <span className="profile-security-copy">
                  <Typography.Text strong>邮箱验证</Typography.Text>
                  <Typography.Text type="secondary">控制账号激活与通知送达状态</Typography.Text>
                </span>
                <StatusTag kind="user" value={emailVerifiedValue} />
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
