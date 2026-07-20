import { useState } from "react";
import { SoundOutlined } from "@ant-design/icons";
import { Badge, Button, Drawer, Empty, List, Pagination, Segmented, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate } from "react-router-dom";

import { listAnnouncements, markAnnouncementRead } from "../api/announcements";
import { useSessionMutation as useMutation } from "../hooks/useSessionMutation";
import { useAuthStore } from "../store/auth.store";
import "./AnnouncementCenter.css";

const PAGE_SIZE = 10;

export function AnnouncementCenter() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const userId = useAuthStore((state) => state.user?.id);
  const [open, setOpen] = useState(false);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const badgeQuery = useQuery({
    queryKey: ["announcements", "badge", userId],
    queryFn: () => listAnnouncements({ state: "active", page: 1, page_size: 1 }),
    enabled: Boolean(userId),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const drawerQuery = useQuery({
    queryKey: ["announcements", "drawer", userId, unreadOnly, page],
    queryFn: () =>
      listAnnouncements({ state: "active", unread_only: unreadOnly, page, page_size: PAGE_SIZE }),
    enabled: Boolean(userId) && open,
    staleTime: 15_000,
  });
  const readMutation = useMutation({
    mutationFn: (id: string) => markAnnouncementRead(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["announcements"] });
    },
  });

  const openDetail = async (id: string, isRead: boolean) => {
    if (!isRead) await readMutation.mutateAsync(id);
    setOpen(false);
    navigate(`/announcements/${id}`);
  };

  const unreadCount = drawerQuery.data?.unread_count ?? badgeQuery.data?.unread_count ?? 0;
  return (
    <>
      <Badge count={unreadCount} size="small" overflowCount={99}>
        <Button
          type="text"
          icon={<SoundOutlined />}
          aria-label="公告中心"
          onClick={() => {
            setPage(1);
            setOpen(true);
          }}
        />
      </Badge>
      <Drawer
        title="公告中心"
        placement="right"
        width={440}
        open={open}
        onClose={() => setOpen(false)}
      >
        <div className="announcement-center__toolbar">
          <Segmented
            value={unreadOnly ? "unread" : "all"}
            options={[
              { label: "当前公告", value: "all" },
              { label: "未读", value: "unread" },
            ]}
            onChange={(value) => {
              setUnreadOnly(value === "unread");
              setPage(1);
            }}
          />
          <Typography.Text type="secondary">{unreadCount} 条未读</Typography.Text>
        </div>
        <List
          loading={drawerQuery.isPending}
          dataSource={drawerQuery.data?.items ?? []}
          locale={{
            emptyText: <Empty description={unreadOnly ? "没有未读公告" : "暂无当前公告"} />,
          }}
          renderItem={(item) => (
            <List.Item
              className={
                item.is_read
                  ? "announcement-center__item"
                  : "announcement-center__item announcement-center__item--unread"
              }
            >
              <button type="button" onClick={() => void openDetail(item.id, item.is_read)}>
                <Typography.Text strong>{item.title}</Typography.Text>
                <Typography.Text type="secondary">
                  {dayjs(item.visible_from).format("MM-DD HH:mm")}
                </Typography.Text>
              </button>
            </List.Item>
          )}
        />
        {(drawerQuery.data?.total ?? 0) > PAGE_SIZE ? (
          <Pagination
            current={page}
            pageSize={PAGE_SIZE}
            total={drawerQuery.data?.total ?? 0}
            showSizeChanger={false}
            onChange={setPage}
          />
        ) : null}
        <Button
          block
          className="announcement-center__all"
          onClick={() => {
            setOpen(false);
            navigate("/announcements");
          }}
        >
          查看全部公告
        </Button>
      </Drawer>
    </>
  );
}
