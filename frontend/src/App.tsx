import { type CSSProperties, type ReactNode, useSyncExternalStore } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { queryClient } from "./queryClient";
import { router } from "./router";
import { getAuthSessionGeneration, subscribeAuthSessionGeneration } from "./sessionIdentity";
import { antdTheme } from "./theme/antd-theme";
import { themeCssVariables } from "./theme/tokens";

interface SessionApplicationProps {
  children?: ReactNode;
}

export function SessionApplication({ children }: SessionApplicationProps) {
  const sessionGeneration = useSyncExternalStore(
    subscribeAuthSessionGeneration,
    getAuthSessionGeneration,
    getAuthSessionGeneration,
  );

  return (
    <AntdApp key={sessionGeneration}>
      <QueryClientProvider client={queryClient}>
        <div className="app-root" style={themeCssVariables as CSSProperties}>
          {children ?? <RouterProvider router={router} />}
        </div>
      </QueryClientProvider>
    </AntdApp>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={antdTheme}>
      <SessionApplication />
    </ConfigProvider>
  );
}
