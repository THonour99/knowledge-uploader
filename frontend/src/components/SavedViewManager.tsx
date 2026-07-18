import { DeleteOutlined, EditOutlined, SaveOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Input, Pagination, Radio, Select, Space, Typography } from "antd";
import { useDeferredValue, useEffect, useMemo, useState } from "react";

import {
  SAVED_VIEW_DEFINITION_SCHEMA_VERSION,
  createSavedView,
  deleteSavedView,
  getSavedView,
  getUserFacingErrorMessage,
  isApiError,
  listSavedViews,
  type SavedViewItem,
  type SavedViewPageKey,
  type SavedViewScope,
  updateSavedView,
} from "../api/client";
import {
  SessionBoundModal as Modal,
  SessionBoundPopconfirm as Popconfirm,
} from "./SessionBoundActions";
import { useSessionMutation as useMutation } from "../hooks/useSessionMutation";
import { Roles, useAuthStore } from "../store/auth.store";

export interface SavedViewDepartmentOption {
  label: string;
  value: string;
}

export interface SavedViewManagerProps {
  pageKey: SavedViewPageKey;
  queryDefinition: Record<string, unknown>;
  onApply: (queryDefinition: Record<string, unknown>) => void;
  departmentOptions?: SavedViewDepartmentOption[];
}

interface CreateVariables {
  name: string;
  scope: SavedViewScope;
  departmentId?: string;
}

const SAVED_VIEW_PAGE_SIZE = 20;
const SAVED_VIEW_QUOTA_EXCEEDED_CODE = "SAVED_VIEW_QUOTA_EXCEEDED";
const EMPTY_SAVED_VIEWS: SavedViewItem[] = [];

function viewLabel(view: SavedViewItem): string {
  const scope = view.scope === "department" ? "部门共享" : "仅自己";
  const compatibility =
    view.compatibility === "migrated"
      ? " · 已兼容升级"
      : view.compatibility === "unsupported"
        ? " · 不兼容"
        : "";
  return `${view.name}（${scope}${compatibility}）`;
}

function dedupeDepartmentOptions(
  options: SavedViewDepartmentOption[],
): SavedViewDepartmentOption[] {
  const seen = new Set<string>();
  return options.filter((option) => {
    if (!option.value || seen.has(option.value)) {
      return false;
    }
    seen.add(option.value);
    return true;
  });
}

