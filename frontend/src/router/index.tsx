import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppShell } from "../layouts/AppShell";
import { appRoutes, publicRoutes } from "./routes";
import { PublicRoute, RequireAuth, RoleGuard, RootRedirect } from "./guards";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootRedirect />,
  },
  ...publicRoutes.map((route) => ({
    path: route.path,
    element: <PublicRoute>{route.element}</PublicRoute>,
  })),
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: appRoutes.map((route) => ({
      path: route.path.replace(/^\//, ""),
      element: <RoleGuard roles={route.roles}>{route.element}</RoleGuard>,
    })),
  },
  {
    path: "*",
    element: <Navigate to="/" replace />,
  },
]);
