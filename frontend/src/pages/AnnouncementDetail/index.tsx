import { useEffect, useRef } from "react";
import { Alert, Button, Card, Skeleton, Space, Typography } from "antd";
import { PushpinFilled } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate, useParams } from "react-router-dom";

import { getAnnouncement, markAnnouncementRead } from "../../api/announcements";
import { MarkdownContent } from "../../components/MarkdownContent";
import { StatusTag } from "../../components/StatusTag";
import { useSessionMutation as useMutation } from "../../hooks/useSessionMutation";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

export default function AnnouncementDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const markedRef = useRef<string | null>(null);
  const query = useQuery({
    queryKey: ["announcements", "detail", id],
    queryFn: () => getAnnouncement(id),
    enabled: Boolean(id),
  });
  const readMutation = useMutation({
    mutationFn: () => markAnnouncementRead(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["announcements"] });
    },
  });

  useEffect(() => {
    if (query.data && !query.data.is_read && markedRef.current !== id) {
      markedRef.current = id;
      readMutation.mutate();
    }
  }, [id, query.data, readMutation]);

  if (query.isPending) {
    return (
      <PageContainer title="公告详情">
        <Card>
          <Skeleton active />
        </Card>
      </PageContainer>
    );
  }
  if (query.isError || !query.data) {
    return (
      <PageContainer title="公告详情">
        <Alert
          type="error"
          showIcon
          message="公告不存在或你无权查看"
          action={<Button onClick={() => navigate("/announcements")}>返回公告中心</Button>}
        />
      </PageContainer>
    );
  }

  const item = query.data;
  return (
    <PageContainer
      title={item.title}
      breadcrumb={[{ label: "公告中心", path: "/announcements" }, { label: "公告详情" }]}
    >
      <Card className="announcement-detail-card">
        <Space wrap className="announcement-detail-meta">
          <StatusTag kind="announcement" value={item.state} />
          {item.is_pinned ? (
            <Typography.Text>
              <PushpinFilled /> 置顶
            </Typography.Text>
          ) : null}
          <Typography.Text type="secondary">
            生效于 {dayjs(item.visible_from).format("YYYY-MM-DD HH:mm")}
          </Typography.Text>
          {item.expires_at ? (
            <Typography.Text type="secondary">
              到期于 {dayjs(item.expires_at).format("YYYY-MM-DD HH:mm")}
            </Typography.Text>
          ) : null}
        </Space>
        <div className="announcement-detail-divider" />
        <MarkdownContent>{item.body_markdown}</MarkdownContent>
      </Card>
    </PageContainer>
  );
}
