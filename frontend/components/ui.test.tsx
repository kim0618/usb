import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState, RuntimeBanner, StatusBadge } from "./ui";

describe("operations status components", () => {
  it("renders empty states without fake data", () => { render(<EmptyState title="아직 완료된 종목 분석이 없습니다."/>); expect(screen.getByText("아직 완료된 종목 분석이 없습니다.")).toBeInTheDocument(); });
  it("keeps critical status text in addition to color", () => { render(<><StatusBadge value="HALTED" label="중지"/><RuntimeBanner mode="HALTED"/></>); expect(screen.getAllByText(/중지/).length).toBeGreaterThan(1); });
  it("does not show a global banner for normal mode", () => { const { container }=render(<RuntimeBanner mode="NORMAL"/>); expect(container).toBeEmptyDOMElement(); });
});
