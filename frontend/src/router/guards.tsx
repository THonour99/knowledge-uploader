import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { defaultRouteForRole, type Role, useAuthStore } from "../store/auth.store";

interface GuardProps {
  children: ReactNode;
}

interface RoleGuardProps extends GuardProps {
  roles?: Role[];
}
function sourceRouteFromState(state: unknown): string | null {
  if (
    typeof state !== "object" ||
    state === null ||
    !("from" in state) ||
    typeof state.from !== "object" ||
    state.from === null ||
    !("pathname" in state.from) ||
    typeof state.from.pathname !== "string" ||
    !state.from.pathname.startsWith("/") ||
    state.from.pathname.startsWith("//")
  ) {
    return null;
  }

  const search =
    "search" in state.from &&
    typeof state.from.search === "string" &&
    (state.from.search === "" || state.from.search.startsWith("?"))
      ? state.from.search
      : "";
  const hash =
    "hash" in state.from &&
    typeof state.from.hash === "string" &&
    (state.from.hash === "" || state.from.hash.startsWith("#"))
      ? state.from.hash
      : "";
  return `${state.from.pathname}${search}${hash}`;
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
  const location = useLocation();
  const user = useAuthStore((state) => state.user);

  if (accessToken && user) {
    const sourceRoute = sourceRouteFromState(location.state);
    return <Navigate to={sourceRoute ?? defaultRouteForRole[user.role]} replace />;
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
