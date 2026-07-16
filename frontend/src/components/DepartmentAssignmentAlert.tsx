import { Alert, Button, Space } from "antd";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";

import { useAuthStore } from "../store/auth.store";

interface DepartmentAssignmentAlertProps {
  className?: string;
}

export function DepartmentAssignmentAlert({ className }: DepartmentAssignmentAlertProps) {
  const queryClient = useQueryClient();
  const profileFetching = useIsFetching({ queryKey: ["auth", "me"] }) > 0;
  const user = useAuthStore((state) => state.user);
  const subject = encodeURIComponent("知识库账号部门分配申请");
  const body = encodeURIComponent(
    `您好，请为知识库账号 ${user?.email ?? "（请填写登录邮箱）"} 分配正确部门。部门决定文档审核范围与知识库归属，谢谢。`,
  );

  return (
    <Alert
      className={className}
      type="warning"
      showIcon
      message="尚未分配有效部门"
      description="部门决定审核范围和知识库归属。完成分配前不能上传或提交文档。"
      action={
        <Space wrap>
          <Button
            size="small"
            loading={profileFetching}
            onClick={() => void queryClient.invalidateQueries({ queryKey: ["auth", "me"] })}
          >
            刷新账号状态
          </Button>
          <Button type="link" href={`mailto:?subject=${subject}&body=${body}`}>
            联系管理员分配部门
          </Button>
        </Space>
      }
    />
  );
}
