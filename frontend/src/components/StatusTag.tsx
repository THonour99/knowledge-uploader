import { Tag } from "antd";

import { statusTagColors } from "../theme/tokens";

export type StatusKind = "file" | "review" | "sync" | "risk" | "user";
type StatusTone = keyof typeof statusTagColors | "processing";

export interface StatusTagProps {
  kind: StatusKind;
  value: string;
  processing?: boolean;
}

interface StatusMeta {
  label: string;
  color: StatusTone;
  italic?: boolean;
}

const statusMap: Record<StatusKind, Record<string, StatusMeta>> = {
  file: {
    uploaded: { label: "已上传", color: "info" },
    extracting_text: { label: "文本抽取中", color: "ai" },
    analysis_queued: { label: "等待分析", color: "primary" },
    analyzing: { label: "AI 分析中", color: "ai" },
    analysis_failed: { label: "分析失败", color: "orange" },
    analyzed: { label: "分析完成", color: "cyan" },
    pending_review: { label: "待审核", color: "queued" },
    sensitive_review_required: { label: "敏感审核", color: "danger" },
    approved: { label: "已审核", color: "success" },
    rejected: { label: "已拒绝", color: "danger" },
    queued: { label: "等待同步", color: "default" },
    syncing: { label: "同步中", color: "processing" },
    uploaded_to_ragflow: { label: "已上传至 RAGFlow", color: "cyan" },
    parsing: { label: "解析中", color: "processing" },
    parsed: { label: "解析完成", color: "success" },
    failed: { label: "失败", color: "danger" },
    disabled: { label: "已禁用", color: "default" },
    deleted: { label: "已删除", color: "default", italic: true },
  },
  review: {
    pending: { label: "待审核", color: "queued" },
    in_review: { label: "审核中", color: "info" },
    approved: { label: "已通过", color: "success" },
    rejected: { label: "未通过", color: "danger" },
  },
  sync: {
    not_synced: { label: "未同步", color: "default" },
    queued: { label: "待同步", color: "info" },
    syncing: { label: "同步中", color: "processing" },
    synced: { label: "已同步", color: "success" },
    failed: { label: "同步失败", color: "danger" },
  },
  risk: {
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
};

export function StatusTag({ kind, value, processing = false }: StatusTagProps) {
  const meta = statusMap[kind][value] ?? { label: value, color: "default" };
  const color =
    processing || meta.color === "processing" ? "processing" : statusTagColors[meta.color];

  return (
    <Tag color={color} className={meta.italic ? "status-tag status-tag--italic" : "status-tag"}>
      {meta.label}
    </Tag>
  );
}
