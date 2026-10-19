"""Shared, outcome-free plumbing for the H-N1 census: contract check, calendar, prices, CIK->ticker maps.

Nothing in this module computes a return. The price panel is loaded whole, but the accessors that
Phase 1 uses (``close_at``, ``present``) are called only with session indexes at or before the
signal-formation session, except ``present`` on the entry session, which reads a presence bit and
no price. Phase 3 is the only caller of the forward accessors in ``outcomes.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import bisect
import glob
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
RT = ROOT / "data/runtime"
DOCS = ROOT / "docs/backtest/strategy_h_n1"
OUT = RT / "strategy_h_n1"
CONTRACT_PATH = DOCS / "h_n1_contract_v1.json"
CONTRACT_CANONICAL = "5353017cdf4d59388a46c7882b4f95c78a4d6c88d2d33de7ad4f63c1d560d9f8"

GROUPED = RT / "strategy_c/raw/grouped"
SPLITS = RT / "strategy_c/raw/splits"
UNIVERSE = RT / "research_universe_u1/reference/tickers/CS_2024-10-25.json.gz"
COMPANYFACTS = RT / "strategy_h_v2/d1_1/sec_raw/companyfacts"
SUBMISSIONS = [RT / "strategy_h_v2/d1_1/sec_raw/submissions", RT / "strategy_c/e0/raw/submissions"]

SNAPSHOT_DATE = "2024-10-25"
DEV_START, DEV_END, HOLD_START = "2024-10-25", "2025-12-31", "2026-01-01"
FILING_W0, FILING_W1 = "2024-10-01", "2026-10-09"
EXCHANGES = {"XNYS", "XNAS", "XASE"}
BENCH = "SPY"


class HN1Stop(RuntimeError):
    """A stop condition of the run brief; the run must halt and report."""


def canonical_digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_contract() -> dict:
    c = json.loads(CONTRACT_PATH.read_text())
    got = canonical_digest(c)
    if got != CONTRACT_CANONICAL or c.get("status") != "FROZEN_BEFORE_OUTCOME":
        raise HN1Stop(f"contract checksum {got} != frozen {CONTRACT_CANONICAL}")
    return c


def split_of(signal_date: str) -> str:
    if signal_date < SNAPSHOT_DATE:
        return "PRE_SNAPSHOT"
    return "DEV" if signal_date <= DEV_END else "HOLDOUT"


def _gz(path):
    return json.loads(gzip.open(path).read())


def _snapshot_rows(obj) -> list[dict]:
    if "pages" in obj and isinstance(obj["pages"], list):
        return [r for p in obj["pages"] for r in p.get("results", [])]
    return obj.get("results", [])


def snapshot_paths() -> list[tuple[str, Path]]:
    """Every CS snapshot under data/runtime dated on or after the universe snapshot."""
    out = {}
    for p in glob.glob(str(RT / "**/CS_*.json.gz"), recursive=True):
        d = Path(p).name[3:13]
        if d >= SNAPSHOT_DATE:
            out.setdefault(d, Path(p))
    return sorted(out.items())


@dataclass
class TickerMaps:
    universe: dict[str, list[str]]           # cik -> tickers in CS_2024-10-25 (filtered rows)
    by_snapshot: list[tuple[str, dict[str, list[str]]]]   # (date, cik -> tickers), ascending

    def at_or_before(self, cik: str, date: str) -> list[str]:
        out = list(self.universe.get(cik, []))
        dates = [d for d, _ in self.by_snapshot]
        i = bisect.bisect_right(dates, date) - 1
        if i >= 0:
            for t in self.by_snapshot[i][1].get(cik, []):
                if t not in out:
                    out.append(t)
        return out

    def any_snapshot(self, cik: str) -> list[str]:
        out = list(self.universe.get(cik, []))
        for _, m in self.by_snapshot:
            for t in m.get(cik, []):
                if t not in out:
                    out.append(t)
        return out


def load_ticker_maps() -> TickerMaps:
    uni: dict[str, list[str]] = {}
    for r in _snapshot_rows(_gz(UNIVERSE)):
        cik = (r.get("cik") or "").lstrip("0")
        if r.get("type") == "CS" and cik and r.get("primary_exchange") in EXCHANGES:
            uni.setdefault(cik, []).append(r["ticker"])
    snaps = []
    for d, p in snapshot_paths():
        m: dict[str, list[str]] = {}
        for r in _snapshot_rows(_gz(p)):
            cik = (r.get("cik") or "").lstrip("0")
            if cik and r.get("type", "CS") == "CS":
                m.setdefault(cik, []).append(r["ticker"])
        snaps.append((d, m))
    return TickerMaps(uni, snaps)


@dataclass
class Panel:
    sessions: list[str]
    tickers: dict[str, int]
    open: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    splits: dict[str, list[tuple[str, float]]]   # ticker -> [(execution_date, to/from)]

    def idx(self, date: str) -> int:
        return self.sessions.index(date)

    def first_after(self, date: str) -> int | None:
        i = bisect.bisect_right(self.sessions, date)
        return i if i < len(self.sessions) else None

    def last_before(self, date: str) -> int | None:
        i = bisect.bisect_left(self.sessions, date) - 1
        return i if i >= 0 else None

    def present(self, ticker: str, t: int) -> bool:
        j = self.tickers.get(ticker)
        return j is not None and bool(np.isfinite(self.close[t, j]))

    def close_at(self, ticker: str, t: int) -> float:
        return float(self.close[t, self.tickers[ticker]])

    def split_factor(self, ticker: str, a: int, b: int) -> float:
        """Product of to/from for splits of ``ticker`` executing in sessions (a, b]."""
        lo, hi = self.sessions[a], self.sessions[b]
        f = 1.0
        for d, r in self.splits.get(ticker, []):
            if lo < d <= hi:
                f *= r
        return f


def load_panel(wanted: set[str]) -> Panel:
    files = sorted(glob.glob(str(GROUPED / "*.json.gz")))
    sessions, rows = [], []
    for p in files:
        j = _gz(p)
        if j.get("session") == "2024-09-16":
            continue    # NOT_AUTHORIZED stub, excluded by contract
        res = j["body"].get("results") or []
        if not res:
            raise HN1Stop(f"empty grouped session {p}")
        sessions.append(j["session"])
        rows.append({r["T"]: (r.get("o"), r.get("c"), r.get("v")) for r in res if r.get("T") in wanted})
    tick = {t: i for i, t in enumerate(sorted(wanted))}
    shape = (len(sessions), len(tick))
    o, c, v = (np.full(shape, np.nan) for _ in range(3))
    for t, rr in enumerate(rows):
        for k, (oo, cc, vv) in rr.items():
            j = tick[k]
            if oo is not None and cc is not None and cc > 0 and oo > 0:
                o[t, j], c[t, j], v[t, j] = oo, cc, (vv or 0.0)
    splits: dict[str, list[tuple[str, float]]] = {}
    for p in sorted(glob.glob(str(SPLITS / "*.json.gz"))):
        for r in _gz(p).get("results", []):
            if r.get("ticker") in wanted and r.get("split_from") and r.get("split_to"):
                splits.setdefault(r["ticker"], []).append(
                    (r["execution_date"], float(r["split_to"]) / float(r["split_from"])))
    for k in splits:
        splits[k] = sorted(set(splits[k]))
    if BENCH not in tick or np.isnan(c[:, tick[BENCH]]).any() or np.isnan(o[:, tick[BENCH]]).any():
        raise HN1Stop("SPY missing on some session")
    return Panel(sessions, tick, o, c, v, splits)
