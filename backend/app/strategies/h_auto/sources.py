"""H's production data sources for new-candidate discovery: Strategy A's stores, read and never written.

Measured on 2026-10-14 (local tree and trader-j), before any of this was written:

* **Strategy A** reads ``data/runtime/strategy_c/raw/{grouped,splits,tickers}``. ``grouped`` is the
  market-wide Massive grouped-daily store (unadjusted closes, ``usb-grouped-daily`` 00:40 ET, one file
  per session); ``splits`` is the rolling split window (``usb-splits-refresh``); ``tickers`` holds
  one manual CS reference snapshot, ``CS_2026-10-01`` (A takes the newest snapshot on or before the
  session and lets it age; ``A_MOVER_NEXT_SESSION_AUTHORITY_V1.md`` section 10).
* **Strategy E** builds its universe from a frozen base slice and its own 20-session forward store
  under ``/root/usb_runtime``; it carries no CIK and no reference snapshot, so H cannot take identity
  from it.
* **C / shared**: ``strategy_c_e0.cik_map`` already defines ticker -> CIK as "the ``cik`` of the CS
  snapshot with the latest ``as_of <= D``"; ``MarketCalendar`` is the one calendar A, E and H use.

H's research datasets (the 2024-10-25 reference, the HIST-V1 mirror ending 2026-09-16, the full
D1.1 companyfacts store fetched 2026-09-28) stay what they are: frozen research inputs. Production
candidate discovery reads the three A stores above plus H's own dated reference snapshots
(``<H store>/auto/reference/CS_<as_of>.json.gz``), fetched with the same ``raw_fetch.fetch_tickers``
that produced A's snapshot, in the same format, into H's own directory - A's ``tickers`` directory is
never written. Prices come from A's grouped store only (the frozen mirror is not a production
source); the executor's cohort panel (``inputs.load_panel``) keeps its own merge for launch
reproducibility.

Everything in this module is a pure read of files, except ``refresh_reference`` which writes one
file per session under H's own store.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from app.market.calendar import MarketCalendar
from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import store as AS

A_RAW_ROOT = IN.REPO_ROOT / "data/runtime/strategy_c/raw"
A_REFERENCE_DIR = A_RAW_ROOT / "tickers"
A_GROUPED_DIR = IN.GROUPED_DIR
A_SPLITS_DIR = A_RAW_ROOT / "splits"
H_REFERENCE = "reference"
#: Dated H reference snapshots kept (about 120 KB each). The newest is what the scan reads.
KEEP_REFERENCE_SNAPSHOTS = 30
#: E1's own floor (``eligibility.MIN_TRAILING_SESSIONS``) and the executor's (``check_panel``): the
#: market-wide store must reach back at least this far for any candidate to be valued.
MIN_HISTORY_SESSIONS = 252
BENCHMARK = IN.BENCHMARK

SOURCE_CONTRACT: dict[str, str] = {
    "active_universe": "newest CS reference snapshot on or before the session, from A's "
                       "data/runtime/strategy_c/raw/tickers and H's own auto/reference (same fetcher, "
                       "same format); active US common stock with a CIK",
    "ticker_cik": "the cik field of that snapshot row (strategy_c_e0.cik_map rule); never ticker text",
    "daily_prices": "A's data/runtime/strategy_c/raw/grouped/<session>.json.gz (Massive grouped daily, "
                    "unadjusted, written by usb-grouped-daily)",
    "historical_lookback": "every session file in A's grouped store; at least 252 sessions are required",
    "market_calendar": "app.market.calendar.MarketCalendar (shared with A and E)",
    "split_adjustment": "none in the store (unadjusted); split execution dates from A's "
                        "data/runtime/strategy_c/raw/splits are passed to the frozen D1 dilution check",
    "fundamentals": "SEC submissions + companyfacts per CIK, on demand, through sec_store/xbrl_store "
                    "(h_auto.facts_cache), never a full-universe copy",
    "research_frozen_inputs_not_used_here": "research_universe_u1 CS_2024-10-25, strategy_b_e0 HIST-V1 "
                                            "mirror, strategy_h_v2/d1_1 companyfacts store",
}


# -------------------------------------------------------------------------------------------------
# Reference snapshots (identity, ticker <-> CIK, listing status)
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceSnapshot:
    as_of: date
    path: Path
    owner: str                      # "A" or "H"


def h_reference_dir() -> Path:
    return AS.root() / H_REFERENCE


def reference_snapshots(*, a_dir: Path | None = None, h_dir: Path | None = None) -> list[ReferenceSnapshot]:
    """Every dated CS snapshot H may read, oldest first. A's directory is read, never written."""
    found: dict[date, ReferenceSnapshot] = {}
    for owner, directory in (("A", a_dir if a_dir is not None else A_REFERENCE_DIR),
                             ("H", h_dir if h_dir is not None else h_reference_dir())):
        if not directory.is_dir():
            continue
        for path in directory.glob("CS_*.json.gz"):
            try:
                as_of = date.fromisoformat(path.name[3:13])
            except ValueError:
                continue
            if as_of not in found or owner == "H":      # the same day from H's own fetch is preferred
                found[as_of] = ReferenceSnapshot(as_of, path, owner)
    return [found[k] for k in sorted(found)]


