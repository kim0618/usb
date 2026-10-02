"""H-V2-D5-P0.1: TTM construction, PIT safety, D&A canonicalisation and provenance.

The data-backed tests read `data/runtime/`, which is gitignored, and skip when it is absent. The pure
tests do not skip: the contract they assert is code, not data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest.strategy_h0.facts import (
    ANNUAL_SPAN_DAYS,
    COMPARABLE_DURATION_TOLERANCE_DAYS,
    COMPARABLE_FY_DURATION_TOLERANCE_DAYS,
    FIELD_SPECS,
    YEAR_APART_DAYS,
    CanonicalFact,
    DurationFamily,
    FactStatus,
    extract_companyfacts,
    resolve_fact,
)
from app.backtest.strategy_h_v2.valuation.fundamental_fields import (
    DEPRECIATION_AMORTIZATION_COMPONENT_TAGS_NOT_SUMMED,
    DEPRECIATION_AMORTIZATION_TAGS,
    VALUATION_FIELD_SPECS,
    VALUATION_ONLY_FIELDS,
)
from app.backtest.strategy_h_v2.valuation.ttm import (
    MAX_TTM_PERIOD_AGE_DAYS,
    NEVER_CONSTRUCTED,
    QUARTERS_PER_YEAR,
    YTD_DIFFERENCE_IS_UNSAFE_FOR,
    TtmMethod,
    TtmStatus,
    construct_ttm,
    construct_ttm_aligned,
    construct_ttm_bundle,
    construct_ttm_free_cash_flow,
    ebitda_feasible,
)

from conftest import mkfact, utc

# A decision time close behind the periods under test, so the staleness bound is not what is being
# measured except where it is.
DECISION = utc(2026, 8, 15)


def quarter(field: str, value: float, start: date, end: date, *, fp: str, accession: str,
            tag: str | None = None, **kw) -> CanonicalFact:
    fact = mkfact(field, value, end, start=start, fiscal_period=fp, accession=accession, **kw)
    return fact if tag is None else _retag(fact, tag)


def _retag(fact: CanonicalFact, tag: str) -> CanonicalFact:
    from dataclasses import replace
    return replace(fact, tag=tag)


def four_quarters(field: str = "revenue", values=(10.0, 20.0, 30.0, 40.0)) -> list[CanonicalFact]:
    """A filer that reports every discrete quarter, including the fourth inside its 10-K."""
    spans = [(date(2025, 7, 1), date(2025, 9, 30), "Q3", "10-Q"),
             (date(2025, 10, 1), date(2025, 12, 31), "FY", "10-K"),
             (date(2026, 1, 1), date(2026, 3, 31), "Q1", "10-Q"),
             (date(2026, 4, 1), date(2026, 6, 30), "Q2", "10-Q")]
    return [mkfact(field, value, end, start=start, fiscal_period=fp, form=form,
                   accession=f"ACC-{i}")
            for i, (value, (start, end, fp, form)) in enumerate(zip(values, spans))]


def ytd_triple(field: str = "operating_cash_flow", *, fy: float = 400.0, current: float = 180.0,
               prior: float = 160.0, unit: str = "USD",
               tags: tuple[str | None, str | None, str | None] = (None, None, None),
               accepted: tuple[datetime | None, ...] = (None, None, None)) -> list[CanonicalFact]:
    """The normal case: a 10-K fiscal year, this year's six months, last year's six months."""
    rows = [
        mkfact(field, fy, date(2025, 12, 31), start=date(2025, 1, 1), form="10-K",
               fiscal_period="FY", accession="ACC-K", unit=unit, accepted_at=accepted[0]),
        mkfact(field, current, date(2026, 6, 30), start=date(2026, 1, 1), fiscal_period="Q2",
               accession="ACC-Q2-26", unit=unit, accepted_at=accepted[1]),
        mkfact(field, prior, date(2025, 6, 30), start=date(2025, 1, 1), fiscal_period="Q2",
               accession="ACC-Q2-25", unit=unit, accepted_at=accepted[2]),
    ]
    return [row if tag is None else _retag(row, tag) for row, tag in zip(rows, tags)]


# ------------------------------------------------------------------------------------------
# D0. A reported fiscal year
# ------------------------------------------------------------------------------------------


def fiscal_year(field: str = "revenue", value: float = 500.0, *, start=date(2025, 7, 1),
                end=date(2026, 6, 30)) -> list[CanonicalFact]:
    """A June fiscal-year filer's 10-K, which is COHR's measured shape."""
    return [mkfact(field, value, end, start=start, form="10-K", fiscal_period="FY",
                   accession="ACC-K")]


class TestReportedFiscalYear:
    def test_a_recent_fiscal_year_is_the_trailing_year_with_no_arithmetic(self):
        result = construct_ttm(fiscal_year(), "revenue", utc(2026, 9, 28))
        assert result.status is TtmStatus.OK
        assert result.method is TtmMethod.REPORTED_FISCAL_YEAR
        assert result.value == 500.0
        assert (result.period_start, result.period_end) == (date(2025, 7, 1), date(2026, 6, 30))
        assert len(result.components) == 1
        assert result.components[0].role == "fiscal_year"

    def test_it_is_preferred_over_the_quarter_chain_at_the_same_period_end(self):
        """Not because the sum would be wrong, but because the filer already published the total."""
        rows = four_quarters(values=(10.0, 20.0, 30.0, 40.0)) + [
            mkfact("revenue", 100.0, date(2026, 6, 30), start=date(2025, 7, 1), form="10-K",
                   fiscal_period="FY", accession="ACC-K")]
        assert construct_ttm(rows, "revenue", DECISION).method is TtmMethod.REPORTED_FISCAL_YEAR

    def test_a_10_ks_own_fourth_quarter_is_not_mistaken_for_the_fiscal_year(self):
        """P0 classified both; this asserts the TTM layer inherits that separation."""
        rows = [
            mkfact("revenue", 500.0, date(2026, 6, 30), start=date(2025, 7, 1), form="10-K",
                   fiscal_period="FY", accession="ACC-K"),
            mkfact("revenue", 120.0, date(2026, 6, 30), start=date(2026, 4, 1), form="10-K",
                   fiscal_period="FY", accession="ACC-K"),
        ]
        result = construct_ttm(rows, "revenue", utc(2026, 9, 28))
        assert result.value == 500.0
        assert result.components[0].fact.duration_days > 300

    def test_a_stale_fiscal_year_is_not_the_present(self):
        result = construct_ttm(fiscal_year(start=date(2024, 7, 1), end=date(2025, 6, 30)),
                               "revenue", utc(2026, 9, 28))
        assert result.status is TtmStatus.STALE_PERIOD
        assert result.value is None

    def test_eps_may_come_from_a_reported_fiscal_year(self):
        """The one path to a TTM EPS besides four discrete quarters, and it is a reported figure."""
        result = construct_ttm(fiscal_year("eps_diluted", 2.46), "eps_diluted", utc(2026, 9, 28))
        assert result.status is TtmStatus.OK
        assert result.method is TtmMethod.REPORTED_FISCAL_YEAR
        assert result.value == pytest.approx(2.46)


# ------------------------------------------------------------------------------------------
# D. Four discrete quarters
# ------------------------------------------------------------------------------------------


class TestFourDiscreteQuarters:
    def test_four_adjacent_quarters_sum_to_a_year(self):
        result = construct_ttm(four_quarters(), "revenue", DECISION)
        assert result.status is TtmStatus.OK
        assert result.method is TtmMethod.FOUR_DISCRETE_QUARTERS
        assert result.value == 100.0
        assert (result.period_start, result.period_end) == (date(2025, 7, 1), date(2026, 6, 30))
        assert ANNUAL_SPAN_DAYS[0] <= result.duration_days <= ANNUAL_SPAN_DAYS[1]
        assert len(result.components) == QUARTERS_PER_YEAR
        assert all(c.sign == 1 for c in result.components)

    def test_the_fourth_quarter_inside_a_10_k_is_usable(self):
        """P0 classified a 10-K's own discrete Q4 as QUARTER; this is what that was for."""
        forms = {c.fact.form for c in construct_ttm(four_quarters(), "revenue",
                                                   DECISION).components}
        assert forms == {"10-Q", "10-K"}

    def test_a_missing_quarter_is_not_interpolated(self):
        rows = [f for f in four_quarters() if f.end != date(2025, 12, 31)]
        result = construct_ttm(rows, "revenue", DECISION)
        assert result.value is None
        assert result.status in (TtmStatus.MISSING_COMPONENT, TtmStatus.STALE_PERIOD)

    def test_a_gap_between_quarters_refuses_rather_than_closing_it(self):
        """Adjacency is pinned, so a quarter that does not abut the previous one is not reachable."""
        rows = four_quarters()
        shifted = [f for f in rows if f.end != date(2025, 9, 30)]
        shifted.append(mkfact("revenue", 10.0, date(2025, 9, 20), start=date(2025, 6, 22),
                              fiscal_period="Q3", accession="ACC-GAP"))
        assert construct_ttm(shifted, "revenue", DECISION).status is not TtmStatus.OK

    def test_four_adjacent_quarters_that_do_not_span_a_year_refuse(self):
        """Each span is a quarter and the four together are 283 days, so this is four quarters of
        something that is not a year. The total is checked, not assumed from the count."""
        rows = [mkfact("revenue", 1.0, end, start=start, fiscal_period=fp, accession=f"A{i}")
                for i, (start, end, fp) in enumerate([
                    (date(2025, 7, 22), date(2025, 9, 30), "Q1"),
                    (date(2025, 10, 1), date(2025, 12, 10), "Q2"),
                    (date(2025, 12, 11), date(2026, 2, 19), "Q3"),
                    (date(2026, 2, 20), date(2026, 5, 1), "Q1")])]
        result = construct_ttm(rows, "revenue", utc(2026, 6, 15))
        assert result.status is TtmStatus.PERIOD_MISMATCH
        assert "283" in result.reason and "outside" in result.reason

    def test_components_from_different_tags_refuse(self):
        rows = four_quarters()
        rows[0] = _retag(rows[0], "SalesRevenueNet")
        result = construct_ttm(rows, "revenue", DECISION)
        assert result.status is TtmStatus.TAG_MISMATCH
        assert result.value is None


