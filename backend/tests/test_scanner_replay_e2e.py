from pathlib import Path

from app.market.fake import FakeMarketDataProvider
from app.market.reference import FakeSymbolMetadataProvider
from app.market.replay import ReplayMarketDataProvider
from app.recorder.market_recorder import MarketRecorder
from app.recorder.parquet import ParquetMarketDataStorage
from app.scanner.domain import ExclusionReason
from app.scanner.scanner import QuantScanner
from tests.scanner_fixtures import SCAN_AS_OF, TRADING_DATE, history, symbol_metadata


def test_fake_recorder_parquet_replay_scanner_pit_alignment_and_determinism(
    tmp_path: Path,
) -> None:
    good = history("GOOD")
    halted = history("HALTED", count=22)
    del halted[-6]
    future = history("FUTURE")
    future[-1] = future[-1].model_copy(update={
        "observed_at": SCAN_AS_OF.replace(hour=19),
        "available_at": SCAN_AS_OF.replace(hour=19),
    })
    all_bars = [*history("SPY", daily_step=0.1), *good, *halted, *future]
    storage = ParquetMarketDataStorage(tmp_path, source="fake")
    MarketRecorder(FakeMarketDataProvider(daily_bars=all_bars), storage).record_daily(
        ["SPY", "GOOD", "HALTED", "FUTURE"],
        min(bar.trading_date for bar in all_bars),
        TRADING_DATE,
    )
    replay = ReplayMarketDataProvider(SCAN_AS_OF, storage=storage)
    metadata = FakeSymbolMetadataProvider([
        symbol_metadata("GOOD"), symbol_metadata("HALTED"), symbol_metadata("FUTURE")
    ])
    scanner = QuantScanner(replay, metadata)
    first = scanner.scan(
        ["GOOD", "HALTED", "FUTURE"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    second = scanner.scan(
        ["GOOD", "HALTED", "FUTURE"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    assert first == second
    assert [item.symbol for item in first.candidates] == ["GOOD"]
    reasons = {item.symbol: item.reason for item in first.excluded}
    assert reasons["HALTED"] is ExclusionReason.MISALIGNED_HISTORY
    assert reasons["FUTURE"] is ExclusionReason.INSUFFICIENT_HISTORY
