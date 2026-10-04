"""The union acquisition universe: one plan for both cuts, recomputed and never assumed.

A's live universe is the active common-stock universe of the dated reference cache in force on
the session, reduced only by prunings that are *derivable before the session opens*:

* a full twenty-session daily baseline must exist, because ``scan`` refuses a symbol without
  one (``NO_DAILY_VOLUME_BASELINE``) and that refusal is knowable from D-1 data alone;
* a symbol whose split executes that morning is dropped, because the stored previous close is
  on the old basis and the premarket prints are on the new one.

Nothing else is pruned, and the reason is a measured one. A rejects most of its universe on
*premarket* evidence - no print, too few prints, too little notional - and those facts do not
exist until the morning. A prune such as "it had no premarket print yesterday" would remove
precisely the catalyst-day symbol the scanner exists to find, which is the same failure the
Kiwoom ranking prefilter produced: recall 40-62% with the missed names carrying a median gap
of +5.1%. A price floor on the D-1 close is also *not* applied here: A rejects on the
premarket print price, so a D-1 floor is a judgment rather than a derivation.

E's universe is read from the canonical artifact E itself stages, never rebuilt here. The two
are unioned because E is nearly a subset of A - measured at four to five E-only symbols - so
one acquisition plan costs what A's own plan costs and E rides along.

Every count this module reports is computed from the files on disk. No size is written down.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.collector.range import sessions_between
from app.backtest.mover_scanner_v1 import daily as D
from app.backtest.mover_scanner_v1 import universe as U
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.market.calendar import MarketCalendar

E_UNIVERSE_DIR = "data/runtime/strategy_e_max/rt2"
#: The prunings applied, named so a report can show that nothing else was.
PRUNE_RULES = ("ACTIVE_COMMON_STOCK_REFERENCE_CACHE",
               "FULL_DAILY_VOLUME_BASELINE_AT_D_MINUS_1",
               "NO_SPLIT_EXECUTING_THIS_SESSION")
#: Prunings deliberately not applied, with why, so the choice is reviewable.
PRUNE_REJECTED = {
    "D_MINUS_1_CLOSE_FLOOR": "A rejects on the premarket print price, so a D-1 close floor is a "
                             "judgment, not a derivation of A's own rule",
    "PRIOR_SESSION_PREMARKET_ACTIVITY": "removes the catalyst-day symbol the scanner exists to "
                                        "find; the measured failure mode of a ranking prefilter",
}


class UniverseUnavailable(RuntimeError):
    """A universe input for the session is missing, so no acquisition plan exists."""


@dataclass(frozen=True)
class UnionUniverse:
    """One session's acquisition plan and the arithmetic behind its size."""

    session: date
    a_symbols: tuple[str, ...]
    e_symbols: tuple[str, ...]
    union: tuple[str, ...]
    reference_as_of: date
    reference_checksum: str
    reference_active_rows: int
    pruned_no_daily_baseline: int
    pruned_split_session: int
    e_artifact: str | None

    @property
    def e_only(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.e_symbols) - set(self.a_symbols)))

    @property
    def intersection(self) -> int:
        return len(set(self.a_symbols) & set(self.e_symbols))

    @property
    def checksum(self) -> str:
        return hashlib.sha256(
            json.dumps(list(self.union), separators=(",", ":")).encode()).hexdigest()

    def declaration(self) -> dict[str, Any]:
        return {
            "session": self.session.isoformat(),
            "reference_as_of": self.reference_as_of.isoformat(),
            "reference_checksum": self.reference_checksum,
            "reference_active_rows": self.reference_active_rows,
            "a_symbols": len(self.a_symbols),
            "e_symbols": len(self.e_symbols),
            "union_symbols": len(self.union),
            "e_only_symbols": len(self.e_only),
            "e_only_examples": list(self.e_only[:10]),
            "intersection": self.intersection,
            "pruned_no_daily_baseline": self.pruned_no_daily_baseline,
            "pruned_split_session": self.pruned_split_session,
            "prune_rules_applied": list(PRUNE_RULES),
            "prune_rules_rejected": dict(PRUNE_REJECTED),
            "e_artifact": self.e_artifact,
            # An absent artifact is said out loud. E's universe is only ever read from the
            # artifact E itself staged, dated on or before the session, so "no E side" means
            # "not staged for this session" and never "E has no universe".
            "e_side_status": ("STAGED" if self.e_artifact
                              else "NOT_STAGED_FOR_THIS_SESSION"),
            "union_checksum": self.checksum,
        }


def e_universe_path(repo: Path, session: date, directory: str = E_UNIVERSE_DIR) -> Path | None:
    """E's canonical artifact for the session, or the newest one on or before it."""
    base = Path(repo) / directory
    exact = base / f"universe_{session.isoformat()}.json"
    if exact.is_file():
        return exact
    if not base.is_dir():
        return None
    prior = sorted(path for path in base.glob("universe_*.json")
                   if date.fromisoformat(path.stem.split("_")[1]) <= session)
    return prior[-1] if prior else None


