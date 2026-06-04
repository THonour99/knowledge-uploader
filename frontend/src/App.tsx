import type { CSSProperties } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { router } from "./router";
import { antdTheme } from "./theme/antd-theme";
import { themeCssVariables } from "./theme/tokens";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <ConfigProvider theme={antdTheme}>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <div className="app-root" style={themeCssVariables as CSSProperties}>
            <RouterProvider router={router} />
          </div>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  );
}
