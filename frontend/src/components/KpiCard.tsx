import type { KeyboardEvent, ReactNode } from "react";
import { FallOutlined, RiseOutlined } from "@ant-design/icons";
import { Card, Typography } from "antd";

import { useCountUp } from "../hooks/useCountUp";
import { formatNumber } from "../utils/format";
import { Sparkline } from "./Sparkline";
import "./KpiCard.css";

export type KpiTone = "primary" | "success" | "warning" | "danger" | "purple" | "info";

export interface KpiCardProps {
  icon: ReactNode;
  title: string;
  /** 主数值(用于滚动动画) */
  value: number | string;
  /** 数值格式化,默认千分位整数(动画过程自动取整);字符串值会原样展示 */
  formatter?: (value: number) => string;
  /** 副描述,渲染在 footer 末尾 */
  description?: ReactNode;
  tone: KpiTone;
  /** 真实迷你趋势序列;不足两点不渲染 sparkline */
  trend?: number[];
  /** 环比百分比(已算好,正=升 负=降);null/undefined 不显示 */
  deltaPct?: number | null;
  /** 环比口径说明,如「较前一周期」 */
  deltaLabel?: string;
  /** 数值上升是否代表“好”(默认 true)。失败/风险类指标传 false,使上升显红 */
  deltaPositiveIsGood?: boolean;
  onClick?: () => void;
}

const TONE_VAR: Record<KpiTone, string> = {
  primary: "var(--ku-color-primary)",
  success: "var(--ku-color-success)",
  warning: "var(--ku-color-orange)",
  danger: "var(--ku-color-danger)",
  purple: "var(--ku-color-purple)",
  info: "var(--ku-color-info)",
};

function defaultFormatter(value: number): string {
  return formatNumber(Math.round(value));
}

export function KpiCard(props: KpiCardProps) {
  const {
    icon,
    title,
    value,
    formatter = defaultFormatter,
    description,
    tone,
    trend,
    deltaPct,
    deltaLabel,
    deltaPositiveIsGood = true,
    onClick,
  } = props;
  const isNumericValue = typeof value === "number";
  const animated = useCountUp(isNumericValue ? value : 0);
  const clickable = Boolean(onClick);
  const displayValue = isNumericValue ? formatter(animated) : value;

  const hasDelta = deltaPct !== null && deltaPct !== undefined && Number.isFinite(deltaPct);
  const deltaUp = hasDelta && deltaPct! >= 0;
  const deltaIsGood = deltaPositiveIsGood ? deltaUp : !deltaUp;
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onClick) {
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onClick();
    }
  };

  return (
    <Card
      className={`kpi-card kpi-card--${tone}${clickable ? " kpi-card--clickable" : ""}`}
      onClick={onClick}
      onKeyDown={clickable ? handleKeyDown : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
    >
      <div className="kpi-card__top">
        <span className="kpi-card__icon">{icon}</span>
        {trend && trend.length >= 2 ? (
          <Sparkline data={trend} color={TONE_VAR[tone]} className="kpi-card__spark" />
        ) : null}
      </div>
      <Typography.Text type="secondary" className="kpi-card__title">
        {title}
      </Typography.Text>
      <Typography.Title level={3} className="kpi-card__value">
        {displayValue}
      </Typography.Title>
      <div className="kpi-card__footer">
        {hasDelta ? (
          <span className={`kpi-card__delta kpi-card__delta--${deltaIsGood ? "good" : "bad"}`}>
            {deltaUp ? <RiseOutlined /> : <FallOutlined />}
            {Math.abs(deltaPct!).toFixed(1)}%
          </span>
        ) : null}
        {hasDelta && deltaLabel ? (
          <Typography.Text type="secondary" className="kpi-card__delta-label">
            {deltaLabel}
          </Typography.Text>
        ) : null}
        {description ? (
          <Typography.Text type="secondary" className="kpi-card__description">
            {description}
          </Typography.Text>
        ) : null}
      </div>
    </Card>
  );
}
