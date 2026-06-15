import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) {
            return undefined;
          }

          const normalizedId = id.replace(/\\/g, "/");

          if (normalizedId.includes("/echarts") || normalizedId.includes("/zrender")) {
            return "vendor-charts";
          }

          if (
            normalizedId.includes("/antd/") ||
            normalizedId.includes("/@ant-design/") ||
            normalizedId.includes("/rc-")
          ) {
            return "vendor-antd";
          }

          if (
            normalizedId.includes("/react/") ||
            normalizedId.includes("/react-dom/") ||
            normalizedId.includes("/scheduler/")
          ) {
            return "vendor-react";
          }

          if (
            normalizedId.includes("/@tanstack/") ||
            normalizedId.includes("/axios/") ||
            normalizedId.includes("/zustand/")
          ) {
            return "vendor-data";
          }

          if (normalizedId.includes("/react-router") || normalizedId.includes("/dayjs/")) {
            return "vendor-routing";
          }

          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:18000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
