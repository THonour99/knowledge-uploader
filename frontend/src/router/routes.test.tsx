import { describe, expect, it } from "vitest";

import { defaultRouteForRole, type Role, Roles } from "../store/auth.store";
import { appNavigationRoutes, appRoutes } from "./routes";

function navigationLabelsForRole(role: Role): string[] {
  return appNavigationRoutes
    .filter((route) => !route.roles || route.roles.includes(role))
    .map((route) => route.nav?.label)
    .filter((label): label is string => Boolean(label));
}

describe("route role matrix", () => {
  it("sends dept admins to file management by default", () => {
    expect(defaultRouteForRole[Roles.DEPT_ADMIN]).toBe("/files");
  });

  it("limits dept admin navigation to scoped file and task surfaces", () => {
    const labels = navigationLabelsForRole(Roles.DEPT_ADMIN);

    expect(labels).toEqual(
      expect.arrayContaining(["上传文件", "我的文件", "文件审核", "任务日志"]),
    );

    for (const hiddenLabel of [
      "运营总览",
      "Dataset 配置",
      "AI 配置",
      "统计报表",
      "用户管理",
      "部门管理",
      "系统设置",
      "操作日志",
      "分类管理",
      "标签管理",
    ]) {
      expect(labels).not.toContain(hiddenLabel);
    }

    expect(appRoutes.find((route) => route.path === "/profile")?.roles).toBeUndefined();
  });
});
