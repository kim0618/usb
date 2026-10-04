import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./app-shell";

let pathname = "/crypto-paper";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/hooks/use-api", () => ({ useApi: () => ({ data: undefined }) }));
afterEach(() => cleanup());

describe("retained production crypto navigation and header", () => {
  it("opens the crypto terminal without equity context and restores it on an equity route", () => {
    pathname = "/crypto-paper";
    const view = render(<AppShell><p>terminal</p></AppShell>);
    expect(screen.getByRole("link", { name: /선물/ })).toHaveAttribute("href", "/crypto-paper");
    expect(screen.getByRole("link", { name: /선물/ })).toHaveClass("bg-primary-soft");
    expect(screen.getByText("선물 수동매매")).toBeInTheDocument();
    for (const label of ["현재시각 :", "기준거래일 :", "USD/KRW :", "시장"]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    expect(screen.queryByLabelText("미국 시장 세션")).not.toBeInTheDocument();
    expect(screen.getByText("시스템", { selector: "span" })).toBeInTheDocument();

    pathname = "/daily";
    view.rerender(<AppShell><p>daily</p></AppShell>);
    expect(screen.getByRole("link", { name: /전략/ })).toHaveAttribute("href", "/daily");
    expect(screen.getByRole("link", { name: /전략/ })).toHaveClass("bg-primary-soft");
    expect(screen.queryByText("선물 수동매매")).not.toBeInTheDocument();
    for (const label of ["현재시각 :", "기준거래일 :", "USD/KRW :", "시장"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByLabelText("미국 시장 세션")).toBeInTheDocument();
  });
});
