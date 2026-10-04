"""H-V2-D5-D1: the seven multiples, the readiness classification and the suitability diagnostic.

Pure tests throughout: every input is a resolution object built by hand, so what is asserted is the
contract rather than the state of `data/runtime/`. The issuers named in the test names are the ones
whose stored values the fixture numbers were taken from, so a test that stops holding points at the
real row it came from.

The numbers are the measured ones. ADBE's 250.50 x 397,500,000 and WBD's -1,272,000,000 operating
income against +5,075,000,000 of D&A are what the pilot actually ran on, and the arithmetic below is
computed independently of the module - by hand in the assertion - rather than by calling it twice.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h_v2.valuation.capital_structure import (
    MAX_INSTANT_STALENESS_DAYS,
    CapitalStructureStatus,
    DebtMethod,
    DebtResolution,
    DebtSlot,
    DebtStatus,
    EnterpriseValueResolution,
    InstantResolution,
    InstantStatus,
    NetDebtResolution,
)
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import (
    MIN_METHODS_FOR_COMPLETE,
    MethodId,
    ValuationCompleteness,
)
from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
    MarketCapResolution,
    MarketCapStatus,
)
from app.backtest.strategy_h_v2.valuation.multiples import (
    MATERIAL_NET_DEBT_SHARE_OF_EV,
    METHOD_ORDER,
    MIN_MARGIN_FOR_PRIMARY,
    MULTIPLE_SPECS,
    MULTIPLES_CONTRACT_VERSION,
    DenominatorKind,
    MethodSuitability,
    MultipleBucket,
    MultipleResult,
    MultipleStatus,
    Numerator,
    ValuationDataReadiness,
    compute_multiple,
    compute_multiples,
    d0_completeness,
    data_readiness,
    derive_ebitda,
    method_suitability,
    usable_methods,
)
from app.backtest.strategy_h_v2.valuation.ttm import (
    EBITDA_DERIVED_LABEL,
    MAX_TTM_PERIOD_AGE_DAYS,
    TtmMethod,
    TtmResult,
    TtmStatus,
)

DECISION_DATE = date(2026, 9, 16)
DECISION_TIME = datetime(2026, 9, 28, 5, 49, 37, tzinfo=timezone.utc)

#: The TTM window every fixture flow uses: ADBE's measured one, 363 days ending inside the bound.
TTM_START = date(2025, 5, 31)
TTM_END = date(2026, 5, 29)


# -------------------------------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------------------------------

def ttm(field: str, value: float | None, *, status: TtmStatus = TtmStatus.OK,
        start: date = TTM_START, end: date = TTM_END, unit: str = "USD") -> TtmResult:
    ok = status is TtmStatus.OK
    return TtmResult(field, status, DECISION_TIME, "fixture", value=value if ok else None,
                     unit=unit if ok else None,
                     method=TtmMethod.REPORTED_FISCAL_YEAR if ok else None,
                     period_start=start if ok else None, period_end=end if ok else None,
                     duration_days=(end - start).days if ok else None)


def bundle(**overrides: TtmResult) -> dict[str, TtmResult]:
    """An all-OK TTM bundle with ADBE's measured values, then the overrides."""
    base = {
        "revenue": ttm("revenue", 25_198_000_000.0),
        "operating_income": ttm("operating_income", 9_090_000_000.0),
        "net_income": ttm("net_income", 7_229_000_000.0),
        "eps_diluted": ttm("eps_diluted", 4.12, unit="USD/shares"),
        "operating_cash_flow": ttm("operating_cash_flow", 10_481_000_000.0),
        "capex": ttm("capex", 201_000_000.0),
        "free_cash_flow": ttm("free_cash_flow", 10_280_000_000.0),
        "depreciation_amortization": ttm("depreciation_amortization", 759_000_000.0),
    }
    base.update(overrides)
    return base


def fact(value: float, end: date, tag: str = "StockholdersEquity",
         unit: str = "USD") -> CanonicalFact:
    return CanonicalFact(
        field="equity", taxonomy="us-gaap", tag=tag, unit=unit, value=value, start=None, end=end,
        filed=end, accepted_at=datetime.combine(end + timedelta(days=25), datetime.min.time(),
                                                tzinfo=timezone.utc),
        accession="ACC-0000000000", form="10-Q", fiscal_year=end.year, fiscal_period="Q2",
        frame=None)