def e_symbols(repo: Path, session: date, directory: str = E_UNIVERSE_DIR,
              ) -> tuple[tuple[str, ...], str | None]:
    """E's universe as E staged it. A missing artifact is an empty E side, not a guess."""
    path = e_universe_path(repo, session, directory)
    if path is None:
        return (), None
    body = json.loads(path.read_text(encoding="utf-8"))
    return tuple(sorted(str(symbol) for symbol in body.get("symbols") or ())), path.name


def daily_baseline_symbols(daily: D.DailyPanel, symbols: Iterable[str], position: int,
                           baseline_sessions: int) -> tuple[frozenset[str], int]:
    """Symbols with the full daily baseline ``scan`` requires, and how many were dropped."""
    keep: set[str] = set()
    dropped = 0
    for symbol in symbols:
        adv, addv, used = daily.baselines(symbol, position, baseline_sessions)
        if adv and addv and adv > 0 and addv > 0 and used >= baseline_sessions:
            keep.add(symbol)
        else:
            dropped += 1
    return frozenset(keep), dropped


def session_daily_panel(repo: Path, grid: Sequence[date], symbols: frozenset[str],
                        ) -> D.DailyPanel:
    """The grid's daily panel, with the session's own column an index when its file is absent.

    A session's grouped daily file is produced after that session's close, so on the morning of
    a live session it cannot exist yet. Requiring it would refuse every live morning for the
    one column nothing reads: ``previous_close`` and ``baselines`` both read strictly before the
    column, which is the same contract ``features.live_daily_panel`` states for the scan panel.
    A *prior* missing file stays a refusal, because those columns are read. A session whose file
    does exist is loaded exactly as before, so no past run changes.
    """
    if D.grouped_path(repo, grid[-1]) is not None:
        return D.load_panel(repo, grid, symbols=symbols)
    prior = D.load_panel(repo, grid[:-1], symbols=symbols)
    close = {symbol: np.append(series, np.nan) for symbol, series in prior.close.items()}
    volume = {symbol: np.append(series, np.nan) for symbol, series in prior.volume.items()}
    return D.DailyPanel(tuple(grid), close, volume)


def build(repo: Path, session: date, *, config: MoverScannerConfig | None = None,
          calendar: MarketCalendar | None = None,
          daily: D.DailyPanel | None = None,
          e_directory: str = E_UNIVERSE_DIR) -> UnionUniverse:
    """The union plan for one session, every count computed from the stores on disk."""
    config = config or MoverScannerConfig()
    calendar = calendar or MarketCalendar()
    repo = Path(repo)
    universes = U.load_universes(repo, exclude_non_common=config.exclude_non_common_by_cik_prefix)
    base = U.universe_for(universes, session)
    if base is None:
        raise UniverseUnavailable(f"no dated reference cache is in force on {session.isoformat()}")
    splits = U.split_sessions(repo)
    active = sorted(base.symbols)
    if daily is None:
        start = session - timedelta(days=D.DAILY_LOOKBACK_DAYS + 10)
        grid = [item.session_date for item in sessions_between(calendar, start, session)]
        try:
            daily = session_daily_panel(repo, grid, frozenset(active))
        except FileNotFoundError as error:
            raise UniverseUnavailable(str(error)) from error
    position = daily.index[session]
    with_baseline, dropped_baseline = daily_baseline_symbols(
        daily, active, position, config.daily_baseline_sessions)
    a_live = U.eligible_symbols(base, with_baseline, splits, session,
                               exclude_split_sessions=config.exclude_split_execution_sessions)
    dropped_split = len(with_baseline) - len(a_live)
    e_side, artifact = e_symbols(repo, session, e_directory)
    union = tuple(sorted(set(a_live) | set(e_side)))
    return UnionUniverse(session=session, a_symbols=tuple(a_live), e_symbols=e_side,
                         union=union, reference_as_of=base.as_of,
                         reference_checksum=base.checksum, reference_active_rows=len(active),
                         pruned_no_daily_baseline=dropped_baseline,
                         pruned_split_session=dropped_split, e_artifact=artifact)


def acquisition_plan(union: UnionUniverse, shard_minute: Sequence[str],
                     shard_tick: Sequence[str]) -> Mapping[str, Any]:
    """The union plan expressed as one ordered pass, E's tick shard last."""
    from app.strategy_a_mover_live.schedule import a_pass_order
    order = a_pass_order([s for s in union.union if s not in set(shard_tick)], shard_tick)
    return {"symbols": len(order), "order": order,
            "tail_is_e_tick_shard": tuple(order[-len(shard_tick):]) == tuple(shard_tick)
            if shard_tick else True,
            "unordered_minute_shard": len(shard_minute)}