# ------------------------------------------------------------------------------------------
# E. FY + current YTD - prior YTD
# ------------------------------------------------------------------------------------------


class TestYtdDifference:
    def test_the_fiscal_year_plus_the_ytd_difference_is_the_trailing_year(self):
        result = construct_ttm(ytd_triple(), "operating_cash_flow", DECISION)
        assert result.status is TtmStatus.OK
        assert result.method is TtmMethod.FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD
        assert result.value == 400.0 + 180.0 - 160.0
        assert (result.period_start, result.period_end) == (date(2025, 7, 1), date(2026, 6, 30))
        assert dict((c.role, c.sign) for c in result.components) == {
            "prior_fy": 1, "current_ytd": 1, "prior_ytd": -1}

    def test_a_q2_cash_flow_statement_reports_six_months_and_that_is_what_is_used(self):
        """§6: cash-flow statements report no discrete Q2, so the YTD path is the only one."""
        result = construct_ttm(ytd_triple(), "operating_cash_flow", DECISION)
        current = next(c for c in result.components if c.role == "current_ytd")
        assert current.fact.duration_days == 180
        assert current.fact.duration_family is DurationFamily.YTD_Q2

    def test_a_q3_cash_flow_statement_reports_nine_months(self):
        rows = [
            mkfact("operating_cash_flow", 400.0, date(2025, 12, 31), start=date(2025, 1, 1),
                   form="10-K", fiscal_period="FY", accession="K"),
            mkfact("operating_cash_flow", 270.0, date(2026, 9, 30), start=date(2026, 1, 1),
                   fiscal_period="Q3", accession="Q3-26"),
            mkfact("operating_cash_flow", 250.0, date(2025, 9, 30), start=date(2025, 1, 1),
                   fiscal_period="Q3", accession="Q3-25"),
        ]
        result = construct_ttm(rows, "operating_cash_flow", utc(2026, 11, 15))
        assert result.status is TtmStatus.OK
        assert result.value == 420.0
        assert next(c for c in result.components
                    if c.role == "current_ytd").fact.duration_family is DurationFamily.YTD_Q3
        assert (result.period_start, result.period_end) == (date(2025, 10, 1), date(2026, 9, 30))

    def test_a_missing_prior_ytd_is_not_estimated(self):
        """§11's exact case: the fiscal year and the current six months exist, the comparison
        does not. Doubling the six months is not a construction, so there is no TTM."""
        rows = [f for f in ytd_triple() if f.end != date(2025, 6, 30)]
        result = construct_ttm(rows, "operating_cash_flow", DECISION)
        assert result.status is TtmStatus.MISSING_COMPONENT
        assert result.value is None
        assert "prior" in result.reason

    def test_a_missing_prior_fiscal_year_is_not_estimated(self):
        rows = [f for f in ytd_triple() if f.form != "10-K"]
        result = construct_ttm(rows, "operating_cash_flow", DECISION)
        assert result.status is TtmStatus.MISSING_COMPONENT
        assert result.value is None

    def test_the_prior_year_must_end_the_day_before_the_current_ytd_begins(self):
        """Not 'about a year back': the anchor is exact, so a stub year cannot stand in."""
        rows = ytd_triple()
        rows[0] = mkfact("operating_cash_flow", 400.0, date(2025, 11, 30),
                         start=date(2024, 12, 1), form="10-K", fiscal_period="FY",
                         accession="ACC-K")
        result = construct_ttm(rows, "operating_cash_flow", DECISION)
        assert result.status is TtmStatus.MISSING_COMPONENT
        assert "the day before" in result.reason

    def test_the_prior_ytd_must_accumulate_from_the_prior_fiscal_years_own_start(self):
        rows = ytd_triple()
        rows[2] = mkfact("operating_cash_flow", 160.0, date(2025, 6, 30), start=date(2025, 2, 1),
                         fiscal_period="Q2", accession="ACC-Q2-25")
        assert construct_ttm(rows, "operating_cash_flow",
                             DECISION).status is TtmStatus.MISSING_COMPONENT

    def _nine_month_triple(self, prior_end: date, prior_days_label: str):
        return [
            mkfact("operating_cash_flow", 400.0, date(2025, 12, 31), start=date(2025, 1, 1),
                   form="10-K", fiscal_period="FY", accession="K"),
            mkfact("operating_cash_flow", 270.0, date(2026, 9, 30), start=date(2026, 1, 1),
                   fiscal_period="Q3", accession="Q3-26"),
            mkfact("operating_cash_flow", 250.0, prior_end, start=date(2025, 1, 1),
                   fiscal_period="Q3", accession=f"Q3-25-{prior_days_label}"),
        ]

    def test_two_ytd_periods_too_far_apart_refuse_on_the_frozen_tolerance(self):
        """Both periods are nine-month year-to-date figures accumulating from their own fiscal year
        starts, and they still end 338 days apart, under H-PV2's frozen 345."""
        result = construct_ttm(self._nine_month_triple(date(2025, 10, 27), "299d"),
                               "operating_cash_flow", utc(2026, 11, 15))
        assert result.status is TtmStatus.PERIOD_MISMATCH
        assert "338" in result.reason and str(YEAR_APART_DAYS[0]) in result.reason

    def test_two_ytd_spans_of_different_lengths_refuse_on_the_frozen_tolerance(self):
        """272 days against 280: a year apart, and not the same slice of the year."""
        result = construct_ttm(self._nine_month_triple(date(2025, 10, 8), "280d"),
                               "operating_cash_flow", utc(2026, 11, 15))
        assert result.status is TtmStatus.PERIOD_MISMATCH
        assert str(COMPARABLE_DURATION_TOLERANCE_DAYS) in result.reason

    def test_the_exact_anchors_are_stricter_than_the_tolerance_for_a_six_month_ytd(self):
        """A measured property of the contract, not a hypothetical: once the prior year-to-date must
        start on the prior fiscal year's own start and its span must sit in the frozen 160-200 day
        semi-annual window, the two ends cannot fall outside 345-385 days apart. The tolerance is
        therefore only reachable for the nine-month family, and the anchors do the work below it."""
        rows = ytd_triple()
        rows[2] = mkfact("operating_cash_flow", 160.0, date(2025, 5, 1), start=date(2025, 1, 1),
                         fiscal_period="Q2", accession="ACC-Q2-25")
        result = construct_ttm(rows, "operating_cash_flow", DECISION)
        assert result.status is TtmStatus.MISSING_COMPONENT, (
            "a 120-day span is not a six-month year to date, so it is not a candidate at all")

    def test_components_in_different_units_refuse(self):
        rows = ytd_triple()
        rows[1] = mkfact("operating_cash_flow", 180.0, date(2026, 6, 30), start=date(2026, 1, 1),
                         fiscal_period="Q2", accession="ACC-Q2-26", unit="USD/shares")
        result = construct_ttm(rows, "operating_cash_flow", DECISION)
        assert result.status is TtmStatus.UNIT_MISMATCH
        assert result.value is None

    def test_a_q1_report_supports_the_path_through_p0s_ytd_q1_identity(self):
        rows = [
            mkfact("operating_cash_flow", 400.0, date(2025, 12, 31), start=date(2025, 1, 1),
                   form="10-K", fiscal_period="FY", accession="K"),
            mkfact("operating_cash_flow", 95.0, date(2026, 3, 31), start=date(2026, 1, 1),
                   fiscal_period="Q1", accession="Q1-26"),
            mkfact("operating_cash_flow", 90.0, date(2025, 3, 31), start=date(2025, 1, 1),
                   fiscal_period="Q1", accession="Q1-25"),
        ]
        result = construct_ttm(rows, "operating_cash_flow", utc(2026, 5, 20))
        assert result.status is TtmStatus.OK
        assert result.value == 405.0
        assert (result.period_start, result.period_end) == (date(2025, 4, 1), date(2026, 3, 31))

    def test_the_quarter_chain_is_preferred_where_both_constructions_reach_one_period_end(self):
        rows = four_quarters(values=(30.0, 20.0, 25.0, 25.0)) + [
            mkfact("revenue", 100.0, date(2025, 12, 31), start=date(2025, 1, 1), form="10-K",
                   fiscal_period="FY", accession="ACC-K"),
            mkfact("revenue", 50.0, date(2026, 1, 1) and date(2026, 6, 30), start=date(2026, 1, 1),
                   fiscal_period="Q2", accession="ACC-Y2"),
            mkfact("revenue", 45.0, date(2025, 6, 30), start=date(2025, 1, 1), fiscal_period="Q2",
                   accession="ACC-Y1"),
        ]
        result = construct_ttm(rows, "revenue", DECISION)
        assert result.method is TtmMethod.FOUR_DISCRETE_QUARTERS


