import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import {
  LiveActivateDialog, LiveLeveragePanel, LiveTradeBar, useBinanceLive,
} from "@/components/crypto-live-terminal";
import { ARM_CONFIRMATION, LIVE_DEFAULT_LEVERAGE, liveTradeGate } from "@/lib/crypto-live";
import type { LiveAccount, LiveArmState, LiveLeverageOptions } from "@/lib/crypto-live";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const arm = (overrides: Partial<LiveArmState> = {}): LiveArmState => ({
  armed: false, armed_by: null, armed_at_ms: null, expires_at_ms: null, remaining_s: null,
  ttl_s: 900, arm_count: 0, disarm_count: 0, last_disarm_reason: "BOOT", last_disarm_ms: null,
  operator_note: null, confirmation_phrase: ARM_CONFIRMATION, env_armed: false,
  role: "MANUAL_ONLY", available: true, capability: true,
  ...overrides,
});

const options = (overrides: Partial<LiveLeverageOptions> = {}): LiveLeverageOptions => ({
  symbol: "BTCUSDT", current: "10", margin_type: "CROSSED", max_leverage: 150,
  options: [1, 2, 3, 5, 10, 20, 50], brackets: [], notional_coef: null,
  authority: "binance GET /fapi/v1/leverageBracket",
  margin_type_note: "마진 모드는 Binance에서 설정한 값을 그대로 표시합니다.",
  ...overrides,
});

const account = (overrides: Partial<LiveAccount> = {}): LiveAccount => ({
  symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1_790_639_000_000, age_ms: 900,
  stale: false, source: "BINANCE_LIVE",
  balance: {
    asset: "USDT", wallet_balance: "374.48970275", available_balance: "374.41481086",
    margin_balance: "374.48970275", unrealized_pnl: "0", max_withdraw: "374.41481086",
    initial_margin: "0", maint_margin: "0", account_wallet_usd: "374.29",
    account_available_usd: "374.21", account_margin_usd: "374.29", account_unrealized_usd: "0",
    usd_valuation_ratio: "0.9995", update_time_ms: 1_790_639_000_000, balance_source: "binance",
  },
  position: {
    symbol: "BTCUSDT", side: null, position_side: "BOTH", qty: "0", signed_qty: "0",
    entry_price: null, break_even_price: null, mark_price: "83394.90", unrealized_pnl: "0",
    liquidation_price: null, isolated_margin: null, notional: "0", initial_margin: "0",
    maint_margin: "0", adl: 0, update_time_ms: null, is_flat: true,
  },
  symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20",
                   max_notional: "100000000", is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol: "BTCUSDT", maker: "0.000200", taker: "0.000500", source: "binance" },
  mark: { symbol: "BTCUSDT", mark_price: "83394.90", index_price: "83400.00",
          last_funding_rate: "0.00007207", next_funding_time_ms: null },
  book: { best_bid: "83394.80", best_ask: "83395.00", bid_qty: "2.1", ask_qty: "1.4",
          spread: "0.20" },
  filters: null, krw: null, position_krw: null, krw_per_usdt: null, krw_note: "",
  gates: { armed: false, env_flag: true, client_armed: false,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null,
  ...overrides,
});


/** The bar is rendered the way the screen renders it: from the server's two answers, through
 *  the one gate function, never from a prop somebody set by hand. A test that passed
 *  `tradable` in directly would pass while the screen lied. */
const bar = (props: Partial<{
  account: LiveAccount | null; arm: LiveArmState | null; onActivate: () => void;
  onDisarm: () => void; busy: boolean; error: string | null;
}> = {}) => {
  const acct = props.account === undefined ? account() : props.account;
  const armState = props.arm === undefined ? arm() : props.arm;
  return render(<LiveTradeBar account={acct} gate={liveTradeGate(acct, armState)}
    onActivate={props.onActivate ?? vi.fn()} onDisarm={props.onDisarm ?? vi.fn()}
    busy={props.busy} error={props.error} />);
};

const armed = (overrides: Partial<LiveArmState> = {}): LiveArmState =>
  arm({ armed: true, armed_by: "SESSION", remaining_s: 185, ...overrides });

