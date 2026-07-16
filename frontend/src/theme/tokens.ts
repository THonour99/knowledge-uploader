export const colors = {
  // Emerald is the product/interaction color. Semantic success remains a
  // deliberately different green so "clickable" and "completed" never blur.
  primary: "#059669",
  primaryHover: "#047857",
  primaryActive: "#065F46",
  primaryLight: "#D1FAE5",
  accent: "#0D9488",
  accentLight: "#CCFBF1",
  infoLight: "#DBEAFE",
  successLight: "#DCFCE7",
  warningLight: "#FEF3C7",
  dangerLight: "#FEE2E2",
  purpleLight: "#F5F5F4",
  bgBase: "#FAFAF9",
  bgCard: "#FFFFFF",
  bgSubtle: "#F5F5F4",
  border: "#E7E5E4",
  borderStrong: "#D6D3D1",
  textPrimary: "#1C1917",
  textSecondary: "#57534E",
  textDisabled: "#A8A29E",
  success: "#16A34A",
  warning: "#D97706",
  danger: "#DC2626",
  info: "#2563EB",
  // Compatibility aliases used by legacy status/data visualisations. They are
  // not product-primary colors and can be removed as those surfaces migrate.
  purple: "#57534E",
  orange: "#D97706",
  cyan: "#0D9488",
  geekblue: "#2563EB",
  volcano: "#DC2626",
  dangerDeep: "#B91C1C",
} as const;

export const statusTagColors = {
  default: colors.textDisabled,
  info: colors.primary,
  primary: colors.primary,
  processing: colors.primary,
  geekblue: colors.primary,
  queued: colors.warning,
  success: colors.success,
  warning: colors.warning,
  danger: colors.danger,
  volcano: colors.danger,
  dangerDeep: colors.danger,
  ai: colors.accent,
  orange: colors.warning,
  cyan: colors.accent,
} as const;

export const radius = {
  card: 12,
  control: 8,
  tag: 999,
} as const;

export const spacing = {
  cardPadding: 20,
  cardPaddingSm: 16,
  pageGutter: 24,
  sectionGap: 16,
} as const;

export const typography = {
  fontFamily: '"Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
} as const;

export const layout = {
  headerHeight: 56,
  sidebarWidth: 220,
  sidebarCollapsedWidth: 64,
} as const;

// 卡片立体感档位:rest 态用 card,hover 抬升用 cardHover,与设计稿「轻微阴影 / 悬浮卡」对齐。
export const shadow = {
  xs: "0 1px 2px rgba(28, 25, 23, 0.04)",
  card: "0 1px 3px rgba(28, 25, 23, 0.06), 0 1px 2px rgba(28, 25, 23, 0.04)",
  cardHover: "0 4px 12px rgba(28, 25, 23, 0.08)",
} as const;

export const themeCssVariables = {
  "--ku-color-primary": colors.primary,
  "--ku-color-primary-hover": colors.primaryHover,
  "--ku-color-primary-light": colors.primaryLight,
  "--ku-color-accent": colors.accent,
  "--ku-color-accent-light": colors.accentLight,
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
  "--ku-bg-subtle": colors.bgSubtle,
  "--ku-border": colors.border,
  "--ku-border-strong": colors.borderStrong,
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