# ------------------------------------------------------------------------------------------
# G. EPS
# ------------------------------------------------------------------------------------------


class TestEps:
    def test_eps_sums_four_reported_quarters(self):
        result = construct_ttm(four_quarters("eps_diluted", (0.10, 0.20, 0.30, 0.40)),
                               "eps_diluted", DECISION)
        assert result.status is TtmStatus.OK
        assert result.method is TtmMethod.FOUR_DISCRETE_QUARTERS
        assert result.value == pytest.approx(1.0)

    def test_eps_refuses_the_ytd_difference_path(self):
        """§12: audited and rejected. A difference of two differently-weighted per-share
        denominators has no residual this repository can bound."""
        result = construct_ttm(ytd_triple("eps_diluted", fy=1.73, current=-0.02, prior=0.44),
                               "eps_diluted", DECISION)
        assert result.status is TtmStatus.NOT_APPLICABLE
        assert result.value is None
        assert "eps_diluted" in YTD_DIFFERENCE_IS_UNSAFE_FOR

    def test_eps_is_never_recomputed_from_net_income_and_shares(self):
        """§12's absolute. A filer with a perfectly constructible TTM net income and a current share
        count still has no TTM EPS, because the quotient of those two is not a reported EPS."""
        rows = four_quarters("net_income", (10.0, 20.0, 30.0, 40.0)) + [
            mkfact("shares_outstanding", 1_000.0, date(2026, 6, 30), unit="shares")]
        assert construct_ttm(rows, "net_income", DECISION).status is TtmStatus.OK
        eps = construct_ttm(rows, "eps_diluted", DECISION)
        assert eps.status is TtmStatus.MISSING_COMPONENT
        assert eps.value is None
        assert any("divided by a share count" in rule for rule in NEVER_CONSTRUCTED)


