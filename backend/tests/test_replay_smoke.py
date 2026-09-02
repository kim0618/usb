import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.replay_smoke import ReplaySmokeRunner, SyntheticReplayDataset


@pytest.fixture(scope="module")
def result():
    return ReplaySmokeRunner().run()


def test_t1_generates_twenty_xnys_sessions_with_early_close_and_rollover():
    data = SyntheticReplayDataset.build()
    assert len(data.trading_days) == 20
    assert all(b > a and (b - a).days <= 4 for a, b in zip(data.trading_days, data.trading_days[1:]))
    assert any(ReplaySmokeRunner(data).calendar.is_early_close(day) for day in data.trading_days)
    assert any((b - a).days > 1 for a, b in zip(data.trading_days, data.trading_days[1:]))


def test_t2_t3_daily_real_scanner_and_deterministic_top8(result):
    assert len(result.scanner_runs) == 20
    assert all(run.top8_count == 8 and run.candidate_count >= 20 for run in result.scanner_runs)
    assert len({run.top8_symbols for run in result.scanner_runs}) > 1
    assert result.scanner_runs == ReplaySmokeRunner().run().scanner_runs


def test_t4_t5_all_variants_fan_out_with_isolated_grain(result):
    assert len(result.shadow_results) == 20 * 8 * 5
    assert result.invariant_results["variant_isolation"]
    keys = {(item.trading_date, item.symbol, item.variant) for item in result.shadow_results}
    assert len(keys) == len(result.shadow_results)


def test_t6_symbol_can_be_reused_on_later_dates(result):
    dates_by_symbol = {}
    for item in result.shadow_results:
        dates_by_symbol.setdefault(item.symbol, set()).add(item.trading_date)
    assert max(map(len, dates_by_symbol.values())) > 1


def test_t7_t8_overnight_day2_and_scanner_coexist(result):
    assert any(item.overnight and item.status == "CLOSED" for item in result.shadow_results)
    assert all(day.day2_exits == day.overnight_carried for day in result.daily_results)
    assert len(result.scanner_runs) == len(result.daily_results)


def test_t9_t10_no_trade_and_unfilled_accumulate(result):
    assert sum(day.no_trade for day in result.daily_results) > 0
    assert sum(day.unfilled for day in result.daily_results) > 0


def test_t11_t12_cost_and_r_aggregation(result):
    for summary in result.variant_summaries:
        assert summary.total_cost == summary.spread_cost + summary.slippage_cost + summary.commission + summary.fx_cost
        assert summary.net_r_sum == sum((item.net_r for item in result.shadow_results
                                        if item.variant == summary.variant and item.status == "CLOSED"), 0)


def test_t13_ambiguity_aggregation(result):
    assert sum(item.ambiguous_bar_count for item in result.shadow_results) > 0
    assert sum(item.ambiguous_bar_count for item in result.shadow_results) == sum(
        summary.ambiguous_bar_count for summary in result.variant_summaries)


def test_t14_t15_no_orphans_negative_cash_or_short(result):
    assert result.invariant_results["no_orphan"]
    assert result.invariant_results["no_negative_cash"]
    assert result.invariant_results["no_short"]


def test_t16_full_repeat_is_deterministic(result):
    assert result.canonical == ReplaySmokeRunner().run().canonical


def test_t17_shuffled_universe_is_order_independent(result):
    shuffled = SyntheticReplayDataset.build(universe=tuple(reversed(result.universe)))
    assert result.canonical == ReplaySmokeRunner(shuffled).run().canonical


def test_t18_available_at_is_one_minute_after_bar_start():
    data = SyntheticReplayDataset.build()
    assert data.minute_bars
    assert all(bar.available_at == bar.timestamp + timedelta(minutes=1) for bar in data.minute_bars)


def test_t19_end_boundary_completes_overnight(result):
    final_day = result.trading_days[-1]
    assert all(item.status in {"CLOSED", "NO_TRADE", "UNFILLED", "REJECTED"}
               for item in result.shadow_results if item.trading_date == final_day)
    assert result.invariant_results["no_day3"]


def test_t20_runtime_report_schema_and_content(tmp_path):
    target = tmp_path / "report.json"
    result = ReplaySmokeRunner().validate(report_path=target)
    report = json.loads(target.read_text())
    assert report["statement"] == "Synthetic Smoke results are implementation validation only."
    assert report["totals"]["trading_days"] == 20
    assert report["totals"]["invariant_violations"] == 0
    assert result.determinism_result and result.order_independence_result
