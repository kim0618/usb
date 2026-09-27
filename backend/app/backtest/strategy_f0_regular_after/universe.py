"""STRATEGY_E_TRADING_UNIVERSE_V1_1, called exactly as E-R3 calls it, plus F0's daily inputs.

The contract function and the panel loader are imported unmodified. For each primary session
index the result carries eligibility, raw close(D-1) (``prev_close``), the universe-window median
dollar volume (matching diagnostic) and the TICKER_SUSPECT diagnostic flag.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_c_selection import panel as P
from app.market.calendar import MarketCalendar
from app.strategy_e_v1_1 import universe as U

DAILY_ROOT = Path(__file__).resolve().parents[4] / "data/runtime/strategy_c/raw"
EXCHANGES = frozenset({"XNAS", "XNYS", "XASE"})
SPLIT_RANGE = (date(2024, 9, 16), date(2026, 9, 16))


@dataclass
class DailyInputs:
    panel: P.Panel
    grid: list[date]
    snapshot_dates: list[date]
    read_set: list[tuple[str, str]]


def load_daily(root: Path = DAILY_ROOT) -> DailyInputs:
    cal = MarketCalendar("America/New_York")
    grouped = sorted((root / "grouped").glob("*.json.gz"))
    grid = [d for d in (date.fromisoformat(x.name[:10]) for x in grouped) if cal.session(d)]
    snaps = sorted(date.fromisoformat(x.name[3:13]) for x in (root / "tickers").glob("CS_*.json.gz"))
    panel = P.load_panel(root, grid, snaps, EXCHANGES, SPLIT_RANGE)
    files = [*grouped, *sorted((root / "tickers").glob("CS_*.json.gz")), *sorted((root / "splits").glob("*.json.gz"))]
    read_set = [(str(f.relative_to(root)), hashlib.sha256(f.read_bytes()).hexdigest()) for f in files]
    return DailyInputs(panel, list(panel.sessions), snaps, read_set)


def median_dv20(panel: P.Panel, d: int) -> np.ndarray:
    """The universe window: close*volume of the 20 rows before the D-1 row (as daily_eligibility)."""
    dollar = panel.close[:d] * panel.volume[:d]
    t = d - 1
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(dollar[t - U.DAILY_WINDOW:t], axis=0)


def eligibility(panel: P.Panel, grid: Sequence[date], d: int, cal: MarketCalendar) -> np.ndarray:
    factor = panel.split_arrays()[0]
    member = panel.membership()
    return U.daily_eligibility(grid[d], list(grid[:d]), close=panel.close[:d], volume=panel.volume[:d],
                               factor=factor[:d], membership=member[d - 1],
                               split_in_window=factor[d] != factor[d - 1], calendar=cal)


def figi_by_snapshot(root: Path, snaps: Sequence[date]) -> dict[date, dict[str, str]]:
    out = {}
    for s in snaps:
        payload = json.loads(gzip.decompress((root / "tickers" / f"CS_{s.isoformat()}.json.gz").read_bytes()))
        out[s] = {r["ticker"]: r.get("composite_figi") for r in payload["results"]}
    return out


def ticker_suspect(figi: dict, snaps: Sequence[date], prev_session: date, ticker: str) -> bool:
    usable = [s for s in snaps if s <= prev_session]    # dated <= D-1
    if len(usable) < 2:
        return False
    a, b = figi[usable[-2]].get(ticker), figi[usable[-1]].get(ticker)
    return bool(a and b and a != b)


def cik_by_ticker(root: Path, snaps: Sequence[date], prev_session: date) -> dict[str, str]:
    usable = [s for s in snaps if s <= prev_session]
    if not usable:
        return {}
    payload = json.loads(gzip.decompress((root / "tickers" / f"CS_{usable[-1].isoformat()}.json.gz").read_bytes()))
    return {r["ticker"]: r["cik"] for r in payload["results"] if r.get("cik")}


def poisoned_panel(panel: P.Panel, d: int, seed: int) -> P.Panel:
    """PIT-2: rows dated >= D, snapshots dated >= D and splits executing after D overwritten."""
    rng = np.random.default_rng([seed, d])
    arrays = {}
    for name in ("open", "high", "low", "close", "volume"):
        a = getattr(panel, name).copy()
        a[d:] = rng.uniform(0.5, 5000.0, a[d:].shape) if name != "volume" else rng.uniform(1, 1e9, a[d:].shape)
        arrays[name] = a
    session = panel.sessions[d]
    tickers = list(panel.tickers)
    snapshots = {}
    for as_of, members in panel.snapshots.items():
        if as_of >= session:
            pick = rng.permutation(len(tickers))[: len(tickers) // 2]
            snapshots[as_of] = frozenset(tickers[i] for i in pick)
        else:
            snapshots[as_of] = members
    splits = [e for e in panel.splits if e.execution_date <= session]
    later = [s for s in panel.sessions if s > session]
    for i in range(50):
        if not later:
            break
        splits.append(P.SplitEvent(tickers[int(rng.integers(len(tickers)))],
                                   later[int(rng.integers(len(later)))], 1.0, float(rng.integers(2, 20))))
    splits = tuple(sorted(set(splits), key=lambda e: (e.execution_date, e.ticker, e.split_from, e.split_to)))
    return P.with_changes(panel, snapshots=snapshots, splits=splits, **arrays)