def equity(value: float = 11_518_000_000.0, *, status: InstantStatus = InstantStatus.OK,
           end: date = date(2026, 5, 29), unit: str = "USD") -> InstantResolution:
    ok = status is InstantStatus.OK
    return InstantResolution("equity", status, "fixture",
                             fact=fact(value, end, unit=unit) if ok else None,
                             period_end=end, age_days=(DECISION_DATE - end).days)


def market_cap(value: float | None = 99_573_750_000.0, *,
               status: MarketCapStatus = MarketCapStatus.OK) -> MarketCapResolution:
    ok = status is MarketCapStatus.OK
    return MarketCapResolution(status, value if ok else None, "fixture")


def ev(value: float | None = 99_456_750_000.0, *,
       status: CapitalStructureStatus = CapitalStructureStatus.OK,
       mc: MarketCapResolution | None = None,
       debt: float = 4_802_000_000.0,
       cash: float = 4_919_000_000.0) -> EnterpriseValueResolution:
    ok = status is CapitalStructureStatus.OK
    cash_leg = InstantResolution("cash_for_ev", InstantStatus.OK, "fixture",
                                 fact=fact(cash, date(2026, 5, 29),
                                           "CashAndCashEquivalentsAtCarryingValue"),
                                 period_end=date(2026, 5, 29))
    debt_leg = DebtResolution(DebtStatus.OK, "fixture", value=debt, unit="USD",
                              method=DebtMethod.REPORTED_TOTAL, period_end=date(2026, 5, 29),
                              slots={DebtSlot.REPORTED_TOTAL: InstantResolution(
                                  "debt_reported_total", InstantStatus.OK, "fixture")})
    net = NetDebtResolution(CapitalStructureStatus.OK, "fixture", value=debt - cash, unit="USD",
                            period_end=date(2026, 5, 29), cash=cash_leg, debt=debt_leg)
    return EnterpriseValueResolution(status, "fixture", DECISION_TIME, DECISION_DATE,
                                     value=value if ok else None,
                                     market_cap=mc if mc is not None else market_cap(),
                                     net_debt=net)


def run(**kwargs) -> dict[str, MultipleResult]:
    args = dict(market_cap=market_cap(), enterprise_value=ev(), ttm=bundle(), equity=equity(),
                price=250.50)
    args.update(kwargs)
    return compute_multiples(**args)


def one(method: str, **kwargs) -> MultipleResult:
    args = dict(market_cap=market_cap(), enterprise_value=ev(), ttm=bundle(), equity=equity(),
                price=250.50)
    args.update(kwargs)
    ebitda = derive_ebitda(args["ttm"].get("operating_income"),
                           args["ttm"].get("depreciation_amortization"))
    return compute_multiple(MULTIPLE_SPECS[method], ebitda=ebitda, **args)


# -------------------------------------------------------------------------------------------------
# §29. P/B
# -------------------------------------------------------------------------------------------------

def test_pb_valid_computes_market_cap_over_book_equity():
    r = one("P/B")
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(99_573_750_000.0 / 11_518_000_000.0)
    assert r.value == pytest.approx(8.6450, abs=1e-4)
    assert r.numerator_kind is Numerator.MARKET_CAP
    assert r.denominator_field == "equity"
    assert r.bucket is MultipleBucket.OK


def test_pb_negative_equity_is_not_a_negative_multiple():
    """§7. Negative book value makes the method inapplicable, not cheap."""
    r = one("P/B", equity=equity(-2_000_000_000.0))
    assert r.status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert r.value is None
    assert r.bucket is MultipleBucket.NOT_APPLICABLE
    assert "-2000000000" in r.reason.replace(".0", "")


def test_pb_zero_equity_is_not_applicable():
    r = one("P/B", equity=equity(0.0))
    assert r.status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert r.value is None


def test_pb_instant_denominator_is_not_refused_for_having_no_period_family():
    """The guard the INSTANT branch exists for: `denominator_period_valid(INSTANT)` is False, so
    running an instant denominator through the flow rule would refuse every P/B ever computed."""
    from app.backtest.strategy_h_v2.valuation.d5_d0_contract import (
        PeriodFamily,
        denominator_period_valid,
    )

    assert not denominator_period_valid(PeriodFamily.INSTANT)
    assert one("P/B").status is MultipleStatus.OK


def test_pb_rejects_an_equity_fact_in_the_wrong_unit():
    """The unit is read off the resolved fact, so the compatibility check can fail on this branch."""
    r = one("P/B", equity=equity(unit="USD/shares"))
    assert r.status is MultipleStatus.UNKNOWN
    assert r.value is None
    assert "USD/shares" in r.reason


