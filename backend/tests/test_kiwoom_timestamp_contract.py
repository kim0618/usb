import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.timestamps import minute_timestamp
from app.market.kiwoom import KiwoomMarketDataProvider  # noqa: F401 - initialize adapter package
from app.integrations.kiwoom.mapping import map_minute_bar


FIXTURE = json.loads((Path(__file__).parent / "fixtures" /
                      "kiwoom_us_minute_timestamps.json").read_text())
RECEIVED = datetime(2026, 9, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("row", FIXTURE)
def test_production_shaped_timestamp_normalization(row) -> None:
    assert minute_timestamp(row).isoformat() == row["expected"]


@pytest.mark.parametrize("row", FIXTURE)
def test_explicit_supported_session_boundaries(row) -> None:
    if row["session"] is None:
        with pytest.raises(MarketDataError, match="outside supported") as error:
            map_minute_bar("AAPL", row, RECEIVED)
        assert error.value.code == "OUTSIDE_SESSION"
    else:
        assert map_minute_bar("AAPL", row, RECEIVED).session.value == row["session"]


@pytest.mark.parametrize(("row", "expected"), [
    ({"bus_dt": "20260115", "cntr_tm": "20260115040000"},
     "2026-01-15T04:00:00-05:00"),
    ({"bus_dt": "20260308", "cntr_tm": "20260308240000"},
     "2026-03-09T00:00:00-04:00"),
    ({"bus_dt": "20261031", "cntr_tm": "20261031260000"},
     "2026-11-01T02:00:00-05:00"),
])
def test_est_and_dst_rollover(row, expected) -> None:
    assert minute_timestamp(row).isoformat() == expected


@pytest.mark.parametrize("cntr_tm", ["20269", "202609111200000", "20260911246000"])
def test_invalid_timestamp_shapes_are_rejected(cntr_tm) -> None:
    with pytest.raises(ValueError):
        minute_timestamp({"bus_dt": "20260911", "cntr_tm": cntr_tm})


def test_xnys_early_close_starts_postmarket_at_official_close() -> None:
    row = {"bus_dt": "20261127", "cntr_tm": "20261127130000", "open_pric": "100",
           "high_pric": "101", "low_pric": "99", "cur_prc": "100", "trde_qty": "10"}
    assert map_minute_bar("AAPL", row, datetime(2026, 11, 28, tzinfo=timezone.utc)).session.value == "POSTMARKET"


@pytest.mark.parametrize(("clock", "expected"), [
    ("235900", "2026-09-10T23:59:00-04:00"),
    ("240000", "2026-09-11T00:00:00-04:00"),
    ("275900", "2026-09-11T03:59:00-04:00"),
])
def test_business_relative_hours_up_to_27_are_valid(clock, expected) -> None:
    assert minute_timestamp({"bus_dt": "20260910", "cntr_tm": f"20260910{clock}"}).isoformat() == expected


@pytest.mark.parametrize("clock", ["280000", "283000", "470000"])
def test_hours_from_28_are_rejected_not_wrapped(clock) -> None:
    with pytest.raises(ValueError, match="out of range"):
        minute_timestamp({"bus_dt": "20260910", "cntr_tm": f"20260910{clock}"})
