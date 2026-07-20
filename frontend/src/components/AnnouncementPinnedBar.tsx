import { CloseOutlined, PushpinFilled } from "@ant-design/icons";
import { Button } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { listAnnouncements, markAnnouncementRead } from "../api/announcements";
import { useSessionMutation as useMutation } from "../hooks/useSessionMutation";
import { useAuthStore } from "../store/auth.store";
import "./AnnouncementPinnedBar.css";

export function AnnouncementPinnedBar() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const userId = useAuthStore((state) => state.user?.id);
  const query = useQuery({
    queryKey: ["announcements", "pinned", userId],
    queryFn: () =>
      listAnnouncements({ state: "active", unread_only: true, page: 1, page_size: 20 }),
    enabled: Boolean(userId),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const readMutation = useMutation({
    mutationFn: (id: string) => markAnnouncementRead(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["announcements"] });
    },
  });
  const item = query.data?.items.find((candidate) => candidate.is_pinned);
  if (!item) return null;

  const markAnd = async (openDetail: boolean) => {
    await readMutation.mutateAsync(item.id);
    if (openDetail) navigate(`/announcements/${item.id}`);
  };
  return (
    <div className="announcement-pinned-bar" role="status">
      <PushpinFilled />
      <button type="button" onClick={() => void markAnd(true)}>
        {item.title}
      </button>
      <Button
        type="text"
        size="small"
        icon={<CloseOutlined />}
        aria-label="关闭置顶公告"
        onClick={() => void markAnd(false)}
      />
    </div>
  );
}
