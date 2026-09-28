from __future__ import annotations

from helpers import mkrow, utc

from app.backtest.strategy_h_v2.evidence.filing_selection import (
    MATERIAL_8K_WINDOW_DAYS,
    MAX_RECENT_10Q,
    select_filings,
)

CUTOFF = utc(2026, 9, 28)


def test_future_filing_excluded_by_pit():
    rows = [
        mkrow("10-K", "A-1", "2025-11-01", "2025-11-01T10:00:00.000Z"),
        mkrow("10-K", "A-2", "2026-11-01", "2026-11-01T10:00:00.000Z"),  # after CUTOFF
    ]
    selection = select_filings(rows, data_cutoff=CUTOFF)
    assert selection.latest_10k["accessionNumber"] == "A-1"
    assert selection.excluded_future == 1


def test_missing_acceptance_time_is_excluded_not_guessed():
    rows = [mkrow("10-Q", "B-1", "2026-05-01", None)]
    selection = select_filings(rows, data_cutoff=CUTOFF)
    assert selection.recent_10q == ()
    assert selection.excluded_future == 1


def test_latest_10k_is_the_most_recently_accepted():
    rows = [
        mkrow("10-K", "A-1", "2024-11-01", "2024-11-01T10:00:00.000Z"),
        mkrow("10-K", "A-2", "2025-11-01", "2025-11-01T10:00:00.000Z"),
    ]
    selection = select_filings(rows, data_cutoff=CUTOFF)
    assert selection.latest_10k["accessionNumber"] == "A-2"


def test_recent_10q_capped_at_policy_maximum():
    rows = [mkrow("10-Q", f"Q-{i}", f"2025-0{i}-01", f"2025-0{i}-01T10:00:00.000Z") for i in range(1, 7)]
    selection = select_filings(rows, data_cutoff=CUTOFF)
    assert len(selection.recent_10q) == MAX_RECENT_10Q


def test_8k_outside_material_window_excluded():
    just_inside = CUTOFF.replace(year=2026, month=4, day=1)  # ~180 days before cutoff
    just_outside = CUTOFF.replace(year=2025, month=1, day=1)  # well before the window
    rows = [
        mkrow("8-K", "K-1", just_inside.date().isoformat(), just_inside.isoformat().replace("+00:00", "Z"),
              items="2.02"),
        mkrow("8-K", "K-2", just_outside.date().isoformat(), just_outside.isoformat().replace("+00:00", "Z"),
              items="2.02"),
    ]
    selection = select_filings(rows, data_cutoff=CUTOFF)
    accessions = {row["accessionNumber"] for row in selection.recent_8k}
    assert "K-1" in accessions
    assert "K-2" not in accessions


def test_no_10k_present_is_none_not_a_fabricated_row():
    selection = select_filings([mkrow("10-Q", "Q-1", "2026-05-01", "2026-05-01T10:00:00.000Z")],
                                data_cutoff=CUTOFF)
    assert selection.latest_10k is None


def test_material_window_matches_policy_constant():
    assert MATERIAL_8K_WINDOW_DAYS == 180


def test_data_cutoff_must_be_aware():
    import pytest
    with pytest.raises(ValueError):
        select_filings([], data_cutoff=CUTOFF.replace(tzinfo=None))
