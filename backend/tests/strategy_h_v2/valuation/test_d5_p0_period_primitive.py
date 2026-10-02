"""H-V2-D5-P0: duration-family classification, resolver narrowing and provenance preservation.

The data-backed tests read `data/runtime/`, which is gitignored, and skip when it is absent. The
pure tests do not skip: the contract they assert is code, not data.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_h0.facts import (
    ANNUAL_SPAN_DAYS,
    NINE_MONTH_SPAN_DAYS,
    QUARTER_SPAN_DAYS,
    REQUEST_ONLY_FAMILIES,
    SEMI_SPAN_DAYS,
    CanonicalFact,
    DurationFamily,
    FactStatus,
    classify_duration,
    extract_companyfacts,
    resolve_fact,
    resolve_period_aligned,
)

from conftest import mkfact, utc


# ------------------------------------------------------------------------------------------
# E. Duration family contract
# ------------------------------------------------------------------------------------------


class TestClassification:
    def test_an_instant_has_no_duration(self):
        assert classify_duration(None, date(2026, 6, 30), "10-Q", "Q2") is DurationFamily.INSTANT

    def test_a_missing_end_is_unknown(self):
        assert classify_duration(date(2026, 1, 1), None, "10-Q", "Q2") is DurationFamily.UNKNOWN

    def test_a_non_positive_span_is_unknown(self):
        assert classify_duration(date(2026, 6, 30), date(2026, 6, 30), "10-Q", "Q2") \
            is DurationFamily.UNKNOWN
        assert classify_duration(date(2026, 7, 1), date(2026, 6, 30), "10-Q", "Q2") \
            is DurationFamily.UNKNOWN

    def test_the_q2_collision_separates(self):
        """The exact AEYE/COLL/IDCC shape: one accession, one tag, one end, one fp, two spans."""
        assert classify_duration(date(2026, 4, 1), date(2026, 6, 30), "10-Q", "Q2") \
            is DurationFamily.QUARTER
        assert classify_duration(date(2026, 1, 1), date(2026, 6, 30), "10-Q", "Q2") \
            is DurationFamily.YTD_Q2

    def test_the_q3_collision_separates(self):
        assert classify_duration(date(2026, 7, 1), date(2026, 9, 30), "10-Q", "Q3") \
            is DurationFamily.QUARTER
        assert classify_duration(date(2026, 1, 1), date(2026, 9, 30), "10-Q", "Q3") \
            is DurationFamily.YTD_Q3

    def test_a_fiscal_year_and_its_own_fourth_quarter_separate(self):
        """A 10-K carries both under one `end`; 1,732 such rows in the ten-issuer sample."""
        assert classify_duration(date(2026, 1, 1), date(2026, 12, 31), "10-K", "FY") \
            is DurationFamily.FY
        assert classify_duration(date(2026, 10, 1), date(2026, 12, 31), "10-K", "FY") \
            is DurationFamily.QUARTER

    def test_a_53_week_fiscal_quarter_is_still_a_quarter(self):
        """DORM's fiscal calendar: a 177-day first half and 90-day quarters off calendar ends."""
        assert classify_duration(date(2026, 3, 29), date(2026, 6, 27), "10-Q", "Q2") \
            is DurationFamily.QUARTER
        assert classify_duration(date(2026, 1, 1), date(2026, 6, 27), "10-Q", "Q2") \
            is DurationFamily.YTD_Q2

    def test_metadata_that_contradicts_itself_is_unknown_not_a_guess(self):
        assert classify_duration(date(2026, 4, 1), date(2026, 6, 30), "10-K", "Q2") \
            is DurationFamily.UNKNOWN
        assert classify_duration(date(2026, 1, 1), date(2026, 12, 31), "10-Q", "FY") \
            is DurationFamily.UNKNOWN

    def test_an_unrecognised_span_is_other_duration_not_the_nearest_family(self):
        for start, end, fp in ((date(2026, 1, 1), date(2026, 2, 15), "Q1"),
                               (date(2026, 1, 1), date(2026, 5, 1), "Q1"),
                               (date(2024, 1, 1), date(2026, 1, 1), "FY")):
            assert classify_duration(start, end, "10-Q" if fp != "FY" else "10-K", fp) \
                is DurationFamily.OTHER_DURATION, (start, end)

    def test_a_ytd_family_is_never_asserted_without_fiscal_period_metadata(self):
        """A six-month span is six months; that it accumulates from the fiscal year start is a
        separate claim, and only `fp` supports it."""
        assert classify_duration(date(2026, 1, 1), date(2026, 6, 30), "10-Q", None) \
            is DurationFamily.OTHER_DURATION
        assert classify_duration(date(2026, 4, 1), date(2026, 6, 30), "10-Q", None) \
            is DurationFamily.QUARTER
        assert classify_duration(date(2026, 1, 1), date(2026, 12, 31), "10-K", None) \
            is DurationFamily.FY

    def test_the_windows_are_disjoint_so_no_span_has_two_families(self):
        windows = [QUARTER_SPAN_DAYS, SEMI_SPAN_DAYS, NINE_MONTH_SPAN_DAYS, ANNUAL_SPAN_DAYS]
        for i, (lo_a, hi_a) in enumerate(windows):
            assert lo_a <= hi_a
            for lo_b, hi_b in windows[i + 1:]:
                assert hi_a < lo_b or hi_b < lo_a, ((lo_a, hi_a), (lo_b, hi_b))

    def test_ytd_q1_is_never_emitted_because_it_is_the_quarter_itself(self):
        assert DurationFamily.YTD_Q1 in REQUEST_ONLY_FAMILIES
        assert classify_duration(date(2026, 1, 1), date(2026, 3, 31), "10-Q", "Q1") \
            is DurationFamily.QUARTER

    def test_the_fact_carries_its_own_family_and_span(self):
        fact = mkfact("revenue", 1.0, date(2026, 6, 30), start=date(2026, 1, 1),
                      fiscal_period="Q2")
        assert fact.duration_family is DurationFamily.YTD_Q2
        assert fact.duration_days == 180
        instant = mkfact("assets", 1.0, date(2026, 6, 30))
        assert instant.duration_family is DurationFamily.INSTANT
        assert instant.duration_days is None


