"""EQM-V0 contracts: same-accession facts only, period pairing, status codes, row assembly.

Every case is hand-written against `EQM_V0_DATA_FEASIBILITY_V1.md`, so the two rules the PIT claim
rests on - a fact is only read through the accession that carries it, and the comparative must come
from that same accession - fail a test if they are ever loosened.
"""

from datetime import date, datetime, timedelta

import pytest

from app.backtest.strategy_c_e0.events import build_cik_events
from app.backtest.strategy_c_e0.pit import ET, SessionGrid
from app.backtest.strategy_c_e0.taxonomy import Taxonomy
from app.backtest.strategy_eqm_v0 import quality, rows as eqm_rows

CIK = "0000012345"
ACCN = "0001234567-26-000001"
OTHER = "0001234567-26-000099"


def make_grid(n: int = 70) -> SessionGrid:
    sessions, opens, closes = [], [], []
    day = date(2026, 1, 5)
    while len(sessions) < n:
        if day.weekday() < 5:
            sessions.append(day)
            opens.append(datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET))
            closes.append(datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET))
        day += timedelta(days=1)
    return SessionGrid(tuple(sessions), tuple(opens), tuple(closes))


def document(*facts):
    """facts: (taxonomy, tag, unit, start, end, val, accn) -> a companyfacts-shaped document."""
    out: dict = {"cik": 12345, "facts": {}}
    for taxonomy, tag, unit, start, end, val, accn in facts:
        units = out["facts"].setdefault(taxonomy, {}).setdefault(tag, {"units": {}})["units"]
        units.setdefault(unit, []).append(
            {"start": start, "end": end, "val": val, "accn": accn, "form": "10-Q", "filed": end})
    return out


REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def quarterly(accn=ACCN, current=125.0, prior=100.0):
    return document(
        ("us-gaap", REV, "USD", "2026-01-01", "2026-03-31", current, accn),
        ("us-gaap", REV, "USD", "2025-01-01", "2025-03-31", prior, accn),
        ("us-gaap", "NetIncomeLoss", "USD", "2026-01-01", "2026-03-31", 12.0, accn),
        ("us-gaap", "NetIncomeLoss", "USD", "2025-01-01", "2025-03-31", -3.0, accn),
        ("dei", "EntityCommonStockSharesOutstanding", "shares", None, "2026-04-20", 50e6, accn),
    )


def extract(doc, accn=ACCN):
    return quality.extract(quality.facts_of_accession(doc, accn), accn)


def test_quarterly_growth_uses_the_same_accession_comparative():
    got = extract(quarterly())
    assert got.status == quality.OK
    assert got.revenue_growth_yoy == pytest.approx(0.25)
    assert got.period_label == "Q"
    assert got.revenue_tag == REV
    assert got.net_income == 12.0 and got.net_income_prior_year == -3.0
    assert got.shares_outstanding == 50e6


def test_a_comparative_filed_under_another_accession_is_never_read():
    doc = document(
        ("us-gaap", REV, "USD", "2026-01-01", "2026-03-31", 125.0, ACCN),
        ("us-gaap", REV, "USD", "2025-01-01", "2025-03-31", 100.0, OTHER),
    )
    got = extract(doc)
    assert got.status == quality.NO_COMPARATIVE
    assert got.revenue_growth_yoy is None


def test_an_eight_k_accession_with_no_facts_is_reported_not_guessed():
    got = extract(quarterly(), accn="0001234567-26-000777")
    assert got.status == quality.NO_FACTS
    assert got.revenue is None


def test_zero_and_negative_bases_do_not_produce_a_growth_rate():
    assert extract(quarterly(prior=0.0)).status == quality.ZERO_BASE
    assert extract(quarterly(prior=-5.0)).status == quality.NEGATIVE_BASE
    assert extract(quarterly(prior=0.0)).revenue_growth_yoy is None


def test_annual_periods_pair_with_annual_periods_only():
    doc = document(
        ("us-gaap", REV, "USD", "2025-01-01", "2025-12-31", 400.0, ACCN),
        ("us-gaap", REV, "USD", "2024-01-01", "2024-12-31", 320.0, ACCN),
        ("us-gaap", REV, "USD", "2025-10-01", "2025-12-31", 110.0, ACCN),
    )
    got = extract(doc)
    # The lone quarter has no comparative, so the annual pair is used rather than a mixed one.
    assert got.period_label == "Y"
    assert got.revenue == 400.0 and got.revenue_prior_year == 320.0
    assert got.revenue_growth_yoy == pytest.approx(0.25)


