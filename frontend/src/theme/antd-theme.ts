import type { ThemeConfig } from "antd";

import { colors, radius, typography } from "./tokens";

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.danger,
    colorInfo: colors.info,
    colorBgLayout: colors.bgBase,
    colorBgContainer: colors.bgCard,
    colorBorder: colors.border,
    colorText: colors.textPrimary,
    colorTextSecondary: colors.textSecondary,
    borderRadius: radius.control,
    borderRadiusLG: radius.card,
    fontFamily: typography.fontFamily,
  },
  components: {
    Card: {
      borderRadiusLG: radius.card,
      paddingLG: 24,
    },
    Layout: {
      headerBg: colors.bgCard,
      siderBg: colors.bgCard,
      bodyBg: colors.bgBase,
    },
    Menu: {
      itemBorderRadius: radius.control,
    },
    Tag: {
      borderRadiusSM: radius.tag,
    },
  },
};
