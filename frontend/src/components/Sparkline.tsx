import { useId } from "react";

export interface SparklineProps {
  /** 数值序列(如近 N 个周期的每日上传量),至少 2 个点才渲染 */
  data: number[];
  /** 线条与填充色,支持 CSS 变量(如 "var(--ku-color-primary)"),默认继承 currentColor */
  color?: string;
  width?: number;
  height?: number;
  strokeWidth?: number;
  /** 是否渲染渐变填充面积 */
  fill?: boolean;
  className?: string;
}

/**
 * 纯 SVG 迷你趋势线,无第三方依赖、不实例化图表引擎,适合在 KPI 卡里大量复用。
 * 数据按自身 min/max 归一化到 viewBox,只表达「走势形状」,不带坐标轴。
 */
export function Sparkline(props: SparklineProps) {
  const {
    data,
    color = "currentColor",
    width = 88,
    height = 32,
    strokeWidth = 2,
    fill = true,
    className,
  } = props;
  const gradientId = useId();

  if (data.length < 2) {
    return null;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const usableHeight = height - strokeWidth * 2;

  const points = data.map((value, index) => {
    const x = index * stepX;
    const y = strokeWidth + usableHeight - ((value - min) / range) * usableHeight;
    return [x, y] as const;
  });

  const linePath = points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(" ");
  const areaPath = `${linePath} L${width.toFixed(2)} ${height.toFixed(2)} L0 ${height.toFixed(2)} Z`;

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="presentation"
      aria-hidden="true"
    >
      {fill ? (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.18} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={areaPath} fill={`url(#${gradientId})`} stroke="none" />
        </>
      ) : null}
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
