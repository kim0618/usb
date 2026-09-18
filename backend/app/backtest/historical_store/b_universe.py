"""Strategy B fetch universe: which symbols, over which sessions, B's 2-year backtest reads (no API).

Rule ``B_FETCH_UNIVERSE_Q1`` (STRATEGY_B_V1_SPEC section 3.5, the fetch set is PIT):

* scope S(D) for session D uses D-1 information only: CS ticker in the latest common reference
  snapshot dated *before* D, primary exchange in ``ScopeConfig.allowed_exchanges``, not a test
  symbol, grouped-daily close of D-1 >= ``price_floor``, median grouped ``close x volume`` over
  the last ``median_lookback_sessions`` sessions before D >= ``median_dollar_volume_floor`` with at
  least ``min_history_sessions`` of them present;
* a symbol's fetch range is the contiguous run [first in-scope session - ``RVOL lookback``, last
  in-scope session], clipped to the grid. RVOL reads the 20 sessions before D whether or not the
  symbol was in scope on them, and B's loader reads a covered session without bars as zero
  volume, so the range must not have holes. The range is a superset of what B reads; B still
  filters to S(D) at read time, so the extra sessions carry no look-ahead.

Q1 means the reference cadence is the quarterly common snapshot. Sessions before the first
snapshot have no PIT reference and are out of scope. A later D-1 daily reference cadence
(``B_FETCH_UNIVERSE_D1``) only adds symbols and sessions; it never removes one from Q1.
"""

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from app.strategy_b.config import RvolConfig, ScopeConfig

UNIVERSE_ID = "B_FETCH_UNIVERSE_Q1"
GROUPED_DIR = "market_data/raw/massive/grouped_daily"
REFERENCE_DIR = "market_data/raw/massive/reference_tickers"


def grouped_matrix(root: Path, sessions: Sequence[date]) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """(tickers, close, close x volume, trades) as session x ticker arrays; NaN where no bar."""
    per_session = []
    names: dict[str, int] = {}
    for session in sessions:
        path = root / GROUPED_DIR / str(session.year) / f"{session.isoformat()}.json.gz"
        body = json.loads(gzip.decompress(path.read_bytes()))["body"]
        rows = body["results"] or []
        for row in rows:
            names.setdefault(row["T"], len(names))
        per_session.append(rows)
    close = np.full((len(sessions), len(names)), np.nan)
    dollars = np.full_like(close, np.nan)
    trades = np.full_like(close, np.nan)
    for i, rows in enumerate(per_session):
        cols = np.fromiter((names[r["T"]] for r in rows), dtype=np.int64, count=len(rows))
        close[i, cols] = [r["c"] for r in rows]
        dollars[i, cols] = [r["c"] * r["v"] for r in rows]
        trades[i, cols] = [r.get("n", np.nan) for r in rows]
    return sorted(names, key=names.get), close, dollars, trades


def reference_sets(root: Path, snapshot_dates: Sequence[date], scope: ScopeConfig) -> dict[date, set[str]]:
    out = {}
    for as_of in snapshot_dates:
        rows = json.loads(gzip.decompress((root / REFERENCE_DIR / f"CS_{as_of.isoformat()}.json.gz")
                                          .read_bytes()))["results"]
        out[as_of] = {r["ticker"] for r in rows
                      if r.get("type") in scope.allowed_security_types
                      and r.get("primary_exchange") in scope.allowed_exchanges
                      and r["ticker"] not in scope.test_symbols}
    return out


def scope_membership(names: Sequence[str], close: np.ndarray, dollars: np.ndarray, sessions: Sequence[date],
                     references: dict[date, set[str]], scope: ScopeConfig) -> np.ndarray:
    """Boolean session x ticker matrix of S(D). Row i reads rows < i and snapshots dated < D only."""
    snapshot_dates = sorted(references)
    member = np.zeros(close.shape, dtype=bool)
    lookback = scope.median_lookback_sessions
    for i in range(1, len(sessions)):
        k = bisect_left(snapshot_dates, sessions[i]) - 1
        if k < 0:
            continue
        window = dollars[max(0, i - lookback):i]
        history = np.sum(~np.isnan(window), axis=0)
        median = np.full(window.shape[1], np.nan)
        seen = history > 0
        median[seen] = np.nanmedian(window[:, seen], axis=0)
        with np.errstate(invalid="ignore"):
            ok = (close[i - 1] >= scope.price_floor) & (median >= scope.median_dollar_volume_floor) \
                & (history >= scope.min_history_sessions)
        allowed = references[snapshot_dates[k]]
        member[i] = ok & np.fromiter((t in allowed for t in names), dtype=bool, count=len(names))
    return member


def fetch_ranges(names: Sequence[str], member: np.ndarray, sessions: Sequence[date], warmup: int) -> list[dict]:
    rows = []
    for j in np.flatnonzero(member.any(axis=0)):
        days = np.flatnonzero(member[:, j])
        start = max(0, int(days[0]) - warmup)
        rows.append({"symbol": names[j], "start": sessions[start].isoformat(),
                     "end": sessions[int(days[-1])].isoformat(), "sessions": int(days[-1]) - start + 1,
                     "scope_sessions": int(days.size), "first_scope": sessions[int(days[0])].isoformat(),
                     "warmup_clipped": int(days[0]) - warmup < 0})
    return sorted(rows, key=lambda r: r["symbol"])


def build(root: Path, sessions: Sequence[date],
          snapshot_dates: Sequence[date]) -> tuple[dict, np.ndarray, list[str], np.ndarray]:
    """The universe document, plus the membership matrix, ticker order and grouped trade counts."""
    scope, rvol = ScopeConfig(), RvolConfig()
    names, close, dollars, trades = grouped_matrix(root, sessions)
    member = scope_membership(names, close, dollars, sessions, reference_sets(root, snapshot_dates, scope), scope)
    ranges = fetch_ranges(names, member, sessions, rvol.lookback_sessions)
    per_day = member.sum(axis=1)
    evaluated = per_day[per_day > 0]
    body = {
        "universe_id": UNIVERSE_ID,
        "rule": {"scope": "S(D) from CS snapshot dated < D + grouped daily through D-1 (ScopeConfig)",
                 "reference_cadence": "quarterly common snapshots", "range": "[first S(D) - rvol lookback, last S(D)]",
                 "price_floor": scope.price_floor, "median_dollar_volume_floor": scope.median_dollar_volume_floor,
                 "median_lookback_sessions": scope.median_lookback_sessions,
                 "min_history_sessions": scope.min_history_sessions,
                 "allowed_exchanges": list(scope.allowed_exchanges), "test_symbols": list(scope.test_symbols),
                 "rvol_lookback_sessions": rvol.lookback_sessions},
        "grid": [sessions[0].isoformat(), sessions[-1].isoformat(), len(sessions)],
        "reference_snapshots": [d.isoformat() for d in snapshot_dates],
        "first_scope_session": sessions[int(np.argmax(per_day > 0))].isoformat(),
        "scope_sessions": int(evaluated.size), "unique_symbols": len(ranges),
        "scope_symbol_sessions": int(member.sum()),
        "range_symbol_sessions": sum(r["sessions"] for r in ranges),
        "per_day": {"min": int(evaluated.min()), "median": float(np.median(evaluated)), "max": int(evaluated.max())},
        "symbols": ranges,
    }
    body["digest"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body, member, names, trades
