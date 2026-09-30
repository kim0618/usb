import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import CryptoPaperPage from "@/app/crypto-paper/page";
import { ARM_CONFIRMATION } from "@/lib/crypto-live";

/** The whole operator flow, on the page the operator actually loads.
 *
 *  The pieces are unit-tested elsewhere; what this file checks is the sentence the change was
 *  asked for as: BINANCE LIVE, 거래 활성화, 확인, 거래가능, 수량, LONG, 실주문 확인, 체결 - with
 *  no terminal, no environment variable and no typed phrase anywhere in it, and with the server
 *  still the only thing that decides whether the buttons work.
 *
 *  The fake server below is deliberately a state machine rather than a fixed payload: `armed`
 *  flips only when the POST arrives, and the account snapshot reports it through the router's
 *  gate view, so a screen that turned green on the click alone would fail here.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function fakeBinance({ capability = true }: { capability?: boolean } = {}) {
  const server = { armed: false, ready: true, stale: false, posted: [] as string[],
                   orders: [] as unknown[] };
  const armView = () => ({
    armed: server.armed, armed_by: server.armed ? "SESSION" : null,
    armed_at_ms: server.armed ? 1 : null, expires_at_ms: server.armed ? 2 : null,
    remaining_s: server.armed ? 900 : null, ttl_s: 900, arm_count: server.armed ? 1 : 0,
    disarm_count: 0, last_disarm_reason: "BOOT", last_disarm_ms: null, operator_note: null,
    confirmation_phrase: ARM_CONFIRMATION, env_armed: false, role: "MANUAL_ONLY",
    available: true, capability,
  });
  const accountView = () => ({
    symbol: "BTCUSDT", ready: server.ready, blockers: server.ready ? [] : [
      { code: "BINANCE_AUTH_FAILED", message: "거부" }],
    fetched_at_ms: 1, age_ms: server.stale ? 40_000 : 900, stale: server.stale,
    source: "BINANCE_LIVE",
    balance: { asset: "USDT", wallet_balance: "374.48", available_balance: "374.41",
      margin_balance: "374.48", unrealized_pnl: "0", max_withdraw: "374.41",
      initial_margin: "0", maint_margin: "0", account_wallet_usd: "374.29",
      account_available_usd: "374.21", account_margin_usd: "374.29",
      account_unrealized_usd: "0", usd_valuation_ratio: "0.9995", update_time_ms: 1,
      balance_source: "binance" },
    position: { symbol: "BTCUSDT", side: null, position_side: "BOTH", qty: "0",
      signed_qty: "0", entry_price: null, break_even_price: null, mark_price: "83394.90",
      unrealized_pnl: "0", liquidation_price: null, isolated_margin: null, notional: "0",
      initial_margin: "0", maint_margin: "0", adl: 0, update_time_ms: null, is_flat: true },
    symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20",
      max_notional: null, is_auto_add_margin: false },
    position_mode: { dual_side: false, mode: "ONE_WAY" },
    commission: { symbol: "BTCUSDT", maker: "0.0002", taker: "0.0005", source: "binance" },
    mark: { symbol: "BTCUSDT", mark_price: "83394.90", index_price: "83400.00",
      last_funding_rate: "0.00007", next_funding_time_ms: null },
    book: { best_bid: "83394.80", best_ask: "83395.00", bid_qty: "1", ask_qty: "1",
      spread: "0.20" },
    filters: null, krw: null, position_krw: null, krw_per_usdt: null, krw_note: "",
    // The router's own verdict: capability AND session, exactly as the backend computes it.
    gates: { armed: capability && server.armed, env_flag: capability,
             client_armed: server.armed, env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
    stream: null,
  });

  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method || "GET";
    if (url.includes("/binance/status")) {
      return ok({ source: "BINANCE_LIVE", available: true, ready: true, blockers: [],
                  gates: accountView().gates, config: {}, runtime_error: null, endpoints: [] });
    }
    if (url.includes("/binance/account")) return ok(accountView());
    if (url.includes("/binance/arm")) {
      if (method === "POST") {
        server.posted.push(String(init?.body));
        if (!capability) {
          return { ok: false, status: 409, json: async () => ({ error: {
            code: "LIVE_TRADING_DISABLED", message: "이 서버는 무장할 수 없습니다." } }) };
        }
        server.armed = true;
        // The real route answers a POST with the session view plus `available` and `gates` -
        // and without `capability`, which is why the gate reads the flag off the account.
        const posted: Record<string, unknown> = { ...armView() };
        delete posted.capability;
        return ok({ ...posted, gates: accountView().gates });
      }
      return ok(armView());
    }
    if (url.includes("/binance/disarm")) { server.armed = false; return ok(armView()); }
    if (url.includes("/binance/leverage")) {
      return ok({ symbol: "BTCUSDT", current: "20", margin_type: "CROSSED", max_leverage: 50,
                  options: [1, 5, 10, 20], brackets: [], notional_coef: null,
                  authority: "binance", margin_type_note: "읽기 전용" });
    }
    if (url.includes("/binance/preview")) {
      return ok({ source: "BINANCE_LIVE", symbol: "BTCUSDT", krw_per_usdt: null,
                  mark_price: "83394.90", fetched_at_ms: 1, sides: {} });
    }
    if (url.includes("/binance/order")) {
      server.orders.push(JSON.parse(String(init?.body)));
      return ok({ plan: {}, response: { status: "FILLED" } });
    }
    // Everything the paper half asks for. It is not what this test is about, and a refusal is
    // handled by the paper hook exactly as a dead backend would be.
    return { ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE",
      message: "paper offline" } }) };
  }));
  return server;
}

const enterLive = async () => {
  render(<CryptoPaperPage />);
  await waitFor(() => expect(screen.getByTestId("source-BINANCE_LIVE")).toBeEnabled());
  fireEvent.click(screen.getByTestId("source-BINANCE_LIVE"));
  await waitFor(() => expect(screen.getByTestId("live-trade-state")).toBeInTheDocument());
};

describe("BINANCE LIVE, from entering the screen to a filled order", () => {
  it("opens 거래불가 with the order buttons dead", async () => {
    fakeBinance();
    await enterLive();
    expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래불가");
    await waitFor(() => expect(screen.getByTestId("live-long")).toBeDisabled());
    expect(screen.getByTestId("live-short")).toBeDisabled();
  });

  it("goes 거래 활성화 → 확인 → 거래가능 → LONG → 실주문, without leaving the screen", async () => {
    const server = fakeBinance();
    await enterLive();

    fireEvent.click(screen.getByTestId("live-activate"));
    expect(screen.getByTestId("live-activate-note"))
      .toHaveTextContent("실제 Binance 계좌에서 주문이 실행됩니다.");
    fireEvent.click(screen.getByTestId("live-activate-confirm"));

    // Green only after the server's own re-read says armed.
    await waitFor(() => expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래가능"));
    await waitFor(() => expect(screen.getByTestId("live-long")).toBeEnabled());
    expect(JSON.parse(server.posted[0])).toMatchObject({ confirmation: ARM_CONFIRMATION });

    fireEvent.change(screen.getByTestId("live-qty-input"), { target: { value: "0.002" } });
    fireEvent.click(screen.getByTestId("live-long"));
    // The real-order confirmation is untouched by this change.
    expect(screen.getByTestId("live-confirm")).toHaveTextContent("실계좌");
    fireEvent.click(screen.getByTestId("live-submit"));
    await waitFor(() => expect(server.orders).toHaveLength(1));
    expect(server.orders[0]).toEqual({ side: "LONG", intent: "OPEN", qty: "0.002" });
  });

  it("never asks the operator to type the confirmation phrase", async () => {
    fakeBinance();
    await enterLive();
    fireEvent.click(screen.getByTestId("live-activate"));
    expect(screen.queryByTestId("live-arm-input")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(ARM_CONFIRMATION);
  });

  it("locks itself again the moment the server stops saying armed", async () => {
    // Expiry, a manual disarm and a restart all reach the screen the same way: the next poll
    // answers armed=false. There is no client-side timer to disagree with.
    const server = fakeBinance();
    await enterLive();
    fireEvent.click(screen.getByTestId("live-activate"));
    fireEvent.click(screen.getByTestId("live-activate-confirm"));
    await waitFor(() => expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래가능"));

    server.armed = false;
    await waitFor(() => expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래불가"),
      { timeout: 4_000 });
    expect(screen.getByTestId("live-long")).toBeDisabled();
    expect(screen.getByTestId("live-short")).toBeDisabled();
  });

  it("locks itself when the account snapshot goes stale, armed or not", async () => {
    const server = fakeBinance();
    await enterLive();
    fireEvent.click(screen.getByTestId("live-activate"));
    fireEvent.click(screen.getByTestId("live-activate-confirm"));
    await waitFor(() => expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래가능"));

    server.stale = true;
    await waitFor(() => expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래불가"),
      { timeout: 4_000 });
    expect(screen.getByTestId("live-long")).toBeDisabled();
    fireEvent.click(screen.getByTestId("live-trade-state"));
    expect(screen.getByTestId("live-trade-detail")).toHaveTextContent("계좌 응답 지연");
  });

  it("shows the server's refusal when the deployment has no capability", async () => {
    fakeBinance({ capability: false });
    await enterLive();
    // The button is dead before it is pressed: this is a server setting, not a click.
    expect(screen.getByTestId("live-activate")).toBeDisabled();
    fireEvent.click(screen.getByTestId("live-trade-state"));
    expect(screen.getByTestId("live-trade-detail"))
      .toHaveTextContent("BINANCE_LIVE_TRADING_ENABLED=false");
  });
});
