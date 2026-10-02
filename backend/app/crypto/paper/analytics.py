"""Performance analytics derived from the ledger.

The ledger is the authority (contract P7): every figure here is folded out of the recorded
events, never out of a running total the engine happened to keep. If an aggregate and the
ledger disagree, this module is what is wrong.

A *trade* is one flat-to-flat round trip. A position that was scaled in and partially closed is
still one trade; its exits are recorded individually so the round trip can be audited.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from .ledger import EventType

MANUAL = "MANUAL"
AUTO = "AUTO"
LIQUIDATION = "LIQUIDATION"
EMERGENCY = "EMERGENCY"


def _decimal(value: Any, default: str = "0") -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(default)


@dataclass
class Trade:
    """One flat-to-flat round trip."""
    index: int
    side: str
    origin: str
    leverage: Decimal
    opened_ts_ms: int
    closed_ts_ms: int
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    gross_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    max_adverse_excursion: Decimal = Decimal(0)
    max_favourable_excursion: Decimal = Decimal(0)
    liquidated: bool = False
    exits: int = 1

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees - self.funding

    @property
    def hold_ms(self) -> int:
        return self.closed_ts_ms - self.opened_ts_ms

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0

    def view(self) -> dict[str, Any]:
        return {
            "index": self.index, "side": self.side, "origin": self.origin,
            "leverage": str(self.leverage), "opened_ts_ms": self.opened_ts_ms,
            "closed_ts_ms": self.closed_ts_ms, "hold_ms": self.hold_ms,
            "entry_price": str(self.entry_price), "exit_price": str(self.exit_price),
            "qty": str(self.qty), "gross_pnl": str(self.gross_pnl), "fees": str(self.fees),
            "funding": str(self.funding), "net_pnl": str(self.net_pnl),
            "max_adverse_excursion": str(self.max_adverse_excursion),
            "max_favourable_excursion": str(self.max_favourable_excursion),
            "liquidated": self.liquidated, "exits": self.exits, "is_win": self.is_win,
        }


def _origin(reason: str | None, liquidated: bool) -> str:
    """Where the round trip came from. AUTO exists in the schema and is never produced in D4."""
    if liquidated:
        return LIQUIDATION
    if reason == "EMERGENCY":
        return EMERGENCY
    if reason == "PAPER_C1_AUTO":
        return "PAPER_C1_AUTO"
    if reason == "PAPER_MANUAL":
        return "PAPER_MANUAL"
    if reason == AUTO:
        return AUTO
    return MANUAL


def build_trades(events: Sequence[dict[str, Any]]) -> list[Trade]:
    """Fold the ledger into round trips.

    Funding is attributed to the trade that was open when it settled, which is the only
    attribution that lets a per-trade net PnL be reconciled against the account.
    """
    trades: list[Trade] = []
    open_side: str | None = None
    open_ts: int | None = None
    entry_price: Decimal | None = None
    qty_opened = Decimal(0)
    gross = Decimal(0)
    fees = Decimal(0)
    funding = Decimal(0)
    exits = 0
    liquidated = False
    last_reason: str | None = None
    last_exit = Decimal(0)
    exit_notional = Decimal(0)
    exit_qty = Decimal(0)
    mae = Decimal(0)
    mfe = Decimal(0)
    leverage = Decimal(0)
    # The opening fill's fee is charged before POSITION_OPEN is written, so it arrives while
    # the fold still thinks it is flat. Hold it here and hand it to the trade it belongs to.
    pending_fees = Decimal(0)

    for event in events:
        kind = event["event_type"]
        if kind == EventType.POSITION_OPEN:
            open_side = "LONG" if _decimal(event["signed_qty"]) > 0 else "SHORT"
            open_ts = int(event["ts_ms"])
            entry_price = _decimal(event["avg_entry"])
            leverage = _decimal(event.get("leverage"), "1")
            qty_opened = abs(_decimal(event["signed_qty"]))
            gross = funding = Decimal(0)
            fees = pending_fees
            pending_fees = Decimal(0)
            exits = 0
            liquidated = False
            exit_notional = exit_qty = Decimal(0)
            mae = mfe = Decimal(0)
        elif kind == EventType.POSITION_INCREASE and open_side is not None:
            entry_price = _decimal(event["avg_entry"])
            qty_opened = abs(_decimal(event["signed_qty"]))
            leverage = _decimal(event.get("leverage"), str(leverage or 1))
        elif kind == EventType.FEE:
            if open_side is None:
                pending_fees += _decimal(event["amount"])
            else:
                fees += _decimal(event["amount"])
        elif kind == EventType.FUNDING and open_side is not None:
            funding += _decimal(event["amount_paid"])
        elif kind == EventType.LIQUIDATION:
            liquidated = True
        elif kind in (EventType.POSITION_REDUCE, EventType.POSITION_CLOSE) and open_side is not None:
            gross += _decimal(event["gross_pnl"])
            exits += 1
            last_reason = event.get("reason")
            closed_qty = _decimal(event["closed_qty"])
            last_exit = _decimal(event["exit_price"])
            exit_notional += last_exit * closed_qty
            exit_qty += closed_qty
            mae = min(mae, _decimal(event.get("max_adverse_excursion")))
            mfe = max(mfe, _decimal(event.get("max_favourable_excursion")))
            if kind == EventType.POSITION_CLOSE:
                trades.append(Trade(
                    index=len(trades) + 1, side=open_side,
                    origin=_origin(last_reason, liquidated), leverage=leverage,
                    opened_ts_ms=int(event.get("opened_ts_ms") or open_ts or event["ts_ms"]),
                    closed_ts_ms=int(event["ts_ms"]),
                    entry_price=entry_price or Decimal(0),
                    exit_price=(exit_notional / exit_qty) if exit_qty else last_exit,
                    qty=qty_opened, gross_pnl=gross, fees=fees, funding=funding,
                    max_adverse_excursion=mae, max_favourable_excursion=mfe,
                    liquidated=liquidated, exits=exits))
                open_side = None
                open_ts = None
                entry_price = None
                pending_fees = Decimal(0)
    return trades


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator else None


def _plain(value: Decimal | None) -> str | None:
    """Decimal division can hand back `0E+10` for an exact zero. That reads like a magnitude
    on screen, so every ratio leaves here in plain notation."""
    if value is None:
        return None
    return format(value.normalize() if value == 0 else value, "f")


def drawdown(equity_points: Sequence[Decimal]) -> tuple[Decimal, Decimal | None]:
    """Maximum drawdown in USDT and as a fraction of the peak it fell from."""
    if not equity_points:
        return Decimal(0), None
    peak = equity_points[0]
    worst = Decimal(0)
    worst_fraction: Decimal | None = None
    for value in equity_points:
        peak = max(peak, value)
        fall = peak - value
        if fall > worst:
            worst = fall
            worst_fraction = fall / peak if peak else None
    return worst, worst_fraction


def longest_losing_streak(trades: Sequence[Trade]) -> int:
    longest = current = 0
    for trade in trades:
        current = 0 if trade.is_win else current + 1
        longest = max(longest, current)
    return longest


def summarize(events: Sequence[dict[str, Any]], *, starting_capital: Decimal) -> dict[str, Any]:
    trades = build_trades(events)
    wins = [trade for trade in trades if trade.is_win]
    losses = [trade for trade in trades if not trade.is_win]
    gross = sum((trade.gross_pnl for trade in trades), Decimal(0))
    fees = sum((_decimal(event["amount"]) for event in events
                if event["event_type"] == EventType.FEE), Decimal(0))
    funding = sum((_decimal(event["amount_paid"]) for event in events
                   if event["event_type"] == EventType.FUNDING), Decimal(0))
    net = gross - fees - funding
    win_sum = sum((trade.net_pnl for trade in wins), Decimal(0))
    loss_sum = sum((trade.net_pnl for trade in losses), Decimal(0))

    # Equity after each closed trade, walked from the starting capital and restarted at every
    # capital reset. Walking straight through a reset would book the top-up as a gain and make
    # the drawdown before it look recovered, so the path is cut into segments instead and the
    # worst drawdown is the worst any single segment suffered.
    segments = capital_segments(events, trades, starting_capital=starting_capital)
    max_dd = max((segment["max_drawdown"] for segment in segments), default=Decimal(0))
    # A segment that never fell has no fraction (None), which is not the same as falling 0% and
    # must not be sorted as if it were.
    fractions = [segment["max_drawdown_fraction"] for segment in segments
                 if segment["max_drawdown_fraction"] is not None]
    max_dd_fraction = max(fractions) if fractions else None
    # The segment path only moves on a *closed* trade. Fees and funding already spent on a
    # position that is still open have left the wallet but belong to no completed round trip,
    # so the path stops short of the real balance. Subtracting them is what keeps
    # `ending_equity` and `net_pnl` from telling two different stories about the same run.
    attributed_fees = sum((trade.fees for trade in trades), Decimal(0))
    attributed_funding = sum((trade.funding for trade in trades), Decimal(0))
    open_position_fees = fees - attributed_fees
    open_position_funding = funding - attributed_funding
    closed_trade_equity = segments[-1]["ending_equity"] if segments else starting_capital
    ending = closed_trade_equity - open_position_fees - open_position_funding
    resets = [event for event in events if event["event_type"] == EventType.ACCOUNT_RESET]
    anchor = reset_anchor(events)

    holds = sorted(trade.hold_ms for trade in trades)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _plain(_ratio(Decimal(len(wins)), Decimal(len(trades)))) if trades else None,
        "gross_pnl": str(gross),
        "fees": str(fees),
        "funding": str(funding),
        "net_pnl": str(net),
        "avg_win": _plain(_ratio(win_sum, Decimal(len(wins)))) if wins else None,
        "avg_loss": _plain(_ratio(loss_sum, Decimal(len(losses)))) if losses else None,
        # Payoff is the size question and win rate is the frequency question; a strategy needs
        # both to be read together, so the ratio is carried rather than left to the reader.
        # None when there is no loss yet: dividing by nothing is not an infinite payoff, it is
        # an unanswered question.
        "payoff_ratio": _plain(_ratio(_ratio(win_sum, Decimal(len(wins))),
                                      -_ratio(loss_sum, Decimal(len(losses)))))
        if wins and losses and loss_sum < 0 else None,
        "expectancy": _plain(_ratio(sum((t.net_pnl for t in trades), Decimal(0)),
                                    Decimal(len(trades)))) if trades else None,
        "profit_factor": _plain(_ratio(win_sum, -loss_sum)) if loss_sum < 0 else None,
        "max_drawdown": str(max_dd),
        "max_drawdown_fraction": _plain(max_dd_fraction),
        "longest_losing_streak": longest_losing_streak(trades),
        "hold_ms_median": holds[len(holds) // 2] if holds else None,
        "hold_ms_mean": int(sum(holds) / len(holds)) if holds else None,
        "hold_ms_total": sum(holds),
        "max_adverse_excursion": str(min((t.max_adverse_excursion for t in trades),
                                         default=Decimal(0))),
        "max_favourable_excursion": str(max((t.max_favourable_excursion for t in trades),
                                            default=Decimal(0))),
        "liquidations": sum(1 for trade in trades if trade.liquidated),
        "ending_equity": str(ending),
        "closed_trade_equity": str(closed_trade_equity),
        "open_position_fees": str(open_position_fees),
        "open_position_funding": str(open_position_funding),
        "has_open_position": open_position_fees != 0 or open_position_funding != 0,
        "starting_capital": str(starting_capital),
        # Every trade above is counted across the whole run, resets included. These two say
        # where the capital line was redrawn, so a return or a drawdown can be read per segment
        # instead of across a boundary that means nothing.
        "capital_resets": len(resets),
        # What the account has done since the last reset, which is what an operator reading a
        # balance means by "how am I doing". Subtraction only: the reset recorded the anchors,
        # the lifetime totals are the ones above, and nothing here is derived a second time.
        # The net is the cumulative delta rather than the segment path, so costs already spent
        # on a position that is still open are included and it agrees with the wallet exactly.
        "current_segment": {
            "since_ts_ms": anchor["since_ts_ms"],
            "reset_count": anchor["reset_count"],
            "starting_capital_usdt": (str(anchor["starting_capital"])
                                      if anchor["starting_capital"] is not None
                                      else str(starting_capital)),
            "starting_capital_krw": (str(anchor["starting_capital_krw"])
                                     if anchor["starting_capital_krw"] is not None else None),
            "current_segment_realized_pnl": str(gross - anchor["realized_pnl"]),
            "current_segment_fees": str(fees - anchor["fees"]),
            "current_segment_funding": str(funding - anchor["funding"]),
            "current_segment_net_pnl": str((gross - anchor["realized_pnl"])
                                           - (fees - anchor["fees"])
                                           - (funding - anchor["funding"])),
            # Per-segment trade statistics D4 already folds. Reused, not recomputed.
            "trades": segments[-1]["trades"] if segments else 0,
            "wins": segments[-1]["wins"] if segments else 0,
            "win_rate": (_plain(_ratio(Decimal(segments[-1]["wins"]),
                                       Decimal(segments[-1]["trades"])))
                         if segments and segments[-1]["trades"] else None),
            "max_drawdown": str(segments[-1]["max_drawdown"]) if segments else "0",
        },
        "segments": [
            {key: (str(value) if isinstance(value, Decimal) else value)
             for key, value in segment.items()}
            for segment in segments
        ],
        "by_origin": _group(trades, lambda trade: trade.origin),
        "by_leverage": _group(trades, lambda trade: str(trade.leverage)),
        "by_side": _group(trades, lambda trade: trade.side),
        # Entry hour in UTC. A perpetual trades around the clock, so "when" is a real axis and
        # not a market-session artefact. Keyed as a zero-padded string so the JSON sorts.
        "by_hour_utc": _group(trades, lambda trade: _hour_utc(trade.opened_ts_ms)),
    }


def _hour_utc(ts_ms: int) -> str:
    return f"{datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour:02d}"


def reset_anchor(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """What the most recent ACCOUNT_RESET recorded, or zeros when there has been none.

    The reset event already carries the cumulative figures as they stood at the cut, which is
    why it carries them: a reader can subtract instead of re-deriving the history. No reset
    means the current segment began at RUN_START, and the anchors are zero.
    """
    for event in reversed(list(events)):
        if event["event_type"] == EventType.ACCOUNT_RESET:
            return {
                "since_ts_ms": int(event["ts_ms"]),
                "realized_pnl": _decimal(event["preserved_realized_pnl"]),
                "fees": _decimal(event["preserved_fees"]),
                "funding": _decimal(event["preserved_funding"]),
                "starting_capital": _decimal(event["after_equity"]),
                "starting_capital_krw": _decimal(event["after_equity_krw"]),
                "reset_count": int(event["reset_count"]),
            }
    start = next((int(event["ts_ms"]) for event in events
                  if event["event_type"] == EventType.RUN_START), None)
    return {"since_ts_ms": start, "realized_pnl": Decimal(0), "fees": Decimal(0),
            "funding": Decimal(0), "starting_capital": None, "starting_capital_krw": None,
            "reset_count": 0}


def capital_segments(events: Sequence[dict[str, Any]], trades: Sequence[Trade], *,
                     starting_capital: Decimal) -> list[dict[str, Any]]:
    """Split the equity path at ACCOUNT_RESET boundaries.

    Trades and resets are interleaved by timestamp because they are two kinds of thing that
    move the same line: a trade moves it by its net PnL, a reset moves it to a chosen number.
    Only the second one invalidates the peak a drawdown is measured against.
    """
    timeline: list[tuple[int, int, Any]] = []
    for trade in trades:
        timeline.append((trade.closed_ts_ms, 0, trade))
    for event in events:
        if event["event_type"] == EventType.ACCOUNT_RESET:
            timeline.append((int(event["ts_ms"]), 1, event))
    # A reset recorded in the same millisecond as a close sorts after it: the close is what the
    # operator saw before deciding to reset.
    timeline.sort(key=lambda item: (item[0], item[1]))

    segments: list[dict[str, Any]] = []
    opened_at: int | None = None
    base = starting_capital
    running = starting_capital
    points = [starting_capital]
    counted: list[Trade] = []

    def close_segment(ended_at: int | None) -> None:
        max_dd_segment, max_dd_segment_fraction = drawdown(points)
        segments.append({
            "index": len(segments),
            "started_ts_ms": opened_at,
            "ended_ts_ms": ended_at,
            "starting_capital": base,
            "ending_equity": running,
            "net_pnl": running - base,
            "return_fraction": (running - base) / base if base > 0 else Decimal(0),
            "trades": len(counted),
            "wins": sum(1 for trade in counted if trade.is_win),
            "max_drawdown": max_dd_segment,
            "max_drawdown_fraction": max_dd_segment_fraction,
        })

    for _, kind, item in timeline:
        if kind == 0:
            running += item.net_pnl
            points.append(running)
            counted.append(item)
            continue
        close_segment(int(item["ts_ms"]))
        opened_at = int(item["ts_ms"])
        base = _decimal(item["after_equity"])
        running = base
        points = [base]
        counted = []
    close_segment(None)
    return segments


def _group(trades: Sequence[Trade], key) -> dict[str, dict[str, Any]]:
    """Per-bucket totals. Buckets a run never produced are simply absent, not zero-filled with
    a made-up row: `by_origin` will not claim an AUTO bucket exists until one does."""
    buckets: dict[str, list[Trade]] = {}
    for trade in trades:
        buckets.setdefault(key(trade), []).append(trade)
    return {
        name: {
            "trades": len(items),
            "wins": sum(1 for item in items if item.is_win),
            "net_pnl": str(sum((item.net_pnl for item in items), Decimal(0))),
            "fees": str(sum((item.fees for item in items), Decimal(0))),
            "funding": str(sum((item.funding for item in items), Decimal(0))),
        }
        for name, items in sorted(buckets.items())
    }


def read_ledger(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def reconcile(events: Sequence[dict[str, Any]], *, starting_capital: Decimal,
              account_realized: Decimal, account_fees: Decimal,
              account_funding: Decimal) -> dict[str, Any]:
    """Cross-check the folded figures against the account the engine carries."""
    summary = summarize(events, starting_capital=starting_capital)
    # The wallet the ledger implies. Checking it catches the case the three sums above miss:
    # each total right on its own, yet the equity line built from them landing somewhere else.
    wallet = starting_capital + account_realized - account_fees - account_funding
    return {
        "gross_pnl_matches": Decimal(summary["gross_pnl"]) == account_realized,
        "fees_match": Decimal(summary["fees"]) == account_fees,
        "funding_matches": Decimal(summary["funding"]) == account_funding,
        "wallet_matches": Decimal(summary["ending_equity"]) == wallet,
        "ledger_ending_equity": summary["ending_equity"],
        "account_wallet_balance": str(wallet),
        "ledger_gross_pnl": summary["gross_pnl"],
        "account_realized_pnl": str(account_realized),
        "ledger_fees": summary["fees"],
        "account_fees": str(account_fees),
        "ledger_funding": summary["funding"],
        "account_funding": str(account_funding),
    }
