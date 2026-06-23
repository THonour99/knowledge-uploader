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
}

interface AuthState {
  accessToken: string | null;
  user: CurrentUser | null;
  setSession: (accessToken: string, user: CurrentUser) => void;
  clearSession: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      user: null,
      setSession: (accessToken, user) => set({ accessToken, user }),
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
  employee: "/my-files",
  dept_admin: "/files",
  system_admin: "/dashboard",
};