def test_tag_priority_prefers_the_asc606_revenue_tag():
    doc = document(
        ("us-gaap", "Revenues", "USD", "2026-01-01", "2026-03-31", 200.0, ACCN),
        ("us-gaap", "Revenues", "USD", "2025-01-01", "2025-03-31", 100.0, ACCN),
        ("us-gaap", REV, "USD", "2026-01-01", "2026-03-31", 125.0, ACCN),
        ("us-gaap", REV, "USD", "2025-01-01", "2025-03-31", 100.0, ACCN),
    )
    assert extract(doc).revenue_tag == REV


def submissions(*specs):
    out = []
    for i, (form, items, acceptance, filing_date) in enumerate(specs):
        out.append({"accessionNumber": f"0001234567-26-0000{i:02d}", "form": form, "items": items,
                    "acceptanceDateTime": acceptance, "filingDate": filing_date})
    return out


def build(specs, doc, declaration_taxonomy, *, d_index=40, dilution=False):
    grid = make_grid()
    signal = grid.sessions[d_index]
    view = build_cik_events(CIK, specs, declaration_taxonomy, grid, acceptance_zone="UTC")
    return eqm_rows.build_row(date_idx=d_index, ticker="TEST", cik=CIK, status="EM", grid=grid,
                              view=view, forms_by_accession=eqm_rows.accession_index(specs),
                              facts_document=doc, recent_dilution_20=dilution), signal


@pytest.fixture(scope="module")
def taxonomy():
    from app.backtest.strategy_c_e0.rules import load_declaration
    declaration = load_declaration()
    return Taxonomy(declaration.taxonomy, declaration.addendum)


def test_row_is_observable_when_a_periodic_report_sits_in_the_window(taxonomy):
    grid = make_grid()
    signal = grid.sessions[40]
    specs = submissions(("10-Q", "", f"{signal}T13:00:00.000Z", str(signal)))
    doc = quarterly(accn=specs[0]["accessionNumber"])
    row, _ = build(specs, doc, taxonomy)
    assert row.quality_status == eqm_rows.OBSERVABLE
    assert row.revenue_growth_yoy == pytest.approx(0.25)
    assert row.accession_form == "10-Q"


def test_an_earnings_press_release_alone_is_not_observable(taxonomy):
    grid = make_grid()
    signal = grid.sessions[40]
    specs = submissions(("8-K", "2.02", f"{signal}T13:00:00.000Z", str(signal)))
    row, _ = build(specs, quarterly(), taxonomy)
    assert row.quality_status == eqm_rows.NO_EVENT
    assert row.revenue_growth_yoy is None


def test_a_periodic_report_outside_the_window_is_not_used(taxonomy):
    grid = make_grid()
    stale = grid.sessions[30]
    specs = submissions(("10-Q", "", f"{stale}T13:00:00.000Z", str(stale)))
    row, _ = build(specs, quarterly(accn=specs[0]["accessionNumber"]), taxonomy)
    assert row.quality_status == eqm_rows.NO_EVENT


def test_novelty_counts_exclude_the_window_and_financing_flag_looks_back_sixty_days(taxonomy):
    grid = make_grid()
    signal, older, financing = grid.sessions[40], grid.sessions[20], grid.sessions[25]
    specs = submissions(
        ("10-Q", "", f"{signal}T13:00:00.000Z", str(signal)),
        ("10-Q", "", f"{older}T13:00:00.000Z", str(older)),
        ("8-K", "3.02", f"{financing}T13:00:00.000Z", str(financing)),
    )
    row, _ = build(specs, quarterly(accn=specs[0]["accessionNumber"]), taxonomy)
    assert row.same_class_count_90d == 1
    assert row.same_class_count_180d == 1
    assert row.financing_event_60d is True


# --- declaration, cohorts and the PIT audit -------------------------------------------------

def test_declaration_is_frozen(tmp_path):
    import json
    from app.backtest.strategy_eqm_v0.rules import (DECLARED_RULES_CHECKSUM, DeclarationChanged,
                                                    RULES_PATH, load_declaration)
    declaration = load_declaration()
    assert declaration.rules_checksum == DECLARED_RULES_CHECKSUM
    assert declaration.material_cut == 0.10
    assert declaration.bootstrap == (10, 10000, 20260920)   # a new seed, not C-E0's 20260918
    assert declaration.rules["strategy_c"]["status"] == "CLOSED"
    edited = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    edited["magnitude"]["material_cut"] = 0.25
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(DeclarationChanged):
        load_declaration(path=path)


