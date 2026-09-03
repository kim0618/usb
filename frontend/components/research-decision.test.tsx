import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResearchDecisionControl } from "./research-decision";

describe("final research decision control", () => {
  it("shows no selection while undecided and sends raw payloads", () => {
    const decide = vi.fn();
    render(<ResearchDecisionControl decision={null} onDecide={decide}/>);
    const accept = screen.getByRole("button", { name: "채택" });
    const reject = screen.getByRole("button", { name: "거절" });
    expect(accept).toHaveAttribute("aria-pressed", "false");
    expect(reject).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(accept); fireEvent.click(reject);
    expect(decide).toHaveBeenNthCalledWith(1, "APPROVE");
    expect(decide).toHaveBeenNthCalledWith(2, "REJECT");
  });

  it.each([["APPROVE", "✓ 채택", "거절"], ["REJECT", "✓ 거절", "채택"]] as const)("marks %s as selected and prevents duplicate mutation", (decision, selectedName, otherName) => {
    const decide = vi.fn();
    const view = render(<ResearchDecisionControl decision={decision} onDecide={decide}/>);
    const selected = within(view.container).getByRole("button", { name: selectedName });
    expect(selected).toHaveAttribute("aria-pressed", "true");
    expect(selected).toBeDisabled();
    fireEvent.click(selected);
    expect(decide).not.toHaveBeenCalled();
    fireEvent.click(within(view.container).getByRole("button", { name: otherName }));
    expect(decide).toHaveBeenCalledWith(decision === "APPROVE" ? "REJECT" : "APPROVE");
  });

  it("explains that selection is not an immediate order", () => {
    const view = render(<ResearchDecisionControl onDecide={() => undefined}/>);
    expect(within(view.container).getByText(/실제 주문은 전략 및 리스크 조건/)).toBeInTheDocument();
  });
});
