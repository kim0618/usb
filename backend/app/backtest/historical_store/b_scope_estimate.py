"""How large a 2-year Strategy B minute universe would be, estimated from grouped daily (no API).

The B scope rule (``strategy_b.config.ScopeConfig``) evaluated point-in-time for session D with
data known at D-1 only: CS in the latest reference snapshot dated before D, primary exchange in
the allowed set, D-1 close >= price floor, and the median of the last 20 sessions' dollar volume
(close x volume, D-1 and earlier) >= the floor with at least 5 sessions of history. Sessions
before the first snapshot have no PIT reference and are not counted.

This only sizes the fetch. It is not B's scope implementation and feeds no strategy.
"""

from bisect import bisect_left
from datetime import date
import gzip
import json
from pathlib import Path

import numpy as np

from app.strategy_b.config import ScopeConfig


def estimate(workspace_root: Path, sessions: list[date], snapshot_dates: list[date]) -> dict:
    scope = ScopeConfig()
    grouped = workspace_root / "market_data/raw/massive/grouped_daily"
    ref = workspace_root / "market_data/raw/massive/reference_tickers"
    snapshots = {}
    for as_of in snapshot_dates:
        rows = json.loads(gzip.decompress((ref / f"CS_{as_of.isoformat()}.json.gz").read_bytes()))["results"]
        snapshots[as_of] = {r["ticker"] for r in rows if r.get("primary_exchange") in scope.allowed_exchanges
                            and r["ticker"] not in scope.test_symbols}
    tickers: dict[str, int] = {}
    closes, dollars = [], []
    for session in sessions:
        body = json.loads(gzip.decompress((grouped / str(session.year) / f"{session.isoformat()}.json.gz")
                                          .read_bytes()))["body"]
        c, d = {}, {}
        for r in body["results"]:
            c[r["T"]] = r["c"]
            d[r["T"]] = r["c"] * r["v"]
            tickers.setdefault(r["T"], len(tickers))
        closes.append(c)
        dollars.append(d)
    n, m = len(sessions), len(tickers)
    close = np.full((n, m), np.nan)
    dvol = np.full((n, m), np.nan)
    for i in range(n):
        for t, v in closes[i].items():
            close[i, tickers[t]] = v
            dvol[i, tickers[t]] = dollars[i][t]
    names = np.array(sorted(tickers, key=tickers.get))
    lookback = scope.median_lookback_sessions
    per_day, unique, symbol_days = [], set(), {}
    for i in range(1, n):
        k = bisect_left(snapshot_dates, sessions[i]) - 1
        if k < 0:
            continue
        window = dvol[max(0, i - lookback):i]
        history = np.sum(~np.isnan(window), axis=0)
        with np.errstate(all="ignore"):
            median = np.nanmedian(window, axis=0)
        ok = (close[i - 1] >= scope.price_floor) & (median >= scope.median_dollar_volume_floor) \
            & (history >= scope.min_history_sessions)
        cs = snapshots[snapshot_dates[k]]
        chosen = [s for s in names[ok] if s in cs]
        per_day.append(len(chosen))
        unique.update(chosen)
        for s in chosen:
            symbol_days[s] = symbol_days.get(s, 0) + 1
    return {"sessions_evaluated": len(per_day), "first_evaluated": str(sessions[n - len(per_day)]),
            "unique_symbols": len(unique), "symbol_days": int(sum(per_day)),
            "per_day": {"min": int(min(per_day)), "median": float(np.median(per_day)), "max": int(max(per_day))},
            "symbol_days_per_symbol_median": float(np.median(list(symbol_days.values())))}
