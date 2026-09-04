import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { Drawer } from "./ui";

const adoption = () => readFileSync("app/adoption/page.tsx", "utf8");

describe("Stage 10B-4 human review detail drawer", () => {
  it("is responsive and keyboard dismissible", () => {
    const onClose = vi.fn();
    render(<Drawer open title="TSLA · 채택 검토" onClose={onClose}>상세 내용</Drawer>);
    expect(screen.getByRole("dialog", { name: "TSLA · 채택 검토" })).toHaveClass("w-full", "sm:max-w-[640px]");
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "상세 닫기" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("reuses the research detail API under a Korean review title", () => {
    const page = adoption();
    expect(page).toContain("api.researchDetail(item.analysis_id, item.symbol)");
    expect(page).toContain("· 채택 검토");
    expect(page).not.toContain("Human Review");
  });

  it("keeps decisions only in adoption detail with the two-approval guard", () => {
    const page = adoption();
    expect(page).toContain("<ResearchDecisionControl");
    expect(page).toContain("approveDisabled={approved >= 2");
    expect(page).toContain("error.status === 409");
    expect(page).toContain("await result.refresh()");
  });
});