# ------------------------------------------------------------------------------------------
# F/I. Cash flow alignment and free cash flow
# ------------------------------------------------------------------------------------------


class TestFreeCashFlow:
    def test_free_cash_flow_subtracts_capex_once(self):
        rows = ytd_triple("operating_cash_flow", fy=400.0, current=180.0, prior=160.0) \
            + ytd_triple("capex", fy=40.0, current=18.0, prior=16.0)
        result = construct_ttm_free_cash_flow(rows, DECISION)
        assert result.status is TtmStatus.OK
        assert result.value == pytest.approx((400 + 180 - 160) - (40 + 18 - 16))
        assert result.value == pytest.approx(378.0)

    def test_capex_keeps_the_declared_cash_outflow_positive_convention(self):
        """The sign contract, locked: capex enters as a positive outflow and is subtracted once."""
        assert FIELD_SPECS["capex"].sign == "cash_outflow_positive"
        rows = ytd_triple("operating_cash_flow", fy=400.0, current=180.0, prior=160.0) \
            + ytd_triple("capex", fy=40.0, current=18.0, prior=16.0)
        fcf = construct_ttm_free_cash_flow(rows, DECISION)
        capex_components = [c for c in fcf.components if c.role.startswith("capex:")]
        assert {c.role.split(":")[1]: c.sign for c in capex_components} == {
            "prior_fy": -1, "current_ytd": -1, "prior_ytd": 1}

    def test_a_negatively_signed_capex_refuses_instead_of_being_negated_twice(self):
        rows = ytd_triple("capex", fy=-40.0, current=-18.0, prior=-16.0)
        result = construct_ttm(rows, "capex", DECISION)
        assert result.status is TtmStatus.SIGN_CONVENTION_MISMATCH
        assert result.value is None

    def test_free_cash_flow_refuses_when_no_current_period_end_serves_both_legs(self):
        """The measured shape: cash flow reported to one period end, capital expenditure to another.
        The only end both legs reach is the prior fiscal year's, which is 227 days back here, so there
        is no free cash flow rather than a free cash flow of mismatched halves."""
        rows = ytd_triple("operating_cash_flow") + [
            mkfact("capex", 40.0, date(2025, 12, 31), start=date(2025, 1, 1), form="10-K",
                   fiscal_period="FY", accession="K2"),
            mkfact("capex", 9.0, date(2026, 3, 31), start=date(2026, 1, 1), fiscal_period="Q1",
                   accession="C1-26"),
            mkfact("capex", 8.0, date(2025, 3, 31), start=date(2025, 1, 1), fiscal_period="Q1",
                   accession="C1-25"),
        ]
        result = construct_ttm_free_cash_flow(rows, DECISION)
        assert result.status is TtmStatus.STALE_PERIOD
        assert result.value is None

    def test_free_cash_flow_uses_a_shared_fiscal_year_end_when_there_is_a_recent_one(self):
        rows = [
            mkfact("operating_cash_flow", 400.0, date(2026, 6, 30), start=date(2025, 7, 1),
                   form="10-K", fiscal_period="FY", accession="K"),
            mkfact("capex", 40.0, date(2026, 6, 30), start=date(2025, 7, 1), form="10-K",
                   fiscal_period="FY", accession="K"),
        ]
        result = construct_ttm_free_cash_flow(rows, utc(2026, 9, 28))
        assert result.status is TtmStatus.OK
        assert result.method is TtmMethod.REPORTED_FISCAL_YEAR
        assert result.value == pytest.approx(360.0)

    def test_free_cash_flow_uses_an_older_end_where_both_legs_do_resolve(self):
        """`resolve_period_aligned`'s own rule, inherited: walk back to a shared end rather than
        mixing the newest end of each leg."""
        rows = ytd_triple("operating_cash_flow") + [
            mkfact("operating_cash_flow", 95.0, date(2026, 3, 31), start=date(2026, 1, 1),
                   fiscal_period="Q1", accession="O1-26"),
            mkfact("operating_cash_flow", 90.0, date(2025, 3, 31), start=date(2025, 1, 1),
                   fiscal_period="Q1", accession="O1-25"),
            mkfact("capex", 40.0, date(2025, 12, 31), start=date(2025, 1, 1), form="10-K",
                   fiscal_period="FY", accession="K2"),
            mkfact("capex", 9.0, date(2026, 3, 31), start=date(2026, 1, 1), fiscal_period="Q1",
                   accession="C1-26"),
            mkfact("capex", 8.0, date(2025, 3, 31), start=date(2025, 1, 1), fiscal_period="Q1",
                   accession="C1-25"),
        ]
        result = construct_ttm_free_cash_flow(rows, utc(2026, 8, 1))
        assert result.status is TtmStatus.OK
        assert result.period_end == date(2026, 3, 31)
        assert result.value == pytest.approx((400 + 95 - 90) - (40 + 9 - 8))

    def test_free_cash_flow_is_unknown_when_capex_is_absent(self):
        result = construct_ttm_free_cash_flow(ytd_triple("operating_cash_flow"), DECISION)
        assert result.status is TtmStatus.MISSING_COMPONENT
        assert result.value is None

    def test_the_aligned_entry_point_puts_both_legs_on_one_period_end(self):
        rows = ytd_triple("operating_cash_flow") + ytd_triple("capex", fy=40.0, current=18.0,
                                                             prior=16.0)
        parts, reason = construct_ttm_aligned(rows, ("operating_cash_flow", "capex"), DECISION)
        assert "aligned" in reason
        assert parts["operating_cash_flow"].period_end == parts["capex"].period_end