# -------------------------------------------------------------------------------------------------
# §29. P/FCF
# -------------------------------------------------------------------------------------------------

def test_p_fcf_positive():
    r = one("P/FCF")
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(99_573_750_000.0 / 10_280_000_000.0)
    assert r.value == pytest.approx(9.6861, abs=1e-4)


def test_p_fcf_negative_is_not_applicable():
    """§8, and COHR's measured case: OCF 79,514k less capex 1,102,909k is -1,023,395k."""
    r = one("P/FCF", ttm=bundle(free_cash_flow=ttm("free_cash_flow", -1_023_395_000.0)))
    assert r.status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert r.value is None
    assert r.bucket is MultipleBucket.NOT_APPLICABLE
    assert r.denominator_value == -1_023_395_000.0


def test_p_fcf_zero_is_not_applicable():
    r = one("P/FCF", ttm=bundle(free_cash_flow=ttm("free_cash_flow", 0.0)))
    assert r.status is MultipleStatus.NEGATIVE_DENOMINATOR


# -------------------------------------------------------------------------------------------------
# §29. EV/Sales, EV/EBIT, EV/FCF
# -------------------------------------------------------------------------------------------------

def test_ev_sales():
    r = one("EV/Sales")
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(99_456_750_000.0 / 25_198_000_000.0)
    assert r.value == pytest.approx(3.9470, abs=1e-4)
    assert r.numerator_kind is Numerator.ENTERPRISE_VALUE


def test_ev_ebit_positive():
    r = one("EV/EBIT")
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(99_456_750_000.0 / 9_090_000_000.0)
    assert r.value == pytest.approx(10.9413, abs=1e-4)


def test_ev_ebit_negative_operating_income_is_not_applicable():
    """§10 and WBD's measured -1,272,000,000: no loss company gets a negative multiple."""
    r = one("EV/EBIT", ttm=bundle(operating_income=ttm("operating_income", -1_272_000_000.0)))
    assert r.status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert r.value is None


def test_ev_fcf():
    r = one("EV/FCF")
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(99_456_750_000.0 / 10_280_000_000.0)


def test_ev_fcf_has_no_d5_d0_method_spec_and_says_so():
    """It was introduced by P1's `EV_METHOD_DENOMINATORS`; D5-D0's frozen twelve do not include it.
    The divergence is recorded on the spec rather than patched into the frozen tuple."""
    assert MULTIPLE_SPECS["EV/FCF"].d0_method is None
    assert all(MULTIPLE_SPECS[m].d0_method is not None for m in METHOD_ORDER if m != "EV/FCF")


# -------------------------------------------------------------------------------------------------
# §29. Derived EBITDA and EV/EBITDA
# -------------------------------------------------------------------------------------------------

def test_derived_ebitda_is_operating_income_plus_da_over_one_period():
    e = derive_ebitda(ttm("operating_income", 9_090_000_000.0),
                      ttm("depreciation_amortization", 759_000_000.0))
    assert e.status is MultipleStatus.OK
    assert e.value == 9_090_000_000.0 + 759_000_000.0 == 9_849_000_000.0
    assert e.provenance == EBITDA_DERIVED_LABEL
    assert (e.period_start, e.period_end) == (TTM_START, TTM_END)


def test_derived_ebitda_can_be_positive_while_ebit_is_negative():
    """WBD, measured: EBIT -1,272,000,000 and D&A +5,075,000,000 give EBITDA +3,803,000,000. The two
    methods must disagree about applicability, and neither may produce a negative multiple."""
    losing = bundle(operating_income=ttm("operating_income", -1_272_000_000.0),
                    depreciation_amortization=ttm("depreciation_amortization", 5_075_000_000.0))
    e = derive_ebitda(losing["operating_income"], losing["depreciation_amortization"])
    assert e.value == 3_803_000_000.0
    assert one("EV/EBIT", ttm=losing).status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert one("EV/EBITDA", ttm=losing).status is MultipleStatus.OK


def test_derived_ebitda_refuses_two_different_periods():
    """§11. EBITDA is one period or it is nothing."""
    e = derive_ebitda(ttm("operating_income", 9_090_000_000.0),
                      ttm("depreciation_amortization", 759_000_000.0,
                          start=date(2025, 6, 30), end=date(2026, 6, 30)))
    assert e.status is MultipleStatus.PERIOD_MISMATCH
    assert e.value is None
    assert one("EV/EBITDA", ttm=bundle(depreciation_amortization=ttm(
        "depreciation_amortization", 759_000_000.0,
        start=date(2025, 6, 30), end=date(2026, 6, 30)))).status is MultipleStatus.PERIOD_MISMATCH


