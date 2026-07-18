import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { PublicRoute } from "./guards";
import { useAuthStore } from "../store/auth.store";

function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</span>
  );
}

afterEach(() => {
  useAuthStore.setState({ accessToken: null, user: null });
});

describe("PublicRoute", () => {
  it("returns an authenticated user to the protected source route", async () => {
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "employee-1",
        name: "员工",
        email: "employee@example.com",
        role: "employee",
      },
    });

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/login",
            state: {
              from: {
                pathname: "/my-files",
                search: "?status=pending",
                hash: "#review",
              },
            },
          },
        ]}
      >
        <Routes>
          <Route
            path="/login"
            element={
              <PublicRoute>
                <span>login-entry</span>
              </PublicRoute>
            }
          />
          <Route path="/my-files" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/my-files?status=pending#review",
    );
  });

  it("rejects a protocol-relative source and uses the role default", async () => {
    useAuthStore.setState({
      accessToken: "token",
      user: {
        id: "admin-1",
        name: "管理员",
        email: "admin@example.com",
        role: "system_admin",
      },
    });

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/login",
            state: { from: { pathname: "//external.example/steal" } },
          },
        ]}
      >
        <Routes>
          <Route
            path="/login"
            element={
              <PublicRoute>
                <span>login-entry</span>
              </PublicRoute>
            }
          />
          <Route path="/dashboard" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("location")).toHaveTextContent("/dashboard");
  });
});
