export const colors = {
  primary: "#1677FF",
  primaryHover: "#4096FF",
  primaryLight: "#E6F4FF",
  infoLight: "#DBEAFE",
  successLight: "#DCFCE7",
  warningLight: "#FFF7ED",
  dangerLight: "#FEE2E2",
  purpleLight: "#F3E8FF",
  bgBase: "#F5F7FA",
  bgCard: "#FFFFFF",
  border: "#E5EAF2",
  textPrimary: "#1F2937",
  textSecondary: "#667085",
  textDisabled: "#98A2B3",
  success: "#16A34A",
  warning: "#F59E0B",
  danger: "#EF4444",
  info: "#3B82F6",
  purple: "#7C3AED",
  orange: "#F97316",
  cyan: "#06B6D4",
  geekblue: "#2F54EB",
  volcano: "#DC2626",
  dangerDeep: "#9D174D",
} as const;

export const statusTagColors = {
  default: colors.textDisabled,
  info: colors.info,
  primary: colors.primary,
  processing: colors.info,
  geekblue: colors.geekblue,
  queued: colors.warning,
  success: colors.success,
  warning: colors.warning,
  danger: colors.danger,
  volcano: colors.volcano,
  dangerDeep: colors.dangerDeep,
  ai: colors.purple,
  orange: colors.orange,
  cyan: colors.cyan,
} as const;

export const radius = {
  card: 12,
  control: 8,
  tag: 4,
} as const;

export const spacing = {
  cardPadding: 24,
  cardPaddingSm: 20,
  pageGutter: 24,
  sectionGap: 16,
} as const;

export const typography = {
  fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
} as const;

export const layout = {
  headerHeight: 56,
  sidebarWidth: 220,
  sidebarCollapsedWidth: 64,
} as const;

// 卡片立体感档位:rest 态用 card,hover 抬升用 cardHover,与设计稿「轻微阴影 / 悬浮卡」对齐。
export const shadow = {
  xs: "0 1px 2px rgba(16, 24, 40, 0.05)",
  card: "0 2px 8px rgba(16, 24, 40, 0.06)",
  cardHover: "0 8px 24px rgba(16, 24, 40, 0.1)",
} as const;

export const themeCssVariables = {
  "--ku-color-primary": colors.primary,
  "--ku-color-primary-light": colors.primaryLight,
  "--ku-color-success": colors.success,
  "--ku-color-warning": colors.warning,
  "--ku-color-danger": colors.danger,
  "--ku-color-info": colors.info,
  "--ku-color-purple": colors.purple,
  "--ku-color-orange": colors.orange,
  "--ku-color-cyan": colors.cyan,
  "--ku-color-geekblue": colors.geekblue,
  "--ku-color-volcano": colors.volcano,
  "--ku-color-info-light": colors.infoLight,
  "--ku-color-success-light": colors.successLight,
  "--ku-color-warning-light": colors.warningLight,
  "--ku-color-danger-light": colors.dangerLight,
  "--ku-color-purple-light": colors.purpleLight,
  "--ku-bg-base": colors.bgBase,
  "--ku-bg-card": colors.bgCard,
  "--ku-border": colors.border,
  "--ku-text-primary": colors.textPrimary,
  "--ku-text-secondary": colors.textSecondary,
  "--ku-text-disabled": colors.textDisabled,
  "--ku-radius-card": `${radius.card}px`,
  "--ku-radius-control": `${radius.control}px`,
  "--ku-radius-tag": `${radius.tag}px`,
  "--ku-spacing-page": `${spacing.pageGutter}px`,
  "--ku-spacing-section": `${spacing.sectionGap}px`,
  "--ku-shadow-xs": shadow.xs,
  "--ku-shadow-card": shadow.card,
  "--ku-shadow-card-hover": shadow.cardHover,
  "--ku-font-family": typography.fontFamily,
} as const;
