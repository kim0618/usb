"""The shadow trade: what C1 would have returned, whether or not anyone traded it.

Every signal gets one, and it is created and settled from public candles alone. It is not an
order, not a paper order, and it is kept in its own ledger so it can never be added to the manual
PAPER or LIVE accounts.

The official result is the 4 h horizon with the contract's VIP0_BASE cost, exactly as D5.2
measured it:

    enter at open[t+1], leave at open[t+1+240]
    gross = exit / entry - 1
    cost  = taker * (1 + exit / entry) + micro_BASE + funding settled while held
    net   = gross - cost

The other horizons are observations for a later holding-period question. They are computed the
same way and kept in the same record, and nothing sums them into C1's performance - 4 h is the
official number because 4 h is what the frozen contract scored.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .contract import (
    COST_SCENARIO, MICRO_BASE_FRAC, MINUTE_MS, OBSERVATION_HORIZONS_MIN, OFFICIAL_HORIZON_MIN,
    TAKER_RATE,
)
from .features import NAN, is_nan
from .grid import Grid
from .models import ShadowTrade, Signal

FundingSchedule = Sequence[tuple[int, float]]


def funding_paid(schedule: FundingSchedule, entry_at_ms: int, exit_at_ms: int) -> float:
    """Rates settled while the position was held: settlement T in [entry, exit).

    A LONG pays this when it is positive, which is why it is added to the cost rather than
    subtracted. Same window the study uses (`features.targets`): a settlement exactly at entry
    counts, one exactly at exit does not.
    """
    total = 0.0
    for settlement_ms, rate in schedule:
        if entry_at_ms <= settlement_ms < exit_at_ms:
            total += float(rate)
    return total


def open_shadow(signal: Signal, grid: Grid, schedule: FundingSchedule) -> ShadowTrade:
    """The record as it exists the moment the signal fires: entry known, nothing else."""
    entry_index = grid.index_of(signal.official_entry_at_ms)
    entry_price = grid.opens[entry_index] if grid.has_bar(entry_index) else NAN
    trade = ShadowTrade(signal_id=signal.signal_id, direction=signal.direction,
                        entry_at_ms=signal.official_entry_at_ms, entry_price=entry_price,
                        exit_at_ms=signal.planned_exit_at_ms,
                        horizon_min=signal.horizon_min,
                        status="OPEN" if not is_nan(entry_price) else "AWAITING_ENTRY")
    return trade


def _outcome(entry: float, exit_price: float, funding: float) -> tuple[float, float, float]:
    """gross, cost and net for a LONG, in return units (0.001 = 10 bp)."""
    ratio = exit_price / entry
    gross = ratio - 1.0
    cost = TAKER_RATE * (1.0 + ratio) + MICRO_BASE_FRAC + funding
    return gross, cost, gross - cost


def settle(trade: ShadowTrade, signal: Signal, grid: Grid, schedule: FundingSchedule) -> ShadowTrade:
    """Fill in whatever the candles now support, and mark the trade SETTLED once 4 h is done.

    Called on every tick and on restart; it is idempotent, and a horizon whose exit bar does not
    exist yet stays absent rather than being extrapolated from the last price.
    """
    entry_index = grid.index_of(trade.entry_at_ms)
    if is_nan(trade.entry_price):
        if not grid.has_bar(entry_index):
            trade.status = "AWAITING_ENTRY"
            return trade
        trade.entry_price = grid.opens[entry_index]
    entry = trade.entry_price
    if entry <= 0:
        trade.status = "UNUSABLE_ENTRY"
        return trade

    for horizon in OBSERVATION_HORIZONS_MIN:
        exit_index = entry_index + horizon
        label = f"{horizon}m"
        if not grid.has_bar(exit_index):
            continue
        exit_at = grid.ts(exit_index)
        funding = funding_paid(schedule, trade.entry_at_ms, exit_at)
        gross, cost, net = _outcome(entry, grid.opens[exit_index], funding)
        trade.observations[f"{label}_gross"] = gross
        trade.observations[f"{label}_net"] = net

    official_index = entry_index + trade.horizon_min
    if not grid.has_bar(official_index):
        trade.status = "ACTIVE"
        return trade

    # Excursions span the bars actually held: the entry bar through the bar before the exit bar,
    # which is the window the study's `targets` takes its high and low over.
    highs = [grid.highs[i] for i in range(entry_index, official_index)
             if grid.has_bar(i) and not is_nan(grid.highs[i])]
    lows = [grid.lows[i] for i in range(entry_index, official_index)
            if grid.has_bar(i) and not is_nan(grid.lows[i])]
    trade.mfe = max(highs) / entry - 1.0 if highs else None
    trade.mae = min(lows) / entry - 1.0 if lows else None

    trade.exit_at_ms = grid.ts(official_index)
    trade.exit_price = grid.opens[official_index]
    trade.funding_paid = funding_paid(schedule, trade.entry_at_ms, trade.exit_at_ms)
    gross, cost, net = _outcome(entry, trade.exit_price, trade.funding_paid)
    trade.gross_return, trade.cost, trade.net_return = gross, cost, net
    trade.status = "SETTLED"
    return trade


def summarize(trades: Iterable[ShadowTrade]) -> dict[str, object]:
    """The official 4 h tally and nothing else.

    Observations are excluded by construction: this function never looks at `observations`. If a
    holding-period study wants them it reads the ledger, and then it is a study and says so.
    """
    settled = [trade for trade in trades if trade.status == "SETTLED" and trade.net_return is not None]
    count = len(settled)
    body: dict[str, object] = {
        "scenario": COST_SCENARIO, "horizon_min": OFFICIAL_HORIZON_MIN,
        "settled": count, "wins": sum(1 for t in settled if (t.net_return or 0) > 0),
    }
    if count:
        nets = [t.net_return or 0.0 for t in settled]
        grosses = [t.gross_return or 0.0 for t in settled]
        body["net_mean_bp"] = sum(nets) / count * 10_000
        body["gross_mean_bp"] = sum(grosses) / count * 10_000
        body["net_sum_bp"] = sum(nets) * 10_000
    return body
