import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisTabs, SectionTabs, SystemTabs, isRouteActive } from "./section-tabs";

let pathname = "/candidates";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const source = (path: string) => readFileSync(path, "utf8");

describe("Stage 9.9 navigation", () => {
  beforeEach(() => { pathname = "/candidates"; });
  afterEach(() => cleanup());

  it("defines exactly five top-level menu entries", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell.match(/^  \{ href:/gm)).toHaveLength(5);
    ["대시보드", "분석", "매매", "전략 성과", "시스템"].forEach(label => expect(shell).toContain(`label: "${label}"`));
    ["후보 종목", "GPT 분석", "설정"].forEach(label => expect(shell).not.toContain(`label: "${label}"`));
  });

  it.each([
    ["/", "대시보드"],
    ["/candidates", "분석"],
    ["/candidates/NVDA", "분석"],
    ["/research", "분석"],
    ["/trading", "매매"],
    ["/shadow", "전략 성과"],
    ["/runtime", "시스템"],
    ["/settings", "시스템"],
  ])("maps %s to the %s group", (route, label) => {
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain(`label: "${label}"`);
    if (route !== "/") expect(shell).toContain(`"${route.split("/").slice(0, 2).join("/")}"`);
  });

  it("matches tab routes by exact path and nested prefix", () => {
    expect(isRouteActive("/research", "/research")).toBe(true);
    expect(isRouteActive("/research/NVDA", "/research")).toBe(true);
    expect(isRouteActive("/research-old", "/research")).toBe(false);
  });

  it("renders analysis tabs as links with the current route", () => {
    render(<AnalysisTabs/>);
    expect(screen.getByRole("navigation", { name: "분석 화면" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "후보 종목" })).toHaveAttribute("href", "/candidates");
    expect(screen.getByRole("link", { name: "후보 종목" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "GPT 분석" })).toHaveAttribute("href", "/research");
  });

  it("renders system tabs and activates settings", () => {
    pathname = "/settings";
    render(<SystemTabs/>);
    expect(screen.getByRole("link", { name: "시스템 상태" })).toHaveAttribute("href", "/runtime");
    expect(screen.getByRole("link", { name: "설정" })).toHaveAttribute("aria-current", "page");
  });

  it("uses a semantic navigation label supplied by the caller", () => {
    render(<SectionTabs label="테스트 그룹" tabs={[{ href: "/one", label: "하나" }]}/>);
    expect(screen.getByRole("navigation", { name: "테스트 그룹" })).toBeInTheDocument();
  });

  it("keeps all seven route pages and grouped tabs in place", () => {
    ["page.tsx", "candidates/page.tsx", "research/page.tsx", "trading/page.tsx", "shadow/page.tsx", "runtime/page.tsx", "settings/page.tsx"].forEach(path => expect(() => source(`app/${path}`)).not.toThrow());
    expect(source("app/candidates/page.tsx")).toContain("<AnalysisTabs/>");
    expect(source("app/research/page.tsx")).toContain("<AnalysisTabs/>");
    expect(source("app/runtime/page.tsx")).toContain("<SystemTabs/>");
    expect(source("app/settings/page.tsx")).toContain("<SystemTabs/>");
  });
});
