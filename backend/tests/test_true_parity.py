"""Paper <-> backtest environment parity: the premarket volume basis and the recorded universe.

1. The premarket denominator. Paper divides premarket volume by its provider's daily bar
   (Kiwoom ``acc_trde_qty``, the whole session). The replay provider derives its daily bar
   from REGULAR minute sums (V1, every stored run). ``PREMARKET_VOLUME_BASIS_V2`` hands the
   replay a daily aggregate source instead, and then the same minute tape and the same daily
   bars give the paper path and the backtest path the same canonical premarket context.
2. The recorded paper universe. A scanner run can now record the provider universe it
   ranked, in provider order, with each symbol's scanner outcome, in the run's transaction.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.backtest.baseline.config_snapshot import strategy_config_snapshot
from app.backtest.baseline.errors import BaselineError
from app.backtest.baseline.runner import baseline_identity, with_premarket_volume_basis
from app.backtest.portfolio.candidates import DeclaredCandidateSource
from app.backtest.portfolio.config import PortfolioConfig
from app.backtest.portfolio.errors import PortfolioConfigInvalid
from app.backtest.portfolio.replay import MultiSymbolPortfolioReplay
from app.backtest.replay.daily_volume import (
    PREMARKET_VOLUME_BASIS_V1, PREMARKET_VOLUME_BASIS_V2, DailyAggregateVolumeProvider,
)
from app.backtest.replay.dataset import ReplayDataset
from app.backtest.replay.provider import HistoricalReplayMarketDataProvider, PointInTimeAudit
from app.core.database import Base, create_db_engine
from app.execution.config import ExecutionConfig
from app.market.domain import DailyBar
from app.market.provider import MarketDataProvider
from app.repositories.scanner import ScannerSnapshotRepository, universe_checksum
from app.risk.config import RiskConfig
from app.scanner.scanner import QuantScanner
from app.services.entry_management_runtime import build_premarket_context
from app.services.scanner import ScannerService
from app.strategy.config import StrategyConfig
from app.strategy.engine import StrategyV0Engine
from tests.scanner_fixtures import SCAN_AS_OF, TRADING_DATE, scanner_providers
from tests.test_current_strategy_baseline import Synthetic, tape
from tests.test_historical_replay_core import (
    CALENDAR, ENTRY, PREVIOUS, SYMBOL, entry_session, flat_session, write_dataset,
)
from tests.test_pyramid_disable_contract import STORED_DEFAULT_FINGERPRINTS

GATE_AT = datetime.combine(ENTRY, time(9, 20), CALENDAR.timezone)
MINUTE_SUM = 390 * 1000  # flat_session: 390 regular minutes of 1,000 shares
AGGREGATE = 471_000       # the whole-session daily aggregate: more than the regular minutes


def aggregate_bar(volume: int = AGGREGATE) -> DailyBar:
    window = CALENDAR.session(PREVIOUS)
    assert window is not None
    published = datetime.combine(PREVIOUS, time(20), CALENDAR.timezone)
    return DailyBar(symbol=SYMBOL, trading_date=PREVIOUS, open=100.0, high=100.0, low=100.0,
                    close=100.0, volume=volume, observed_at=published, available_at=published)


class PaperLikeProvider(MarketDataProvider):
    """Paper's shape: one provider whose daily bar is the whole-session aggregate."""

    def __init__(self, minute: MarketDataProvider, daily: list[DailyBar]) -> None:
        self.minute = minute
        self.daily = daily

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        return self.minute.get_minute_bars(symbols, start, end, session)

    def get_daily_bars(self, symbols, start=None, end=None):  # type: ignore[no-untyped-def]
        return [bar for bar in self.daily if bar.symbol in symbols
                and (start is None or bar.trading_date >= start)
                and (end is None or bar.trading_date <= end)]


@pytest.fixture()
def replay(tmp_path: Path) -> HistoricalReplayMarketDataProvider:
    root = write_dataset(tmp_path, {PREVIOUS: flat_session(PREVIOUS), ENTRY: entry_session()})
    dataset = ReplayDataset.load(root, SYMBOL)
    return HistoricalReplayMarketDataProvider(dataset, as_of=GATE_AT, calendar=CALENDAR,
                                              audit=PointInTimeAudit(raising=False))


def contexts(replay: HistoricalReplayMarketDataProvider):  # type: ignore[no-untyped-def]
    start = datetime.combine(ENTRY, time(4), CALENDAR.timezone)
    minute = [bar for bar in replay.get_minute_bars([SYMBOL], start, GATE_AT, None)
              if bar.available_at <= GATE_AT]
    paper = build_premarket_context(CALENDAR, SYMBOL, ENTRY, minute,
                                    PaperLikeProvider(replay, [aggregate_bar()]), GATE_AT)
    v1 = build_premarket_context(CALENDAR, SYMBOL, ENTRY, minute, replay, GATE_AT)
    audit = PointInTimeAudit(raising=True)
    daily = DailyAggregateVolumeProvider({SYMBOL: [aggregate_bar()]}, clock=lambda: GATE_AT,
                                         calendar=CALENDAR, audit=audit)
    v2 = build_premarket_context(CALENDAR, SYMBOL, ENTRY, minute, replay, GATE_AT,
                                 daily_volume_provider=daily)
    return paper, v1, v2, audit