# ------------------------------------------------------------------------------------------
# F. Resolver contract, G. no silent fallback
# ------------------------------------------------------------------------------------------


def _collision(field: str = "revenue") -> list[CanonicalFact]:
    """One accession reporting the discrete quarter and the year to date, as 10-Qs really do."""
    return [
        mkfact(field, 90.0, date(2026, 6, 30), start=date(2026, 4, 1), fiscal_period="Q2",
               accession="ACC-1"),
        mkfact(field, 180.0, date(2026, 6, 30), start=date(2026, 1, 1), fiscal_period="Q2",
               accession="ACC-1"),
    ]


class TestResolverNarrowing:
    def test_without_a_request_the_collision_is_still_ambiguous(self):
        """Unchanged pre-D5-P0 behaviour, so D1-D4 are unaffected. AMBIGUOUS is honest here."""
        result = resolve_fact(_collision(), "revenue", utc(2026, 9, 1))
        assert result.status is FactStatus.AMBIGUOUS
        assert result.fact is None
        assert "QUARTER" in result.reason and "YTD_Q2" in result.reason

    def test_a_quarter_request_selects_the_quarter(self):
        result = resolve_fact(_collision(), "revenue", utc(2026, 9, 1),
                              duration_family=DurationFamily.QUARTER)
        assert result.status is FactStatus.OK
        assert result.fact.value == 90.0
        assert result.duration_family is DurationFamily.QUARTER

    def test_a_ytd_request_selects_the_year_to_date(self):
        result = resolve_fact(_collision(), "revenue", utc(2026, 9, 1),
                              duration_family=DurationFamily.YTD_Q2)
        assert result.status is FactStatus.OK
        assert result.fact.value == 180.0
        assert result.fact.duration_days == 180

    def test_a_quarter_request_rejects_a_ytd_only_filer(self):
        """§8: the measured AEYE `operating_cash_flow` shape. MISSING, never the 180-day number."""
        facts = [mkfact("operating_cash_flow", 2_277_000.0, date(2026, 6, 30),
                        start=date(2026, 1, 1), fiscal_period="Q2")]
        result = resolve_fact(facts, "operating_cash_flow", utc(2026, 9, 1),
                              duration_family=DurationFamily.QUARTER)
        assert result.status is FactStatus.MISSING
        assert result.fact is None
        assert "QUARTER" in result.reason

    def test_a_ytd_request_rejects_a_quarter_only_filer(self):
        facts = [mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                        fiscal_period="Q2")]
        assert resolve_fact(facts, "revenue", utc(2026, 9, 1),
                            duration_family=DurationFamily.YTD_Q2).status is FactStatus.MISSING

    def test_an_annual_request_rejects_the_discrete_fourth_quarter_and_the_reverse(self):
        facts = [
            mkfact("revenue", 400.0, date(2026, 12, 31), start=date(2026, 1, 1), form="10-K",
                   fiscal_period="FY", accession="ACC-K"),
            mkfact("revenue", 100.0, date(2026, 12, 31), start=date(2026, 10, 1), form="10-K",
                   fiscal_period="FY", accession="ACC-K"),
        ]
        annual = resolve_fact(facts, "revenue", utc(2027, 3, 1), duration_family=DurationFamily.FY)
        quarter = resolve_fact(facts, "revenue", utc(2027, 3, 1),
                               duration_family=DurationFamily.QUARTER)
        assert (annual.fact.value, quarter.fact.value) == (400.0, 100.0)

    def test_narrowing_happens_before_the_latest_end_is_chosen(self):
        """Otherwise a quarter request would pick the newest end, find only a YTD row there and
        report MISSING even though an older discrete quarter exists."""
        facts = _collision()[1:] + [
            mkfact("revenue", 85.0, date(2026, 3, 31), start=date(2026, 1, 1), fiscal_period="Q1",
                   accession="ACC-0"),
        ]
        result = resolve_fact(facts, "revenue", utc(2026, 9, 1),
                              duration_family=DurationFamily.QUARTER)
        assert result.status is FactStatus.OK
        assert (result.fact.value, result.fact.end) == (85.0, date(2026, 3, 31))

    def test_a_ytd_q1_request_is_satisfied_by_the_q1_quarter_identity(self):
        facts = [mkfact("revenue", 85.0, date(2026, 3, 31), start=date(2026, 1, 1),
                        fiscal_period="Q1")]
        assert resolve_fact(facts, "revenue", utc(2026, 6, 1),
                            duration_family=DurationFamily.YTD_Q1).fact.value == 85.0
        q2 = [mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                     fiscal_period="Q2")]
        assert resolve_fact(q2, "revenue", utc(2026, 9, 1),
                            duration_family=DurationFamily.YTD_Q1).status is FactStatus.MISSING

    def test_unknown_is_not_a_requestable_family(self):
        with pytest.raises(ValueError):
            resolve_fact(_collision(), "revenue", utc(2026, 9, 1),
                         duration_family=DurationFamily.UNKNOWN)

    def test_an_instant_request_cannot_return_a_duration(self):
        facts = [mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                        fiscal_period="Q2")]
        assert resolve_fact(facts, "revenue", utc(2026, 9, 1),
                            duration_family=DurationFamily.INSTANT).status is FactStatus.MISSING

    def test_other_duration_facts_never_satisfy_a_real_family(self):
        facts = [mkfact("revenue", 7.0, date(2026, 5, 1), start=date(2026, 1, 1),
                        fiscal_period="Q1")]
        assert facts[0].duration_family is DurationFamily.OTHER_DURATION
        for family in (DurationFamily.QUARTER, DurationFamily.YTD_Q2, DurationFamily.FY):
            assert resolve_fact(facts, "revenue", utc(2026, 9, 1),
                                duration_family=family).status is FactStatus.MISSING