const armedAccount = (overrides: Partial<LiveAccount> = {}): LiveAccount => account({
  gates: { armed: true, env_flag: true, client_armed: true,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  ...overrides,
});


describe("the trade state bar", () => {
  it("opens 거래불가 and offers one button to change it", () => {
    bar();
    expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래불가");
    expect(screen.getByTestId("live-activate")).toBeEnabled();
  });

  it("shows the sync age and the trade state, and nothing about arming", () => {
    // The screen this replaces led with 무장 / 해제됨 / 실주문 잠금 and a paragraph naming the
    // environment variable. The operator needs two facts to act; the rest is on request.
    bar();
    const rendered = screen.getByTestId("live-trade-bar");
    expect(screen.getByTestId("live-sync")).toHaveTextContent("동기화 1초 전");
    expect(rendered).not.toHaveTextContent("무장");
    expect(rendered).not.toHaveTextContent("해제됨");
    expect(rendered).not.toHaveTextContent("실주문 잠금");
    expect(rendered).not.toHaveTextContent("BINANCE_LIVE_TRADING_ENABLED");
  });

  it("does not arm on the button itself - it asks first", () => {
    const onActivate = vi.fn();
    bar({ onActivate });
    fireEvent.click(screen.getByTestId("live-activate"));
    expect(onActivate).toHaveBeenCalled();
  });

  it("turns 거래가능 only once the server itself reports armed", () => {
    // Both halves have to agree: the router's gate view on the account snapshot and the arm
    // session. A screen that flipped green on the click would be claiming the server's answer.
    bar({ account: armedAccount(), arm: armed() });
    expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래가능");
    expect(screen.queryByTestId("live-activate")).not.toBeInTheDocument();
  });

  it("keeps the window's countdown out of the default bar", () => {
    // BTCUSDT trades around the clock. A countdown beside the verdict read as trading hours,
    // so the remaining time moved to the panel about the arm session, where it belongs.
    bar({ account: armedAccount(), arm: armed() });
    expect(screen.queryByTestId("live-arm-remaining")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("live-trade-state"));
    expect(screen.getByTestId("live-arm-remaining")).toHaveTextContent("3분 5초 남음");
  });

  it("carries no notion of a trading session or market hours", () => {
    // The gate is account state, never a clock. These are equity-screen words and must not
    // reach a perpetual futures terminal.
    bar({ account: armedAccount(), arm: armed() });
    const rendered = screen.getByTestId("live-trade-bar");
    for (const word of ["거래가능시간", "거래 가능 시간", "장 시작", "장 마감", "시장 오픈",
                        "시장 종료", "정규장", "거래 시간대"]) {
      expect(rendered).not.toHaveTextContent(word);
    }
  });

  it("stays 거래불가 while only the session says armed", () => {
    // The account snapshot is the router's verdict. If the two reads disagree, the screen
    // takes the shut one.
    bar({ account: account(), arm: armed() });
    expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래불가");
  });

  it.each([
    ["the window expired", armedAccount({ gates: { armed: false, env_flag: true,
      client_armed: false, env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" } }),
      arm({ armed: false, last_disarm_reason: "EXPIRED" })],
    ["the operator disarmed", account(), arm({ armed: false, last_disarm_reason: "MANUAL" })],
    ["the server restarted", account(), arm({ armed: false, last_disarm_reason: "BOOT" })],
    ["the key was refused", armedAccount({ ready: false,
      blockers: [{ code: "BINANCE_AUTH_FAILED", message: "거부" }] }), armed()],
    ["the snapshot went stale", armedAccount({ stale: true, age_ms: 40_000 }), armed()],
  ])("falls back to 거래불가 when %s", (_label, acct, armState) => {
    bar({ account: acct, arm: armState });
    expect(screen.getByTestId("live-trade-state")).toHaveTextContent("거래불가");
  });

  it("explains the block only when the badge is clicked", () => {
    bar({ account: armedAccount({ stale: true, age_ms: 40_000 }), arm: armed() });
    expect(screen.queryByTestId("live-trade-detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("live-trade-state"));
    expect(screen.getByTestId("live-trade-detail")).toHaveTextContent("계좌 응답 지연");
  });

  it("cannot be activated at all when the deployment has no capability", () => {
    bar({ arm: arm({ capability: false }) });
    expect(screen.getByTestId("live-activate")).toBeDisabled();
    fireEvent.click(screen.getByTestId("live-trade-state"));
    expect(screen.getByTestId("live-trade-detail"))
      .toHaveTextContent("BINANCE_LIVE_TRADING_ENABLED=false");
  });

  it("keeps the manual disarm, inside the detail", () => {
    const onDisarm = vi.fn();
    bar({ account: armedAccount(), arm: armed(), onDisarm });
    fireEvent.click(screen.getByTestId("live-trade-state"));
    fireEvent.click(screen.getByTestId("live-disarm"));
    expect(onDisarm).toHaveBeenCalled();
  });

  it("says when the process was armed by its environment rather than by a click", () => {
    bar({ account: armedAccount(), arm: armed({ armed_by: "ENV", env_armed: true, remaining_s: null }) });
    fireEvent.click(screen.getByTestId("live-trade-state"));
    expect(screen.getByTestId("live-arm-by-env")).toBeInTheDocument();
    // Nothing on this screen can take away an environment arm; the button would lie.
    expect(screen.queryByTestId("live-disarm")).not.toBeInTheDocument();
  });

  it("still states the three ways the window closes, one click away", () => {
    bar();
    fireEvent.click(screen.getByTestId("live-trade-state"));
    const note = screen.getByTestId("live-arm-note");
    expect(note).toHaveTextContent("제한 시간");
    expect(note).toHaveTextContent("재시작");
    expect(note).toHaveTextContent("AUTO");
  });

  it("keeps a refused activation on screen without opening the detail", () => {
    bar({ error: "실주문 잠금 · 무장할 수 없습니다." });
    expect(screen.getByTestId("live-arm-error")).toBeInTheDocument();
    expect(screen.queryByTestId("live-trade-detail")).not.toBeInTheDocument();
  });
});


describe("the activation dialog", () => {
  it("states what the click does, in the account's terms", () => {
    render(<LiveActivateDialog open onCancel={vi.fn()} onConfirm={vi.fn()} ttlS={900} />);
    expect(screen.getByTestId("live-activate-note"))
      .toHaveTextContent("실제 Binance 계좌에서 주문이 실행됩니다.");
    expect(screen.getByTestId("live-activate-ttl")).toHaveTextContent("15분");
  });

  it("no longer asks anyone to type the confirmation phrase", () => {
    render(<LiveActivateDialog open onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId("live-arm-input")).not.toBeInTheDocument();
    expect(screen.getByTestId("live-activate-dialog")).not.toHaveTextContent(ARM_CONFIRMATION);
    expect(screen.getByTestId("live-activate-confirm")).toBeEnabled();
  });

  it("cancels without calling anything", () => {
    const onCancel = vi.fn(); const onConfirm = vi.fn();
    render(<LiveActivateDialog open onCancel={onCancel} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId("live-activate-cancel"));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("confirms on the one red button", () => {
    const onConfirm = vi.fn();
    render(<LiveActivateDialog open onCancel={vi.fn()} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId("live-activate-confirm"));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("is not in the tree until it is asked for", () => {
    render(<LiveActivateDialog open={false} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId("live-activate-dialog")).not.toBeInTheDocument();
  });
});


describe("arming from the screen", () => {
  it("sends the server's confirmation phrase without the operator typing it", async () => {
    // The phrase did not stop being required. It stopped being the operator's job: the server
    // still refuses an arm request that does not carry it verbatim.
    const posted: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") posted.push(String(init.body));
      return { ok: true, json: async () => (String(url).includes("/arm")
        ? { armed: true, available: true, capability: true } : { available: true }) };
    }));
    const { result } = renderHook(() => useBinanceLive(false));
    await act(async () => { await result.current.armLive(); });
    await waitFor(() => expect(posted.length).toBeGreaterThan(0));
    expect(JSON.parse(posted[0])).toMatchObject({ confirmation: ARM_CONFIRMATION });
  });
});


describe("the leverage panel", () => {
  it("offers only the values the account's bracket table allows", () => {
    render(<LiveLeveragePanel account={account()} options={options({ max_leverage: 10,
      options: [1, 2, 3, 5, 10] })} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-10")).toBeInTheDocument();
    expect(screen.queryByTestId("live-leverage-20")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-leverage-50")).not.toBeInTheDocument();
  });

  it("marks the value Binance currently reports, not the one last clicked", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    // The account fixture says 20x; the options payload says current 10x. The account is the
    // one Binance answered most recently, and it wins.
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("live-leverage-10")).toHaveAttribute("aria-pressed", "false");
  });

  it("sends the chosen value and does not repaint until the caller refreshes", () => {
    const onSelect = vi.fn();
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("live-leverage-5"));
    expect(onSelect).toHaveBeenCalledWith(5);
    // Still 20x on screen: the panel shows what Binance said, never what was requested.
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("aria-pressed", "true");
  });

  it("refuses to change leverage while a position is open", () => {
    const open = account({
      position: { ...account().position!, is_flat: false, side: "LONG", qty: "0.001",
                  notional: "83.39", initial_margin: "4.17", liquidation_price: "45120.10" },
    });
    render(<LiveLeveragePanel account={open} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-5")).toBeDisabled();
    expect(screen.getByTestId("live-leverage-locked")).toBeInTheDocument();
  });

  it("removes duplicate position metrics from the leverage panel", () => {
    const open = account({
      position: { ...account().position!, is_flat: false, side: "LONG", qty: "0.001",
                  notional: "83.39", initial_margin: "4.17", liquidation_price: "45120.10" },
    });
    render(<LiveLeveragePanel account={open} options={options()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-risk-notional")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-risk-margin")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-risk-liq")).not.toBeInTheDocument();
  });

  it("does not render empty position metrics while the account is flat", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-risk-liq")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-risk-available")).not.toBeInTheDocument();
  });

  it("says leverage is a margin setting and not an exposure multiplier", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    const note = screen.getByTestId("live-leverage-sizing-note");
    expect(note).toHaveTextContent("증거금 설정");
    expect(note).toHaveTextContent("0.001 BTC");
  });

  it("marks the operating default without being on it", () => {
    // The account fixture is on 20x. The tag says which value operations settled on; it does
    // not claim the account is there, and it does not put it there.
    const onSelect = vi.fn();
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={onSelect} />);
    expect(screen.getByTestId("live-leverage-policy-tag")).toBeInTheDocument();
    expect(screen.getByTestId(`live-leverage-${LIVE_DEFAULT_LEVERAGE}`))
      .toHaveAttribute("aria-pressed", "false");
    // Rendering is not a decision: nothing was sent.
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("says the screen will not apply the default by itself", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    const note = screen.getByTestId("live-leverage-policy-note");
    expect(note).toHaveTextContent(`운영 기본은 ${LIVE_DEFAULT_LEVERAGE}x`);
    expect(note).toHaveTextContent("화면이 알아서 바꾸지 않으니");
  });

  it("drops the nudge once Binance reports the default", () => {
    const onPolicy = account({ symbol_config: { ...account().symbol_config!,
      leverage: String(LIVE_DEFAULT_LEVERAGE) } });
    render(<LiveLeveragePanel account={onPolicy} options={options()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-leverage-policy-note")).not.toBeInTheDocument();
    expect(screen.getByTestId(`live-leverage-${LIVE_DEFAULT_LEVERAGE}`))
      .toHaveAttribute("aria-pressed", "true");
  });

  it("sends the default only on a click, and still shows Binance's value afterwards", () => {
    const onSelect = vi.fn();
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId(`live-leverage-${LIVE_DEFAULT_LEVERAGE}`));
    expect(onSelect).toHaveBeenCalledWith(LIVE_DEFAULT_LEVERAGE);
    // No optimistic repaint: the account still says 20x until a refreshed snapshot says otherwise.
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId(`live-leverage-${LIVE_DEFAULT_LEVERAGE}`))
      .toHaveAttribute("aria-pressed", "false");
  });

  it("does not nudge while a position is holding the setting", () => {
    const open = account({
      position: { ...account().position!, is_flat: false, side: "LONG", qty: "0.001",
                  notional: "83.39", initial_margin: "4.17", liquidation_price: "45120.10" },
    });
    render(<LiveLeveragePanel account={open} options={options()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-leverage-policy-note")).not.toBeInTheDocument();
    expect(screen.getByTestId("live-leverage-locked")).toBeInTheDocument();
    expect(screen.getByTestId(`live-leverage-${LIVE_DEFAULT_LEVERAGE}`)).toBeDisabled();
  });

  it("says nothing about a default this account's bracket cannot reach", () => {
    render(<LiveLeveragePanel account={account()} onSelect={vi.fn()}
      options={options({ max_leverage: 5, options: [1, 2, 3, 5] })} />);
    expect(screen.queryByTestId("live-leverage-policy-tag")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-leverage-policy-note")).not.toBeInTheDocument();
  });

  it("states that the margin mode is read only here", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-margin-readonly-note")).toHaveTextContent("Binance");
  });

  it("does not repeat available balance below leverage", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-risk-available")).not.toBeInTheDocument();
  });
});