# ------------------------------------------------------------------------------------------
# H. D&A canonicalisation
# ------------------------------------------------------------------------------------------


class TestDepreciationAmortization:
    def test_d_and_a_is_canonical_for_the_valuation_layer_only(self):
        assert VALUATION_ONLY_FIELDS == {"depreciation_amortization"}
        assert "depreciation_amortization" not in FIELD_SPECS, (
            "widening FIELD_SPECS would move D1's total_field_count and its eligibility floor")
        assert set(FIELD_SPECS) < set(VALUATION_FIELD_SPECS)

    def test_the_unified_tags_are_the_ones_the_filings_actually_carry(self):
        assert DEPRECIATION_AMORTIZATION_TAGS == (
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
        )
        assert VALUATION_FIELD_SPECS["depreciation_amortization"].units == ("USD",)
        assert VALUATION_FIELD_SPECS["depreciation_amortization"].period_type == "duration"

    def test_component_tags_are_never_canonical_so_they_cannot_be_summed(self):
        canonical = set(DEPRECIATION_AMORTIZATION_TAGS)
        for component in DEPRECIATION_AMORTIZATION_COMPONENT_TAGS_NOT_SUMMED:
            assert component not in canonical, component
        assert "Depreciation" in DEPRECIATION_AMORTIZATION_COMPONENT_TAGS_NOT_SUMMED
        assert "AmortizationOfIntangibleAssets" in \
            DEPRECIATION_AMORTIZATION_COMPONENT_TAGS_NOT_SUMMED

    def test_a_filer_reporting_only_components_resolves_nothing_rather_than_a_sum(self):
        document = {"facts": {"us-gaap": {
            "Depreciation": {"units": {"USD": [
                {"start": "2026-01-01", "end": "2026-06-30", "val": 2_275_000, "accn": "ACC-1",
                 "form": "10-Q", "filed": "2026-07-20", "fy": 2026, "fp": "Q2"}]}},
            "AmortizationOfIntangibleAssets": {"units": {"USD": [
                {"start": "2026-01-01", "end": "2026-06-30", "val": 118_426_000, "accn": "ACC-1",
                 "form": "10-Q", "filed": "2026-07-20", "fy": 2026, "fp": "Q2"}]}},
        }}}
        facts = extract_companyfacts(document, {"ACC-1": utc(2026, 7, 20)},
                                     specs=VALUATION_FIELD_SPECS)
        assert facts == [], "a component tag is not a D&A fact"
        assert resolve_fact(facts, "depreciation_amortization", DECISION,
                            specs=VALUATION_FIELD_SPECS).status is FactStatus.MISSING

    def test_a_unified_tag_is_read_as_reported(self):
        document = {"facts": {"us-gaap": {"DepreciationDepletionAndAmortization": {"units": {
            "USD": [{"start": "2026-01-01", "end": "2026-06-30", "val": 47_859_000,
                     "accn": "ACC-1", "form": "10-Q", "filed": "2026-07-20", "fy": 2026,
                     "fp": "Q2"}]}}}}}
        facts = extract_companyfacts(document, {"ACC-1": utc(2026, 7, 20)},
                                     specs=VALUATION_FIELD_SPECS)
        assert len(facts) == 1
        assert facts[0].field == "depreciation_amortization"
        assert facts[0].value == 47_859_000

    def test_d_and_a_from_two_tags_in_one_ttm_refuses(self):
        """FRPT's measured shape: the fiscal year under one tag, the interim periods under another."""
        rows = ytd_triple("depreciation_amortization", fy=89_721_000.0, current=47_859_000.0,
                          prior=42_436_000.0,
                          tags=("DepreciationAndAmortization",
                                "DepreciationDepletionAndAmortization",
                                "DepreciationDepletionAndAmortization"))
        result = construct_ttm(rows, "depreciation_amortization", DECISION,
                               specs=VALUATION_FIELD_SPECS)
        assert result.status is TtmStatus.TAG_MISMATCH
        assert result.value is None