# --- 1-2. canonical premarket context --------------------------------------------------------

def test_paper_and_the_v2_backtest_build_the_same_canonical_context(replay) -> None:  # type: ignore[no-untyped-def]
    paper, _, v2, audit = contexts(replay)
    assert v2.context == paper.context
    assert v2.context.historical_average_daily_volume == Decimal(AGGREGATE)
    assert audit.violations == 0


def test_v1_divides_by_regular_minute_sums_and_v2_by_the_daily_aggregate(replay) -> None:  # type: ignore[no-untyped-def]
    paper, v1, v2, _ = contexts(replay)
    assert v1.context.historical_average_daily_volume == Decimal(MINUTE_SUM)
    assert v2.context.historical_average_daily_volume == Decimal(AGGREGATE)
    # Only the denominator moves: gap, reference price, previous close and numerator are
    # read from the same tape in every basis.
    for field in ("previous_regular_close", "reference_price", "premarket_volume"):
        assert getattr(v1.context, field) == getattr(v2.context, field) == getattr(paper.context, field)
    assert v1.context.volume_ratio > v2.context.volume_ratio  # the V1 denominator is smaller


def test_the_v2_source_never_serves_a_daily_bar_before_it_is_published() -> None:
    audit = PointInTimeAudit(raising=True)
    before = datetime.combine(PREVIOUS, time(19, 59), CALENDAR.timezone)
    moment = {"now": before}
    daily = DailyAggregateVolumeProvider({SYMBOL: [aggregate_bar()]}, clock=lambda: moment["now"],
                                         calendar=CALENDAR, audit=audit)
    assert daily.get_daily_bars([SYMBOL]) == []
    moment["now"] = before + timedelta(minutes=1)
    assert [bar.trading_date for bar in daily.get_daily_bars([SYMBOL])] == [PREVIOUS]
    assert audit.violations == 0
    with pytest.raises(NotImplementedError):
        daily.get_minute_bars([SYMBOL])


# --- identity and defaults ------------------------------------------------------------------

def config(basis: str = PREMARKET_VOLUME_BASIS_V1) -> PortfolioConfig:
    return PortfolioConfig(starting_cash=Decimal("10000"), universe=("AAA",),
                           trading_dates=(ENTRY,), premarket_volume_basis=basis)


def test_the_default_basis_writes_nothing_new() -> None:
    default = config()
    assert "premarket_volume_basis" not in default.as_dict()
    assert not any(line.startswith("premarket_volume_basis=") for line in default.lines())
    v2 = config(PREMARKET_VOLUME_BASIS_V2)
    assert v2.as_dict()["premarket_volume_basis"] == PREMARKET_VOLUME_BASIS_V2
    assert set(v2.lines()) - set(default.lines()) == {f"premarket_volume_basis={PREMARKET_VOLUME_BASIS_V2}"}
    with pytest.raises(PortfolioConfigInvalid):
        config("V3_SOMETHING")


def test_the_replay_refuses_a_basis_its_bars_do_not_match(tmp_path: Path) -> None:
    root = write_dataset(tmp_path, {PREVIOUS: flat_session(PREVIOUS), ENTRY: entry_session()})
    datasets = {SYMBOL: ReplayDataset.load(root, SYMBOL)}
    source = DeclaredCandidateSource({ENTRY: (SYMBOL,)}, calendar=CALENDAR)
    from app.backtest.authority.contract import research_modes
    modes = research_modes((SYMBOL,))
    cfg = PortfolioConfig(starting_cash=Decimal("10000"), universe=(SYMBOL,), trading_dates=(ENTRY,),
                          premarket_volume_basis=PREMARKET_VOLUME_BASIS_V2)
    with pytest.raises(PortfolioConfigInvalid):
        MultiSymbolPortfolioReplay(datasets, config=cfg, modes=modes, candidates=source, calendar=CALENDAR)
    with pytest.raises(PortfolioConfigInvalid):
        MultiSymbolPortfolioReplay(datasets, config=config().__class__(
            starting_cash=Decimal("10000"), universe=(SYMBOL,), trading_dates=(ENTRY,)),
            modes=modes, candidates=source, calendar=CALENDAR,
            premarket_daily_bars={SYMBOL: [aggregate_bar()]})


