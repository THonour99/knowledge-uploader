import { PlaceholderPage } from "../PlaceholderPage";

export default function UsersPage() {
  return (
    <PlaceholderPage
      title="用户管理"
      description="管理用户、角色、账号状态和上传数量。"
      primaryAction="新增用户"
      samples={[
        { kind: "user", value: "pending_email_verification" },
        { kind: "user", value: "locked" },
      ]}
    />
  );
}
