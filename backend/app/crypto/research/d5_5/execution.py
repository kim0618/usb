"""Turning confirmations into trades through the real paper engine.

Same division as D5.4 and D6-C: this module decides when and how much, the engine decides the
fill and the money. Per-trade net comes from the account's cumulative counters, because
`POSITION_CLOSE.net_pnl` is `gross - exit fee` and omits the entry fee and all funding.

The exit differs from D5.4 on purpose. There the bracket was symmetric and turned out to be a
coin flip; here the stop is a structural level the confirmation itself defines, candidates A and
C have no profit target at all, and only B has one, at the pre-shock price its thesis names.
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
from .confirmation import LONG, SHORT, Confirmation

MINUTE_MS = 60_000

TARGET_EXIT = "X1_TARGET"
STOP_EXIT = "X2_STRUCTURAL_STOP"
TIME_EXIT = "X3_MAX_HOLD"

REJECT_QTY = "EXECUTION_REJECT_QTY_BELOW_MIN"
REJECT_NOTIONAL = "EXECUTION_REJECT_NOTIONAL_BELOW_MIN"
REJECT_ENGINE = "EXECUTION_REJECT_ENGINE"
REJECT_NO_EXIT_WINDOW = "EXECUTION_REJECT_NO_EXIT_WINDOW"
REJECT_ROOM = "EXECUTION_REJECT_ROOM_BELOW_COST"
REJECT_STOP_SHAPE = "EXECUTION_REJECT_STOP_ON_WRONG_SIDE"


def to_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp() * 1000)


def target_notional(risk_budget: float, cap: float, equity: float, stop_distance: float) -> float:
    """Price free, so the decision can happen before the entry price exists."""
    return min(equity * risk_budget / stop_distance, equity * cap)


@dataclass
class Trade:
    candidate: str
    side: str
    event_start_index: int
    confirm_bar: int
    wait_minutes: float
    confirm_distance_bp: float
    shock_range_bp: float
    decision_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    entry_reference_price: float
    entry_fill_price: float
    exit_reference_price: float
    exit_fill_price: float
    stop_price: float
    target_price: float | None
    stop_distance: float
    qty: float
    target_notional: float
    entry_notional: float
    exit_notional: float
    exit_reason: str
    hold_minutes: float
    gross_pnl_usdt: float
    fees_usdt: float
    funding_usdt: float
    net_usdt: float
    gross_bp: float
    net_bp: float
    equity_before: float
    equity_after: float
    notional_capped: bool
    same_bar_target_and_stop: bool

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RunResult:
    candidate: str
    arm: str
    trades: list[Trade] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)
    events_total: int = 0
    events_in_oos: int = 0
    confirmed: int = 0
    confirmed_long: int = 0
    confirmed_short: int = 0
    no_trade: int = 0
    ignored_position_open: int = 0
    ignored_cooldown: int = 0
    starting_equity: float = 0.0
    final_equity: float = 0.0
    max_qty: float = 0.0
    identity_violations: int = 0
    confirmations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def confirmation_rate(self) -> float | None:
        return self.confirmed / self.events_in_oos if self.events_in_oos else None

    def as_dict(self) -> dict[str, Any]:
        codes: dict[str, int] = {}
        for row in self.rejects:
            codes[row["code"]] = codes.get(row["code"], 0) + 1
        return {"candidate": self.candidate, "arm": self.arm, "trades": len(self.trades),
                "rejects": len(self.rejects), "reject_codes": codes,
                "events_total": self.events_total, "events_in_oos": self.events_in_oos,
                "confirmed": self.confirmed, "confirmed_long": self.confirmed_long,
                "confirmed_short": self.confirmed_short, "no_trade": self.no_trade,
                "confirmation_rate": self.confirmation_rate,
                "ignored_position_open": self.ignored_position_open,
                "ignored_cooldown": self.ignored_cooldown,
                "starting_equity": self.starting_equity, "final_equity": self.final_equity,
                "max_qty": self.max_qty, "identity_violations": self.identity_violations}


def build_engine(contract: dict, fee_path, risk_limit_path) -> PaperEngine:
    risk = contract["risk"]
    fees = json.loads(fee_path.read_text())["adopted"]
    tiers = RiskTierTable.from_payload(json.loads(risk_limit_path.read_text()),
                                       source=risk_limit_path.as_posix(), source_sha256="d5_5")
    config = build_config(
        run_id="d5-5-confirmation", starting_capital_krw=risk["starting_capital_krw"],
        fx_krw_per_usdt=risk["fx_krw_per_usdt"], fx_source="D4 run_config record",
        fx_asof_utc="2026-09-23T00:00:00Z", fee_version="VIP_0",
        fee_taker_rate=fees["taker_rate"], fee_maker_rate=fees["maker_rate"],
        fee_source="fee_source_verification_v1.json", fee_effective_date="2026-09-02",
        slippage_model="NONE", slippage_bps="0", leverage=str(risk["leverage"]),
        risk_limit_source=risk_limit_path.as_posix(), risk_limit_sha256="d5_5")
    engine = PaperEngine(config, tiers)
    engine.start(0)
    return engine


def _quote(book: SyntheticBook, ts_ms: int, price: float, mark: float,
           funding_rate: float | None) -> Quote:
    bids, asks = book.sides(Decimal(str(price)))
    return Quote(ts_ms=ts_ms, bids=BookSide.from_rows(bids, descending=True),
                 asks=BookSide.from_rows(asks, descending=False),
                 mark_price=Decimal(str(mark)), last_price=Decimal(str(price)),
                 funding_rate=None if funding_rate is None else Decimal(str(funding_rate)),
                 next_funding_time_ms=None)


def _funding_rate_at(funding_ts: np.ndarray, funding_rate: np.ndarray,
                     ts_ms: int) -> float | None:
    position = int(np.searchsorted(funding_ts, ts_ms, side="right")) - 1
    return None if position < 0 else float(funding_rate[position])


def find_exit(side: str, grid: dict[str, np.ndarray], entry_index: int, last_index: int,
              stop_price: float, target_price: float | None
              ) -> tuple[int, str, float, bool]:
    """First structural level touched, on the mark extremes. Adverse first when both."""
    low = grid["mark_low"][entry_index:last_index + 1]
    high = grid["mark_high"][entry_index:last_index + 1]
    if side == LONG:
        hit_stop = low <= stop_price
        hit_target = (high >= target_price) if target_price is not None \
            else np.zeros(len(high), dtype=bool)
    else:
        hit_stop = high >= stop_price
        hit_target = (low <= target_price) if target_price is not None \
            else np.zeros(len(low), dtype=bool)
    touched = hit_stop | hit_target
    where = np.nonzero(touched)[0]
    if len(where) == 0:
        return last_index, TIME_EXIT, float(grid["open"][last_index]), False

    offset = int(where[0])
    index = entry_index + offset
    both = bool(hit_stop[offset] and hit_target[offset])
    bar_open = float(grid["open"][index])
    if hit_stop[offset]:
        price = min(stop_price, bar_open) if side == LONG else max(stop_price, bar_open)
        return index, STOP_EXIT, price, both
    price = max(target_price, bar_open) if side == LONG else min(target_price, bar_open)
    return index, TARGET_EXIT, price, both


def run(grid: dict[str, np.ndarray], contract: dict, candidate: dict, events, detector, *,
        arm: str, hold_bars: int, fee_path, risk_limit_path,
        oos_start_ms: int, oos_end_ms: int) -> RunResult:
    """One candidate, one arm. Confirm, then trade at most one position at a time."""
    risk = contract["risk"]
    lo_clip, hi_clip = contract["exits"]["stop_distance_clip"]
    cooldown_bars = contract["position"]["reentry_cooldown_bars"]
    min_margin = contract["confirmation_common"]["min_margin_bp"] / 1e4

    book = SyntheticBook(spread=Decimal(contract["costs"]["spread_usdt"]),
                         depth_btc=Decimal(contract["costs"]["depth_btc_per_side"]),
                         tick=Decimal("0.10"))
    engine = build_engine(contract, fee_path, risk_limit_path)
    result = RunResult(candidate=candidate["id"], arm=arm,
                       starting_equity=float(engine.account.starting_capital_usdt),
                       events_total=len(events))

    ts, open_, close, mark_close = grid["ts"], grid["open"], grid["close"], grid["mark_close"]
    funding_ts, funding_rate = grid["funding_ts"], grid["funding_rate"]
    n = len(ts)
    step = Decimal(str(risk["qty_step"]))
    budget, cap = risk["risk_budget_pct_of_equity"] / 100.0, 1.0
    request = 0
    busy_until = -1
    cooldown_until = -1

    for event in events:
        if not (oos_start_ms <= int(ts[event.start]) < oos_end_ms):
            continue
        result.events_in_oos += 1

        confirmation: Confirmation = detector(event)
        result.confirmations.append({"candidate": candidate["id"], "arm": arm,
                                     "event_ts_ms": int(ts[event.start]),
                                     **confirmation.as_dict()})
        if not confirmation.confirmed:
            result.no_trade += 1
            continue
        result.confirmed += 1
        if confirmation.side == LONG:
            result.confirmed_long += 1
        else:
            result.confirmed_short += 1

        bar = confirmation.bar
        if bar <= busy_until:
            result.ignored_position_open += 1
            continue
        if bar < cooldown_until:
            result.ignored_cooldown += 1
            continue

        entry_index = bar + 1
        last_index = entry_index + hold_bars
        if last_index >= n:
            result.rejects.append({"ts_ms": int(ts[bar]) + MINUTE_MS,
                                   "code": REJECT_NO_EXIT_WINDOW})
            continue

        decision_ts = int(ts[bar]) + MINUTE_MS
        reference = float(open_[entry_index])
        side = confirmation.side

        # The structural stop must sit on the losing side of the entry, or the trade is not the
        # one the confirmation described.
        raw_stop = confirmation.stop_price
        if (side == LONG and raw_stop >= reference) or (side == SHORT and raw_stop <= reference):
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_STOP_SHAPE})
            continue
        stop_distance = min(max(abs(reference - raw_stop) / reference, lo_clip), hi_clip)

        if confirmation.target_price is not None:
            room = abs(confirmation.target_price - reference) / reference
            if room < min_margin:
                result.rejects.append({"ts_ms": decision_ts, "code": REJECT_ROOM})
                continue

        equity_before = float(engine.account.equity(Decimal(str(mark_close[bar]))))
        notional = target_notional(budget, cap, equity_before, stop_distance)
        capped = notional >= equity_before * cap - 1e-12
        qty = float(SIZING.floor_to_step(Decimal(str(notional)) / Decimal(str(reference)), step))
        if qty < risk["min_order_qty"]:
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_QTY, "qty": qty})
            continue
        if qty * reference < risk["min_notional_usdt"]:
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_NOTIONAL})
            continue

        fees_before = engine.account.cumulative_fees
        funding_before = engine.account.cumulative_funding_paid
        realized_before = engine.account.realized_pnl

        entry_ts = int(ts[entry_index])
        engine.apply_market(_quote(book, entry_ts, reference, float(mark_close[entry_index]),
                                   _funding_rate_at(funding_ts, funding_rate, entry_ts)))
        request += 1
        try:
            engine.submit_order(ts_ms=entry_ts, side=side, qty=Decimal(str(qty)), intent="OPEN",
                                request_id=f"{candidate['id']}-{arm}-{request}",
                                reason="D5_5_CONFIRMED")
        except Exception as exc:                      # noqa: BLE001 - recorded, never silent
            result.rejects.append({"ts_ms": decision_ts, "code": REJECT_ENGINE,
                                   "message": str(exc)})
            continue

        entry_fill = float(engine.account.position.avg_entry)
        direction = 1.0 if side == LONG else -1.0
        stop_price = entry_fill * (1 - direction * stop_distance)
        target_price = None
        if confirmation.target_price is not None:
            target_price = entry_fill * (1 + direction * room)
        result.max_qty = max(result.max_qty, qty)

        exit_bar, reason, exit_reference, both = find_exit(
            side, grid, entry_index, last_index, stop_price, target_price)
        exit_ts = int(ts[exit_bar])

        boundary = (entry_ts // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
        while boundary <= exit_ts:
            funding_bar = int(np.searchsorted(ts, boundary, side="left"))
            if funding_bar >= n:
                break
            engine.apply_market(_quote(book, int(ts[funding_bar]), float(close[funding_bar]),
                                       float(mark_close[funding_bar]),
                                       _funding_rate_at(funding_ts, funding_rate,
                                                        int(ts[funding_bar]))))
            boundary += FUNDING_INTERVAL_MS

        engine.apply_market(_quote(book, exit_ts, exit_reference, float(mark_close[exit_bar]),
                                   _funding_rate_at(funding_ts, funding_rate, exit_ts)))
        first_new = len(engine.ledger.events)
        request += 1
        engine.submit_order(ts_ms=exit_ts, side=side, qty=Decimal(str(qty)), intent="CLOSE",
                            request_id=f"{candidate['id']}-{arm}-{request}", reason=reason)
        closed = [e for e in engine.ledger.events[first_new:]
                  if e["event_type"] == EventType.POSITION_CLOSE]
        if not closed:
            raise RuntimeError(f"{candidate['id']}: no POSITION_CLOSE at {exit_ts}")
        exit_fill = float(closed[-1]["exit_price"])

        gross = float(engine.account.realized_pnl - realized_before)
        fees = float(engine.account.cumulative_fees - fees_before)
        funding = float(engine.account.cumulative_funding_paid - funding_before)
        net = gross - fees - funding
        equity_after = float(engine.account.equity(Decimal(str(mark_close[exit_bar]))))
        if abs((equity_after - equity_before) - net) > 1e-6:
            result.identity_violations += 1

        entry_notional = qty * entry_fill
        result.trades.append(Trade(
            candidate=candidate["id"], side=side, event_start_index=event.start,
            confirm_bar=bar, wait_minutes=float(bar - event.end),
            confirm_distance_bp=(confirmation.distance or 0.0) * 1e4,
            shock_range_bp=(confirmation.shock.range_fraction if confirmation.shock else 0.0) * 1e4,
            decision_ts_ms=decision_ts, entry_ts_ms=entry_ts, exit_ts_ms=exit_ts,
            entry_reference_price=reference, entry_fill_price=entry_fill,
            exit_reference_price=exit_reference, exit_fill_price=exit_fill,
            stop_price=stop_price, target_price=target_price, stop_distance=stop_distance,
            qty=qty, target_notional=notional, entry_notional=entry_notional,
            exit_notional=qty * exit_fill, exit_reason=reason,
            hold_minutes=(exit_ts - entry_ts) / 60_000, gross_pnl_usdt=gross, fees_usdt=fees,
            funding_usdt=funding, net_usdt=net,
            gross_bp=direction * (exit_reference / reference - 1) * 1e4,
            net_bp=net / entry_notional * 1e4 if entry_notional else 0.0,
            equity_before=equity_before, equity_after=equity_after, notional_capped=capped,
            same_bar_target_and_stop=both))

        busy_until = exit_bar
        cooldown_until = exit_bar + 1 + cooldown_bars

    result.final_equity = float(engine.account.equity(Decimal(str(mark_close[-1]))))
    return result
