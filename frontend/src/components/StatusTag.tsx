import type { CSSProperties } from "react";
import { Tag } from "antd";

import { statusTagColors } from "../theme/tokens";

export type StatusKind =
  | "file"
  | "review"
  | "sync"
  | "risk"
  | "user"
  | "dataset"
  | "expiry"
  | "health";
type StatusTone = keyof typeof statusTagColors;

const statusKindLabels: Record<StatusKind, string> = {
  file: "文件状态",
  review: "审核状态",
  sync: "同步状态",
  risk: "风险等级",
  user: "用户状态",
  dataset: "Dataset 状态",
  expiry: "有效期状态",
  health: "健康状态",
};

export interface StatusTagProps {
  kind: StatusKind;
  value: string;
  processing?: boolean;
  variant?: "tag" | "dot";
}

interface StatusMeta {
  label: string;
  color: StatusTone;
  italic?: boolean;
  processing?: boolean;
}

const statusMap: Record<StatusKind, Record<string, StatusMeta>> = {
  file: {
    uploaded: { label: "草稿", color: "default" },
    extracting_text: { label: "文本抽取中", color: "ai" },
    analysis_queued: { label: "等待分析", color: "geekblue" },
    analyzing: { label: "AI 分析中", color: "ai" },
    analysis_failed: { label: "AI 分析失败", color: "danger" },
    analyzed: { label: "分析完成·待提交", color: "ai" },
    pending_review: { label: "待审核", color: "queued" },
    sensitive_review_required: { label: "敏感审核", color: "danger" },
    approved: { label: "已批准·未入库", color: "success" },
    rejected: { label: "已驳回", color: "volcano" },
    queued: { label: "等待同步", color: "default" },
    syncing: { label: "同步中", color: "processing", processing: true },
    uploaded_to_ragflow: { label: "已上传至 RAGFlow", color: "cyan" },
    parsing: { label: "解析中", color: "processing", processing: true },
    parsed: { label: "已入库", color: "success" },
    failed: { label: "失败", color: "danger" },
    disabled: { label: "已归档", color: "default" },
    deleted: { label: "已删除", color: "default", italic: true },
    ragflow_cleanup_failed: { label: "远端清理失败", color: "danger" },
  },
  review: {
    pending: { label: "待审核", color: "queued" },
    in_review: { label: "审核中", color: "primary" },
    approved: { label: "已通过", color: "success" },
    rejected: { label: "未通过", color: "danger" },
  },
  sync: {
    not_synced: { label: "未同步", color: "default" },
    queued: { label: "待同步", color: "primary" },
    running: { label: "执行中", color: "processing", processing: true },
    syncing: { label: "同步中", color: "processing", processing: true },
    synced: { label: "已同步", color: "success" },
    succeeded: { label: "已成功", color: "success" },
    failed: { label: "同步失败", color: "danger" },
    canceled: { label: "已取消", color: "default" },
  },
  risk: {
    none: { label: "无风险", color: "default" },
    low: { label: "低风险", color: "success" },
    medium: { label: "中风险", color: "warning" },
    high: { label: "高风险", color: "danger" },
    critical: { label: "严重风险", color: "dangerDeep" },
  },
  user: {
    active: { label: "正常", color: "success" },
    pending_email_verification: { label: "待激活", color: "queued" },
    disabled: { label: "已禁用", color: "default" },
    locked: { label: "锁定中", color: "danger" },
  },
  dataset: {
    enabled: { label: "已启用", color: "success" },
    pending: { label: "待完善", color: "warning" },
    disabled: { label: "已禁用", color: "default" },
    required: { label: "是", color: "success" },
    skipped: { label: "否", color: "default" },
    unbound: { label: "未绑定 Dataset", color: "danger" },
  },
  expiry: {
    active: { label: "有效", color: "success" },
    expiring: { label: "即将过期", color: "warning" },
    expired: { label: "已过期", color: "danger" },
    never: { label: "长期有效", color: "default" },
  },
  health: {
    ok: { label: "正常", color: "success" },
    error: { label: "异常", color: "danger" },
    unknown: { label: "未知", color: "default" },
  },
};

export function StatusTag({ kind, value, processing = false, variant = "tag" }: StatusTagProps) {
  const meta = statusMap[kind][value] ?? { label: value, color: "default" };
  const color = statusTagColors[meta.color];
  const isProcessing = processing || meta.processing === true;
  const ariaLabel = `${statusKindLabels[kind]}：${meta.label}`;

  if (variant === "dot") {
    return (
      <span
        aria-label={ariaLabel}
        className={`status-tag-dot status-tag-dot--${meta.color}`}
        title={ariaLabel}
      >
        {meta.label}
      </span>
    );
  }

  return (
    <Tag
      aria-label={ariaLabel}
      style={{ "--status-color": color } as CSSProperties}
      title={ariaLabel}
      className={[
        "status-tag",
        `status-tag--${meta.color}`,
        meta.italic ? "status-tag--italic" : "",
        isProcessing ? "status-tag--processing" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {meta.label}
    </Tag>
  );
}
