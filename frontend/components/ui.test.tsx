import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState, RuntimeBanner, StatusBadge } from "./ui";

describe("operations status components", () => {
  it("renders empty states without fake data", () => { render(<EmptyState title="No completed scan yet."/>); expect(screen.getByText("No completed scan yet.")).toBeInTheDocument(); });
  it("keeps critical status text in addition to color", () => { render(<><StatusBadge value="HALTED"/><RuntimeBanner mode="HALTED"/></>); expect(screen.getAllByText(/HALTED/).length).toBeGreaterThan(1); });
  it("does not show a global banner for normal mode", () => { const { container }=render(<RuntimeBanner mode="NORMAL"/>); expect(container).toBeEmptyDOMElement(); });
});
