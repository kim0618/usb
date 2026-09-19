"""C-E0 contracts: frozen declaration, form/item classification, PIT placement, dedup, cohorts.

Every case here is hand-written against `c_e0_rules_v1.json` / `c_e0_event_taxonomy_v1.json`, so a
silent edit to either file fails a test instead of quietly changing a cohort.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
import json

import numpy as np
import pandas as pd
import pytest

from app.backtest.strategy_c_e0 import audit, cohorts, stats
from app.backtest.strategy_c_e0.cik_map import SnapshotMap, map_candidate
from app.backtest.strategy_c_e0.events import build_cik_events
from app.backtest.strategy_c_e0.pit import (AcceptanceZoneUndecided, ET, SessionGrid, parse_acceptance,
                                            place)
from app.backtest.strategy_c_e0.rules import (DECLARED_RULES_CHECKSUM, DECLARED_TAXONOMY_CHECKSUM,
                                              DeclarationChanged, RULES_PATH, load_declaration)
from app.backtest.strategy_c_e0.taxonomy import Taxonomy, parse_items

CIK = "0000012345"


@pytest.fixture(scope="module")
def declaration():
    return load_declaration()


@pytest.fixture(scope="module")
def taxonomy(declaration):
    return Taxonomy(declaration.taxonomy, declaration.addendum)


def make_grid(n: int = 70) -> SessionGrid:
    """A synthetic weekday grid; session 5 is an early close at 13:00 ET."""
    sessions, opens, closes = [], [], []
    day = date(2026, 1, 5)
    while len(sessions) < n:
        if day.weekday() < 5:
            sessions.append(day)
            opens.append(datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET))
            hour = 13 if len(sessions) == 6 else 16
            closes.append(datetime(day.year, day.month, day.day, hour, 0, tzinfo=ET))
        day += timedelta(days=1)
    return SessionGrid(tuple(sessions), tuple(opens), tuple(closes))


def rows(*specs):
    """(form, items, acceptance, filingDate, accession) -> submissions-shaped dicts."""
    out = []
    for i, (form, items, acceptance, filing_date) in enumerate(specs):
        out.append({"accessionNumber": f"0001-{i:02d}-000001", "form": form, "items": items,
                    "acceptanceDateTime": acceptance, "filingDate": filing_date})
    return out


def test_declaration_checksums_are_frozen(declaration, tmp_path):
    assert declaration.rules_checksum == DECLARED_RULES_CHECKSUM
    assert declaration.taxonomy_checksum == DECLARED_TAXONOMY_CHECKSUM
    assert declaration.rules["taxonomy"]["checksum"] == declaration.taxonomy_checksum
    assert declaration.baseline_run_id == "cmsel1-855b6a0ce64e3698fc74"
    assert declaration.base_variant == "C-M0"
    assert declaration.bootstrap == (10, 10000, 20260918)
    edited = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    edited["event_windows"]["primary"]["definition"] = "effective_time in [open(D-5), close(D))"
    path = tmp_path / "c_e0_rules_v1.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    from app.backtest.strategy_c_e0 import rules as rules_module
    original = rules_module.RULES_PATH
    rules_module.RULES_PATH = path
    try:
        with pytest.raises(DeclarationChanged):
            load_declaration()
    finally:
        rules_module.RULES_PATH = original


@pytest.mark.parametrize("form,items,expected", [
    ("8-K", "1.01,9.01", ("E1",)),
    ("8-K", "2.01", ("E2",)),
    ("8-K", "2.02", ("E3",)),
    ("10-Q", None, ("E3",)),
    ("10-KT", None, ("E3",)),
    ("8-K", "3.02,1.01", ("E1", "E4a")),
    ("424B5", None, ("E4b",)),
    ("S-3ASR", None, ("E4c",)),
    ("8-K", "4.02", ("E4d",)),
    ("8-K", "7.01", ("E5",)),
    ("8-K", "2.03", ()),        # 2.03 alone is routine, not financing
    ("424B2", None, ()),        # bank structured notes
    ("S-8", None, ()),
    ("8-K/A", "1.01", ()),      # amendments never create an event
    ("4", None, ()),
])
def test_classification_follows_the_frozen_table(taxonomy, form, items, expected):
    assert taxonomy.classify(form, items).classes == expected


def test_flags_outside_the_event_classes(taxonomy):
    assert taxonomy.classify("SC 14D9/A", None).ma_pin is True
    assert taxonomy.classify("PREM14A", None).ma_pin is True
    assert taxonomy.classify("6-K", None).unclassifiable is True
    assert taxonomy.classify("20-F", None).unclassifiable is True
    assert taxonomy.classify("8-K", "").unknown_items is True
    assert taxonomy.classify("8-K", "5.02,9.01").routine is True
    assert parse_items("Item 1.01, 9.01") == ("1.01", "9.01")


def test_placement_rules_including_early_close_and_weekend():
    grid = make_grid()
    close_day = grid.sessions[5]          # early close 13:00
    next_day = grid.sessions[6]
    during = place(grid, datetime(close_day.year, close_day.month, close_day.day, 12, 59, tzinfo=ET))
    assert (grid.sessions[during.session_index], during.rule) == (close_day, "DURING_SESSION")
    at_close = place(grid, datetime(close_day.year, close_day.month, close_day.day, 13, 0, tzinfo=ET))
    assert grid.sessions[at_close.session_index] == next_day
    assert at_close.effective_time == grid.open_of(at_close.session_index)
    before = place(grid, datetime(next_day.year, next_day.month, next_day.day, 7, 0, tzinfo=ET))
    assert (before.rule, before.effective_time) == ("BEFORE_OPEN", grid.open_of(before.session_index))
    friday = grid.sessions[4]
    weekend = place(grid, datetime(friday.year, friday.month, friday.day, 18, 0, tzinfo=ET))
    assert grid.sessions[weekend.session_index] == grid.sessions[5]
    assert place(grid, datetime(2030, 1, 1, 10, 0, tzinfo=ET)) is None
    assert place(grid, datetime(2020, 1, 1, 10, 0, tzinfo=ET)) is None  # older than the grid


def test_acceptance_zone_is_never_guessed():
    text = "2026-01-08T17:30:00.000Z"
    assert parse_acceptance(text, "ET").hour == 17
    assert parse_acceptance(text, "UTC").hour == 12
    with pytest.raises(AcceptanceZoneUndecided):
        parse_acceptance(text, "LOCAL")


def test_dedup_merges_class_and_session_but_not_across_sessions(taxonomy):
    grid = make_grid()
    d = grid.sessions[10].isoformat()
    later = grid.sessions[11].isoformat()
    view = build_cik_events(CIK, rows(
        ("8-K", "2.02", f"{d}T10:00:00.000Z", d),
        ("10-Q", None, f"{d}T11:00:00.000Z", d),      # E3a + E3b same session -> one E3
        ("8-K", "1.01", f"{d}T09:45:00.000Z", d),
        ("8-K", "1.01", f"{d}T15:00:00.000Z", d),     # same class/session -> one event
        ("8-K", "1.01", f"{later}T10:00:00.000Z", later),  # next session -> separate event
    ), taxonomy, grid, acceptance_zone="ET")
    by_key = {(e.event_class, e.session_index): e for e in view.events}
    assert sorted(k[0] for k in by_key) == ["E1", "E1", "E3"]
    e1 = by_key[("E1", 10)]
    assert len(e1.accessions) == 2
    assert e1.effective_time.hour == 9 and e1.effective_time.minute == 45
    assert by_key[("E3", 10)].subtypes == ("E3a", "E3b")


def test_multi_item_8k_yields_one_event_per_class(taxonomy):
    grid = make_grid()
    d = grid.sessions[10].isoformat()
    view = build_cik_events(CIK, rows(("8-K", "1.01,2.03,3.02,9.01", f"{d}T10:00:00.000Z", d)),
                            taxonomy, grid, acceptance_zone="ET")
    assert sorted(e.event_class for e in view.events) == ["E1", "E4a"]


def _view(taxonomy, grid, *specs):
    return build_cik_events(CIK, rows(*specs), taxonomy, grid, acceptance_zone="ET")


def _coverage(grid):
    return cohorts.Coverage(True, "OK", grid.sessions[0], grid.sessions[-1])


def _mapping(grid, d_index):
    return map_candidate((SnapshotMap(grid.sessions[0], {"AAA": CIK}),), "AAA", grid.sessions[d_index])


def test_cohort_assignment_order(taxonomy):
    grid = make_grid()
    d = 40
    day = grid.sessions[d].isoformat()
    two_back = grid.sessions[d - 2].isoformat()
    three_back = grid.sessions[d - 3].isoformat()
    mapping, cover = _mapping(grid, d), _coverage(grid)

    material = _view(taxonomy, grid, ("8-K", "1.01", f"{two_back}T10:00:00.000Z", two_back))
    row = cohorts.assign(grid, d, mapping, material, cover)
    assert (row["status"], row["event_count"], row["event_types"]) == ("EM", 1, ["E1"])

    outside = _view(taxonomy, grid, ("8-K", "1.01", f"{three_back}T10:00:00.000Z", three_back))
    assert cohorts.assign(grid, d, mapping, outside, cover)["status"] == "M_ONLY"

    both = _view(taxonomy, grid, ("8-K", "1.01", f"{day}T10:00:00.000Z", day),
                 ("424B5", None, f"{day}T11:00:00.000Z", day))
    risky = cohorts.assign(grid, d, mapping, both, cover)
    assert risky["status"] == "EM_NEGATIVE_RISK" and risky["negative_risk_types"] == ["E4b"]

    pinned = _view(taxonomy, grid, ("8-K", "1.01", f"{day}T10:00:00.000Z", day),
                   ("SC 14D9", None, f"{grid.sessions[d - 30].isoformat()}T10:00:00.000Z",
                    grid.sessions[d - 30].isoformat()))
    assert cohorts.assign(grid, d, mapping, pinned, cover)["status"] == "EXCLUDED_MA_TARGET"

    unknown_form = _view(taxonomy, grid, ("6-K", None, f"{day}T10:00:00.000Z", day))
    assert cohorts.assign(grid, d, mapping, unknown_form, cover)["status"] == "UNKNOWN_FORM"

    missing = _view(taxonomy, grid, ("8-K", "1.01", "", day))
    assert cohorts.assign(grid, d, mapping, missing, cover)["status"] == "UNKNOWN_PIT"

    routine = _view(taxonomy, grid, ("4", None, f"{day}T10:00:00.000Z", day))
    only = cohorts.assign(grid, d, mapping, routine, cover)
    assert only["status"] == "M_ONLY" and only["routine_count"] == 1


def test_unknown_mapping_and_coverage_take_precedence(taxonomy):
    grid = make_grid()
    d = 40
    day = grid.sessions[d].isoformat()
    view = _view(taxonomy, grid, ("8-K", "1.01", f"{day}T10:00:00.000Z", day))
    no_cik = map_candidate((SnapshotMap(grid.sessions[0], {}),), "AAA", grid.sessions[d])
    assert cohorts.assign(grid, d, no_cik, view, _coverage(grid))["status"] == "UNKNOWN_MAPPING"
    not_covered = cohorts.Coverage(False, "NOT_COVERED", None, None)
    assert cohorts.assign(grid, d, _mapping(grid, d), view, not_covered)["status"] == "UNKNOWN_COVERAGE"
    idle = _view(taxonomy, grid, ("4", None, f"{grid.sessions[0].isoformat()}T10:00:00.000Z",
                                  grid.sessions[0].isoformat()))
    assert cohorts.assign(grid, d, _mapping(grid, d), idle, _coverage(grid))["status"] == "M_ONLY"
    dormant = replace(idle, latest_filing_date=grid.sessions[d] - timedelta(days=401))
    assert cohorts.assign(grid, d, _mapping(grid, d), dormant, _coverage(grid))["status"] \
        == "UNKNOWN_MAPPING"


def test_cik_changed_across_d_is_unknown_mapping():
    maps = (SnapshotMap(date(2026, 1, 1), {"AAA": "0000000001"}),
            SnapshotMap(date(2026, 4, 1), {"AAA": "0000000002"}))
    assert map_candidate(maps, "AAA", date(2026, 2, 1)).reason == "CIK_CHANGED_ACROSS_D"
    assert map_candidate(maps, "AAA", date(2026, 5, 1)).cik == "0000000002"


def test_post_signal_filing_is_flagged_and_never_an_event(taxonomy):
    grid = make_grid()
    d = 40
    after_close = f"{grid.sessions[d].isoformat()}T17:00:00.000Z"
    view = _view(taxonomy, grid, ("8-K", "1.01", after_close, grid.sessions[d].isoformat()))
    row = cohorts.assign(grid, d, _mapping(grid, d), view, _coverage(grid))
    assert row["status"] == "M_ONLY"
    assert row["post_signal_event_flag"] is True
    assert row["event_count"] == 0


def test_pit_audit_detects_the_injected_filing(taxonomy):
    grid = make_grid()
    d = 40
    day = grid.sessions[d].isoformat()
    view = _view(taxonomy, grid, ("8-K", "1.01", f"{day}T10:00:00.000Z", day))
    mapping, cover = _mapping(grid, d), _coverage(grid)
    status = cohorts.assign(grid, d, mapping, view, cover)["status"]
    result = audit.pit_audit(grid, [audit.RowInput(d, "AAA", mapping, view, cover, status)], sample=1)
    assert result["violations"] == 0
    assert result["positive_control_detected"] is True
    assert result["checks"]["events_before_close"]["violations"] == 0


def test_excess_matches_the_v1_estimator():
    frame = pd.DataFrame({"date_idx": [0, 0, 1], "ticker": ["A", "B", "A"],
                          "close_5": [0.10, -0.02, 0.04], "base_close_5": [0.01, 0.00, 0.02]})
    result = stats.excess(frame, [0, 1], "close_5", None)
    assert result["point"] == pytest.approx(((0.10 - 0.01) + (-0.02 - 0.00) + (0.04 - 0.02)) / 3)
    assert result["n"] == 3
    left = frame.iloc[:2]
    right = frame.iloc[2:]
    difference = stats.difference(left, right, [0, 1], "close_5", None)
    assert difference["point"] == pytest.approx(
        ((0.10 - 0.01) + (-0.02 - 0.00)) / 2 - (0.04 - 0.02))


def test_bootstrap_draws_are_reproducible_and_declared():
    assert (stats.BLOCK_LENGTH, stats.REPLICATES, stats.SEED) == (10, 10_000, 20260918)
    first = stats.block_indices(240, replicates=50)
    second = stats.block_indices(240, replicates=50)
    assert np.array_equal(first, second)
    assert first.shape == (50, 240)


def test_pre_outcome_checks_fail_closed(declaration):
    frame = pd.DataFrame({"date_idx": [0] * 10, "ticker": [f"T{i}" for i in range(10)],
                          "status": ["EM"] * 5 + ["M_ONLY"] * 5, "matched_5": [True] * 10})
    result = audit.pre_outcome_checks(frame, timezone_audit_passed=False, pit_violations=0,
                                     unknown_share=0.5, blocks=[[0]], expected_dates=240,
                                     present_dates=239, declaration=declaration.rules)
    assert result["all_pass"] is False
    failed = {k for k, v in result["checks"].items() if not v["pass"]}
    assert {"P1_timezone_audit", "P3_unknown_share", "P4_em_sample", "P8_window"} <= failed


def test_timezone_audit_needs_thirty_agreeing_samples():
    samples = [{"accession": f"a{i}", "acceptance": "2026-01-08T17:30:00.000Z",
                "header": "20260108173000"} for i in range(35)]
    decided = audit.timezone_audit(samples, parse=parse_acceptance)
    assert decided["zone"] == "ET" and decided["pass"] is True
    too_few = audit.timezone_audit(samples[:5], parse=parse_acceptance)
    assert too_few["zone"] == "ET" and too_few["pass"] is False
    mixed = samples[:20] + [{"accession": "x", "acceptance": "2026-01-08T17:30:00.000Z",
                             "header": "20260108123000"} for _ in range(15)]
    assert audit.timezone_audit(mixed, parse=parse_acceptance)["zone"] is None