class TestPitRulesPreserved:
    def test_acceptance_time_still_gates_a_narrowed_request(self):
        facts = _collision()
        accepted = facts[0].accepted_at
        one_second_early = accepted - timedelta(seconds=1)
        assert resolve_fact(facts, "revenue", one_second_early,
                            duration_family=DurationFamily.QUARTER).status is FactStatus.MISSING
        assert resolve_fact(facts, "revenue", accepted,
                            duration_family=DurationFamily.QUARTER).status is FactStatus.OK

    def test_amendment_versioning_still_applies_within_a_family(self):
        original = mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                          fiscal_period="Q2", accession="ACC-1", accepted_at=utc(2026, 8, 1))
        amended = mkfact("revenue", 95.0, date(2026, 6, 30), start=date(2026, 4, 1),
                         form="10-Q/A", fiscal_period="Q2", accession="ACC-2",
                         accepted_at=utc(2026, 9, 1))
        rows = [original, amended]
        assert resolve_fact(rows, "revenue", utc(2026, 8, 15),
                            duration_family=DurationFamily.QUARTER).fact.value == 90.0
        assert resolve_fact(rows, "revenue", utc(2026, 9, 15),
                            duration_family=DurationFamily.QUARTER).fact.value == 95.0

    def test_a_genuine_conflict_inside_one_family_is_still_ambiguous(self):
        rows = [
            mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                   fiscal_period="Q2", accession="ACC-1"),
            mkfact("revenue", 91.0, date(2026, 6, 30), start=date(2026, 4, 1),
                   fiscal_period="Q2", accession="ACC-1"),
        ]
        assert resolve_fact(rows, "revenue", utc(2026, 9, 1),
                            duration_family=DurationFamily.QUARTER).status is FactStatus.AMBIGUOUS