def test_derived_ebitda_inherits_a_leg_refusal_by_name():
    """COHR, measured: `OperatingIncomeLoss` ends 2024-06-30, so EBITDA is stale, not missing."""
    e = derive_ebitda(ttm("operating_income", None, status=TtmStatus.STALE_PERIOD),
                      ttm("depreciation_amortization", 521_895_000.0))
    assert e.status is MultipleStatus.STALE_INPUT
    assert "STALE_PERIOD" in e.reason


def test_ev_ebitda_is_labelled_derived_not_reported():
    r = one("EV/EBITDA")
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(99_456_750_000.0 / 9_849_000_000.0)
    assert r.denominator_provenance == EBITDA_DERIVED_LABEL
    assert MULTIPLE_SPECS["EV/EBITDA"].denominator_kind is DenominatorKind.DERIVED


def test_ebitda_multiple_is_below_ebit_multiple_when_both_compute():
    """A sanity identity rather than a formula: D&A is positive, so EBITDA exceeds EBIT and the
    multiple on it must be the smaller one. It catches an add/subtract inversion in one line."""
    assert one("EV/EBITDA").value < one("EV/EBIT").value


# -------------------------------------------------------------------------------------------------
# §29. P/E
# -------------------------------------------------------------------------------------------------

def test_pe_is_price_over_eps_not_market_cap_over_eps():
    """COHR, measured: 289.93 / 4.12 = 70.37. Market cap over EPS is a share count."""
    r = one("P/E", market_cap=market_cap(56_777_643_082.78), price=289.93)
    assert r.status is MultipleStatus.OK
    assert r.value == pytest.approx(289.93 / 4.12)
    assert r.value == pytest.approx(70.3714, abs=1e-4)
    assert r.numerator_kind is Numerator.PRICE_PER_SHARE
    assert r.numerator_value == 289.93
    assert r.value < 1_000  # a market-cap numerator would be ten orders of magnitude larger


def test_pe_unavailable_when_eps_does_not_resolve():
    """§13, and ADBE/WBD/NATR measured: only 2 of 4 adjacent quarters are reported, and the
    YTD-difference path is refused for EPS, so TTM EPS is MISSING_COMPONENT. Net income resolving
    does NOT open P/E - reconstructing EPS from it is forbidden."""
    r = one("P/E", ttm=bundle(eps_diluted=ttm("eps_diluted", None,
                                              status=TtmStatus.MISSING_COMPONENT,
                                              unit="USD/shares")))
    assert r.status is MultipleStatus.MISSING_INPUT
    assert r.value is None
    assert bundle()["net_income"].ok  # available, and deliberately unused


def test_pe_units_must_be_per_share_on_both_sides():
    """A USD-denominated denominator in the EPS slot is a unit error, not a multiple."""
    r = one("P/E", ttm=bundle(eps_diluted=ttm("eps_diluted", 4.12, unit="USD")))
    assert r.status is MultipleStatus.UNKNOWN
    assert "USD/shares" in r.reason


def test_pe_is_gated_on_market_cap_and_the_cost_is_recorded():
    """`PE_IS_PRICE_OVER_EPS`: stricter than price/EPS needs, deliberately, and fail-closed."""
    from app.backtest.strategy_h_v2.valuation.multiples import PE_IS_PRICE_OVER_EPS

    r = one("P/E", market_cap=market_cap(None, status=MarketCapStatus.UNKNOWN_SHARES))
    assert r.status is MultipleStatus.MARKET_CAP_UNAVAILABLE
    assert "LOWER bound" in PE_IS_PRICE_OVER_EPS


# -------------------------------------------------------------------------------------------------
# §29. Stale and future input rejection
# -------------------------------------------------------------------------------------------------

def test_stale_ttm_period_is_rejected_by_name():
    r = one("EV/Sales", ttm=bundle(revenue=ttm("revenue", None, status=TtmStatus.STALE_PERIOD)))
    assert r.status is MultipleStatus.STALE_INPUT
    assert r.value is None
    assert r.bucket is MultipleBucket.MISSING


def test_stale_instant_equity_is_rejected_by_name():
    r = one("P/B", equity=equity(status=InstantStatus.STALE, end=date(2024, 3, 31)))
    assert r.status is MultipleStatus.STALE_INPUT
    assert r.value is None


