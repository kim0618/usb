from __future__ import annotations

from conftest import utc

from app.backtest.strategy_h_v2.universe import (
    SecurityTypeStatus,
    UniverseRow,
    build_universe,
    classify_security_type,
)

KNOWN_AT = utc(2026, 9, 28, 0, 0)


def _row(**overrides):
    base = {
        "ticker": "AAPL", "cik": "0000320193", "type": "CS", "market": "stocks", "locale": "us",
        "primary_exchange": "XNAS", "share_class_figi": "BBG001S5N8V8",
    }
    base.update(overrides)
    return base


def test_common_stock_inclusion():
    [row] = build_universe([_row()], snapshot_date="2026-09-28", known_at=KNOWN_AT)
    assert row.security_type_status == SecurityTypeStatus.COMMON_STOCK
    assert row.exchange_supported is True
    assert row.cik == "0000320193"
    assert row.security_id == "BBG001S5N8V8"


def test_etf_excluded_by_type():
    [row] = build_universe([_row(type="ETF")], snapshot_date="2026-09-28", known_at=KNOWN_AT)
    assert row.security_type_status == SecurityTypeStatus.NOT_COMMON_STOCK


def test_preferred_excluded_by_type():
    [row] = build_universe([_row(type="PFD")], snapshot_date="2026-09-28", known_at=KNOWN_AT)
    assert row.security_type_status == SecurityTypeStatus.NOT_COMMON_STOCK


def test_unsupported_exchange_flagged_not_dropped():
    [row] = build_universe([_row(primary_exchange="BATS")], snapshot_date="2026-09-28", known_at=KNOWN_AT)
    # Universe never silently drops a row; it flags it for E1 to decide.
    assert row.exchange_supported is False
    assert row.security_type_status == SecurityTypeStatus.COMMON_STOCK


def test_unknown_security_type_stays_unknown_not_common():
    [row] = build_universe([_row(type=None)], snapshot_date="2026-09-28", known_at=KNOWN_AT)
    assert row.security_type_status == SecurityTypeStatus.UNKNOWN


def test_classify_security_type_requires_all_three_fields():
    assert classify_security_type("CS", "stocks", None) == SecurityTypeStatus.UNKNOWN
    assert classify_security_type("CS", "stocks", "us") == SecurityTypeStatus.COMMON_STOCK
    assert classify_security_type("ETF", "stocks", "us") == SecurityTypeStatus.NOT_COMMON_STOCK


def test_missing_cik_and_figi_preserved_as_none_not_dropped():
    [row] = build_universe([_row(cik=None, share_class_figi=None, composite_figi=None)],
                            snapshot_date="2026-09-28", known_at=KNOWN_AT)
    assert row.cik is None
    assert row.security_id is None


def test_known_at_must_be_aware():
    import pytest
    with pytest.raises(ValueError):
        UniverseRow(
            ticker="X", cik=None, security_id=None, exchange=None, exchange_supported=False,
            security_type_status=SecurityTypeStatus.UNKNOWN, market=None, locale=None,
            snapshot_date="2026-09-28", known_at=__import__("datetime").datetime(2026, 9, 28),
        )
