export const colors = {
  primary: "#1677FF",
  primaryHover: "#4096FF",
  primaryLight: "#E6F4FF",
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
  dangerDeep: "#9D174D",
} as const;

export const statusTagColors = {
  default: colors.textDisabled,
  info: colors.info,
  primary: colors.primary,
  queued: colors.warning,
  success: colors.success,
  warning: colors.warning,
  danger: colors.danger,
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

export const themeCssVariables = {
  "--ku-color-primary": colors.primary,
  "--ku-color-primary-light": colors.primaryLight,
  "--ku-bg-base": colors.bgBase,
  "--ku-bg-card": colors.bgCard,
  "--ku-border": colors.border,
  "--ku-text-primary": colors.textPrimary,
  "--ku-text-secondary": colors.textSecondary,
  "--ku-text-disabled": colors.textDisabled,
  "--ku-radius-card": `${radius.card}px`,
  "--ku-radius-control": `${radius.control}px`,
  "--ku-spacing-page": `${spacing.pageGutter}px`,
  "--ku-spacing-section": `${spacing.sectionGap}px`,
  "--ku-font-family": typography.fontFamily,
} as const;
