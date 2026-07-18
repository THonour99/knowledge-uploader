import { useRef, useState } from "react";
import { App as AntdApp, Button, Input } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { SessionApplication } from "./App";
import { getAuthSessionGeneration } from "./sessionIdentity";
import { type CurrentUser, useAuthStore } from "./store/auth.store";

const sessionA: { accessToken: string; user: CurrentUser } = {
  accessToken: "token-a",
  user: {
    id: "user-a",
    name: "甲用户",
    email: "a@example.com",
    role: "system_admin",
    email_verified: true,
    department_assigned: true,
    department_id: "dept-a",
    department_code: "a",
  },
};

const sessionB: { accessToken: string; user: CurrentUser } = {
  accessToken: "token-b",
  user: {
    ...sessionA.user,
    id: "user-b",
    name: "乙用户",
    email: "b@example.com",
    role: "employee",
  },
};

let mountSequence = 0;

function SensitiveHarness() {
  const { message, modal } = AntdApp.useApp();
  const instanceId = useRef(++mountSequence);
  const [secret, setSecret] = useState("");
  const [selection, setSelection] = useState("");

  return (
    <div>
      <span data-testid="instance-id">{instanceId.current}</span>
      <Input
        aria-label="会话密钥"
        value={secret}
        onChange={(event) => setSecret(event.target.value)}
      />
      <input aria-label="会话文件" type="file" />
      <Button onClick={() => setSelection("file-a")}>选择文档</Button>
      <span data-testid="selection">{selection}</span>
      <Button
        onClick={() => {
          modal.confirm({ title: "旧会话确认", content: "确认内容" });
          void message.open({ key: "old-session-message", content: "旧会话消息", duration: 0 });
        }}
      >
        打开会话浮层
      </Button>
    </div>
  );
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  Object.defineProperty(window, "getComputedStyle", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      getPropertyValue: () => "",
    })),
  });
});

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("SessionApplication", () => {
  it("remounts the entire authenticated subtree and destroys sensitive state on A to B", async () => {
    useAuthStore.setState(sessionA);
    render(
      <SessionApplication>
        <SensitiveHarness />
      </SessionApplication>,
    );
    const firstInstance = screen.getByTestId("instance-id").textContent;

    fireEvent.change(screen.getByLabelText("会话密钥"), { target: { value: "sk-session-a" } });
    const firstFileInput = screen.getByLabelText<HTMLInputElement>("会话文件");
    fireEvent.change(firstFileInput, {
      target: { files: [new File(["secret"], "session-a.txt", { type: "text/plain" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "选择文档" }));
    fireEvent.click(screen.getByRole("button", { name: "打开会话浮层" }));

    expect(screen.getByLabelText("会话密钥")).toHaveValue("sk-session-a");
    expect(firstFileInput.files).toHaveLength(1);
    expect(screen.getByTestId("selection")).toHaveTextContent("file-a");
    expect(await screen.findAllByText("旧会话确认")).not.toHaveLength(0);
    expect(await screen.findByText("旧会话消息")).toBeInTheDocument();

    act(() => {
      useAuthStore.setState(sessionB);
    });

    await waitFor(() => {
      expect(screen.getByTestId("instance-id")).not.toHaveTextContent(firstInstance ?? "");
    });
    expect(screen.getByLabelText("会话密钥")).toHaveValue("");
    expect(screen.getByLabelText<HTMLInputElement>("会话文件").files).toHaveLength(0);
    expect(screen.getByTestId("selection")).toHaveTextContent("");
    await waitFor(() => {
      expect(screen.queryAllByText("旧会话确认")).toHaveLength(0);
      expect(screen.queryAllByText("旧会话消息")).toHaveLength(0);
    });
  });

  it("uses a monotonic generation key so an immediate ABA switch still remounts", async () => {
    useAuthStore.setState(sessionA);
    const startingGeneration = getAuthSessionGeneration();
    render(
      <SessionApplication>
        <SensitiveHarness />
      </SessionApplication>,
    );
    const firstInstance = screen.getByTestId("instance-id").textContent;
    fireEvent.change(screen.getByLabelText("会话密钥"), { target: { value: "must-disappear" } });

    act(() => {
      useAuthStore.setState(sessionB);
      useAuthStore.setState(sessionA);
    });

    await waitFor(() => {
      expect(screen.getByTestId("instance-id")).not.toHaveTextContent(firstInstance ?? "");
    });
    expect(getAuthSessionGeneration()).toBeGreaterThanOrEqual(startingGeneration + 2);
    expect(screen.getByLabelText("会话密钥")).toHaveValue("");
  });
});