# ------------------------------------------------------------------------------------------
# J. PIT and amendments
# ------------------------------------------------------------------------------------------


class TestPointInTime:
    def test_a_component_filed_after_the_decision_time_is_not_used(self):
        rows = ytd_triple(accepted=(utc(2026, 2, 20), utc(2026, 7, 20), utc(2025, 7, 20)))
        before = construct_ttm(rows, "operating_cash_flow", utc(2026, 7, 19, 23, 59))
        after = construct_ttm(rows, "operating_cash_flow", utc(2026, 7, 20))
        assert before.status is TtmStatus.MISSING_COMPONENT
        assert after.status is TtmStatus.OK

    def test_an_after_hours_filing_is_unavailable_until_the_decision_time_passes_it(self):
        """A filing accepted at 20:05 UTC is not knowable at that afternoon's close and is knowable
        the next morning. The rule is `accepted_at <= decision_time` on exact timestamps, inherited
        from `resolve_fact` rather than re-implemented here."""
        accepted = utc(2026, 7, 20, 20, 5)
        rows = ytd_triple(accepted=(utc(2026, 2, 20), accepted, utc(2025, 7, 20)))
        assert construct_ttm(rows, "operating_cash_flow",
                             utc(2026, 7, 20, 16, 0)).status is TtmStatus.MISSING_COMPONENT
        assert construct_ttm(rows, "operating_cash_flow",
                             utc(2026, 7, 21, 9, 30)).status is TtmStatus.OK

    def test_an_amendment_known_at_the_decision_time_supersedes_the_original(self):
        original = mkfact("operating_cash_flow", 180.0, date(2026, 6, 30), start=date(2026, 1, 1),
                          fiscal_period="Q2", accession="ACC-A", accepted_at=utc(2026, 7, 20))
        amended = mkfact("operating_cash_flow", 190.0, date(2026, 6, 30), start=date(2026, 1, 1),
                         form="10-Q/A", fiscal_period="Q2", accession="ACC-B",
                         accepted_at=utc(2026, 8, 10))
        rest = [f for f in ytd_triple() if f.end != date(2026, 6, 30)]
        before = construct_ttm(rest + [original, amended], "operating_cash_flow", utc(2026, 8, 1))
        after = construct_ttm(rest + [original, amended], "operating_cash_flow", utc(2026, 8, 20))
        assert before.value == 400.0 + 180.0 - 160.0
        assert after.value == 400.0 + 190.0 - 160.0

    def test_a_future_amendment_does_not_reach_back_into_an_earlier_decision(self):
        rows = ytd_triple()
        rows.append(mkfact("operating_cash_flow", 999.0, date(2026, 6, 30), start=date(2026, 1, 1),
                           form="10-Q/A", fiscal_period="Q2", accession="ACC-Z",
                           accepted_at=utc(2026, 12, 1)))
        result = construct_ttm(rows, "operating_cash_flow", DECISION)
        assert result.value == 420.0
        assert all(c.fact.accepted_at <= DECISION for c in result.components)