class TestPeriodAlignment:
    def test_fields_are_aligned_onto_one_period_end(self):
        facts = [
            mkfact("operating_cash_flow", 10.0, date(2026, 6, 30), start=date(2026, 4, 1),
                   fiscal_period="Q2"),
            mkfact("capex", 2.0, date(2026, 6, 30), start=date(2026, 4, 1), fiscal_period="Q2"),
        ]
        rows, reason = resolve_period_aligned(facts, ["operating_cash_flow", "capex"],
                                              utc(2026, 9, 1), DurationFamily.QUARTER)
        assert reason == "aligned"
        assert rows["operating_cash_flow"].end == rows["capex"].end == date(2026, 6, 30)

    def test_it_refuses_rather_than_mixing_two_period_ends(self):
        """The measured shape: cash-flow statements report no discrete Q2, so capex exists only at
        the Q1 end while revenue exists only at the Q2 end."""
        facts = [
            mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                   fiscal_period="Q2"),
            mkfact("capex", 2.0, date(2026, 3, 31), start=date(2026, 1, 1), fiscal_period="Q1"),
        ]
        rows, reason = resolve_period_aligned(facts, ["revenue", "capex"], utc(2026, 9, 1),
                                              DurationFamily.QUARTER)
        assert rows is None
        assert "no period end" in reason

    def test_it_falls_back_to_an_older_end_where_everything_resolves(self):
        facts = [
            mkfact("revenue", 90.0, date(2026, 6, 30), start=date(2026, 4, 1),
                   fiscal_period="Q2", accession="ACC-2"),
            mkfact("revenue", 85.0, date(2026, 3, 31), start=date(2026, 1, 1),
                   fiscal_period="Q1", accession="ACC-1"),
            mkfact("capex", 2.0, date(2026, 3, 31), start=date(2026, 1, 1),
                   fiscal_period="Q1", accession="ACC-1"),
        ]
        rows, reason = resolve_period_aligned(facts, ["revenue", "capex"], utc(2026, 9, 1),
                                              DurationFamily.QUARTER)
        assert reason == "aligned"
        assert rows["revenue"].end == date(2026, 3, 31)


# ------------------------------------------------------------------------------------------
# J. Multi-class wiring
# ------------------------------------------------------------------------------------------


