import { DeleteOutlined, EditOutlined, SaveOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Input, Radio, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  SAVED_VIEW_DEFINITION_SCHEMA_VERSION,
  createSavedView,
  deleteSavedView,
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
  const [selectedId, setSelectedId] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<SavedViewScope>("private");
  const [departmentId, setDepartmentId] = useState<string>();

  const savedViewsQuery = useQuery({
    queryKey: ["saved-views", pageKey, user?.id ?? null, user?.role ?? null],
    queryFn: () => listSavedViews({ page_key: pageKey, page: 1, page_size: 100 }),
    enabled: Boolean(user?.id),
  });
  const views = savedViewsQuery.data?.items ?? [];
  const selectedView = views.find((view) => view.id === selectedId);
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
    if (selectedId && !views.some((view) => view.id === selectedId)) {
      setSelectedId(undefined);
    }
  }, [selectedId, views]);

  const refreshViews = async () => {
    await queryClient.invalidateQueries({ queryKey: ["saved-views", pageKey] });
  };

  const recoverMutationFailure = async (error: unknown, action: "更新" | "删除"): Promise<void> => {
    await refreshViews();
    if (action === "更新" && isApiError(error) && error.status === 409) {
      void message.error("视图已被其他人更新，已刷新为最新版本，请确认后重试");
      return;
    }
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
      await refreshViews();
      setSelectedId(created.id);
      setCreateOpen(false);
      setName("");
      setScope("private");
      setDepartmentId(undefined);
      void message.success("视图已保存");
    },
    onError: (error) => {
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
      await refreshViews();
      setSelectedId(updated.id);
      void message.success("视图已更新为当前筛选");
    },
    onError: (error) => recoverMutationFailure(error, "更新"),
  });

  const deleteMutation = useMutation({
    mutationFn: (view: SavedViewItem) => deleteSavedView(view.id),
    onSuccess: async () => {
      setSelectedId(undefined);
      await refreshViews();
      void message.success("视图已删除");
    },
    onError: (error) => recoverMutationFailure(error, "删除"),
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
        <Select
          aria-label="选择保存视图"
          placeholder={savedViewsQuery.isLoading ? "正在加载保存视图" : "选择保存视图"}
          value={selectedId}
          loading={savedViewsQuery.isLoading}
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ minWidth: 220 }}
          options={views.map((view) => ({
            label: viewLabel(view),
            value: view.id,
            disabled: view.compatibility === "unsupported" || !view.effective_definition,
          }))}
          onChange={setSelectedId}
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
          <Typography.Text type="secondary">
            本页面不应用或修改列偏好；只保存筛选和排序，不保存结果行、文件内容或权限范围。
          </Typography.Text>
        </Space>
      </Modal>
    </>
  );
}