def test_stale_debt_refuses_every_ev_method_and_the_stale_figure_is_never_used():
    """NATR, measured: the newest borrowing balance ends 2024-03-31, 899 days out. Its EV must be
    refused rather than built from the stale figure, and that refusal must reach all four EV
    methods."""
    blocked = ev(None, status=CapitalStructureStatus.STALE_DEBT)
    results = run(enterprise_value=blocked)
    for method in ("EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF"):
        assert results[method].status is MultipleStatus.STALE_INPUT
        assert results[method].value is None
        assert results[method].denominator_value is None
    # And the equity-side methods, which do not touch debt, still compute.
    assert results["P/B"].ok and results["P/FCF"].ok


def test_a_future_dated_input_cannot_reach_a_multiple():
    """The primitives refuse a period end after the decision date before this module sees it, so the
    assertion here is that the refusal propagates by name rather than being re-derived."""
    future = ttm("revenue", None, status=TtmStatus.MISSING_COMPONENT)
    r = one("EV/Sales", ttm=bundle(revenue=future))
    assert r.status is MultipleStatus.MISSING_INPUT
    assert r.value is None


def test_partial_period_denominator_is_refused():
    """A six-month revenue is a number that looks like a denominator and is not one. The frozen
    `denominator_applicable` is what refuses it, via the period family."""
    half = TtmResult("revenue", TtmStatus.OK, DECISION_TIME, "fixture", value=12_000_000_000.0,
                     unit="USD", method=TtmMethod.REPORTED_FISCAL_YEAR,
                     period_start=date(2025, 12, 1), period_end=TTM_END, duration_days=179)
    # The period family is MEASURED from the denominator's own dates, so the frozen rule fires here
    # rather than trusting P0.1's OK to mean "twelve months".
    refused = one("EV/Sales", ttm=bundle(revenue=half))
    assert refused.status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert refused.value is None
    assert "YTD" in refused.reason
    # The runner's independent span audit catches the same shape from the stored record.
    from app.dev.run_strategy_h_v2_d5_d1 import audit

    forged = dict(one("EV/Sales").to_dict(), denominator_period_start="2025-12-01",
                  denominator_period_end=TTM_END.isoformat())
    row = {"ticker": "FIXTURE", "multiples": {"EV/Sales": forged},
           "inputs": {"pit_shares": {}, "price": {"period": DECISION_DATE.isoformat()},
                      "cash": {"status": "MISSING", "freshness_days": None},
                      "book_equity": {"status": "MISSING", "freshness_days": None},
                      "enterprise_value": {"status": "MISSING", "components_sum": None}}}
    assert audit([row], DECISION_DATE)["denominator_period_not_a_full_year"] == [
        "FIXTURE/EV/Sales: denominator spans 179 days"]


def test_an_unclassifiable_denominator_period_refuses():
    """A flow with no dates cannot be shown to cover a year, so it is not a denominator."""
    undated = TtmResult("revenue", TtmStatus.OK, DECISION_TIME, "fixture", value=1.0, unit="USD",
                        method=TtmMethod.REPORTED_FISCAL_YEAR)
    r = one("EV/Sales", ttm=bundle(revenue=undated))
    assert r.status is MultipleStatus.NEGATIVE_DENOMINATOR
    assert "UNKNOWN" in r.reason


# -------------------------------------------------------------------------------------------------
# §29. Numerator refusals keep their own names
# -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED, MultipleStatus.MULTI_CLASS_UNRESOLVED),
    (MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES, MultipleStatus.MULTI_CLASS_UNRESOLVED),
    (MarketCapStatus.UNKNOWN_SHARES, MultipleStatus.MARKET_CAP_UNAVAILABLE),
    (MarketCapStatus.UNKNOWN_PRICE, MultipleStatus.MARKET_CAP_UNAVAILABLE),
])
def test_market_cap_refusal_reaches_the_multiple_under_its_own_name(status, expected):
    r = one("P/B", market_cap=market_cap(None, status=status))
    assert r.status is expected
    assert r.value is None


