"""D-1 daily eligibility for forward sessions: the frozen USB-HIST-V1 panel extended with verified
forward grouped daily, per-D split snapshots and CS reference snapshots.

For a forward session D, only these are read (E-R1 contract):

* grouped daily rows dated <= D-1 (historical rows through 2026-09-16, forward rows after);
* the split events of the frozen list plus ``splits_asof_<D>`` (executions <= D only);
* the latest CS snapshot dated <= D-1, filtered to E0's allowed exchanges.

``universe.daily_eligibility`` then applies E0's rule unchanged. A forward grouped file counts only
when its validation sidecar says PASS. Nothing here computes a return.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import gzip
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_c_selection.panel import SplitEvent
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward import storage as ST
from app.strategy_e_v1_1 import context, universe as U


@dataclass
class DailyBase:
    sessions: list[date]
    tickers: tuple[str, ...]
    close: np.ndarray
    volume: np.ndarray
    splits: tuple[SplitEvent, ...]
    snapshots: dict[date, frozenset[str]]
    allowed_exchanges: frozenset[str]


def load_base(root: Path) -> DailyBase:
    """The frozen development daily panel (read-only, identity-checked by its own loader)."""
    from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
    from app.backtest.strategy_e0_overnight.dataset import load_daily_rows
    rules = load_e0_rules()
    _, history = load_daily_rows(root, rules)
    p = history.panel
    return DailyBase(list(p.sessions), p.tickers, p.close.copy(), p.volume.copy(), tuple(p.splits),
                     dict(p.snapshots), rules.allowed_exchanges)


def _forward_rows(root: Path, session: date, columns: Mapping[str, int], n: int) -> tuple[np.ndarray, np.ndarray] | None:
    path = ST.grouped_path(root, session)
    if not ST.verified(path):
        return None
    close, volume = np.full(n, np.nan), np.full(n, np.nan)
    body = (json.loads(gzip.decompress(path.read_bytes())).get("body") or {})
    for item in body.get("results") or ():
        j = columns.get(item.get("T"))
        if j is None or item.get("c") is None or item.get("v") is None:
            continue
        close[j], volume[j] = float(item["c"]), float(item["v"])
    return close, volume


def _forward_splits(root: Path, session: date) -> tuple[SplitEvent, ...] | None:
    path = ST.splits_asof_path(root, session)
    if not ST.verified(path):
        return None
    out = []
    for item in json.loads(gzip.decompress(path.read_bytes())).get("results") or ():
        try:
            event = SplitEvent(str(item["ticker"]), date.fromisoformat(item["execution_date"]),
                               float(item["split_from"]), float(item["split_to"]))
        except (KeyError, TypeError, ValueError):
            continue
        if event.execution_date <= session and event.split_from > 0 and event.split_to > 0 \
                and event.split_from != event.split_to:
            out.append(event)
    return tuple(out)


def _forward_snapshots(root: Path, allowed: frozenset[str]) -> dict[date, frozenset[str]]:
    out = {}
    directory = root / ST.ROOT / "reference"
    for path in sorted(directory.glob("CS_*.json.gz")) if directory.exists() else ():
        as_of = date.fromisoformat(path.name[3:13])
        if not ST.verified(path):
            continue
        payload = json.loads(gzip.decompress(path.read_bytes()))
        out[as_of] = frozenset(r["ticker"] for r in payload.get("results") or ()
                               if r.get("type") == "CS" and r.get("market") == "stocks"
                               and r.get("active") is True and r.get("primary_exchange") in allowed)
    return out


def eligible_universe(base: DailyBase, root: Path, session: date, calendar: MarketCalendar, *,
                      forward_splits: tuple[SplitEvent, ...] | None = None
                      ) -> tuple[tuple[str, ...] | None, list[str]]:
    """(eligible symbols, missing inputs). ``None`` when any required input is missing.

    ``forward_splits`` replaces the verified ``splits_asof_<D>`` file (a caller that holds a split
    list published before the session, e.g. a live runtime); executions after D are still ignored."""
    previous = calendar.previous_trading_day(session)
    window, cur = [], session
    for _ in range(context.REQUIRED_DAILY_SESSIONS):
        cur = calendar.previous_trading_day(cur)
        window.append(cur)
    window.reverse()
    snapshots = {**base.snapshots, **_forward_snapshots(root, base.allowed_exchanges)}
    usable = [d for d in snapshots if d <= previous]
    extra = sorted(set().union(*(snapshots[d] for d in usable)) - set(base.tickers)) if usable else []
    tickers = base.tickers + tuple(extra)
    columns = {t: j for j, t in enumerate(tickers)}
    index = {d: i for i, d in enumerate(base.sessions)}
    close = np.full((len(window), len(tickers)), np.nan)
    volume = np.full((len(window), len(tickers)), np.nan)
    missing = []
    for k, day in enumerate(window):
        if day in index:
            close[k, :len(base.tickers)] = base.close[index[day]]
            volume[k, :len(base.tickers)] = base.volume[index[day]]
            continue
        rows = _forward_rows(root, day, columns, len(tickers))
        if rows is None:
            missing.append(f"grouped daily {day.isoformat()}")
            continue
        close[k], volume[k] = rows
    forward = _forward_splits(root, session) if forward_splits is None else tuple(forward_splits)
    if forward is None:
        missing.append(f"splits_asof_{session.isoformat()}")
    if not usable:
        missing.append("CS reference <= D-1")
    if missing:
        return None, missing
    seen = {(e.ticker, e.execution_date) for e in base.splits}
    events = list(base.splits) + [e for e in forward if (e.ticker, e.execution_date) not in seen]
    factor = np.ones_like(close)
    ordinals = np.array([d.toordinal() for d in window])
    split_today = np.zeros(len(tickers), dtype=bool)
    for event in events:
        j = columns.get(event.ticker)
        if j is None or event.execution_date > session:
            continue
        factor[ordinals >= event.execution_date.toordinal(), j] *= event.split_from / event.split_to
        if previous < event.execution_date <= session:
            split_today[j] = True
    member = np.zeros(len(tickers), dtype=bool)
    latest = max(usable)
    member[[columns[t] for t in snapshots[latest] if t in columns]] = True
    ok = U.daily_eligibility(session, window, close=close, volume=volume, factor=factor,
                             membership=member, split_in_window=split_today, calendar=calendar)
    return tuple(sorted(t for t, e in zip(tickers, ok) if e)), []
