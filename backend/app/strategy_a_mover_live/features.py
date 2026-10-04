"""The live feature contract: Kiwoom premarket + Massive daily, assembled into the panels
``scan_session`` already consumes. The scanner is not reimplemented and not modified.

The research scanner's interface is two panels and a symbol list, and that is exactly the seam
the live path uses. Nothing in ``scan``, ``score`` or ``actionability`` changes; only where the
numbers come from changes, and the contract says so field by field.

**The premarket panel.** One grid, ``max_lookback`` prior XNYS sessions oldest first with the
scan session last. The prior columns carry one number per symbol-session - A's premarket share
volume from the durable ``A_MOVER_PM_VOLUME_V1`` rows - and ``covered`` marks the sessions the
source answered for. The scan session's column carries the full snapshot. This is not a
convenience: ``PremarketPanel.rvol_baseline`` takes "the last N covered sessions strictly
before this column", so expressing A's covered-session walk as panel coverage makes the live
denominator the research denominator by construction, and makes it structurally impossible for
the scan session's own volume to enter its own denominator.

**The daily panel.** The previous close, the twenty-session ADV and ADDV, the reference
universe and the split calendar are Massive's. The live session's own grouped daily file does
not exist yet - it is produced after the close - so its column is present as an index and empty
as data, which is all ``previous_close`` and ``baselines`` need, since both read strictly
before the column.

**The gate window is absent, not faked.** Strategy A's deployed premarket gate reads
``[04:00, 09:30)``. At 09:15 that window is not over, so ``gate_*`` stays NaN and
``scan.gate_reading`` returns an unavailable reading. The real gate still runs at the open in
the entry path; what is lost is only the study's gate-pass *count*, which was a measurement,
never an admission.

This module registers the live feed into ``mover_scanner_source.LIVE_PREMARKET_FEEDS``, the
extension point that module was written with, so the production source stops answering
``NO_LIVE_PREMARKET_SOURCE`` only once a feed is actually registered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.collector.range import sessions_between
from app.backtest.mover_scanner_v1 import daily as D
from app.backtest.mover_scanner_v1 import premarket as P
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.market.calendar import MarketCalendar
from app.services.mover_scanner_source import (
    DataUnavailable, MoverDataUnavailableError, MoverPremarketSource, MoverScanInput,
)
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import universe as UNI
from app.strategy_a_mover_live.snapshot import SymbolSnapshot

#: The live source's name on a persisted run.
SOURCE_NAME = "KIWOOM_PREMARKET_LIVE"


@dataclass(frozen=True)
class LivePanels:
    """The assembled inputs plus the provenance of each side of the hybrid."""

    session: date
    symbols: tuple[str, ...]
    premarket: P.PremarketPanel
    daily: D.DailyPanel
    universe: UNI.UnionUniverse
    baselines: Mapping[str, B.AMoverBaseline]
    snapshots: Mapping[str, SymbolSnapshot]
    digest: str

    @property
    def baseline_provider_mix(self) -> dict[str, Any]:
        """The run's denominator provenance, as section I's named block."""
        return B.provider_mix(self.baselines)

    def declaration(self) -> dict[str, Any]:
        available = sum(1 for item in self.baselines.values() if item.available)
        return {
            "session": self.session.isoformat(),
            "feature_contract_version": LC.FEATURE_CONTRACT_VERSION,
            "hybrid_sources": dict(sorted(LC.HYBRID_SOURCES.items())),
            "scan_symbols": len(self.symbols),
            "snapshots": len(self.snapshots),
            "baselines_available": available,
            "baselines_insufficient": len(self.baselines) - available,
            "baseline_provider_mix": self.baseline_provider_mix,
            "gate_window_available_at_cut": False,
            "premarket_digest": self.digest,
            "universe": self.universe.declaration(),
        }


def live_daily_panel(repo: Path, session: date, symbols: Sequence[str], *,
                     calendar: MarketCalendar | None = None,
                     lookback_days: int = D.DAILY_LOOKBACK_DAYS + 10) -> D.DailyPanel:
    """Massive grouped daily up to D-1, with the live session as an empty trailing column.

    The live session's file cannot exist yet, so it is a grid position and nothing more. A
    *prior* missing file is a refusal, because the previous close and the baselines are read
    from those columns.
    """
    calendar = calendar or MarketCalendar()
    grid = [item.session_date for item in
            sessions_between(calendar, session - timedelta(days=lookback_days), session)]
    if not grid or grid[-1] != session:
        raise MoverDataUnavailableError(DataUnavailable.SESSION_NOT_COVERED,
                                        f"{session.isoformat()} is not an XNYS session")
    wanted = frozenset(symbols)
    close: dict[str, np.ndarray] = {}
    volume: dict[str, np.ndarray] = {}
    span = len(grid)
    for position, day in enumerate(grid[:-1]):
        path = D.grouped_path(repo, day)
        if path is None:
            raise MoverDataUnavailableError(
                DataUnavailable.NO_GROUPED_DAILY,
                f"grouped daily is missing for {day.isoformat()}")
        for row in D.grouped_rows(path):
            symbol, bar_close, bar_volume = row.get("T"), row.get("c"), row.get("v")
            if not isinstance(symbol, str) or symbol not in wanted:
                continue
            if not isinstance(bar_close, (int, float)) or not isinstance(bar_volume, (int, float)):
                continue
            if symbol not in close:
                close[symbol] = np.full(span, np.nan)
                volume[symbol] = np.full(span, np.nan)
            close[symbol][position] = float(bar_close)
            volume[symbol][position] = float(bar_volume)
    return D.DailyPanel(tuple(grid), close, volume)


def build_premarket_panel(session: date, symbols: Sequence[str],
                          snapshots: Mapping[str, SymbolSnapshot],
                          baselines: Mapping[str, B.AMoverBaseline], *,
                          config: MoverScannerConfig | None = None,
                          calendar: MarketCalendar | None = None,
                          rule: B.BaselineRule | None = None) -> P.PremarketPanel:
    """One grid: the lookback columns from the durable rows, the scan session from the snapshot."""
    config = config or MoverScannerConfig()
    calendar = calendar or MarketCalendar()
    rule = rule or B.BaselineRule()
    prior = tuple(reversed(B.lookback_sessions(calendar, session,
                                               limit=rule.max_lookback_sessions)))
    grid = prior + (session,)
    members = tuple(symbols)
    span = (len(members), len(grid))
    values = {name: np.full(span, np.nan) for name in P.FIELDS}
    covered = np.zeros(span, dtype=bool)
    column_of = {day: index for index, day in enumerate(grid)}
    for row, symbol in enumerate(members):
        item = baselines.get(symbol)
        if item is not None:
            for day, volume in zip(item.used_sessions, item.volumes, strict=True):
                column = column_of[day]
                covered[row, column] = True
                values["pm_volume"][row, column] = float(volume)
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            continue
        covered[row, span[1] - 1] = True
        for name in P.SCAN_FIELDS:
            values[name][row, span[1] - 1] = float(snapshot.derived[name])
    digest = hashlib.sha256(json.dumps(
        {"source": SOURCE_NAME, "baseline": rule.declaration(), "session": session.isoformat(),
         "symbols": list(members)}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return P.PremarketPanel(members, grid, values, covered, config.scan_cut_minute, digest)


def assemble(repo: Path, session: date, snapshots: Mapping[str, SymbolSnapshot],
             baselines: Mapping[str, B.AMoverBaseline], union: UNI.UnionUniverse, *,
             config: MoverScannerConfig | None = None,
             calendar: MarketCalendar | None = None,
             rule: B.BaselineRule | None = None) -> LivePanels:
    """Assemble the live inputs. Only A's own universe is scanned; E-only symbols are not."""
    config = config or MoverScannerConfig()
    calendar = calendar or MarketCalendar()
    scan_symbols = tuple(symbol for symbol in union.a_symbols if symbol in snapshots)
    if not scan_symbols:
        raise MoverDataUnavailableError(
            DataUnavailable.NO_MINUTE_TAPE,
            f"no finalized A snapshot for any universe member on {session.isoformat()}")
    daily = live_daily_panel(repo, session, union.a_symbols, calendar=calendar)
    premarket = build_premarket_panel(session, scan_symbols, snapshots, baselines,
                                      config=config, calendar=calendar, rule=rule)
    return LivePanels(session=session, symbols=scan_symbols, premarket=premarket, daily=daily,
                      universe=union, baselines=dict(baselines), snapshots=dict(snapshots),
                      digest=premarket.cache_digest)