@pytest.mark.parametrize("status,expected", [
    (CapitalStructureStatus.MULTI_CLASS_UNKNOWN, MultipleStatus.MULTI_CLASS_UNRESOLVED),
    (CapitalStructureStatus.MARKET_CAP_UNKNOWN, MultipleStatus.MARKET_CAP_UNAVAILABLE),
    (CapitalStructureStatus.MISSING_CASH, MultipleStatus.MISSING_INPUT),
    (CapitalStructureStatus.MISSING_DEBT, MultipleStatus.MISSING_INPUT),
    (CapitalStructureStatus.STALE_CASH, MultipleStatus.STALE_INPUT),
    (CapitalStructureStatus.STALE_DEBT, MultipleStatus.STALE_INPUT),
    (CapitalStructureStatus.INCOMPLETE_DEBT_COMPONENTS, MultipleStatus.MISSING_INPUT),
    (CapitalStructureStatus.AMBIGUOUS_DEBT_TAGS, MultipleStatus.UNKNOWN),
    (CapitalStructureStatus.AMBIGUOUS_CASH_TAGS, MultipleStatus.UNKNOWN),
    (CapitalStructureStatus.COMPONENT_DATE_MISMATCH, MultipleStatus.PERIOD_MISMATCH),
    (CapitalStructureStatus.UNKNOWN, MultipleStatus.EV_UNAVAILABLE),
])
def test_ev_refusal_reaches_the_multiple_under_the_name_of_the_leg_that_caused_it(status, expected):
    r = one("EV/Sales", enterprise_value=ev(None, status=status))
    assert r.status is expected


def test_every_refusal_enum_member_is_mapped():
    """Totality, not coverage: an unmapped status falls back to a refusal, and this asserts that no
    fallback is being exercised silently - every member of each source enum has an explicit row."""
    from app.backtest.strategy_h_v2.valuation.multiples import (
        _EV_REFUSALS,
        _INSTANT_REFUSALS,
        _MARKET_CAP_REFUSALS,
        _TTM_REFUSALS,
    )

    assert set(_MARKET_CAP_REFUSALS) == set(MarketCapStatus) - {MarketCapStatus.OK}
    assert set(_EV_REFUSALS) == set(CapitalStructureStatus) - {CapitalStructureStatus.OK}
    assert set(_TTM_REFUSALS) == set(TtmStatus) - {TtmStatus.OK}
    assert set(_INSTANT_REFUSALS) == set(InstantStatus) - {InstantStatus.OK}


def test_every_status_has_exactly_one_bucket():
    from app.backtest.strategy_h_v2.valuation.multiples import _BUCKETS

    assert set(_BUCKETS) == set(MultipleStatus)
    assert _BUCKETS[MultipleStatus.NEGATIVE_DENOMINATOR] is MultipleBucket.NOT_APPLICABLE


# -------------------------------------------------------------------------------------------------
# §29. Deterministic arithmetic, and the no-silent-number invariant
# -------------------------------------------------------------------------------------------------

def test_deterministic_arithmetic_to_the_bit():
    first, second = run(), run()
    for method in METHOD_ORDER:
        assert first[method].value == second[method].value
        assert first[method].status is second[method].status
        assert first[method].to_dict() == second[method].to_dict()


def test_every_published_multiple_equals_its_own_published_inputs():
    for method, r in run().items():
        if r.ok:
            assert r.numerator_value / r.denominator_value == r.value, method


def test_a_refusal_cannot_carry_a_value_and_an_ok_cannot_lack_one():
    with pytest.raises(AssertionError):
        MultipleResult("P/B", MultipleStatus.MISSING_INPUT, "fixture", value=1.0)
    with pytest.raises(AssertionError):
        MultipleResult("P/B", MultipleStatus.OK, "fixture", value=None)


def test_no_published_multiple_is_negative_or_zero():
    for method, r in run(ttm=bundle(
            operating_income=ttm("operating_income", -1_272_000_000.0),
            free_cash_flow=ttm("free_cash_flow", -1_023_395_000.0)),
            equity=equity(-2_000_000_000.0)).items():
        assert r.value is None or r.value > 0, method


def test_contract_version_is_stamped_on_every_result():
    assert all(r.to_dict()["contract_version"] == MULTIPLES_CONTRACT_VERSION
               for r in run().values())


# -------------------------------------------------------------------------------------------------
# §29. Company readiness
# -------------------------------------------------------------------------------------------------

def test_zero_method_issuer_is_not_ready():
    """DALN/FET/CHWY/BRY/FULT, measured: no market cap means no numerator for any of the seven."""
    results = run(market_cap=market_cap(None, status=MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED),
                  enterprise_value=ev(None, status=CapitalStructureStatus.MULTI_CLASS_UNKNOWN))
    assert usable_methods(results) == ()
    assert data_readiness(0) is ValuationDataReadiness.NOT_READY
    assert d0_completeness(market_cap_valid=False, usable_method_count=0) \
        is ValuationCompleteness.NOT_READY


