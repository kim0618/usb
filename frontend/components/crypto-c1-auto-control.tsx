"use client";

import type { C1Marker } from "@/lib/crypto-c1";
import { cryptoApi, type CryptoState } from "@/lib/crypto-paper";

function shortTime(value: number | null | undefined) {
  if (value == null) return "-";
  return new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit",
    minute: "2-digit", hour12: false }).format(new Date(value));
}

export function C1AutoControl({ state, markers, busy, onAction }: {
  state: CryptoState; markers: C1Marker[]; busy: boolean;
  onAction: (run: () => Promise<unknown>) => void;
}) {
  // Keep the control discoverable while the frontend and PAPER backend are restarted in either
  // order. A backend that predates C1 AUTO omits this object; that means OFF, not "hide the
  // feature". Pressing ON still goes through the server endpoint and surfaces its refusal.
  const auto = state.c1_auto ?? {
    enabled: false, active_signal_id: null, active_trade_id: null,
    enabled_at: null, disabled_at: null, source: "PAPER_C1_AUTO" as const,
    leverage: "10" as const, has_position: false, entry_at: null, benchmark_at: null,
  };
  const marker = markers.find(row => row.signal_id === auto.active_signal_id);
  return (
    <section className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border bg-panel px-3 py-2"
      data-testid="paper-c1-auto">
      <span className="text-xs font-semibold text-foreground">C1 AUTO</span>
      <button type="button" disabled={busy}
        className={`rounded-md px-3 py-1 text-xs font-semibold ${auto.enabled
          ? "bg-profit-soft text-profit" : "bg-surface text-muted"}`}
        onClick={() => onAction(() => cryptoApi.c1Auto(!auto.enabled))}>
        {auto.enabled ? "ON" : "OFF"}
      </button>
      {auto.enabled && (
        <div className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
          <span>{auto.has_position ? "LONG 보유" : "대기"}</span>
          <span>active C1 {marker?.display_seq ? `#${marker.display_seq}` : "-"}</span>
          <span>entry {shortTime(auto.entry_at)}</span>
          <span>4H {shortTime(auto.benchmark_at)}</span>
        </div>
      )}
    </section>
  );
}