class TestStaleness:
    def test_a_trailing_year_that_ended_long_ago_is_refused_rather_than_reported(self):
        """The defect the first measurement of this step contained: DORM's TTM D&A came out OK over
        2013-09-29..2014-09-27 at a 2026 decision time, because the search walked back until
        something constructed."""
        result = construct_ttm(ytd_triple(), "operating_cash_flow", utc(2030, 1, 1))
        assert result.status is TtmStatus.STALE_PERIOD
        assert result.value is None
        assert "2026-06-30" in result.reason

    def test_the_bound_is_the_repositorys_existing_one_rather_than_a_new_number(self):
        from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS
        assert MAX_TTM_PERIOD_AGE_DAYS == MAX_SHARES_STALENESS_DAYS == 135

    def test_the_bound_is_measured_from_the_period_end_to_the_decision_date(self):
        rows = ytd_triple()
        edge = datetime.combine(date(2026, 6, 30) + timedelta(days=MAX_TTM_PERIOD_AGE_DAYS),
                                datetime.min.time(), tzinfo=timezone.utc)
        assert construct_ttm(rows, "operating_cash_flow", edge).status is TtmStatus.OK
        assert construct_ttm(rows, "operating_cash_flow",
                             edge + timedelta(days=1)).status is TtmStatus.STALE_PERIOD


# ------------------------------------------------------------------------------------------
# K. Provenance and determinism
# ------------------------------------------------------------------------------------------


class TestProvenance:
    def test_every_component_carries_its_period_acceptance_and_fact_id(self):
        record = construct_ttm(ytd_triple(), "operating_cash_flow", DECISION).to_dict()
        assert record["construction_method"] == "FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD"
        for key in ("field", "value", "unit", "period_start", "period_end", "duration_days",
                    "decision_time", "reason", "contract_version"):
            assert record[key] is not None, key
        assert len(record["components"]) == 3
        for component in record["components"]:
            for key in ("role", "sign", "fact_id", "tag", "unit", "value", "start", "end",
                        "duration_days", "duration_family", "form", "accession",
                        "acceptance_time"):
                assert component[key] is not None, key

    def test_a_fact_id_identifies_the_period_as_well_as_the_filing(self):
        components = construct_ttm(ytd_triple(), "operating_cash_flow", DECISION).components
        assert len({c.fact_id for c in components}) == 3
        current = next(c for c in components if c.role == "current_ytd")
        assert current.fact_id.endswith("2026-01-01:2026-06-30")

    def test_a_refusal_carries_no_value_and_still_says_why(self):
        record = construct_ttm([], "revenue", DECISION).to_dict()
        assert record["value"] is None and record["unit"] is None
        assert record["construction_method"] is None
        assert record["reason"]

    def test_the_same_input_gives_the_same_result_regardless_of_fact_order(self):
        rows = ytd_triple() + four_quarters()
        forward = construct_ttm_bundle(rows, DECISION, specs=VALUATION_FIELD_SPECS)
        backward = construct_ttm_bundle(list(reversed(rows)), DECISION,
                                        specs=VALUATION_FIELD_SPECS)
        assert {k: v.to_dict() for k, v in forward.items()} == \
            {k: v.to_dict() for k, v in backward.items()}


class TestEbitdaFeasibility:
    def test_feasibility_is_a_verdict_and_no_ebitda_value_is_produced(self):
        operating = construct_ttm(ytd_triple("operating_income", fy=100.0, current=50.0,
                                             prior=45.0), "operating_income", DECISION)
        da = construct_ttm(ytd_triple("depreciation_amortization", fy=40.0, current=20.0,
                                      prior=18.0), "depreciation_amortization", DECISION,
                           specs=VALUATION_FIELD_SPECS)
        assert ebitda_feasible(operating, da) is True
        assert isinstance(ebitda_feasible(operating, da), bool)

    def test_feasibility_is_false_when_the_two_legs_cover_different_periods(self):
        operating = construct_ttm(ytd_triple("operating_income", fy=100.0, current=50.0,
                                             prior=45.0), "operating_income", DECISION)
        missing = construct_ttm([], "depreciation_amortization", DECISION,
                                specs=VALUATION_FIELD_SPECS)
        assert ebitda_feasible(operating, missing) is False

    def test_no_ebitda_value_is_exported_by_this_step(self):
        import app.backtest.strategy_h_v2.valuation.ttm as module
        exported = [n for n in dir(module) if "ebitda" in n.lower()]
        assert sorted(exported) == ["EBITDA_CANDIDATE_DEFINITION", "EBITDA_DERIVED_LABEL",
                                   "EBITDA_IS_NOT_CONSTRUCTED_HERE", "ebitda_feasible"]


# ------------------------------------------------------------------------------------------
# Tolerance provenance and layer boundaries
# ------------------------------------------------------------------------------------------


class TestNoNewTolerances:
    def test_the_comparable_period_tolerances_are_h_pv2s_frozen_numbers(self):
        """§7 forbids inventing one. These are the literals inside frozen `h_pv2.comparable_pair`."""
        from pathlib import Path
        import app.backtest.strategy_h0.h_pv2 as frozen
        source = Path(frozen.__file__).read_text()
        assert f"{YEAR_APART_DAYS[0]}<=(current.end-f.end).days<={YEAR_APART_DAYS[1]}" in source
        assert (f"({COMPARABLE_FY_DURATION_TOLERANCE_DAYS} if family=='FY' else "
                f"{COMPARABLE_DURATION_TOLERANCE_DAYS})") in source

    def test_the_annual_span_window_is_the_one_p0_froze(self):
        assert ANNUAL_SPAN_DAYS == (300, 400)