def test_one_method_issuer_is_ready_with_limitations():
    """GNW/CHRS/STAA, measured: market cap and book equity resolve and nothing else does, so P/B
    alone is the whole valuation - which is exactly the case D5-D0 says cannot be cross-checked."""
    blocked = {f: ttm(f, None, status=TtmStatus.MISSING_COMPONENT) for f in
               ("revenue", "operating_income", "net_income", "operating_cash_flow", "capex",
                "free_cash_flow", "depreciation_amortization")}
    blocked["eps_diluted"] = ttm("eps_diluted", None, status=TtmStatus.MISSING_COMPONENT,
                                 unit="USD/shares")
    results = run(ttm=bundle(**blocked),
                  enterprise_value=ev(None, status=CapitalStructureStatus.MISSING_DEBT))
    assert usable_methods(results) == ("P/B",)
    assert data_readiness(1) is ValuationDataReadiness.READY_WITH_LIMITATIONS
    assert d0_completeness(market_cap_valid=True, usable_method_count=1) \
        is ValuationCompleteness.PARTIAL


def test_two_or_more_methods_is_ready_and_the_boundary_is_the_frozen_constant():
    assert data_readiness(MIN_METHODS_FOR_COMPLETE) is ValuationDataReadiness.READY
    assert data_readiness(MIN_METHODS_FOR_COMPLETE - 1) \
        is ValuationDataReadiness.READY_WITH_LIMITATIONS
    assert MIN_METHODS_FOR_COMPLETE == 2
    # The all-OK fixture resolves all seven. ADBE, measured, resolves six: its TTM EPS is
    # MISSING_COMPONENT, which `test_pe_unavailable_when_eps_does_not_resolve` covers.
    assert len(usable_methods(run())) == 7


def test_d0_complete_is_unreachable_at_this_stage_and_that_is_the_stage_not_the_data():
    """§4 forbids scenarios here, and D5-D0 requires a provenanced scenario set for COMPLETE. So
    every issuer is PARTIAL at best today, and conflating that with the data readiness would read a
    staging constraint as a defect."""
    assert d0_completeness(market_cap_valid=True, usable_method_count=7) \
        is ValuationCompleteness.PARTIAL
    assert data_readiness(7) is ValuationDataReadiness.READY


def test_usable_methods_is_ordered_and_counts_secondary_methods_too():
    assert usable_methods(run()) == METHOD_ORDER
    # Order is METHOD_ORDER's, not the dict's insertion order or a sort of the method names.
    dropped = run(ttm=bundle(revenue=ttm("revenue", None, status=TtmStatus.MISSING_COMPONENT)))
    assert usable_methods(dropped) == tuple(m for m in METHOD_ORDER if m != "EV/Sales")


# -------------------------------------------------------------------------------------------------
# §29. Suitability is a diagnostic and never a gate
# -------------------------------------------------------------------------------------------------

def test_pb_is_never_promoted_by_code():
    label, reason = method_suitability("P/B", net_debt=0.0, enterprise_value=1.0,
                                       ttm_revenue=25_198_000_000.0,
                                       ttm_operating_income=9_090_000_000.0)
    assert label is MethodSuitability.SECONDARY_ONLY
    assert "argued per issuer" in reason


def test_ev_method_is_secondary_when_net_debt_is_immaterial():
    """ADBE, measured: net debt -117,000,000 against an EV of 99,456,750,000 is 0.1%, so EV/Sales
    repeats what P/Sales would have said."""
    label, reason = method_suitability("EV/Sales", net_debt=-117_000_000.0,
                                       enterprise_value=99_456_750_000.0,
                                       ttm_revenue=25_198_000_000.0,
                                       ttm_operating_income=9_090_000_000.0)
    assert label is MethodSuitability.SECONDARY_ONLY
    assert "materiality" in reason
    assert abs(-117_000_000.0) / 99_456_750_000.0 < MATERIAL_NET_DEBT_SHARE_OF_EV


def test_ev_sales_is_reasonable_for_a_thin_margin_issuer_carrying_real_debt():
    """WBD, measured: EBIT -1,272,000,000 on 36,115,000,000 of revenue, net debt 29% of EV."""
    label, _ = method_suitability("EV/Sales", net_debt=28_654_000_000.0,
                                  enterprise_value=99_129_442_023.98,
                                  ttm_revenue=36_115_000_000.0,
                                  ttm_operating_income=-1_272_000_000.0)
    assert label is MethodSuitability.ECONOMICALLY_REASONABLE


