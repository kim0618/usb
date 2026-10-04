"""Where the forward mover scan gets its 09:15 ET premarket data, and when it must refuse.

The V1.2 scanner reads four market-wide inputs: a dated common-stock reference universe, the
splits store, grouped daily bars for the previous close and the 20-session volume baselines,
and premarket minute bars for the scan session plus the 20 sessions behind it that the
relative-volume baseline is a median over. ``scan_session`` is handed those as two panels and
a symbol list, so a data source is exactly the thing that can build them for one session.

Two sources exist, and the difference between them is the point of this module.

``LocalSnapshotSource`` builds the panels from the research stores already on disk. It is
point-in-time for the session it is asked about - the premarket panel reads only bars that
closed before the cut - but the stores themselves are collected end-of-day, so it can only
answer about a session that has already been collected. It is the shadow and dry-run source,
and :attr:`live` is False so a runtime cannot mistake it for one.

``LiveRuntimeSource`` is the production source, and it currently answers
``NO_LIVE_PREMARKET_SOURCE`` for every session. That is a measurement of the runtime's data
authority, not a placeholder: Kiwoom's universe TRs rank at most 100 symbols and its minute
chart is one paginated request per symbol, and the Massive Stocks Basic plan is documented
end-of-day at five calls per minute, so neither can produce a market-wide premarket
cross-section at 09:15 ET. A feed that can is registered in ``LIVE_PREMARKET_FEEDS`` and the
source starts using it; until one is, the runtime records DATA_UNAVAILABLE and runs nothing,
because a quiet fallback onto an end-of-day store would be a scan of yesterday presented as
this morning's.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.backtest.collector.range import sessions_between
from app.backtest.mover_scanner_v1 import daily as D
from app.backtest.mover_scanner_v1 import premarket as P
from app.backtest.mover_scanner_v1 import run as R
from app.backtest.mover_scanner_v1 import universe as U
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.strategy_b_e0.session_cache import SessionCache
from app.market.calendar import MarketCalendar


class DataUnavailable(StrEnum):
    """Why a session cannot be scanned. Every value is recorded, never absorbed."""

    NO_LIVE_PREMARKET_SOURCE = "NO_LIVE_PREMARKET_SOURCE"
    NO_MINUTE_TAPE = "NO_MINUTE_TAPE"
    SESSION_NOT_COVERED = "SESSION_NOT_COVERED"
    PREMARKET_BASELINE_TOO_SHORT = "PREMARKET_BASELINE_TOO_SHORT"
    NO_REFERENCE_UNIVERSE = "NO_REFERENCE_UNIVERSE"
    NO_GROUPED_DAILY = "NO_GROUPED_DAILY"


class MoverDataUnavailableError(RuntimeError):
    """The named reason one session's inputs could not be assembled."""

    def __init__(self, reason: DataUnavailable, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class MoverScanInput:
    """One session's assembled inputs, in the shape ``scan_session`` consumes."""

    session: date
    symbols: tuple[str, ...]
    premarket: P.PremarketPanel
    daily: D.DailyPanel
    #: Named on the persisted run as its data provider, so a row says what it read.
    source_name: str
    #: False for every store that is collected after the close.
    live: bool
    universe_as_of: date
    universe_checksum: str
    premarket_digest: str
    baseline_sessions: int
    #: Which provider supplied each session of the relative-volume denominator, when the source
    #: knows. A research store reads one tape and has nothing to mix, so it leaves this None;
    #: the live source fills it and the run record carries it. See
    #: ``strategy_a_mover_live.baseline.provider_mix``.
    baseline_provider_mix: Mapping[str, Any] | None = None
    baseline_readiness: Mapping[str, Any] | None = None


class MoverPremarketSource:
    """A source of one session's mover-scan inputs."""

    name: str = "UNSET"
    live: bool = False

    def load(self, session: date, config: MoverScannerConfig) -> MoverScanInput:
        raise NotImplementedError


#: Live market-wide premarket feeds this runtime can read. Empty: see the module docstring.
#: A registered factory takes the session and config and returns a ``MoverScanInput``.
LIVE_PREMARKET_FEEDS: dict[str, Callable[[date, MoverScannerConfig], MoverScanInput]] = {}


class LiveRuntimeSource(MoverPremarketSource):
    """The production source: a registered live feed, or a named refusal."""

    name = "LIVE_RUNTIME"
    live = True

    def __init__(self, feeds: Mapping[str, Callable[..., MoverScanInput]] | None = None) -> None:
        self.feeds = dict(LIVE_PREMARKET_FEEDS if feeds is None else feeds)

    def load(self, session: date, config: MoverScannerConfig) -> MoverScanInput:
        for feed_name in sorted(self.feeds):
            return self.feeds[feed_name](session, config)
        raise MoverDataUnavailableError(
            DataUnavailable.NO_LIVE_PREMARKET_SOURCE,
            "no runtime feed supplies a market-wide premarket cross-section at the scan cut; "
            "Kiwoom ranks at most 100 symbols and charts one symbol per request, and the "
            "Massive Stocks Basic plan is end-of-day at five calls per minute")


class LocalSnapshotSource(MoverPremarketSource):
    """Research stores, read only, for the shadow and dry-run paths.

    The panels are built by the same modules the FINAL study used, over a window ending at the
    requested session, so a snapshot run reaches the same ``scan_session`` with the same kind
    of input. Nothing is written and no provider is reached.
    """

    name = "LOCAL_SNAPSHOT"
    live = False

    def __init__(self, repo: Path, *, cache_directory: Path | None = None,
                 calendar: MarketCalendar | None = None) -> None:
        self.repo = Path(repo)
        self.cache_directory = cache_directory
        self.calendar = calendar or MarketCalendar()
        self._cache: SessionCache | None = None
        self._selected: R.SelectedCache | None = None

    def _session_cache(self) -> tuple[SessionCache, R.SelectedCache]:
        if self._cache is None:
            try:
                selected = R.select_cache(self.repo, self.cache_directory)
            except R.StudyHardFail as error:
                raise MoverDataUnavailableError(DataUnavailable.NO_MINUTE_TAPE, str(error))
            self._selected, self._cache = selected, SessionCache(selected.directory)
        assert self._cache is not None and self._selected is not None
        return self._cache, self._selected

    def covered_sessions(self, config: MoverScannerConfig) -> tuple[date, ...]:
        """Sessions with a full relative-volume baseline behind them in this tape."""
        cache, _ = self._session_cache()
        return tuple(cache.sessions[config.premarket_rvol_baseline_sessions:])

    def load(self, session: date, config: MoverScannerConfig) -> MoverScanInput:
        cache, selected = self._session_cache()
        tape = list(cache.sessions)
        if session not in tape:
            raise MoverDataUnavailableError(
                DataUnavailable.SESSION_NOT_COVERED,
                f"{session.isoformat()} is not in the minute tape at {selected.directory}")
        position = tape.index(session)
        baseline = config.premarket_rvol_baseline_sessions
        if position < baseline:
            raise MoverDataUnavailableError(
                DataUnavailable.PREMARKET_BASELINE_TOO_SHORT,
                f"{position} covered sessions precede {session.isoformat()}, "
                f"and the relative-volume baseline needs {baseline}")
        # Only the baseline window and the session itself are read: the panel's own baseline
        # takes the last covered sessions before the column, so a narrower grid is the same
        # median over the same sessions and nothing after the session is ever in the grid.
        grid = tuple(tape[position - baseline:position + 1])
        premarket = P.build_panel(cache, config, sessions=grid)

        universes = U.load_universes(
            self.repo, exclude_non_common=config.exclude_non_common_by_cik_prefix)
        base = U.universe_for(universes, session)
        if base is None:
            raise MoverDataUnavailableError(
                DataUnavailable.NO_REFERENCE_UNIVERSE,
                f"no dated reference cache is in force on {session.isoformat()}")
        splits = U.split_sessions(self.repo)
        symbols = U.eligible_symbols(
            base, cache.symbols, splits, session,
            exclude_split_sessions=config.exclude_split_execution_sessions)

        start = session - timedelta(days=D.DAILY_LOOKBACK_DAYS + 10)
        daily_grid = [item.session_date for item in sessions_between(self.calendar, start, session)]
        try:
            daily = D.load_panel(self.repo, daily_grid, symbols=frozenset(symbols))
        except FileNotFoundError as error:
            raise MoverDataUnavailableError(DataUnavailable.NO_GROUPED_DAILY, str(error))

        return MoverScanInput(
            session=session, symbols=symbols, premarket=premarket, daily=daily,
            source_name=self.name, live=self.live, universe_as_of=base.as_of,
            universe_checksum=base.checksum, premarket_digest=premarket.cache_digest,
            baseline_sessions=baseline)


def sessions_for_dry_run(source: LocalSnapshotSource, config: MoverScannerConfig,
                         limit: int = 1) -> Sequence[date]:
    """The newest scannable snapshot sessions, newest last."""
    covered = source.covered_sessions(config)
    return covered[-limit:] if covered else ()
