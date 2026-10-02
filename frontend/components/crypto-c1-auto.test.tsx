import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { C1AutoControl } from "@/components/crypto-c1-auto-control";
import type { CryptoState } from "@/lib/crypto-paper";
import type { C1Marker } from "@/lib/crypto-c1";

afterEach(cleanup);

const state = (enabled: boolean): CryptoState => ({
  c1_auto: { enabled, active_signal_id: enabled ? "C1-5" : null,
    active_trade_id: enabled ? "trade-5" : null, enabled_at: 1, disabled_at: null,
    source: "PAPER_C1_AUTO", leverage: "10", has_position: enabled,
    entry_at: enabled ? Date.UTC(2026, 0, 1, 5, 5) : null,
    benchmark_at: enabled ? Date.UTC(2026, 0, 1, 9, 5) : null },
} as CryptoState);

const marker = { signal_id: "C1-5", display_seq: 5 } as C1Marker;

describe("PAPER C1 AUTO control", () => {
  it("keeps the OFF button visible when an older backend omits c1_auto", () => {
    render(<C1AutoControl state={{} as CryptoState} markers={[]} busy={false}
      onAction={() => {}} />);
    expect(screen.getByTestId("paper-c1-auto")).toHaveTextContent("C1 AUTOOFF");
    expect(screen.getByRole("button", { name: "OFF" })).toBeEnabled();
  });

  it("shows the compact active pairing and benchmark without overflowing phone layout", () => {
    render(<C1AutoControl state={state(true)} markers={[marker]} busy={false}
      onAction={() => {}} />);
    const panel = screen.getByTestId("paper-c1-auto");
    expect(panel).toHaveClass("flex-wrap");
    expect(panel).toHaveTextContent("C1 AUTOON");
    expect(panel).toHaveTextContent("active C1 #5");
    expect(panel).toHaveTextContent("entry 14:05");
    expect(panel).toHaveTextContent("4H 18:05");
  });

  it("requests ON from OFF and keeps the switch server-driven", () => {
    const action = vi.fn();
    render(<C1AutoControl state={state(false)} markers={[]} busy={false} onAction={action} />);
    fireEvent.click(screen.getByRole("button", { name: "OFF" }));
    expect(action).toHaveBeenCalledOnce();
    expect(screen.queryByText(/active C1/)).not.toBeInTheDocument();
  });
});