def test_ev_sales_is_secondary_for_a_profitable_issuer():
    label, reason = method_suitability("EV/Sales", net_debt=28_654_000_000.0,
                                       enterprise_value=99_129_442_023.98,
                                       ttm_revenue=25_198_000_000.0,
                                       ttm_operating_income=9_090_000_000.0)
    assert label is MethodSuitability.SECONDARY_ONLY
    assert "only method for a mature profitable issuer" in reason


def test_margin_sensitive_method_is_secondary_on_a_razor_thin_margin():
    thin = MIN_MARGIN_FOR_PRIMARY / 2
    label, _ = method_suitability("EV/EBIT", net_debt=28_654_000_000.0,
                                  enterprise_value=99_129_442_023.98,
                                  ttm_revenue=1_000_000_000.0,
                                  ttm_operating_income=1_000_000_000.0 * thin)
    assert label is MethodSuitability.SECONDARY_ONLY


def test_suitability_is_data_applicable_when_the_margin_regime_is_unmeasurable():
    label, reason = method_suitability("P/FCF", net_debt=None, enterprise_value=None,
                                       ttm_revenue=None, ttm_operating_income=None)
    assert label is MethodSuitability.DATA_APPLICABLE
    assert "no economic judgement" in reason


def test_code_never_returns_not_suitable():
    """`NOT_SUITABLE_IS_AI_OWNED`: declining to promote a method and asserting it is wrong are
    different claims, and only the first is supportable from a balance sheet."""
    from app.backtest.strategy_h_v2.valuation.multiples import NOT_SUITABLE_IS_AI_OWNED

    cases = [(m, nd, e, rev, oi)
             for m in METHOD_ORDER
             for nd in (None, 0.0, -117_000_000.0, 28_654_000_000.0)
             for e in (None, 99_456_750_000.0)
             for rev in (None, 0.0, 1_000_000_000.0, 36_115_000_000.0)
             for oi in (None, -1_272_000_000.0, 0.0, 9_090_000_000.0)]
    labels = {method_suitability(m, net_debt=nd, enterprise_value=e, ttm_revenue=rev,
                                 ttm_operating_income=oi)[0] for m, nd, e, rev, oi in cases}
    assert MethodSuitability.NOT_SUITABLE not in labels
    assert "never returns it" in NOT_SUITABLE_IS_AI_OWNED


def test_suitability_does_not_subtract_from_readiness():
    """§17's prohibition, asserted: every method that produced a number counts toward readiness,
    SECONDARY_ONLY included. A diagnostic that moved coverage would be choosing methods by result."""
    results = run()
    labels = {m: method_suitability(m, net_debt=-117_000_000.0,
                                    enterprise_value=99_456_750_000.0,
                                    ttm_revenue=25_198_000_000.0,
                                    ttm_operating_income=9_090_000_000.0)[0]
              for m in usable_methods(results)}
    assert MethodSuitability.SECONDARY_ONLY in labels.values()
    assert len(usable_methods(results)) == len(METHOD_ORDER)


# -------------------------------------------------------------------------------------------------
# §4. What this step must not produce
# -------------------------------------------------------------------------------------------------

def test_no_fair_value_or_target_price_field_exists_anywhere():
    """Structural, not conventional: there is no field a fair value or a target price could occupy."""
    import dataclasses

    from app.backtest.strategy_h_v2.valuation import multiples

    forbidden = ("fair_value", "target_price", "tp1", "tp2", "scenario", "bear", "base", "bull",
                 "verdict", "recommendation", "upside", "forward")
    for name in dir(multiples):
        obj = getattr(multiples, name)
        if dataclasses.is_dataclass(obj) and isinstance(obj, type):
            fields = {f.name for f in dataclasses.fields(obj)}
            assert not fields & set(forbidden), (name, fields & set(forbidden))


def test_the_method_table_is_the_seven_the_brief_named_in_its_order():
    assert METHOD_ORDER == ("P/B", "P/FCF", "EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF", "P/E")
    assert tuple(MULTIPLE_SPECS) == METHOD_ORDER
    assert {MULTIPLE_SPECS[m].d0_method for m in METHOD_ORDER if
            MULTIPLE_SPECS[m].d0_method is not None} == {
        MethodId.PB, MethodId.P_FCF, MethodId.EV_SALES, MethodId.EV_EBIT, MethodId.EV_EBITDA,
        MethodId.PE}


def test_bounds_are_imported_from_the_primitives_rather_than_restated():
    assert MAX_TTM_PERIOD_AGE_DAYS == MAX_INSTANT_STALENESS_DAYS == 135
