import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { defaultRouteForRole, type Role, useAuthStore } from "../store/auth.store";

interface GuardProps {
  children: ReactNode;
}

interface RoleGuardProps extends GuardProps {
  roles?: Role[];
}

export function RequireAuth({ children }: GuardProps) {
  const location = useLocation();
  const accessToken = useAuthStore((state) => state.accessToken);
  const user = useAuthStore((state) => state.user);

  if (!accessToken || !user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}

export function PublicRoute({ children }: GuardProps) {
  const accessToken = useAuthStore((state) => state.accessToken);
  const user = useAuthStore((state) => state.user);

  if (accessToken && user) {
    return <Navigate to={defaultRouteForRole[user.role]} replace />;
  }

  return <>{children}</>;
}

export function RoleGuard({ roles, children }: RoleGuardProps) {
  const user = useAuthStore((state) => state.user);

  if (!roles || roles.length === 0) {
    return <>{children}</>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (!roles.includes(user.role)) {
    return <Navigate to={defaultRouteForRole[user.role]} replace />;
  }

  return <>{children}</>;
}

export function RootRedirect() {
  const accessToken = useAuthStore((state) => state.accessToken);
  const user = useAuthStore((state) => state.user);

  if (!accessToken || !user) {
    return <Navigate to="/login" replace />;
  }

  return <Navigate to={defaultRouteForRole[user.role]} replace />;
}
