"""HYBRID-S session validation, halt inference, research scope, corporate actions, spread inputs."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.strategy_b.config import (
    CorporateActionConfig, FeatureConfig, HaltInferenceConfig, ScopeConfig, SparseValidationConfig,
    StrategyBConfig,
)
from app.strategy_b.corporate_actions import (
    DelistingNotice, PriorSessionAudit, SymbolChange, derive_corporate_action_flags,
)
from app.strategy_b.errors import PointInTimeViolation
from app.strategy_b.halt_inference import HaltGapEvidence, infer_halt, judge_gap
from app.strategy_b.models import (
    CorporateActionFlag, HaltStatus, Measured, OfficialDailyBar, SessionVerdict, TapeDensity,
)
from app.strategy_b.scope import (
    ListingStatus, PriorDailyBar, ScopeExclusion, ScopeInputs, TickerMetadataAsOf,
    evaluate_research_scope, evaluate_scope_batch,
)
from app.strategy_b.snapshot import compute_feature_snapshot
from app.strategy_b.sparse_session import ValidationFinding, validate_sparse_session
from app.strategy_b.split_adjustment import SplitRecord
from app.strategy_b.spread import spread_feature_input, spread_features
from tests.strategy_b.fixtures import (
    D, SYMBOL, boundaries, delisting_window, et, halt_like_gap_session, ipo_short_history, make_bar,
    make_tape, reverse_split_day, scope_inputs, sparse_low_liquidity_session,
)


SPARSE_CONFIG = SparseValidationConfig()


def sparse_daily(**overrides) -> OfficialDailyBar:
    # Minute tape: open 9.90, high 11.20, low 9.80, volume 600. Official close differs.
    values = dict(session_date=D, open=9.90, high=11.20, low=9.80, close=10.40, volume=700.0)
    return OfficialDailyBar(**{**values, **overrides})


# ---- sparse session validation ---------------------------------------------------------

def test_sparse_tape_matching_the_daily_bar_is_verified_despite_missing_minutes() -> None:
    result = validate_sparse_session(sparse_low_liquidity_session(), sparse_daily(), boundaries(),
                                     config=SPARSE_CONFIG)
    assert result.verdict is SessionVerdict.VERIFIED_SPARSE
    assert result.missing_minute_ratio == pytest.approx(1 - 3 / 390)
    assert result.volume_coverage == pytest.approx(600 / 700)


def test_validator_orders_bars_and_refuses_duplicates() -> None:
    bars = sparse_low_liquidity_session()
    shuffled = validate_sparse_session(list(reversed(bars)), sparse_daily(), boundaries(), config=SPARSE_CONFIG)
    assert shuffled.verdict is SessionVerdict.VERIFIED_SPARSE
    with pytest.raises(ValueError, match="repeat"):
        validate_sparse_session(bars + bars[:1], sparse_daily(), boundaries(), config=SPARSE_CONFIG)


def test_close_mismatch_alone_is_not_a_finding() -> None:
    result = validate_sparse_session(sparse_low_liquidity_session(), sparse_daily(close=2.80),
                                     boundaries(), config=SPARSE_CONFIG)
    assert result.verdict is SessionVerdict.VERIFIED_SPARSE


def test_empty_session_and_lost_minute_tape() -> None:
    empty = validate_sparse_session([], sparse_daily(volume=0.0), boundaries(), config=SPARSE_CONFIG)
    assert empty.verdict is SessionVerdict.EMPTY_SESSION
    lost = validate_sparse_session([], sparse_daily(), boundaries(), config=SPARSE_CONFIG)
    assert (lost.verdict, lost.findings) == (SessionVerdict.API_LOSS_SUSPECT, (ValidationFinding.MINUTE_TAPE_EMPTY,))


@pytest.mark.parametrize("override, finding", [
    ({"open": 9.95}, ValidationFinding.OPEN_MISMATCH),
    ({"high": 11.50}, ValidationFinding.HIGH_MISMATCH),
    ({"low": 9.50}, ValidationFinding.LOW_MISMATCH),
    ({"volume": 500.0}, ValidationFinding.VOLUME_EXCEEDS_DAILY),
])
def test_price_or_volume_mismatch_without_split_is_api_loss_suspect(override: dict, finding) -> None:
    result = validate_sparse_session(sparse_low_liquidity_session(), sparse_daily(**override),
                                     boundaries(), config=SPARSE_CONFIG)
    assert result.verdict is SessionVerdict.API_LOSS_SUSPECT
    assert finding in result.findings


def test_optional_volume_coverage_floor() -> None:
    config = SparseValidationConfig(min_volume_coverage=0.9)
    result = validate_sparse_session(sparse_low_liquidity_session(), sparse_daily(), boundaries(), config=config)
    assert result.findings == (ValidationFinding.VOLUME_COVERAGE_LOW,)


def test_split_day_mismatch_is_corporate_action_suspect() -> None:
    case = reverse_split_day()
    clean = validate_sparse_session(case.bars, case.daily, boundaries(), splits=[case.split], config=SPARSE_CONFIG)
    assert clean.verdict is SessionVerdict.VERIFIED_SPARSE and clean.split_on_day
    # A daily bar still on the pre-split basis: every price 10x lower than the minute tape.
    stale = OfficialDailyBar(D, open=0.51, high=0.54, low=0.50, close=0.518, volume=40_000.0)
    result = validate_sparse_session(case.bars, stale, boundaries(), splits=[case.split], config=SPARSE_CONFIG)
    assert result.verdict is SessionVerdict.CORPORATE_ACTION_SUSPECT
    assert ValidationFinding.MATCHES_SPLIT_RATIO in result.findings


def test_split_ratio_match_on_another_day_is_corporate_action_suspect() -> None:
    bars = sparse_low_liquidity_session()
    adjusted_daily = sparse_daily(open=4.95, high=5.60, low=4.90)
    split = SplitRecord(D + timedelta(days=30), split_from=1, split_to=2)
    result = validate_sparse_session(bars, adjusted_daily, boundaries(), splits=[split], config=SPARSE_CONFIG)
    assert result.verdict is SessionVerdict.CORPORATE_ACTION_SUSPECT


# ---- halt inference --------------------------------------------------------------------

def test_halt_like_gap_is_inferred_only_after_the_reopening_bar_is_available() -> None:
    tape = make_tape(halt_like_gap_session())
    config = HaltInferenceConfig()
    assert infer_halt(tape, et(9, 43), config).status is HaltStatus.NO_HALT_SIGNAL
    in_gap = infer_halt(tape, et(9, 45, 30), config)
    assert (in_gap.status, in_gap.open_gap_minutes) == (HaltStatus.UNKNOWN, 5)
    assert infer_halt(tape, et(9, 45, 59, 999999), config).status is HaltStatus.UNKNOWN
    after = infer_halt(tape, et(9, 46), config)
    assert (after.status, after.last_inferred_gap_end) == (HaltStatus.HALT_INFERRED, et(9, 45))
    assert infer_halt(tape, et(9, 55), config).status is HaltStatus.HALT_INFERRED


def test_long_illiquid_silence_is_not_a_halt() -> None:
    bars = [make_bar(9, 30, 10.0, 100.0), make_bar(9, 31, 10.0, 100.0), make_bar(10, 5, 12.0, 900.0)]
    tape = make_tape(bars)
    assert infer_halt(tape, et(10, 7), HaltInferenceConfig()).status is HaltStatus.NO_HALT_SIGNAL
    assert infer_halt(tape, et(9, 50), HaltInferenceConfig()).status is HaltStatus.NO_HALT_SIGNAL
    # Five silent minutes after the 10:05 print could be a halt in progress: UNKNOWN, not "no halt".
    assert infer_halt(tape, et(10, 10), HaltInferenceConfig()).open_gap_minutes == 5
    assert infer_halt(tape, et(10, 10), HaltInferenceConfig()).status is HaltStatus.UNKNOWN


def test_quiet_reopen_is_not_a_halt_and_no_session_bars_is_unknown() -> None:
    bars = [make_bar(9, m, 10.0, 1000.0) for m in range(30, 35)] + [make_bar(9, 40, 10.05, 900.0)]
    tape = make_tape(bars)
    assert infer_halt(tape, et(9, 42), HaltInferenceConfig()).status is HaltStatus.NO_HALT_SIGNAL
    assert infer_halt(tape, et(9, 30, 30), HaltInferenceConfig()).status is HaltStatus.UNKNOWN


def test_judge_gap_contract() -> None:
    config = HaltInferenceConfig()
    assert judge_gap(HaltGapEvidence(5, 12.0, 5000, 1000), config) is HaltStatus.HALT_INFERRED
    assert judge_gap(HaltGapEvidence(5, -12.0, 5000, 1000), config) is HaltStatus.HALT_INFERRED
    assert judge_gap(HaltGapEvidence(5, 12.0, 5000, None), config) is HaltStatus.UNKNOWN
    assert judge_gap(HaltGapEvidence(4, 12.0, 5000, 1000), config) is HaltStatus.NO_HALT_SIGNAL
    assert judge_gap(HaltGapEvidence(11, 12.0, 5000, 1000), config) is HaltStatus.NO_HALT_SIGNAL
    assert judge_gap(HaltGapEvidence(5, 12.0, 1500, 1000), config) is HaltStatus.NO_HALT_SIGNAL


def test_unknown_is_its_own_halt_answer() -> None:
    assert len({HaltStatus.HALT_INFERRED, HaltStatus.NO_HALT_SIGNAL, HaltStatus.UNKNOWN}) == 3
    assert HaltStatus.UNKNOWN not in (HaltStatus.HALT_INFERRED, HaltStatus.NO_HALT_SIGNAL)
    assert HaltStatus.UNKNOWN.value not in ("HALT_INFERRED", "NO_HALT_SIGNAL")
    # The same tape moves UNKNOWN -> HALT_INFERRED once the reopening bar arrives, so UNKNOWN
    # was not an early "no halt", and a first-bar-only session is UNKNOWN, not "halted".
    tape = make_tape(halt_like_gap_session())
    config = HaltInferenceConfig()
    assert [infer_halt(tape, et(9, m, s), config).status for m, s in ((45, 30), (46, 0))] == [
        HaltStatus.UNKNOWN, HaltStatus.HALT_INFERRED]


def test_halt_inference_does_not_judge_liquidity() -> None:
    config = HaltInferenceConfig()
    # A few shares still count: the rule compares the reopening bar to the same tape's own past.
    assert judge_gap(HaltGapEvidence(5, 12.0, 5.0, 1.0), config) is HaltStatus.HALT_INFERRED
    assert judge_gap(HaltGapEvidence(5, 1.0, 5.0, 1.0), config) is HaltStatus.NO_HALT_SIGNAL


def test_halt_unknown_and_low_liquidity_are_reported_side_by_side() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    strategy = StrategyBConfig()
    as_of = et(9, 45, 30)
    snapshot = compute_feature_snapshot(tape, as_of, strategy)
    assert snapshot.halt_inferred is HaltStatus.UNKNOWN
    assert infer_halt(tape, as_of, strategy.halt).open_gap_minutes == 6
    assert snapshot.rolling_dollar_volume == Measured.of(0.0)
    assert snapshot.cumulative_dollar_volume == Measured.of(6400.0)
    assert snapshot.cumulative_dollar_volume.value < strategy.scanner.min_dollar_volume
    assert snapshot.sparse_status is TapeDensity.VERY_SPARSE
    assert snapshot.price == Measured.of(11.00)


# ---- research scope --------------------------------------------------------------------

def test_liquid_common_stock_is_included_from_d_minus_1_data() -> None:
    decision = evaluate_research_scope(scope_inputs(), ScopeConfig())
    assert decision.included and decision.exclusion_reasons == ()
    assert (decision.reference_price, decision.median_dollar_volume, decision.history_sessions) == (5.0, 5_000_000.0, 20)


@pytest.mark.parametrize("kwargs, reason", [
    ({"security_type": "ETF"}, ScopeExclusion.SECURITY_TYPE),
    ({"exchange": "OTCM", "market": "otc"}, ScopeExclusion.OTC),
    ({"exchange": "ARCX"}, ScopeExclusion.EXCHANGE),
    ({"symbol": "ZVZZT"}, ScopeExclusion.TEST_TICKER_UNVERIFIED_LIST),
    ({"test_issue": True}, ScopeExclusion.TEST_TICKER),
    ({"status": ListingStatus.INACTIVE}, ScopeExclusion.NOT_ACTIVE),
    ({"close": 0.80}, ScopeExclusion.PRICE_BELOW_FLOOR),
    ({"volume": 100_000.0}, ScopeExclusion.DOLLAR_VOLUME_BELOW_FLOOR),
    ({"sessions": 3}, ScopeExclusion.INSUFFICIENT_HISTORY),
])
def test_each_exclusion_reason(kwargs: dict, reason: ScopeExclusion) -> None:
    decision = evaluate_research_scope(scope_inputs(**kwargs), ScopeConfig())
    assert not decision.included
    assert reason in decision.exclusion_reasons


def test_test_issue_metadata_decides_and_the_unverified_list_is_only_a_backup() -> None:
    config = ScopeConfig()
    flagged = evaluate_research_scope(scope_inputs(test_issue=True), config)
    assert flagged.exclusion_reasons == (ScopeExclusion.TEST_TICKER,)
    listed = evaluate_research_scope(scope_inputs(symbol="ZVZZT"), config)
    assert listed.exclusion_reasons == (ScopeExclusion.TEST_TICKER_UNVERIFIED_LIST,)
    # Metadata saying "not a test issue" does not switch the safeguard off.
    both = evaluate_research_scope(scope_inputs(symbol="ZVZZT", test_issue=False), config)
    assert both.exclusion_reasons == (ScopeExclusion.TEST_TICKER_UNVERIFIED_LIST,)
    assert evaluate_research_scope(scope_inputs(test_issue=False), config).included
    assert evaluate_research_scope(scope_inputs(test_issue=True), replace(config, test_symbols=())).exclusion_reasons \
        == (ScopeExclusion.TEST_TICKER,)


def test_all_reasons_are_reported_in_fixed_order() -> None:
    decision = evaluate_research_scope(scope_inputs(exchange="OTCM", market="otc", close=0.5), ScopeConfig())
    assert decision.exclusion_reasons == (ScopeExclusion.EXCHANGE, ScopeExclusion.OTC,
                                          ScopeExclusion.PRICE_BELOW_FLOOR, ScopeExclusion.DOLLAR_VOLUME_BELOW_FLOOR)


def test_scope_uses_the_most_recent_median_window() -> None:
    days = [D - timedelta(days=k) for k in range(40, 0, -1)]
    history = tuple(PriorDailyBar(d, 5.0, 10_000_000.0 if k < 20 else 10_000.0) for k, d in enumerate(days))
    metadata = TickerMetadataAsOf(SYMBOL, D - timedelta(days=1), "CS", "XNYS", "stocks", ListingStatus.ACTIVE)
    decision = evaluate_research_scope(ScopeInputs(D, metadata, history), ScopeConfig())
    assert decision.median_dollar_volume == 50_000.0
    assert ScopeExclusion.DOLLAR_VOLUME_BELOW_FLOOR in decision.exclusion_reasons


def test_ipo_short_history_is_excluded_as_insufficient_history() -> None:
    inputs, _ = ipo_short_history()
    decision = evaluate_research_scope(inputs, ScopeConfig())
    assert decision.exclusion_reasons == (ScopeExclusion.INSUFFICIENT_HISTORY,)


def test_scope_batch_is_one_decision_date() -> None:
    assert len(evaluate_scope_batch([scope_inputs(), scope_inputs(symbol="OTHER")], ScopeConfig())) == 2
    shifted = scope_inputs()
    other_day = ScopeInputs(D + timedelta(days=1), shifted.metadata, shifted.daily_history)
    with pytest.raises(ValueError):
        evaluate_scope_batch([scope_inputs(), other_day], ScopeConfig())


# ---- corporate actions -----------------------------------------------------------------

def test_delisting_window_flag_from_a_notice_known_before_d() -> None:
    flags = derive_corporate_action_flags(D, config=CorporateActionConfig(), delisting_notices=[delisting_window()])
    assert flags == {CorporateActionFlag.DELISTING_WINDOW}
    far = DelistingNotice(announced_on=D - timedelta(days=7), effective_date=D + timedelta(days=60))
    assert derive_corporate_action_flags(D, config=CorporateActionConfig(), delisting_notices=[far]) == frozenset()
    with pytest.raises(PointInTimeViolation):
        derive_corporate_action_flags(D, config=CorporateActionConfig(),
                                      delisting_notices=[DelistingNotice(D, D + timedelta(days=3))])


def test_split_ipo_symbol_change_and_prior_audit_flags() -> None:
    config = CorporateActionConfig()
    flags = derive_corporate_action_flags(
        D, config=config,
        splits=[reverse_split_day().split, SplitRecord(D - timedelta(days=3), 1, 2),
                SplitRecord(D + timedelta(days=2), 1, 3)],
        prior_sessions_since_listing=3,
        symbol_changes=[SymbolChange(D - timedelta(days=2), "OLD", SYMBOL),
                        SymbolChange(D + timedelta(days=1), SYMBOL, "NEXT")],
        prior_audit=PriorSessionAudit(D - timedelta(days=1), SessionVerdict.CORPORATE_ACTION_SUSPECT),
    )
    assert flags == {CorporateActionFlag.SPLIT_ON_DAY, CorporateActionFlag.RECENT_SPLIT,
                     CorporateActionFlag.IPO_WARMUP, CorporateActionFlag.SYMBOL_CHANGE,
                     CorporateActionFlag.CA_SUSPECT}
    with pytest.raises(PointInTimeViolation):
        derive_corporate_action_flags(D, config=config,
                                      prior_audit=PriorSessionAudit(D, SessionVerdict.CORPORATE_ACTION_SUSPECT))


# ---- spread inputs ---------------------------------------------------------------------

def test_spread_inputs_come_from_the_last_available_actual_bar() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    value = spread_feature_input(tape, et(9, 36), features=FeatureConfig(), halt=HaltInferenceConfig())
    assert value is not None
    assert (value.price, value.minute_dollar_volume, value.transactions) == (10.50, 2100.0, 10.0)
    assert value.high_low_range == pytest.approx(0.30)
    assert value.vwap_close_distance == pytest.approx(0.10)
    features = spread_features(value)
    assert features.range_pct == pytest.approx(0.30 / 10.5 * 100)
    assert features.dollar_volume_per_transaction == 210.0
    assert spread_feature_input(tape, et(9, 31, 30), features=FeatureConfig(), halt=HaltInferenceConfig()) is None