def reference_in_force(session: date, snapshots: Sequence[ReferenceSnapshot] | None = None) -> ReferenceSnapshot | None:
    """The snapshot a candidate at ``session`` is identified from: newest ``as_of <= session``."""
    prior = [s for s in (snapshots if snapshots is not None else reference_snapshots()) if s.as_of <= session]
    return prior[-1] if prior else None


def reference_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(gzip.decompress(path.read_bytes()))
    if isinstance(doc.get("results"), list):
        return list(doc["results"])
    return [row for page in doc.get("pages") or [] for row in page.get("results") or []]


def active_cs_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows if r.get("type") == "CS" and r.get("active") is True
            and r.get("market") == "stocks" and r.get("locale") == "us"]


def universe_rows(snapshot: ReferenceSnapshot, *, known_at: datetime) -> list:
    """``universe.build_universe`` (frozen D1 code) over the snapshot's active CS rows."""
    from app.backtest.strategy_h_v2.universe import build_universe
    return build_universe(active_cs_rows(reference_rows(snapshot.path)),
                          snapshot_date=snapshot.as_of.isoformat(), known_at=known_at)


def cik_map(snapshot: ReferenceSnapshot) -> dict[str, str]:
    """ticker -> 10-digit CIK from one snapshot (``cik_map.load_snapshot_maps`` rule)."""
    out: dict[str, str] = {}
    for row in reference_rows(snapshot.path):
        ticker, cik = row.get("ticker"), str(row.get("cik") or "").strip()
        if ticker and cik:
            out[str(ticker)] = f"{int(cik):010d}"
    return out