class TestMultiClassWiring:
    def test_the_valuation_path_refuses_an_unresolved_share_class(self):
        from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
            MarketCapStatus, ShareClassState, valuation_market_cap,
        )
        shares = [mkfact("shares_outstanding", 1_000.0, date(2026, 6, 30), unit="shares")]
        result = valuation_market_cap(shares, date(2026, 6, 30),
                                      [utc(2026, 8, 1)], 10.0, ShareClassState.UNRESOLVED)
        assert result.status is MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED
        assert result.value is None and not result.valuation_ready

    def test_multiple_classes_are_refused_too(self):
        from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
            MarketCapStatus, ShareClassState, valuation_market_cap,
        )
        result = valuation_market_cap([], date(2026, 6, 30), [], 10.0,
                                      ShareClassState.MULTIPLE_CLASSES)
        assert result.status is MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES

    def test_the_share_class_argument_has_no_permissive_default(self):
        """The defect D5-D0 named was a boolean defaulting to the permissive answer."""
        import inspect
        from app.backtest.strategy_h_v2.valuation import market_cap_gate
        parameter = inspect.signature(
            market_cap_gate.valuation_market_cap).parameters["share_class_state"]
        assert parameter.default is inspect.Parameter.empty

    def test_h0_5_itself_is_untouched_so_the_frozen_pv_runs_are_unchanged(self):
        import subprocess
        changed = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--",
             "backend/app/backtest/strategy_h0/h0_5.py"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[4])
        assert changed.stdout.strip() == ""


# ------------------------------------------------------------------------------------------
# B/H. Actual stored ambiguity, replayed
# ------------------------------------------------------------------------------------------

_D2_1 = Path("data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages")
_FACTS_ROOTS = [Path("data/runtime/strategy_h/h0/raw"),
                Path("data/runtime/strategy_h/h_pv2c/sec_raw"),
                Path("data/runtime/strategy_h_v2/d1_1/sec_raw")]
_SUB_ROOTS = [Path("data/runtime/strategy_h/h_pv2c/sec_raw/submissions"),
              Path("data/runtime/strategy_c/e0/raw/submissions"),
              Path("data/runtime/strategy_h/h0_5/sec_raw/submissions"),
              Path("data/runtime/strategy_h_v2/d1_1/sec_raw/submissions")]

D5_ISSUERS = ("AEYE", "COLL", "FG", "VRRM", "IDCC", "DORM", "FRPT", "TG", "CRK", "SPSC")


def _gz(path: Path) -> dict:
    return json.loads(gzip.decompress(path.read_bytes()))


def _stored(ticker: str):
    from app.backtest.strategy_h0.pilot import acceptance_index
    package = _D2_1 / f"{ticker}.json"
    if not package.exists():
        pytest.skip("D2.1 packages absent (data/runtime is gitignored)")
    bundle = json.loads(package.read_text())["evidence_bundle"]
    cik = bundle["identity"]["cik"]
    cutoff = datetime.fromisoformat(bundle["data_cutoff"])
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    facts_root = next((r for r in _FACTS_ROOTS
                       if (r / "companyfacts" / f"CIK{cik}.json.gz").exists()), None)
    sub = next((_gz(r / f"CIK{cik}" / f"CIK{cik}.json.gz") for r in _SUB_ROOTS
                if (r / f"CIK{cik}" / f"CIK{cik}.json.gz").exists()), None)
    if facts_root is None or sub is None:
        pytest.skip(f"local SEC store absent for {ticker}")
    document = _gz(facts_root / "companyfacts" / f"CIK{cik}.json.gz")
    return extract_companyfacts(document, acceptance_index(sub)), cutoff


