from __future__ import annotations

from conftest import utc

from app.backtest.strategy_h_v2.acquisition import (
    AcquisitionStatus,
    SOURCE_COMPANYFACTS,
    SOURCE_SUBMISSIONS,
    build_acquisition_queue,
)
from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow

QUEUED_AT = utc(2026, 9, 28, 12, 0)
CUTOFF = utc(2026, 9, 28, 12, 0)


def _row(ticker: str, cik: str | None, security_id: str | None = "FIGI-X") -> UniverseRow:
    return UniverseRow(
        ticker=ticker, cik=cik, security_id=security_id, exchange="XNAS", exchange_supported=True,
        security_type_status=SecurityTypeStatus.COMMON_STOCK, market="stocks", locale="us",
        snapshot_date="2026-09-28", known_at=QUEUED_AT,
    )


def test_queue_is_deterministic_across_repeated_builds():
    rows = [_row("MSFT", "0000789019"), _row("AAPL", "0000320193")]
    first = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset(), cached_companyfacts_ciks=frozenset(),
        queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    second = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset(), cached_companyfacts_ciks=frozenset(),
        queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    assert first == second
    # sorted by CIK ascending, not input order
    assert [item.cik for item in first] == ["0000320193", "0000789019"]


def test_duplicate_cik_collapses_to_one_item():
    rows = [_row("GOOG", "0001652044"), _row("GOOGL", "0001652044")]
    queue = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset(), cached_companyfacts_ciks=frozenset(),
        queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    assert len(queue) == 1
    assert queue[0].tickers == ("GOOG", "GOOGL")
    assert queue[0].ticker == "GOOG"  # deterministic: alphabetically first


def test_rows_without_a_cik_are_excluded_from_the_sec_targeted_queue():
    rows = [_row("NOCIK", None), _row("AAPL", "0000320193")]
    queue = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset(), cached_companyfacts_ciks=frozenset(),
        queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    assert [item.cik for item in queue] == ["0000320193"]


def test_fully_cached_cik_is_marked_cached_not_pending():
    rows = [_row("AAPL", "0000320193")]
    queue = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset({"0000320193"}),
        cached_companyfacts_ciks=frozenset({"0000320193"}), queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    [item] = queue
    assert item.status == AcquisitionStatus.CACHED
    assert item.missing_sources == ()
    assert item.required_actions == ()


def test_partially_cached_cik_lists_only_the_missing_source():
    rows = [_row("AAPL", "0000320193")]
    queue = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset({"0000320193"}),
        cached_companyfacts_ciks=frozenset(), queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    [item] = queue
    assert item.status == AcquisitionStatus.PENDING
    assert item.missing_sources == (SOURCE_COMPANYFACTS,)


def test_uncached_cik_needs_both_sources():
    rows = [_row("AAPL", "0000320193")]
    queue = build_acquisition_queue(
        rows, cached_submission_ciks=frozenset(), cached_companyfacts_ciks=frozenset(),
        queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    [item] = queue
    assert item.status == AcquisitionStatus.PENDING
    assert item.missing_sources == (SOURCE_SUBMISSIONS, SOURCE_COMPANYFACTS)


def test_build_queue_does_not_mutate_input_rows():
    rows = [_row("AAPL", "0000320193")]
    snapshot_before = list(rows)
    build_acquisition_queue(
        rows, cached_submission_ciks=frozenset(), cached_companyfacts_ciks=frozenset(),
        queued_at=QUEUED_AT, data_cutoff=CUTOFF,
    )
    assert rows == snapshot_before