def scan_input(panels: LivePanels) -> MoverScanInput:
    """The live panels in the shape ``MoverScanInput`` declares, marked live."""
    return MoverScanInput(
        session=panels.session, symbols=panels.symbols, premarket=panels.premarket,
        daily=panels.daily, source_name=SOURCE_NAME, live=True,
        universe_as_of=panels.universe.reference_as_of,
        universe_checksum=panels.universe.reference_checksum,
        premarket_digest=panels.digest,
        baseline_sessions=B.BaselineRule().sessions,
        baseline_provider_mix=panels.baseline_provider_mix)


class LivePremarketFeed(MoverPremarketSource):
    """The registered live feed: a prepared ``LivePanels`` for one session, or a refusal.

    The feed does not acquire anything itself. The shared collector owns the lanes and hands
    the finished snapshots in, which is what keeps one process in charge of the rate limit.
    """

    name = SOURCE_NAME
    live = True

    def __init__(self, panels_by_session: Mapping[date, LivePanels]) -> None:
        self.panels_by_session = dict(panels_by_session)

    def load(self, session: date, config: MoverScannerConfig) -> MoverScanInput:
        panels = self.panels_by_session.get(session)
        if panels is None:
            raise MoverDataUnavailableError(
                DataUnavailable.NO_LIVE_PREMARKET_SOURCE,
                f"the shared collector has staged no {SOURCE_NAME} panels for "
                f"{session.isoformat()}")
        if panels.premarket.cut_minute != config.scan_cut_minute:
            raise MoverDataUnavailableError(
                DataUnavailable.NO_LIVE_PREMARKET_SOURCE,
                "the staged panels were cut at a different minute than the scanner declares")
        return scan_input(panels)


def register(panels: LivePanels, feeds: dict | None = None) -> str:
    """Register one session's panels as the live feed. Returns the registry key used."""
    from app.services import mover_scanner_source as M
    registry = M.LIVE_PREMARKET_FEEDS if feeds is None else feeds
    feed = LivePremarketFeed({panels.session: panels})
    registry[SOURCE_NAME] = feed.load
    return SOURCE_NAME


def unregister(feeds: dict | None = None) -> None:
    from app.services import mover_scanner_source as M
    registry = M.LIVE_PREMARKET_FEEDS if feeds is None else feeds
    registry.pop(SOURCE_NAME, None)