class TestStoredAmbiguityFixtures:
    @pytest.mark.parametrize("ticker", D5_ISSUERS)
    def test_the_stored_collision_resolves_in_both_directions(self, ticker):
        """Not a synthetic example: the real companyfacts rows that made D5-D0 measure 0 of 10."""
        facts, cutoff = _stored(ticker)
        resolved = 0
        for field in ("revenue", "operating_income", "net_income", "eps_diluted"):
            unnarrowed = resolve_fact(facts, field, cutoff)
            if unnarrowed.status is not FactStatus.AMBIGUOUS:
                continue
            resolved += 1
            quarter = resolve_fact(facts, field, cutoff,
                                   duration_family=DurationFamily.QUARTER)
            ytd = resolve_fact(facts, field, cutoff, duration_family=DurationFamily.YTD_Q2)
            assert quarter.status is FactStatus.OK, (ticker, field, quarter.reason)
            assert ytd.status is FactStatus.OK, (ticker, field, ytd.reason)
            assert quarter.fact.duration_family is DurationFamily.QUARTER
            assert ytd.fact.duration_family is DurationFamily.YTD_Q2
            assert quarter.fact.value != ytd.fact.value
            assert quarter.fact.accession == ytd.fact.accession
        assert resolved > 0, f"{ticker} had no stored ambiguity to exercise"

    def test_the_known_aeye_180_day_cash_flow_no_longer_passes_as_a_quarter(self):
        """D5-D0 §B's named case: OCF resolves OK at 180 days and nothing said so."""
        facts, cutoff = _stored("AEYE")
        unnarrowed = resolve_fact(facts, "operating_cash_flow", cutoff)
        assert unnarrowed.status is FactStatus.OK
        assert unnarrowed.fact.duration_days == 180
        assert unnarrowed.fact.duration_family is DurationFamily.YTD_Q2
        quarter = resolve_fact(facts, "operating_cash_flow", cutoff,
                               duration_family=DurationFamily.QUARTER)
        assert quarter.fact is None or quarter.fact.duration_days != 180

    def test_no_resolved_fact_is_ever_outside_the_family_that_was_asked_for(self):
        """§12's silent-fallback criterion, swept over every field, issuer and family."""
        families = (DurationFamily.INSTANT, DurationFamily.QUARTER, DurationFamily.YTD_Q1,
                    DurationFamily.YTD_Q2, DurationFamily.YTD_Q3, DurationFamily.FY,
                    DurationFamily.OTHER_DURATION)
        from app.backtest.strategy_h0.facts import FIELD_SPECS, _matches_requested_family
        checked = 0
        for ticker in D5_ISSUERS:
            facts, cutoff = _stored(ticker)
            for field in FIELD_SPECS:
                for family in families:
                    result = resolve_fact(facts, field, cutoff, duration_family=family)
                    if result.fact is None:
                        continue
                    checked += 1
                    assert _matches_requested_family(result.fact, family), (
                        ticker, field, family, result.fact.duration_family)
        assert checked > 0


class TestProvenancePreservation:
    def test_the_bundle_snapshot_keeps_start_and_the_family(self):
        from app.backtest.strategy_h_v2.pipeline import _snapshot
        facts = [mkfact("operating_cash_flow", 2_277_000.0, date(2026, 6, 30),
                        start=date(2026, 1, 1), fiscal_period="Q2")]
        row = _snapshot(facts, "operating_cash_flow", utc(2026, 9, 1))
        assert row["status"] == "OK"
        assert row["start"] == "2026-01-01"
        assert row["end"] == "2026-06-30"
        assert row["duration_days"] == 180
        assert row["duration_family"] == "YTD_Q2"
        for key in ("form", "tag", "unit", "accession", "accepted_at"):
            assert row[key]

    def test_an_instant_reports_a_null_start_rather_than_omitting_it(self):
        from app.backtest.strategy_h_v2.pipeline import _snapshot
        row = _snapshot([mkfact("assets", 5.0, date(2026, 6, 30))], "assets", utc(2026, 9, 1))
        assert row["start"] is None
        assert row["duration_days"] is None
        assert row["duration_family"] == "INSTANT"

    def test_an_unresolved_field_still_says_why(self):
        from app.backtest.strategy_h_v2.pipeline import _snapshot
        row = _snapshot(_collision(), "revenue", utc(2026, 9, 1))
        assert row["status"] == "AMBIGUOUS"
        assert row["value"] is None
        assert "QUARTER" in row["reason"]
