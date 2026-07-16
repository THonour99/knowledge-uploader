import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { defaultRouteForRole, type Role, Roles, useAuthStore } from "../store/auth.store";
import { appNavigationRoutes, appRoutes, RoleDashboardEntry } from "./routes";

vi.mock("../pages/MyFiles", () => ({
  default: () => <span>员工文档工作台</span>,
}));
vi.mock("../pages/FileManagement", () => ({
  default: () => <span>部门审核工作台</span>,
}));
vi.mock("../pages/Dashboard", () => ({
  default: () => <span>系统运营工作台</span>,
}));

function navigationLabelsForRole(role: Role): string[] {
  return appNavigationRoutes
    .filter((route) => !route.roles || route.roles.includes(role))
    .map((route) => route.nav?.label)
    .filter((label): label is string => Boolean(label));
}

describe("route role matrix", () => {
  afterEach(() => {
    useAuthStore.setState({ accessToken: null, user: null });
  });

  it("uses dashboard as the stable login entry for all roles", () => {
    expect(defaultRouteForRole[Roles.EMPLOYEE]).toBe("/dashboard");
    expect(defaultRouteForRole[Roles.DEPT_ADMIN]).toBe("/dashboard");
    expect(defaultRouteForRole[Roles.SYSTEM_ADMIN]).toBe("/dashboard");
    expect(appRoutes.find((route) => route.path === "/dashboard")?.roles).toEqual([
      Roles.EMPLOYEE,
      Roles.DEPT_ADMIN,
      Roles.SYSTEM_ADMIN,
    ]);
  });

  it("limits dept admin navigation to scoped file and task surfaces", () => {
    const labels = navigationLabelsForRole(Roles.DEPT_ADMIN);

    expect(labels).toEqual(
      expect.arrayContaining(["上传文件", "我的文件", "文件审核", "任务日志"]),
    );

    for (const hiddenLabel of [
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

  it.each([
    [Roles.EMPLOYEE, "员工文档工作台"],
    [Roles.DEPT_ADMIN, "部门审核工作台"],
    [Roles.SYSTEM_ADMIN, "系统运营工作台"],
  ] as const)("keeps %s on dashboard and renders its role workbench", async (role, heading) => {
    useAuthStore.setState({
      accessToken: "token",
      user: { id: "user-1", name: "用户", email: "user@company.com", role },
    });

    function LocationProbe() {
      return <span data-testid="location">{useLocation().pathname}</span>;
    }

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route
            path="/dashboard"
            element={
              <>
                <RoleDashboardEntry />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("location")).toHaveTextContent("/dashboard");
    expect(await screen.findByText(heading)).toBeInTheDocument();
    for (const otherHeading of ["员工文档工作台", "部门审核工作台", "系统运营工作台"]) {
      if (otherHeading !== heading) {
        expect(screen.queryByText(otherHeading)).not.toBeInTheDocument();
      }
    }
  });
});
