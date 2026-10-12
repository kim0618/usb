import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/** [진입분석] local preview: present only with the build flag, one GET per click, 관망 until a
 *  family passes its gate, no internal names, no non-GET. */

const NO_TRADE = {
  available: true, verdict: "NO_TRADE", verdict_word: "관망", reason: "NO_VALIDATED_ENTRY_FAMILY",
  statement: "현재 검증된 진입 조건 없음", entry_zone: null, invalidation: null, targets: [], expected_r: null,
  passing_cells: [], research: { version: "wall-lifecycle.r1", advisor_verdict: "B. NO_VALID_ENTRY_FAMILY", wall_verdict: "B. WALL_INTERACTION_R1_PASS" },
  basis: {
    mid: "83656.5",
    ask: [{ price: "83730.2", notional_usdt: "700738", age_ms: 122000, continuity: "CARRIED", gap_bp: 8.81 },
          { price: "83735.4", notional_usdt: "737290", age_ms: 30001, continuity: "NEW", gap_bp: 0.62 }],
    bid: [{ price: "83587.4", notional_usdt: "701800", age_ms: 122000, continuity: "CARRIED", gap_bp: 8.26 }],
    flow: { window: "60s", buy_pct: 75, sell_pct: 25, live: true },
    oi: { value_usdt: "7774322314", change_pct: "0.51", window: "1h", direction: "UP" },
  },
  analysis_request_id: "0123456789abcdef", sample_ms: 1,
};

async function load(flag: string | undefined) {
  vi.resetModules();
  if (flag === undefined) delete process.env.NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW;
  else process.env.NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW = flag;
  return import("@/components/entry-analysis-preview");
}

describe("[진입분석] preview", () => {
  const calls: { method: string; url: string }[] = [];
  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ method: init?.method ?? "GET", url: String(url) });
      return new Response(JSON.stringify(NO_TRADE), { status: 200, headers: { "content-type": "application/json" } });
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete process.env.NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW; });

  it("renders nothing without the build flag, whatever the base", async () => {
    const m = await load(undefined);
    render(<m.EntryAnalysisPreview base="/btc-context-api" symbol="BTCUSDT" />);
    expect(screen.queryByTestId("entry-analysis-button")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("with the flag: one GET per click, 관망 and the statement, no direction, no internal name", async () => {
    const m = await load("1");
    render(<m.EntryAnalysisPreview base="/btc-context-api" symbol="BTCUSDT" />);
    expect(calls).toHaveLength(0);                       // nothing until the click
    fireEvent.click(screen.getByTestId("entry-analysis-button"));
    await waitFor(() => expect(screen.getByTestId("entry-analysis-verdict")).toHaveTextContent("관망"));
    expect(calls).toEqual([{ method: "GET", url: "/btc-context-api/entry-analysis" }]);
    expect(screen.getByTestId("entry-analysis-statement")).toHaveTextContent("현재 검증된 진입 조건 없음");
    expect(screen.queryByTestId("entry-analysis-plan")).toBeNull();
    const text = screen.getByTestId("entry-analysis-panel").textContent ?? "";
    for (const word of ["F1", "F2", "F3", "F4", "F5", "F6", "Q1", "Q2", "Q3", "Q5", "Q6", "LONG", "SHORT", "추천", "강화", "약화"]) {
      expect(text).not.toContain(word);
    }
    // the basis lists the walls with size, persistence, NEW/CARRIED and gap, the flow and OI
    expect(screen.getAllByTestId("entry-analysis-wall")).toHaveLength(3);
    expect(screen.getAllByTestId("entry-analysis-wall")[0]).toHaveTextContent("상단벽1 83,730.2 · 701K · 2m 유지 · 이어짐 · 간격 0.09%");
    expect(screen.getByTestId("entry-analysis-basis")).toHaveTextContent("체결흐름 매수우세 75%");
    expect(screen.getByTestId("entry-analysis-basis")).toHaveTextContent("OI 7.77B (1h +0.51%)");
  });

  it("renders nothing on a non-BTC tab and shows dashes when the API is down", async () => {
    const m = await load("1");
    const { unmount } = render(<m.EntryAnalysisPreview base="/btc-context-api" symbol="MAGICUSDT" />);
    expect(screen.queryByTestId("entry-analysis-button")).toBeNull();
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    render(<m.EntryAnalysisPreview base="/btc-context-api" symbol="BTCUSDT" />);
    fireEvent.click(screen.getByTestId("entry-analysis-button"));
    await waitFor(() => expect(screen.getByTestId("entry-analysis-statement")).toHaveTextContent("분석 서버 응답 없음"));
    expect(screen.getByTestId("entry-analysis-verdict")).toHaveTextContent("--");
  });

  it("shows the plan rows only for a directional verdict (none exists today)", async () => {
    const m = await load("1");
    render(<m.EntryAnalysisPanel loading={false} error={null}
      analysis={{ ...NO_TRADE, verdict: "SHORT", verdict_word: "SHORT", statement: "", entry_zone: ["83720", "83740"],
        invalidation: "83790", targets: ["83590", "83550", "83500"], expected_r: "2.1" } as never} />);
    expect(screen.getByTestId("entry-analysis-plan")).toHaveTextContent("83,720.0 ~ 83,740.0");
    expect(screen.getByTestId("entry-analysis-plan")).toHaveTextContent("1차 83,590.0 · 2차 83,550.0 · 3차 83,500.0");
    expect(screen.getByTestId("entry-analysis-plan")).toHaveTextContent("1 : 2.1");
  });
});
