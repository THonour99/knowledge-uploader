import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface UiState {
  sidebarCollapsed: boolean;
  mobileNavigationOpen: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
  setMobileNavigationOpen: (open: boolean) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      mobileNavigationOpen: false,
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setMobileNavigationOpen: (mobileNavigationOpen) => set({ mobileNavigationOpen }),
    }),
    {
      name: "knowledge-uploader-ui",
      storage: createJSONStorage(() => localStorage),
      partialize: ({ sidebarCollapsed }) => ({ sidebarCollapsed }),
    },
  ),
);
