import { useState } from "react";
import { Alert, Button, Card, Empty, List, Pagination, Segmented, Space, Typography } from "antd";
import { PushpinFilled, SoundOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useNavigate } from "react-router-dom";

import { listAnnouncements } from "../../api/announcements";
import { StatusTag } from "../../components/StatusTag";
import { PageContainer } from "../../layouts/PageContainer";
import "./styles.css";

type ListState = "active" | "expired" | "all";

export default function AnnouncementsPage() {
  const navigate = useNavigate();
  const [state, setState] = useState<ListState>("active");
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["announcements", "list", state, page],
    queryFn: () => listAnnouncements({ state, page, page_size: 12 }),
  });

  return (
    <PageContainer title="公告中心" description="查看与你当前部门和角色相关的站内公告。">
      <Card className="announcements-list-card">
        <div className="announcements-toolbar">
          <Segmented
            value={state}
            options={[
              { label: "当前公告", value: "active" },
              { label: "历史公告", value: "expired" },
              { label: "全部", value: "all" },
            ]}
            onChange={(value) => {
              setState(value as ListState);
              setPage(1);
            }}
          />
          <Typography.Text type="secondary">{query.data?.unread_count ?? 0} 条未读</Typography.Text>
        </div>
        {query.isError ? (
          <Alert
            type="error"
            showIcon
            message="公告加载失败"
            description="请检查网络连接后重试。"
            action={<Button onClick={() => void query.refetch()}>重新加载</Button>}
          />
        ) : null}
        <List
          loading={query.isPending}
          dataSource={query.data?.items ?? []}
          locale={{ emptyText: <Empty description="暂无可查看的公告" /> }}
          renderItem={(item) => (
            <List.Item
              className={
                item.is_read ? "announcement-row" : "announcement-row announcement-row--unread"
              }
              actions={[
                <Button
                  key="detail"
                  type="link"
                  onClick={() => navigate(`/announcements/${item.id}`)}
                >
                  查看详情
                </Button>,
              ]}
            >
              <List.Item.Meta
                avatar={<SoundOutlined className="announcement-row__icon" />}
                title={
                  <Space wrap>
                    <Typography.Text strong>{item.title}</Typography.Text>
                    {item.is_pinned ? <PushpinFilled title="置顶公告" /> : null}
                    <StatusTag kind="announcement" value={item.state} />
                    {!item.is_read ? <Typography.Text type="success">未读</Typography.Text> : null}
                  </Space>
                }
                description={`生效时间：${item.visible_from ? dayjs(item.visible_from).format("YYYY-MM-DD HH:mm") : "-"}${item.expires_at ? ` · 到期：${dayjs(item.expires_at).format("YYYY-MM-DD HH:mm")}` : ""}`}
              />
            </List.Item>
          )}
        />
        {(query.data?.total ?? 0) > 12 ? (
          <Pagination
            current={page}
            pageSize={12}
            total={query.data?.total ?? 0}
            showSizeChanger={false}
            onChange={setPage}
          />
        ) : null}
      </Card>
    </PageContainer>
  );
}