def refresh_reference(now: datetime, *, client_factory: Callable[[], Any], calendar: MarketCalendar | None = None,
                      log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """One CS snapshot for the publishable session into H's own directory, with A's fetcher.

    ``raw_fetch.fetch_tickers`` is the function that produced A's ``CS_2026-10-01`` (same endpoint,
    same ``strategy-c-selection-raw-v1`` document); it is cached per ``as_of`` and never rewrites.
    About six Massive calls (one per 1,000-row page) under the client's own 5/minute limiter.
    """
    from app.backtest.collector.range import previous_trading_day
    from app.backtest.strategy_c_selection import raw_fetch as RF
    as_of = previous_trading_day(calendar or MarketCalendar(), now)
    staging = AS.root() / "reference_staging"
    final = h_reference_dir() / f"CS_{as_of.isoformat()}.json.gz"
    if final.is_file():
        return {"as_of": as_of.isoformat(), "status": "cached", "path": str(final)}
    client = client_factory()
    try:
        outcome = RF.fetch_tickers(client, staging, as_of, log=log)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    staged = RF.tickers_path(staging, as_of)
    rows = reference_rows(staged)
    if not rows or len({r.get("ticker") for r in rows}) != len(rows):
        staged.unlink(missing_ok=True)
        return {"as_of": as_of.isoformat(), "status": "REJECTED:EMPTY_OR_DUPLICATE_TICKERS", "rows": len(rows)}
    final.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(final)
    pruned = prune_reference()
    return {"as_of": as_of.isoformat(), "status": outcome, "rows": len(rows), "path": str(final), "pruned": pruned}


def prune_reference(keep: int = KEEP_REFERENCE_SNAPSHOTS) -> list[str]:
    mine = [s for s in reference_snapshots(a_dir=Path("/nonexistent")) if s.owner == "H"]
    dropped = []
    for old in mine[:-keep] if keep > 0 else []:
        old.path.unlink(missing_ok=True)
        dropped.append(old.path.name)
    return dropped


# -------------------------------------------------------------------------------------------------
# A's grouped-daily store (prices)
# -------------------------------------------------------------------------------------------------

def session_files(until: date = date.max, *, directory: Path | None = None) -> list[tuple[date, Path]]:
    """Market-wide session files H reads: A's grouped store, plus H's pinned history for sessions A's
    store does not hold (A wins on a shared session). An explicit ``directory`` reads that one only."""
    if directory is not None:
        return list(IN._grouped_files(directory, until))
    found = {d: p for d, p in IN.history_files(until)}
    found.update({d: p for d, p in IN._grouped_files(A_GROUPED_DIR, until)})
    return sorted(found.items())


def grouped_sessions(directory: Path | None = None) -> list[date]:
    return [d for d, _ in session_files(directory=directory)]


def _rows_of(path: Path) -> Iterator[tuple[str, float, float]]:
    for ticker, close, volume in IN.read_session_rows(path):
        if volume is not None:
            yield ticker, close, volume


@dataclass
class MarketGate:
    """Per-ticker market facts over the whole store, enough for E1's market tier and nothing else."""

    sessions: int
    latest_session: date
    latest_close: float
    trailing_dollar_volume: list[float]        # the newest 21 sessions' close*volume, oldest first

    @property
    def trailing_avg_dollar_volume(self) -> float | None:
        return sum(self.trailing_dollar_volume) / len(self.trailing_dollar_volume) if self.trailing_dollar_volume else None


def market_gate(tickers: Iterable[str] | None = None, *, directory: Path | None = None,
                until: date | None = None) -> dict[str, MarketGate]:
    """One streaming pass over A's store: session count, latest close and 21-session ADV per ticker.

    These are exactly the three inputs ``eligibility.evaluate_eligibility`` reads from
    ``MarketSnapshot`` (``market_snapshot`` in run_strategy_h_v2_d1 counts every stored session and
    averages the newest 21). A row that fails them is INELIGIBLE or DATA_NOT_READY whatever its
    fundamentals say, so fetching SEC for it would change nothing.
    """
    wanted = set(tickers) if tickers is not None else None
    out: dict[str, MarketGate] = {}
    for day, path in session_files(until or date.max, directory=directory):
        for ticker, close, volume in _rows_of(path):
            if wanted is not None and ticker not in wanted:
                continue
            gate = out.get(ticker)
            if gate is None:
                out[ticker] = MarketGate(1, day, close, [close * volume])
                continue
            gate.sessions += 1
            if day >= gate.latest_session:
                gate.latest_session, gate.latest_close = day, close
            gate.trailing_dollar_volume.append(close * volume)
            if len(gate.trailing_dollar_volume) > 21:
                del gate.trailing_dollar_volume[0]
    return out


def series_for(tickers: Iterable[str], *, directory: Path | None = None,
               until: date | None = None) -> dict[str, dict[date, tuple[float, float]]]:
    """Full (close, volume) series for a few tickers from A's store - the shape D1's loader produced."""
    wanted = set(tickers) | {BENCHMARK}
    out: dict[str, dict[date, tuple[float, float]]] = {t: {} for t in wanted}
    for day, path in session_files(until or date.max, directory=directory):
        for ticker, close, volume in _rows_of(path):
            if ticker in wanted:
                out[ticker][day] = (close, volume)
    return out


def splits_index(directories: Sequence[Path] | None = None) -> dict[str, list[date]]:
    """Split execution dates per ticker from A's rolling split files (duplicates across files collapse)."""
    found: dict[str, set[date]] = {}
    for directory in (directories if directories is not None else (A_SPLITS_DIR,)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("splits_*.json.gz")):
            try:
                body = json.loads(gzip.decompress(path.read_bytes()))
            except (OSError, ValueError):
                continue
            pages = body.get("pages")
            rows = ([r for p in pages for r in (p.get("results") or [])] if isinstance(pages, list)
                    else (body.get("results") or []))
            for row in rows:
                if row.get("ticker") and row.get("execution_date"):
                    try:
                        found.setdefault(str(row["ticker"]), set()).add(date.fromisoformat(row["execution_date"]))
                    except ValueError:
                        continue
    return {t: sorted(v) for t, v in found.items()}


# -------------------------------------------------------------------------------------------------
# Readiness report (sections 5 and 6 of the completion brief)
# -------------------------------------------------------------------------------------------------

def universe_readiness(now: datetime, *, calendar: MarketCalendar | None = None) -> dict[str, Any]:
    """What the production universe looks like today. Pure read."""
    from app.backtest.collector.range import previous_trading_day
    from app.backtest.strategy_h0.h0_5 import ELIGIBLE_EXCHANGES
    session = previous_trading_day(calendar or MarketCalendar(), now)
    snaps = reference_snapshots()
    snap = reference_in_force(session, snaps)
    if snap is None:
        return {"state": "REFERENCE_NOT_READY", "session": session.isoformat(), "snapshots": []}
    rows = reference_rows(snap.path)
    active = active_cs_rows(rows)
    with_cik = [r for r in active if str(r.get("cik") or "").strip()]
    supported = [r for r in with_cik if r.get("primary_exchange") in ELIGIBLE_EXCHANGES]
    with_identity = [r for r in supported if r.get("share_class_figi") or r.get("composite_figi")]
    today = now.astimezone(timezone.utc).date()
    return {
        "session": session.isoformat(),
        "snapshot": {"as_of": snap.as_of.isoformat(), "owner": snap.owner, "path": str(snap.path),
                     "age_days": (session - snap.as_of).days},
        "snapshots_available": [{"as_of": s.as_of.isoformat(), "owner": s.owner} for s in snaps],
        "rows": len(rows), "active_us_common_stock": len(active),
        "with_cik": len(with_cik), "unresolved_cik": len(active) - len(with_cik),
        "exchange_supported": len(supported), "with_security_id": len(with_identity),
        "unique_tickers": len({r["ticker"] for r in with_identity}),
        "unique_ciks": len({f"{int(r['cik']):010d}" for r in with_identity}),
        "source_contract": SOURCE_CONTRACT, "as_of_today": today.isoformat(),
    }


def price_readiness(now: datetime, sample: Sequence[str] = (), *, calendar: MarketCalendar | None = None,
                    directory: Path | None = None) -> dict[str, Any]:
    """Store depth, latest session versus the publishable one, and a per-ticker coverage sample."""
    from app.backtest.collector.range import previous_trading_day
    cal = calendar or MarketCalendar()
    directory = directory if directory is not None else A_GROUPED_DIR
    publishable = previous_trading_day(cal, now)
    sessions = grouped_sessions(directory)
    out: dict[str, Any] = {
        "store": str(directory) if directory is not None else f"{A_GROUPED_DIR} + {IN.history_dir()}",
        "files": len(sessions), "a_store_files": len(list(IN._grouped_files(A_GROUPED_DIR, date.max))),
        "h_history_files": len(list(IN.history_files())),
        "first_session": sessions[0].isoformat() if sessions else None,
        "latest_session": sessions[-1].isoformat() if sessions else None,
        "publishable_session": publishable.isoformat(),
        "latest_is_publishable": bool(sessions) and sessions[-1] == publishable,
        "history_sufficient": len(sessions) >= MIN_HISTORY_SESSIONS,
        "min_history_sessions": MIN_HISTORY_SESSIONS,
        "adjusted": False, "sample": [],
    }
    if sample:
        series = series_for(sample, directory=directory)
        splits = splits_index()
        for ticker in sample:
            days = sorted(series.get(ticker, {}))
            out["sample"].append({
                "ticker": ticker, "sessions": len(days),
                "coverage_start": days[0].isoformat() if days else None,
                "latest": days[-1].isoformat() if days else None,
                "split_adjusted": False,
                "splits_in_window": [d.isoformat() for d in splits.get(ticker, []) if days and d >= days[0]],
                "usable_for_h": bool(days) and len(days) >= MIN_HISTORY_SESSIONS and days[-1] == publishable,
            })
    return out


def deterministic_sample(tickers: Sequence[str], n: int = 20) -> list[str]:
    """Every k-th ticker of the sorted list: reproducible and not chosen by any result."""
    ordered = sorted(set(tickers))
    if len(ordered) <= n:
        return ordered
    step = len(ordered) / n
    return [ordered[int(i * step)] for i in range(n)]
