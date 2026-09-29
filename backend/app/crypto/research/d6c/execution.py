"""Walking the grid and handing every trade to the real paper engine.

The harness decides *when* and *how much*; the engine decides what the fill and the money look
like. That split is what keeps a second accounting implementation from existing: fees, funding
and the equity identity all come from `app.crypto.paper`, the same code the live terminal runs.

The engine is fed sparsely. It sees a quote at the entry bar, a quote at each funding settlement
crossed while the position is open, and a quote at the exit bar. That is enough for exact
funding, because `_settle_funding` walks the 8 hour grid from the previous observation and takes
the rate off the quote it is given: feeding the settlement bar itself hands it that bar's rate.
Between trades the position is flat, and a flat position accrues nothing.

**Per-trade net is taken from the account's cumulative counters, not from the close event.**
`POSITION_CLOSE.net_pnl` is `gross - exit_fee`: it excludes the entry fee, which was charged when
the position opened, and all funding. Reading it as the trade's net would understate cost. The
counters are the single authority the paper contract names, and the flat-to-flat equity change is
asserted against them on every trade.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np

from ...paper.book import BookSide, Quote
from ...paper.config import build_config
from ...paper.engine import FUNDING_INTERVAL_MS, PaperEngine
from ...paper.historical import SyntheticBook
from ...paper.instrument import RiskTierTable
from ...paper.ledger import EventType
from ..d6 import sizing as SIZING
from ..d6.contract import Contract as StrategyContract
from .contract import FEE_VERIFICATION, RISK_LIMIT, BacktestContract
from .signals import SignalPass

MINUTE_MS = 60_000

TIME_EXIT = "X1_TIME_EXIT"
STOP_EXIT = "X2_VOLATILITY_STOP"

REJECT_QTY = "EXECUTION_REJECT_QTY_BELOW_MIN"
REJECT_NOTIONAL = "EXECUTION_REJECT_NOTIONAL_BELOW_MIN"
REJECT_ENGINE = "EXECUTION_REJECT_ENGINE"


def to_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp() * 1000)


def target_notional(strategy: StrategyContract, equity: float, stop_distance: float) -> float:
    """`equity x risk_budget / stop_distance`, capped at `equity x cap`.

    Price free, which is what makes it a decision-time quantity under I1. At the contract's own
    stop multiplier this reproduces `d6.sizing.size`, which `test_d6c_execution.py` asserts; the
    argument exists because the G8 sensitivity arms move the multiplier.
    """
    budget = strategy.sizing["risk_budget"]
    cap = strategy.sizing["notional_cap_over_equity"]
    return min(equity * budget / stop_distance, equity * cap)


@dataclass
class Trade:
    entry_index: int
    decision_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    entry_reference_price: float
    entry_fill_price: float
    exit_reference_price: float
    exit_fill_price: float
    qty: float
    target_notional: float
    entry_notional: float
    exit_notional: float
    stop_distance: float
    stop_price: float
    exit_reason: str
    hold_minutes: float
    long_score: int
    vol_label: int
    gross_pnl_usdt: float
    fees_usdt: float
    funding_usdt: float
    net_usdt: float
    gross_bp: float
    net_bp: float
    equity_before: float
    equity_after: float
    gap_open_below_stop: bool
    same_bar_stop_and_expiry: bool

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RunResult:
    arm: str
    trades: list[Trade] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)
    excluded_unfinished: int = 0
    signals: int = 0
    starting_equity: float = 0.0
    final_equity: float = 0.0
    max_qty: float = 0.0
    funding_events: int = 0
    engine_fee_total: float = 0.0
    engine_funding_total: float = 0.0
    identity_violations: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"arm": self.arm, "trades": len(self.trades), "rejects": len(self.rejects),
                "reject_codes": _count(r["code"] for r in self.rejects),
                "excluded_unfinished": self.excluded_unfinished, "signals": self.signals,
                "starting_equity": self.starting_equity, "final_equity": self.final_equity,
                "max_qty": self.max_qty, "funding_events": self.funding_events,
                "engine_fee_total": self.engine_fee_total,
                "engine_funding_total": self.engine_funding_total,
                "identity_violations": self.identity_violations}


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def build_engine(backtest: BacktestContract) -> PaperEngine:
    """A throwaway in-memory account. No run directory, no production config, no ledger file."""
    account = backtest.account
    fees = json.loads(FEE_VERIFICATION.read_text())["adopted"]
    tiers = RiskTierTable.from_payload(json.loads(RISK_LIMIT.read_text()),
                                       source=RISK_LIMIT.as_posix(), source_sha256="d6c")
    config = build_config(
        run_id="d6c-historical", starting_capital_krw=account["starting_capital_krw"],
        fx_krw_per_usdt=account["fx_krw_per_usdt"], fx_source=account["fx_source"],
        fx_asof_utc="2026-09-23T00:00:00Z", fee_version="VIP_0",
        fee_taker_rate=fees["taker_rate"], fee_maker_rate=fees["maker_rate"],
        fee_source="fee_source_verification_v1.json", fee_effective_date="2026-09-02",
        slippage_model="NONE", slippage_bps="0", leverage=str(account["leverage"]),
        risk_limit_source=RISK_LIMIT.as_posix(), risk_limit_sha256="d6c")
    engine = PaperEngine(config, tiers)
    engine.start(0)
    return engine


def _quote(book: SyntheticBook, ts_ms: int, price: float, mark: float,
           funding_rate: float | None) -> Quote:
    bids, asks = book.sides(Decimal(str(price)))
    return Quote(ts_ms=ts_ms,
                 bids=BookSide.from_rows(bids, descending=True),
                 asks=BookSide.from_rows(asks, descending=False),
                 mark_price=Decimal(str(mark)), last_price=Decimal(str(price)),
                 funding_rate=None if funding_rate is None else Decimal(str(funding_rate)),
                 next_funding_time_ms=None)


def _funding_rate_at(funding_ts: np.ndarray, funding_rate: np.ndarray,
                     ts_ms: int) -> float | None:
    pos = int(np.searchsorted(funding_ts, ts_ms, side="right")) - 1
    return None if pos < 0 else float(funding_rate[pos])


def run_arm(grid: dict[str, np.ndarray], backtest: BacktestContract, signals: SignalPass,
            arm_name: str) -> RunResult:
    """One arm end to end: walk the bars, place the trades, let the engine keep the books."""
    strategy: StrategyContract = backtest.strategy
    spec = backtest.arm(arm_name)
    hold_bars = int(spec["hold_min"])
    stop_enabled = bool(spec["stop_enabled"])
    cooldown_ms = strategy.bound["cooldown"]["cooldown_ms"]
    guard_pct = strategy.bound["daily_loss_guard"]["guard_pct"]
    max_streak = strategy.bound["consecutive_loss"]["max_consecutive"]
    streak_block_ms = strategy.bound["consecutive_loss"]["block_ms"]
    step = Decimal(str(strategy.qty_step))

    fill = backtest.fill_model
    book = SyntheticBook(spread=Decimal(fill["spread_usdt"]),
                         depth_btc=Decimal(fill["depth_btc_per_side"]),
                         tick=Decimal(fill["tick"]))
    engine = build_engine(backtest)
    result = RunResult(arm=arm_name,
                       starting_equity=float(engine.account.starting_capital_usdt))

    ts, close, open_ = grid["ts"], grid["close"], grid["open"]
    mark_low, mark_close = grid["mark_low"], grid["mark_close"]
    funding_ts, funding_rate = grid["funding_ts"], grid["funding_rate"]
    eligible = signals.bar_eligible
    n = len(ts)

    # The walk starts at the OOS window, not at the sample start. The strategy has nothing to fit,
    # so simulating 2021 would buy no information and would leave the first reported trade running
    # on a compounded balance the contract never fixed. Starting here makes the equity curve begin
    # at exactly the contracted initial capital. Feature history still reaches back through the
    # whole grid; only the trading does not.
    oos_start = to_ms(backtest.doc["window"]["oos_start_utc"])
    last_exit_ts: int | None = None
    consecutive_losses = 0
    last_loss_ts: int | None = None
    day_pnl: dict[int, float] = {}
    request = 0
    index = max(0, int(np.searchsorted(ts, oos_start, side="left")) - 1)

    while index < n - 1:
        if not eligible[index]:
            index += 1
            continue
        decision_ts = int(ts[index]) + MINUTE_MS
        result.signals += 1

        # --- state-dependent hard filters. H9 is implied: the walk is only here when flat.
        if last_exit_ts is not None and decision_ts - last_exit_ts < cooldown_ms:
            index += 1
            continue
        if day_pnl.get(decision_ts // 86_400_000, 0.0) <= -guard_pct:
            index += 1
            continue
        if consecutive_losses >= max_streak and last_loss_ts is not None \
                and decision_ts - last_loss_ts < streak_block_ms:
            index += 1
            continue

        entry_index = index + 1
        exit_index = entry_index + hold_bars
        if exit_index >= n:
            result.excluded_unfinished += 1
            index += 1
            continue

        # --- execution at t+1 (I1): reference price, then quantity ------------------------
        entry_ts = int(ts[entry_index])
        reference = float(open_[entry_index])
        stop_distance = float(signals.stop_distance[index])
        mark_now = Decimal(str(mark_close[index]))
        equity_before = float(engine.account.equity(mark_now))

        notional = target_notional(strategy, equity_before, stop_distance)
        qty = float(SIZING.floor_to_step(Decimal(str(notional)) / Decimal(str(reference)), step))

        if qty < strategy.min_order_qty:
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_QTY, "qty": qty})
            index += 1
            continue
        if qty * reference < strategy.min_notional_usdt:
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_NOTIONAL,
                                   "notional": qty * reference})
            index += 1
            continue

        fees_before = engine.account.cumulative_fees
        funding_before = engine.account.cumulative_funding_paid
        realized_before = engine.account.realized_pnl

        engine.apply_market(_quote(book, entry_ts, reference, float(mark_close[entry_index]),
                                   _funding_rate_at(funding_ts, funding_rate, entry_ts)))
        request += 1
        try:
            engine.submit_order(ts_ms=entry_ts, side="LONG", qty=Decimal(str(qty)),
                                intent="OPEN", request_id=f"{arm_name}-{request}",
                                reason="FDN_V1")
        except Exception as exc:                      # noqa: BLE001 - recorded, never silent
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_ENGINE,
                                   "message": str(exc)})
            index += 1
            continue

        entry_fill = float(engine.account.position.avg_entry)
        stop_price = entry_fill * (1 - stop_distance)
        result.max_qty = max(result.max_qty, qty)

        # --- the exit bar ------------------------------------------------------------------
        # The position is open from the open of `entry_index` to the open of `exit_index`. Bars
        # strictly inside the hold can trigger the stop on their mark low; on the final bar only
        # the open instant exists before the time exit fires, so only a gap through the stop
        # counts there. That gap case is the contract's same-bar collision, and the stop wins.
        exit_reason = TIME_EXIT
        exit_bar = exit_index
        gap_open = False
        same_bar = False
        if stop_enabled:
            inside = mark_low[entry_index:exit_index]
            hit = np.nonzero(inside <= stop_price)[0]
            if len(hit):
                exit_bar = entry_index + int(hit[0])
                exit_reason = STOP_EXIT
                gap_open = float(open_[exit_bar]) < stop_price
            elif float(open_[exit_index]) <= stop_price:
                # The time exit fires at this bar's open. Only that instant exists before it, so
                # the bar's own low and close are past the exit and must not be consulted.
                exit_reason = STOP_EXIT
                same_bar = True
                gap_open = float(open_[exit_index]) < stop_price

        exit_reference = (min(stop_price, float(open_[exit_bar])) if exit_reason == STOP_EXIT
                          else float(open_[exit_bar]))
        exit_ts = int(ts[exit_bar])

        # --- funding settlements crossed while holding -------------------------------------
        boundary = (entry_ts // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
        while boundary <= exit_ts:
            bar = int(np.searchsorted(ts, boundary, side="left"))
            if bar >= n:
                break
            engine.apply_market(_quote(book, int(ts[bar]), float(close[bar]),
                                       float(mark_close[bar]),
                                       _funding_rate_at(funding_ts, funding_rate, int(ts[bar]))))
            result.funding_events += 1
            boundary += FUNDING_INTERVAL_MS

        engine.apply_market(_quote(book, exit_ts, exit_reference, float(mark_close[exit_bar]),
                                   _funding_rate_at(funding_ts, funding_rate, exit_ts)))
        first_new = len(engine.ledger.events)
        request += 1
        engine.submit_order(ts_ms=exit_ts, side="LONG", qty=Decimal(str(qty)),
                            intent="CLOSE", request_id=f"{arm_name}-{request}",
                            reason=exit_reason)
        closed = [e for e in engine.ledger.events[first_new:]
                  if e["event_type"] == EventType.POSITION_CLOSE]
        if not closed:
            raise RuntimeError(f"{arm_name}: close produced no POSITION_CLOSE at {exit_ts}")
        close_event = closed[-1]
        exit_fill = float(close_event["exit_price"])

        # --- money, from the account's own counters ---------------------------------------
        gross = float(engine.account.realized_pnl - realized_before)
        fees = float(engine.account.cumulative_fees - fees_before)
        funding = float(engine.account.cumulative_funding_paid - funding_before)
        net = gross - fees - funding

        equity_after = float(engine.account.equity(Decimal(str(mark_close[exit_bar]))))
        if abs((equity_after - equity_before) - net) > 1e-6:
            result.identity_violations += 1

        entry_notional = qty * entry_fill
        trade = Trade(
            entry_index=index, decision_ts_ms=decision_ts, entry_ts_ms=entry_ts,
            exit_ts_ms=exit_ts, entry_reference_price=reference, entry_fill_price=entry_fill,
            exit_reference_price=exit_reference, exit_fill_price=exit_fill, qty=qty,
            target_notional=notional, entry_notional=entry_notional,
            exit_notional=qty * exit_fill, stop_distance=stop_distance, stop_price=stop_price,
            exit_reason=exit_reason, hold_minutes=(exit_ts - entry_ts) / 60_000,
            long_score=int(signals.long_score[index]), vol_label=int(signals.vol_label[index]),
            gross_pnl_usdt=gross, fees_usdt=fees, funding_usdt=funding, net_usdt=net,
            gross_bp=(exit_reference / reference - 1) * 1e4,
            net_bp=net / entry_notional * 1e4 if entry_notional else 0.0,
            equity_before=equity_before, equity_after=equity_after,
            gap_open_below_stop=gap_open, same_bar_stop_and_expiry=same_bar)
        if entry_ts < oos_start:
            raise RuntimeError(f"{arm_name}: trade entered before the OOS window at {entry_ts}")
        result.trades.append(trade)

        last_exit_ts = exit_ts
        if net < 0:
            consecutive_losses += 1
            last_loss_ts = exit_ts
        else:
            consecutive_losses = 0
        day = exit_ts // 86_400_000
        day_pnl[day] = day_pnl.get(day, 0.0) + net / equity_before * 100

        index = exit_bar + 1

    result.final_equity = float(engine.account.equity(Decimal(str(mark_close[-1]))))
    result.engine_fee_total = float(engine.account.cumulative_fees)
    result.engine_funding_total = float(engine.account.cumulative_funding_paid)
    return result