export function SavedViewManager({
  pageKey,
  queryDefinition,
  onApply,
  departmentOptions = [],
}: SavedViewManagerProps) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const [selectedView, setSelectedView] = useState<SavedViewItem>();
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<SavedViewScope>("private");
  const [departmentId, setDepartmentId] = useState<string>();
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const deferredSearch = useDeferredValue(searchInput.trim());

  const savedViewsQuery = useQuery({
    queryKey: ["saved-views", pageKey, user?.id ?? null, user?.role ?? null, page, deferredSearch],
    queryFn: () =>
      listSavedViews({
        page_key: pageKey,
        ...(deferredSearch ? { q: deferredSearch } : {}),
        page,
        page_size: SAVED_VIEW_PAGE_SIZE,
      }),
    enabled: Boolean(user?.id),
  });
  const views = savedViewsQuery.data?.items ?? EMPTY_SAVED_VIEWS;
  const selectableViews = useMemo(() => {
    if (!selectedView || views.some((view) => view.id === selectedView.id)) {
      return views;
    }
    return [selectedView, ...views];
  }, [selectedView, views]);
  const normalizedDepartments = useMemo(
    () => dedupeDepartmentOptions(departmentOptions),
    [departmentOptions],
  );
  const canCreateDepartmentView =
    (pageKey === "review_files" || pageKey === "task_logs") &&
    (user?.role === Roles.DEPT_ADMIN || user?.role === Roles.SYSTEM_ADMIN) &&
    normalizedDepartments.length > 0;
  const mayMutateSelected =
    selectedView !== undefined &&
    (selectedView.owner_id === user?.id || user?.role === Roles.SYSTEM_ADMIN);
  const selectedDefinition = selectedView?.effective_definition;

  useEffect(() => {
    setSelectedView((current) => {
      if (!current) {
        return current;
      }
      return views.find((view) => view.id === current.id) ?? current;
    });
  }, [views]);

  const refreshViews = async () => {
    await queryClient.invalidateQueries({ queryKey: ["saved-views", pageKey] });
  };

  const recoverMutationFailure = async (
    error: unknown,
    action: "更新" | "删除",
    view: SavedViewItem,
  ): Promise<void> => {
    if (action === "更新" && isApiError(error) && error.status === 409) {
      try {
        setSelectedView(await getSavedView(view.id));
      } catch {
        setSelectedView(undefined);
      }
      setPage(1);
      await refreshViews();
      void message.error("视图已被其他人更新，已刷新为最新版本，请确认后重试");
      return;
    }
    if (isApiError(error) && error.status === 404) {
      setSelectedView(undefined);
    }
    await refreshViews();
    void message.error(
      `${getUserFacingErrorMessage(error, `${action}视图失败`)}；已刷新保存视图列表`,
    );
  };

  const createMutation = useMutation({
    mutationFn: ({
      name: nextName,
      scope: nextScope,
      departmentId: nextDepartmentId,
    }: CreateVariables) =>
      createSavedView({
        page_key: pageKey,
        name: nextName,
        scope: nextScope,
        department_id: nextScope === "department" ? nextDepartmentId : undefined,
        definition_schema_version: SAVED_VIEW_DEFINITION_SCHEMA_VERSION,
        query_definition: queryDefinition,
        column_preferences: {},
      }),
    onSuccess: async (created) => {
      setSelectedView(created);
      setPage(1);
      setSearchInput("");
      await refreshViews();
      setCreateOpen(false);
      setName("");
      setScope("private");
      setDepartmentId(undefined);
      void message.success("视图已保存");
    },
    onError: (error) => {
      if (isApiError(error) && error.code === SAVED_VIEW_QUOTA_EXCEEDED_CODE) {
        void message.error("已达到当前页面和共享范围的保存上限，请删除不再使用的视图后重试");
        return;
      }
      void message.error(getUserFacingErrorMessage(error, "保存视图失败"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: (view: SavedViewItem) =>
      updateSavedView(view.id, {
        row_version: view.row_version,
        definition_schema_version: SAVED_VIEW_DEFINITION_SCHEMA_VERSION,
        query_definition: queryDefinition,
        column_preferences: view.effective_definition?.column_preferences ?? {},
      }),
    onSuccess: async (updated) => {
      setSelectedView(updated);
      setPage(1);
      await refreshViews();
      void message.success("视图已更新为当前筛选");
    },
    onError: (error, view) => recoverMutationFailure(error, "更新", view),
  });

  const deleteMutation = useMutation({
    mutationFn: (view: SavedViewItem) => deleteSavedView(view.id),
    onSuccess: async () => {
      setSelectedView(undefined);
      await refreshViews();
      void message.success("视图已删除");
    },
    onError: (error, view) => recoverMutationFailure(error, "删除", view),
  });

  const openCreate = () => {
    setName("");
    setScope("private");
    setDepartmentId(normalizedDepartments[0]?.value);
    setCreateOpen(true);
  };

  const applySelected = () => {
    if (!selectedDefinition) {
      return;
    }
    onApply(selectedDefinition.query_definition);
    void message.success("已应用保存视图");
  };

  const submitCreate = () => {
    const cleanedName = name.trim();
    if (!cleanedName) {
      void message.warning("请输入视图名称");
      return;
    }
    if (scope === "department" && !departmentId) {
      void message.warning("请选择共享部门");
      return;
    }
    createMutation.mutate({
      name: cleanedName,
      scope,
      departmentId: scope === "department" ? departmentId : undefined,
    });
  };

  return (
    <>
      <Space wrap aria-label="保存视图">
        <Select<string>
          aria-label="选择保存视图"
          placeholder={savedViewsQuery.isLoading ? "正在加载保存视图" : "选择或搜索保存视图"}
          value={selectedView?.id}
          loading={savedViewsQuery.isFetching}
          allowClear
          showSearch
          filterOption={false}
          searchValue={searchInput}
          notFoundContent={savedViewsQuery.isFetching ? "正在搜索" : "未找到匹配视图"}
          style={{ minWidth: 220 }}
          options={selectableViews.map((view) => ({
            label: viewLabel(view),
            value: view.id,
            disabled: view.compatibility === "unsupported" || !view.effective_definition,
          }))}
          onSearch={(value) => {
            setSearchInput(value);
            setPage(1);
          }}
          onClear={() => {
            setSearchInput("");
            setPage(1);
          }}
          onChange={(value) =>
            setSelectedView(value ? selectableViews.find((view) => view.id === value) : undefined)
          }
        />
        <Button disabled={!selectedDefinition} onClick={applySelected} aria-label="应用保存视图">
          应用
        </Button>
        <Button icon={<SaveOutlined />} onClick={openCreate}>
          保存当前筛选
        </Button>
        <Button
          icon={<EditOutlined />}
          disabled={!mayMutateSelected || !selectedDefinition}
          loading={updateMutation.isPending}
          onClick={() => selectedView && updateMutation.mutate(selectedView)}
        >
          更新
        </Button>
        <Popconfirm
          title="删除保存视图"
          description="删除后无法恢复，确认继续？"
          okText="删除"
          cancelText="取消"
          disabled={!mayMutateSelected}
          onConfirm={() => selectedView && deleteMutation.mutate(selectedView)}
        >
          <Button
            danger
            icon={<DeleteOutlined />}
            disabled={!mayMutateSelected}
            loading={deleteMutation.isPending}
          >
            删除
          </Button>
        </Popconfirm>
      </Space>
      {(savedViewsQuery.data?.total_pages ?? 0) > 1 ? (
        <Pagination
          aria-label="保存视图分页"
          size="small"
          current={page}
          pageSize={SAVED_VIEW_PAGE_SIZE}
          total={savedViewsQuery.data?.total ?? 0}
          showSizeChanger={false}
          showTotal={(total) => `共 ${total} 个保存视图`}
          onChange={setPage}
        />
      ) : null}

      {savedViewsQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="保存视图加载失败"
          description={getUserFacingErrorMessage(savedViewsQuery.error, "请稍后重试")}
          action={
            <Button size="small" onClick={() => void savedViewsQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : null}
      {selectedView?.compatibility === "migrated" ? (
        <Typography.Text type="secondary">
          该视图已按最新字段契约兼容转换；点击“更新”可保存为当前版本。
        </Typography.Text>
      ) : null}

      <Modal
        title="保存当前筛选"
        open={createOpen}
        okText="保存"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        onOk={submitCreate}
        onCancel={() => setCreateOpen(false)}
        destroyOnHidden
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <label>
            <Typography.Text strong>视图名称</Typography.Text>
            <Input
              aria-label="视图名称"
              value={name}
              maxLength={80}
              placeholder="例如：我领取的高风险文档"
              onChange={(event) => setName(event.target.value)}
              onPressEnter={submitCreate}
            />
          </label>
          {canCreateDepartmentView ? (
            <>
              <Radio.Group
                aria-label="视图共享范围"
                value={scope}
                options={[
                  { label: "仅自己", value: "private" },
                  { label: "部门共享", value: "department" },
                ]}
                onChange={(event) => setScope(event.target.value as SavedViewScope)}
              />
              {scope === "department" ? (
                <Select
                  aria-label="共享部门"
                  value={departmentId}
                  options={normalizedDepartments}
                  style={{ width: "100%" }}
                  onChange={setDepartmentId}
                />
              ) : null}
            </>
          ) : (
            <Typography.Text type="secondary">
              当前页面将保存为私人视图；部门共享仅在有明确管理范围的审核或任务页面开放。
            </Typography.Text>
          )}
          {savedViewsQuery.data?.quota ? (
            <Typography.Text type="secondary">
              每个页面最多保存 {savedViewsQuery.data.quota.private_per_owner_page}{" "}
              个私人视图；部门共享按部门和页面最多{" "}
              {savedViewsQuery.data.quota.department_per_department_page} 个。
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">保存上限将在视图列表加载后显示。</Typography.Text>
          )}
          <Typography.Text type="secondary">
            本页面不应用或修改列偏好；只保存筛选和排序，不保存结果行、文件内容或权限范围。
          </Typography.Text>
        </Space>
      </Modal>
    </>
  );
}