def test_a_v2_plan_is_an_experiment_that_differs_only_by_its_basis(tmp_path: Path) -> None:
    synthetic = Synthetic(tmp_path, {"AAA": tape()}, ("AAA",))
    datasets = {symbol: ReplayDataset.load(synthetic.workspace, symbol).identity
                for symbol in synthetic.symbols}
    kwargs = dict(engine=StrategyV0Engine(), risk=RiskConfig(), execution=ExecutionConfig())
    v1 = baseline_identity(synthetic.plan, datasets, **kwargs)  # type: ignore[arg-type]
    v2 = baseline_identity(with_premarket_volume_basis(synthetic.plan, PREMARKET_VOLUME_BASIS_V2),
                           datasets, **kwargs)  # type: ignore[arg-type]
    assert set(v2.block.split("\n")) - set(v1.block.split("\n")) == {
        f"config.premarket_volume_basis={PREMARKET_VOLUME_BASIS_V2}"}
    assert set(v1.block.split("\n")) - set(v2.block.split("\n")) == set()
    assert v1.run_id.startswith("csb1-") and v2.run_id.startswith("exp1-")
    assert v1.config_snapshot == v2.config_snapshot
    with pytest.raises(BaselineError):
        with_premarket_volume_basis(synthetic.plan, "V3_SOMETHING")


def test_the_stored_research_v1_fingerprints_are_untouched() -> None:
    snapshot = strategy_config_snapshot(StrategyConfig(), RiskConfig(), ExecutionConfig())
    for name, stored in STORED_DEFAULT_FINGERPRINTS.items():
        assert snapshot[name] == stored


# --- recorded paper universe ------------------------------------------------------------------

def universe_rows(symbols: list[str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(symbol=symbol, exchange_code="NASD", company_name=f"{symbol} Inc",
                            market_cap=float(10_000_000_000 - index)) for index, symbol in enumerate(symbols)]


def test_a_scanner_run_records_the_universe_it_ranked(tmp_path: Path) -> None:
    symbols = [f"S{index:03d}" for index in range(10)]
    market, reference = scanner_providers(symbols)
    engine = create_db_engine(f"sqlite:///{tmp_path / 'scanner.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = ScannerSnapshotRepository(session)
        service = ScannerService(QuantScanner(market, reference), repository, provider_name="fake")
        result = service.scanner.scan(symbols + ["ZZZ"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
        run_id, _ = service.persist_result(result, universe=universe_rows(symbols + ["ZZZ"]),
                                           universe_source="FAKE", universe_acquired_at=SCAN_AS_OF)
        rows = repository.get_universe_inputs(run_id)
        assert [row.position for row in rows] == list(range(1, 12))
        assert [row.symbol for row in rows] == symbols + ["ZZZ"]
        ranked = {candidate.symbol: candidate.rank for candidate in result.candidates}
        for row in rows[:-1]:
            assert (row.outcome, row.scanner_rank) == ("SCANNER_CANDIDATE", ranked[row.symbol])
        assert rows[-1].outcome == "EXCLUDED" and rows[-1].exclusion_reason
        assert len({row.universe_checksum for row in rows}) == 1
        assert rows[0].market_cap == repr(10_000_000_000.0)
    engine.dispose()


def test_the_universe_checksum_is_deterministic_and_order_sensitive() -> None:
    from app.repositories.scanner import ScannerUniverseInputData as Row
    a = [Row(1, "AAA", "NASD", None, "1", "SCANNER_CANDIDATE", 1), Row(2, "BBB", "NYSE", None, "2", "EXCLUDED", None, "PRICE_TOO_LOW")]
    b = [Row(1, "BBB", "NYSE", None, "2", "EXCLUDED", None, "PRICE_TOO_LOW"), Row(2, "AAA", "NASD", None, "1", "SCANNER_CANDIDATE", 1)]
    assert universe_checksum(a) == universe_checksum(list(reversed(a)))
    assert universe_checksum(a) != universe_checksum(b)


def test_a_run_without_a_universe_records_none_and_a_universe_needs_its_source(tmp_path: Path) -> None:
    market, reference = scanner_providers(["AAA"])
    engine = create_db_engine(f"sqlite:///{tmp_path / 'plain.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = ScannerSnapshotRepository(session)
        service = ScannerService(QuantScanner(market, reference), repository, provider_name="fake")
        result = service.scanner.scan(["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
        run_id, _ = service.persist_result(result)
        assert repository.get_universe_inputs(run_id) == []
        with pytest.raises(ValueError):
            service.persist_result(result, universe=universe_rows(["AAA"]))
    engine.dispose()


# --- migration 0018 ---------------------------------------------------------------------------

def test_migration_0018_adds_only_the_universe_table_and_downgrades_cleanly(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import create_engine, inspect, text

    from app.core.config import get_settings
    from tests.test_paper_entry_evaluation_history import _alembic, _fingerprint
    path = tmp_path / "m0018.sqlite3"
    try:
        _alembic(path, monkeypatch, "20260916_0017")
        before = _fingerprint(path)
        _alembic(path, monkeypatch, "20260921_0018")
        engine = create_engine(f"sqlite:///{path}")
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM scanner_universe_inputs")).scalar() == 0
        engine.dispose()
        assert "scanner_universe_inputs" in tables
        _alembic(path, monkeypatch, "20260916_0017", down=True)
        assert _fingerprint(path) == before
    finally:
        get_settings.cache_clear()