def cohort_frame():
    import pandas as pd
    from app.backtest.strategy_eqm_v0 import cohorts
    status = pd.DataFrame({
        "date_idx": [1, 2, 3, 4, 5],
        "ticker": ["A", "B", "C", "D", "E"],
        "cik": ["1", "2", "3", "4", "5"],
        "signal_date": ["2026-01-05"] * 5,
        "status": ["EM", "EM", "EM", "M_ONLY", "EM_NEGATIVE_RISK"],
    })
    quality_rows = pd.DataFrame({
        "date_idx": [1, 2, 3, 4, 5],
        "ticker": ["A", "B", "C", "D", "E"],
        "quality_status": ["OBSERVABLE", "OBSERVABLE", "NO_PERIODIC_EVENT", "NO_PERIODIC_EVENT",
                           "OBSERVABLE"],
        "revenue_growth_yoy": [0.40, 0.02, None, None, 0.30],
        "period_label": ["Q", "Q", None, None, "Q"],
        "recent_dilution_20": [False, False, False, False, False],
        "financing_event_60d": [True, False, False, False, False],
        "accession": ["a", "b", None, None, "e"],
        "accession_form": ["10-Q", "10-Q", None, None, "10-Q"],
        "same_class_count_90d": [0, 0, 0, 0, 0],
        "same_class_count_180d": [0, 0, 0, 0, 0],
    })
    return cohorts.assign(status, quality_rows, material_cut=0.10)


def test_cohorts_are_nested_and_the_risk_cohort_stays_out():
    from app.backtest.strategy_eqm_v0 import cohorts
    frame = cohort_frame()
    counts = cohorts.counts(frame)
    assert counts == {"M_ONLY": 1, "EM": 3, "EQ1": 2, "EQ2": 1, "EQ3": 0,
                      "EM_REST": 2, "EQ_MATERIAL_RISK": 2}
    assert bool(frame.loc[frame["ticker"] == "A", "is_eq2"].iloc[0])      # material
    assert not bool(frame.loc[frame["ticker"] == "A", "is_eq3"].iloc[0])  # but financing nearby
    # EQ2 and EM_REST partition EM, so H2 never compares a set with itself.
    assert int((frame["is_eq2"] & frame["is_em_rest"]).sum()) == 0
    assert int(frame["is_eq2"].sum()) + int(frame["is_em_rest"].sum()) == int(frame["is_em"].sum())
    # An EM_NEGATIVE_RISK row is never an EQ cohort member, only the reported risk cohort.
    row = frame[frame["ticker"] == "E"].iloc[0]
    assert bool(row["is_material_risk"]) and not bool(row["is_eq1"]) and not bool(row["is_eq2"])


def audit_record(grid, *, date_idx, accession="a", offset_sessions=0, foreign=False):
    from app.backtest.strategy_eqm_v0.audit import UsedFact
    session = date_idx + offset_sessions
    acceptance = grid.open_of(session) + timedelta(hours=2)
    facts = (accession, "other") if foreign else (accession, accession)
    return UsedFact(date_idx=date_idx, ticker="T", cik=CIK, accession=accession,
                    acceptance=acceptance, fact_accessions=facts, event_sessions=(session,))


def test_pit_audit_accepts_a_window_filing_and_rejects_everything_else():
    from app.backtest.strategy_eqm_v0 import audit as eqm_audit
    grid = make_grid()
    clean = eqm_audit.audit([audit_record(grid, date_idx=40),
                             audit_record(grid, date_idx=40, offset_sessions=-2)], grid)
    assert clean["violations"] == 0
    assert clean["positive_control_pass"] is True

    late = eqm_audit.audit([audit_record(grid, date_idx=40, offset_sessions=1)], grid)
    assert "ACCESSION_OUTSIDE_WINDOW" in late["violation_kinds"]

    stale = eqm_audit.audit([audit_record(grid, date_idx=40, offset_sessions=-3)], grid)
    assert "ACCESSION_OUTSIDE_WINDOW" in stale["violation_kinds"]

    foreign = eqm_audit.audit([audit_record(grid, date_idx=40, foreign=True)], grid)
    assert "FACT_FROM_ANOTHER_ACCESSION" in foreign["violation_kinds"]


def test_pit_audit_shift_only_moves_rows_at_the_window_edge():
    from app.backtest.strategy_eqm_v0 import audit as eqm_audit
    grid = make_grid()
    records = [audit_record(grid, date_idx=40, offset_sessions=o) for o in (0, -1, -2)]
    result = eqm_audit.audit(records, grid)
    assert result["shift_plus_one_unexplained"] == 0
    assert result["shift_plus_one_changed"] == 1   # only the D-session filing leaves the window
