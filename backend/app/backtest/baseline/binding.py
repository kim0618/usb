"""Where a baseline reads its minute and daily tape from.

``ManifestBinding`` is the legacy path, unchanged: the collector manifest, the legacy Parquet
files, and no identity line of its own, so every run id planned before bindings existed is
still the same run id. ``SnapshotBinding`` reads a frozen Common Historical Store snapshot
through A's STRICT view and names the snapshot in the run identity.
"""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app.backtest.baseline.coverage import SymbolMinuteCoverage, minute_coverage_preflight
from app.backtest.collector.daily_dataset import DAILY_DATA_KIND, DAILY_PROVIDER, DAILY_TIMEFRAME
from app.backtest.replay.dataset import DatasetIdentity, ReplayDataset
from app.backtest.research.daily import row_digest
from app.backtest.research.workspace_store import WorkspaceDailyBarStore
from app.backtest.workspace.layout import Workspace
from app.market.calendar import MarketCalendar


class DataBinding(Protocol):
    def daily_store(self, calendar: MarketCalendar) -> WorkspaceDailyBarStore: ...
    def daily_entries(self, symbols: Sequence[str]) -> tuple: ...
    def minute_bounds(self, symbols: Sequence[str]) -> tuple[date | None, date | None]: ...
    def coverage_preflight(self, symbols: Sequence[str], *, calendar: MarketCalendar,
                           required_start: date, required_end: date) -> tuple[SymbolMinuteCoverage, ...]: ...
    def load_minute(self, symbol: str) -> ReplayDataset: ...
    def minute_identities(self, symbols: Sequence[str]) -> dict[str, DatasetIdentity]: ...
    def identity_lines(self) -> tuple[str, ...]: ...


class ManifestBinding:
    """The collector manifest and legacy Parquet (the only binding before snapshots)."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def daily_store(self, calendar: MarketCalendar) -> WorkspaceDailyBarStore:
        return WorkspaceDailyBarStore(self.workspace, calendar=calendar)

    def daily_entries(self, symbols: Sequence[str]) -> tuple:
        from app.backtest.baseline.runner import daily_entries
        return daily_entries(self.workspace, symbols)

    def minute_bounds(self, symbols: Sequence[str]) -> tuple[date | None, date | None]:
        from app.backtest.baseline.runner import _minute_bounds
        return _minute_bounds(self.workspace, symbols)

    def coverage_preflight(self, symbols: Sequence[str], *, calendar: MarketCalendar,
                           required_start: date, required_end: date) -> tuple[SymbolMinuteCoverage, ...]:
        return minute_coverage_preflight(self.workspace, symbols, calendar=calendar,
                                         required_start=required_start, required_end=required_end)

    def load_minute(self, symbol: str) -> ReplayDataset:
        return ReplayDataset.load(self.workspace, symbol)

    def minute_identities(self, symbols: Sequence[str]) -> dict[str, DatasetIdentity]:
        from app.backtest.baseline.runner import minute_identities
        return minute_identities(self.workspace, symbols)

    def identity_lines(self) -> tuple[str, ...]:
        return ()


class SnapshotBinding:
    """A frozen Common Historical Store snapshot, read through ``A_STRICT_V1``."""

    def __init__(self, snapshot, calendar: MarketCalendar) -> None:  # type: ignore[no-untyped-def]
        from app.backtest.historical_store.a_view import SnapshotDailyBarStore
        self.snapshot = snapshot
        self.calendar = calendar
        self._daily = SnapshotDailyBarStore(snapshot, calendar=calendar)
        self._minute: dict[str, object] = {}

    @classmethod
    def open(cls, workspace: Workspace, snapshot_id: str, calendar: MarketCalendar) -> "SnapshotBinding":
        from app.backtest.historical_store.a_view import CommonSnapshot
        return cls(CommonSnapshot.open(workspace.root, snapshot_id), calendar)

    @property
    def view(self) -> str:
        from app.backtest.historical_store.a_view import A_STRICT_VIEW
        return A_STRICT_VIEW

    def daily_store(self, calendar: MarketCalendar) -> WorkspaceDailyBarStore:
        return self._daily

    def daily_entries(self, symbols: Sequence[str]) -> tuple:
        from app.backtest.baseline.runner import DailyEntry
        entries = []
        for symbol in symbols:
            bars = self._daily._load_symbol(symbol)
            members = self._daily.members.get(symbol, ())
            entries.append(DailyEntry(
                symbol, "COMPLETE", bars[0].trading_date if bars else None,
                bars[-1].trading_date if bars else None, row_digest(bars),
                f"{self.snapshot.snapshot_id}/{DAILY_PROVIDER}:{DAILY_DATA_KIND}:{DAILY_TIMEFRAME}",
                bool(bars), f"{len(members)} snapshot members sha256 verified"))
        return tuple(entries)

    def minute_bounds(self, symbols: Sequence[str]) -> tuple[date | None, date | None]:
        return (self.snapshot.start_date, self.snapshot.end_date) if symbols else (None, None)

    def coverage_preflight(self, symbols: Sequence[str], *, calendar: MarketCalendar,
                           required_start: date, required_end: date) -> tuple[SymbolMinuteCoverage, ...]:
        from app.backtest.historical_store.a_view import strict_coverage
        return strict_coverage(self.snapshot, symbols, calendar=calendar,
                               required_start=required_start, required_end=required_end)

    def strict_view(self, symbol: str):  # type: ignore[no-untyped-def]
        from app.backtest.historical_store.a_view import load_a_strict_minute
        if symbol not in self._minute:
            self._minute[symbol] = load_a_strict_minute(self.snapshot, symbol, calendar=self.calendar)
        return self._minute[symbol]

    def load_minute(self, symbol: str) -> ReplayDataset:
        return self.strict_view(symbol).dataset  # type: ignore[attr-defined]

    def minute_identities(self, symbols: Sequence[str]) -> dict[str, DatasetIdentity]:
        return {symbol: self.load_minute(symbol).identity for symbol in symbols}

    def identity_lines(self) -> tuple[str, ...]:
        return self.snapshot.identity_lines(self.view)