class TestLayerBoundary:
    def test_this_step_produces_no_valuation_no_price_and_no_decision(self):
        from pathlib import Path
        import app.backtest.strategy_h_v2.valuation.ttm as module
        import app.backtest.strategy_h_v2.valuation.fundamental_fields as fields
        for target in (module, fields):
            source = Path(target.__file__).read_text()
            lowered = source.lower()
            for forbidden in ("target_price", "fair_value", "call_opus", "tp1", "tp2",
                              "forward_return", "realized_return"):
                assert forbidden not in lowered, (target.__name__, forbidden)

    def test_the_12_canonical_specs_are_shared_objects_so_they_cannot_drift(self):
        for name, spec in FIELD_SPECS.items():
            assert VALUATION_FIELD_SPECS[name] is spec, name

    def test_passing_no_registry_leaves_the_pre_existing_callers_unchanged(self):
        """`resolve_fact`/`extract_companyfacts` default to FIELD_SPECS, so D1-D4 see what they saw."""
        rows = four_quarters()
        assert resolve_fact(rows, "revenue", DECISION,
                            duration_family=DurationFamily.QUARTER).status is FactStatus.OK
        with pytest.raises(KeyError):
            resolve_fact(rows, "depreciation_amortization", DECISION)

    def test_historical_h_modules_are_untouched(self):
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parents[4]
        for frozen in ("h0_5.py", "h_pv1.py", "h_pv2.py", "h_pv2c.py", "h_pv3.py", "pilot.py"):
            changed = subprocess.run(
                ["git", "diff", "--name-only", "HEAD", "--",
                 f"backend/app/backtest/strategy_h0/{frozen}"],
                capture_output=True, text=True, cwd=root)
            assert changed.stdout.strip() == "", frozen


# ------------------------------------------------------------------------------------------
# L. Stored ten-issuer replay
# ------------------------------------------------------------------------------------------


def _audit():
    from app.dev.audit_strategy_h_v2_d5_p0_1 import D4_ISSUERS, audit, load_issuer
    if load_issuer("AEYE") is None:
        pytest.skip("local SEC store / D2.1 packages absent (data/runtime is gitignored)")
    return audit(D4_ISSUERS)


class TestStoredTenIssuerReplay:
    def test_the_measured_coverage_is_what_the_document_reports(self):
        report = _audit()
        assert report["issuers"] == 10
        assert {name: row["ok"] for name, row in report["coverage"].items()} == {
            "revenue": 9, "operating_income": 8, "net_income": 10, "eps_diluted": 1,
            "operating_cash_flow": 10, "capex": 8, "depreciation_amortization": 7,
            "free_cash_flow": 8,
        }

    def test_no_component_is_from_the_future_and_no_period_is_the_wrong_length(self):
        report = _audit()
        assert report["acceptance"]["future_components"] == []
        assert report["acceptance"]["wrong_period_components"] == []
        assert report["acceptance"]["nondeterministic_replays"] == []

    def test_cash_flow_fields_construct_only_through_the_ytd_path(self):
        """P0 measured no discrete second quarter on any of the ten; this is the consequence."""
        report = _audit()
        for name in ("operating_cash_flow", "capex", "free_cash_flow"):
            assert set(report["coverage"][name]["methods"]) == {
                "FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD"}, name

    def test_only_coll_closes_a_four_quarter_chain(self):
        """A 10-K reports the fiscal year, not its own fourth quarter, for nine of the ten."""
        report = _audit()
        for name in ("revenue", "operating_income", "net_income"):
            assert report["coverage"][name]["methods"].get("FOUR_DISCRETE_QUARTERS") == 1, name
        quarter_built = [row["ticker"] for row in report["rows"]
                         if row["fields"]["revenue"]["construction_method"]
                         == "FOUR_DISCRETE_QUARTERS"]
        assert quarter_built == ["COLL"]

    def test_both_constructions_agree_where_both_are_available(self):
        """COLL is the only issuer with both. They are not asserted equal by construction, so
        agreeing to the cent is evidence the YTD arithmetic is the identity it claims to be."""
        from app.dev.audit_strategy_h_v2_d5_p0_1 import _both_paths_agree
        if _both_paths_agree("COLL") is None:
            pytest.skip("local SEC store absent")
        agreement = _both_paths_agree("COLL")
        assert set(agreement) >= {"revenue", "operating_income", "net_income"}
        for name, row in agreement.items():
            assert row["four_quarters"] == row["ytd_difference"], name

    def test_the_known_aeye_cash_flow_case_now_has_a_trailing_year(self):
        """P0 left AEYE's operating cash flow labelled YTD_Q2 and unusable as a denominator."""
        from app.dev.audit_strategy_h_v2_d5_p0_1 import load_issuer
        loaded = load_issuer("AEYE")
        if loaded is None:
            pytest.skip("local SEC store absent")
        facts, cutoff = loaded
        result = construct_ttm(facts, "operating_cash_flow", cutoff,
                               specs=VALUATION_FIELD_SPECS)
        assert result.status is TtmStatus.OK
        assert result.duration_days is not None
        assert ANNUAL_SPAN_DAYS[0] <= result.duration_days <= ANNUAL_SPAN_DAYS[1]
        assert result.value == pytest.approx(4_753_000 + 2_277_000 - 1_171_000)

    def test_the_named_d_and_a_refusals_are_the_measured_ones(self):
        report = _audit()
        by_ticker = {row["ticker"]: row["fields"]["depreciation_amortization"]["status"]
                     for row in report["rows"]}
        assert by_ticker["COLL"] == TtmStatus.STALE_PERIOD.value, (
            "COLL's unified D&A tag stops in 2020; it now reports only components")
        assert by_ticker["DORM"] == TtmStatus.TAG_MISMATCH.value, (
            "DORM tags its annual D&A and its interim D&A differently")
        assert by_ticker["TG"] == TtmStatus.STALE_PERIOD.value, (
            "TG reports D&A only annually, so its newest is the fiscal year 270 days back")
