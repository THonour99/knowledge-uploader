import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const Roles = {
  EMPLOYEE: "employee",
  DEPT_ADMIN: "dept_admin",
  SYSTEM_ADMIN: "system_admin",
} as const;

export type Role = (typeof Roles)[keyof typeof Roles];

export interface CurrentUser {
  id: string;
  name: string;
  email: string;
  role: Role;
  email_verified?: boolean;
  department_assigned?: boolean;
  department_id?: string | null;
  department_name?: string | null;
  department_code?: string | null;
}

export const UNASSIGNED_DEPARTMENT_ID = "00000000-0000-0000-0000-000000000001";

export function hasAssignedDepartment(user: CurrentUser | null | undefined): boolean {
  return Boolean(
    user?.department_assigned === true &&
    user.department_id &&
    user.department_id !== UNASSIGNED_DEPARTMENT_ID &&
    user.department_code?.toLowerCase() !== "unassigned",
  );
}

interface AuthState {
  accessToken: string | null;
  user: CurrentUser | null;
  setSession: (accessToken: string, user: CurrentUser) => void;
  setUser: (user: CurrentUser) => void;
  clearSession: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      user: null,
      setSession: (accessToken, user) => set({ accessToken, user }),
      setUser: (user) => set({ user }),
      clearSession: () => set({ accessToken: null, user: null }),
    }),
    {
      name: "knowledge-uploader-auth",
      storage: createJSONStorage(() => localStorage),
      partialize: ({ accessToken, user }) => ({ accessToken, user }),
    },
  ),
);

export const defaultRouteForRole: Record<Role, string> = {
  employee: "/dashboard",
  dept_admin: "/dashboard",
  system_admin: "/dashboard",
};
