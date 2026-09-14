"""Production-captured raw Kiwoom rows (2026-09-10/11) pin the timestamp construction fix.

Only data construction is asserted. Nothing here claims what the premarket gate
should have decided on those historical sessions.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession
from app.market.kiwoom import KiwoomMarketDataProvider
from app.services.entry_management_runtime import EntryLifecycleService

ET = ZoneInfo("America/New_York")
RAW = json.loads((Path(__file__).parent / "fixtures" / "kiwoom_us_raw_production.json").read_text())
START = datetime(2026, 9, 10, 4, tzinfo=ET)
AS_OF = datetime(2026, 9, 10, 9, 31, tzinfo=ET)


class RecordedClient:
    def minute_chart(self, symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        rows = list(RAW["amd_20260910_premarket"])
        if start is not None and start.astimezone(ET).date() == date(2026, 9, 9):
            rows.append({
                "bus_dt": "20260909", "cntr_tm": "20260909155900",
                "open_pric": "521.095", "high_pric": "521.095",
                "low_pric": "521.095", "cur_prc": "521.095", "trde_qty": "1000",
            })
        return SimpleNamespace(rows=rows)

    def daily_chart(self, symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        return [row for row in RAW["amd_daily_base_20260909"] if start is None or row["dt"] <= start]


def legacy_timestamp(row: dict[str, str]) -> datetime:
    """The pre-fix parser, kept verbatim to prove the historical failure mode."""
    raw_time = str(row["cntr_tm"]).strip().replace(":", "")[:6].zfill(6)
    return datetime.strptime(f"{row['bus_dt']}{raw_time}", "%Y%m%d%H%M%S").replace(tzinfo=ET)


def test_legacy_parser_produced_no_bars_in_the_entry_window() -> None:
    rows = RAW["amd_20260910_premarket"]
    assert all(len(row["cntr_tm"]) == 14 for row in rows)
    assert sum(START <= legacy_timestamp(row) <= AS_OF for row in rows) == 0


def test_fixed_parser_builds_premarket_bars_and_a_valid_context_without_lookahead() -> None:
    provider = KiwoomMarketDataProvider(RecordedClient(), clock=lambda: AS_OF + timedelta(seconds=1))
    bars = provider.get_minute_bars(["AMD"], START, AS_OF)
    visible = [bar for bar in bars if bar.available_at <= AS_OF]
    premarket = [bar for bar in visible if bar.session is MarketSession.PREMARKET]

    assert len(premarket) > 300
    assert premarket[0].timestamp == START
    assert max(bar.observed_at for bar in visible) <= AS_OF
    assert all(bar.timestamp < datetime(2026, 9, 10, 9, 31, tzinfo=ET) for bar in visible)
    assert all(bar.timestamp.date() == date(2026, 9, 10) for bar in bars)  # 24+ rows of 09/09 dropped

    built = EntryLifecycleService._premarket_context(
        SimpleNamespace(calendar=MarketCalendar()), "AMD", AS_OF.date(), visible, provider, AS_OF)
    assert built.diagnostic.invalid_field is None
    assert str(built.diagnostic.previous_close) == "521.095"  # exact 09/09 final regular minute
    assert built.diagnostic.premarket_bars_count == len(premarket)
    assert built.context.gap_pct is not None and built.context.volume_ratio is not None


def test_production_boundary_rows_map_to_explicit_sessions() -> None:
    received = datetime(2026, 9, 13, tzinfo=ET)
    provider = KiwoomMarketDataProvider(SimpleNamespace(
        minute_chart=lambda *a, **k: SimpleNamespace(rows=RAW["aapl_20260911_boundaries"])), clock=lambda: received)
    sessions = {bar.timestamp.strftime("%H:%M"): bar.session for bar in provider.get_minute_bars(["AAPL"])}
    assert sessions == {"04:00": MarketSession.PREMARKET, "09:29": MarketSession.PREMARKET,
                        "09:30": MarketSession.REGULAR, "11:29": MarketSession.REGULAR,
                        "15:59": MarketSession.REGULAR, "16:00": MarketSession.POSTMARKET,
                        "18:59": MarketSession.POSTMARKET}
    overnight = [row for row in RAW["aapl_20260911_boundaries"] if int(row["cntr_tm"][8:10]) >= 24]
    assert overnight and all(row["bus_dt"] == "20260910" for row in overnight)
