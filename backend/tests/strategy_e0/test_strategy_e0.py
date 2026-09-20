"""Unit, edge-case and determinism tests for the Strategy E0 pre-validation modules.

Every test runs on a synthetic panel or a synthetic tape. None of them touches the workspace,
the frozen snapshot or the network, so the suite is runnable on a PC that has no Drive mount.
"""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent
from app.backtest.strategy_d_analog.models import FreezeIdentity, SessionGrid
from app.backtest.strategy_d_analog.source import DailyHistory
from app.backtest.strategy_e0_overnight import evaluate, gate, minute, pit_audit, stats
from app.backtest.strategy_e0_overnight.config import (
    DECLARED_RULES_CHECKSUM, RulesChanged, canonical_checksum, load_rules,
)
from app.backtest.strategy_e0_overnight.dataset import build

SESSIONS = tuple(date.fromordinal(date(2025, 1, 6).toordinal() + 7 * (i // 5) + (i % 5))
                 for i in range(40))
TICKERS = ("AAA", "BBB", "CCC")


def _panel(*, close=None, volume=None, splits=(), members=None) -> Panel:
    t, n = len(SESSIONS), len(TICKERS)
    base = np.linspace(50.0, 60.0, t)[:, None] * np.ones((1, n))
    close = base if close is None else close
    volume = np.full((t, n), 1_000_000.0) if volume is None else volume
    snapshots = members or {SESSIONS[0]: frozenset(TICKERS)}
    return Panel(SESSIONS, TICKERS, open=close.copy(), high=close * 1.02, low=close * 0.98,
                 close=close, volume=volume, splits=tuple(splits), snapshots=snapshots)


def _history(panel: Panel) -> DailyHistory:
    grid = SessionGrid(SESSIONS, "grid-digest")
    freeze = FreezeIdentity(
        snapshot_id="TEST", snapshot_sha256="0" * 64, freeze_id="TEST_FREEZE",
        freeze_digest="1" * 64, source_digest="2" * 64, d_read_digest="3" * 64,
        daily_authority="MASSIVE_GROUPED_DAILY", first_session=SESSIONS[0].isoformat(),
        last_session=SESSIONS[-1].isoformat(), session_count=len(SESSIONS), grid_digest="grid-digest")
    return DailyHistory(grid, panel, freeze, {}, tuple(sorted(panel.snapshots)), {})


class _Rules:
    """A minimal stand-in carrying only what ``build`` reads."""

    checksum = "test"
    allowed_exchanges = frozenset({"XNAS"})
    min_close = 5.0
    min_dollar_volume = 1_000.0
    min_present_sessions = 15
    warmup = 20
    first_index = 20
    last_index = len(SESSIONS) - 2
    snapshot_id = "TEST"

    def __init__(self, **overrides):
        for key, value in overrides.items():
            setattr(self, key, value)


def _bench() -> np.ndarray:
    return np.linspace(400.0, 420.0, len(SESSIONS))


# -- declaration -----------------------------------------------------------------------------

def test_declared_rules_still_hash_to_the_frozen_checksum():
    rules = load_rules()
    assert rules.checksum == DECLARED_RULES_CHECKSUM
    assert canonical_checksum(rules.raw) == DECLARED_RULES_CHECKSUM


def test_load_rules_refuses_an_edited_declaration(tmp_path):
    rules = load_rules()
    edited = dict(rules.raw)
    edited["gate"] = dict(edited["gate"])
    edited["gate"]["pass_requires_all"] = dict(edited["gate"]["pass_requires_all"])
    edited["gate"]["pass_requires_all"]["min_mean_lift"] = 0.0
    path = tmp_path / "edited.json"
    import json
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)


def test_declared_hypotheses_are_transcribed_one_for_one():
    """The mask code and the declaration must not drift apart."""
    rules = load_rules()
    declared = rules.hypotheses
    assert set(declared) == set(evaluate.DAILY_HYPOTHESES) | set(evaluate.MINUTE_HYPOTHESES)
    assert declared["H2"]["rule"] == "day_return > 0 and clv >= 0.8 and rvol >= 2.0"
    assert declared["H4"]["rule"] == "H2 and return_1530_to_close > 0"


# -- universe and labels ---------------------------------------------------------------------

def test_liquidity_window_excludes_the_session_itself():
    """A single huge volume day at D must not let D through the liquidity filter on its own."""
    volume = np.full((len(SESSIONS), len(TICKERS)), 1_000_000.0)
    volume[:, 0] = 1.0          # AAA is illiquid on every session ...
    volume[25, 0] = 1e12        # ... except for one enormous day
    rows = build(_history(_panel(volume=volume)), _bench(),
                 _Rules(min_dollar_volume=1_000_000.0))
    picked = [(int(t), int(j)) for t, j in zip(rows.session_idx, rows.ticker_idx)]
    assert (25, 0) not in picked, "D's own volume must not satisfy the liquidity filter"
    assert (25, 1) in picked, "the liquid tickers must still be selected"


def test_split_between_close_and_next_open_is_excluded():
    splits = [SplitEvent("AAA", SESSIONS[26], 1.0, 2.0)]
    rows = build(_history(_panel(splits=splits)), _bench(), _Rules())
    picked = {(int(t), int(j)) for t, j in zip(rows.session_idx, rows.ticker_idx)}
    assert (25, 0) not in picked, "the row whose label spans the split must be dropped"
    assert (24, 0) in picked and (26, 0) in picked
    assert rows.counters["ca_excluded"] == 1


def test_overnight_label_is_next_open_over_close():
    panel = _panel()
    rows = build(_history(panel), _bench(), _Rules())
    t, j = int(rows.session_idx[0]), int(rows.ticker_idx[0])
    expected = panel.open[t + 1, j] / panel.close[t, j] - 1.0
    assert rows.overnight[0] == pytest.approx(expected)


def test_membership_is_as_of_the_session():
    members = {SESSIONS[0]: frozenset({"AAA"}), SESSIONS[30]: frozenset(TICKERS)}
    rows = build(_history(_panel(members=members)), _bench(), _Rules())
    before = {int(j) for t, j in zip(rows.session_idx, rows.ticker_idx) if t < 30}
    after = {int(j) for t, j in zip(rows.session_idx, rows.ticker_idx) if t >= 30}
    assert before == {0}
    assert after == {0, 1, 2}


def test_clv_is_undefined_when_high_equals_low():
    close = np.full((len(SESSIONS), len(TICKERS)), 50.0)
    panel = Panel(SESSIONS, TICKERS, open=close.copy(), high=close.copy(), low=close.copy(),
                  close=close, volume=np.full(close.shape, 1e6), splits=(),
                  snapshots={SESSIONS[0]: frozenset(TICKERS)})
    rows = build(_history(panel), _bench(), _Rules())
    assert np.all(~np.isfinite(rows.features["clv"]))
    assert not evaluate.daily_mask("H1", rows.features).any()


# -- point in time ---------------------------------------------------------------------------

def test_pit_audit_passes_on_the_unmodified_builder():
    panel = _panel()
    history = _history(panel)
    rules = _Rules()
    rows = build(history, _bench(), rules)
    result = pit_audit.run(history, _bench(), rules, rows, cut=len(SESSIONS) - 5)
    assert result["verdict"] == "PASS"
    assert all(result["features_identical"].values())


def test_pit_audit_catches_a_planted_look_ahead(monkeypatch):
    """A feature that reads D+1 must make the audit fail, or the audit proves nothing."""
    import app.backtest.strategy_e0_overnight.dataset as dataset_module
    real_build = dataset_module.build

    def leaky_build(history, benchmark, rules):
        rows = real_build(history, benchmark, rules)
        future = history.panel.close[rows.session_idx + 1, rows.ticker_idx]
        features = dict(rows.features)
        features["day_return"] = future  # reads D+1, on purpose
        return type(rows)(session_idx=rows.session_idx, ticker_idx=rows.ticker_idx,
                          sessions=rows.sessions, tickers=rows.tickers, overnight=rows.overnight,
                          features=features, close=rows.close,
                          dollar_volume=rows.dollar_volume, counters=rows.counters)

    panel = _panel()
    history = _history(panel)
    rules = _Rules()
    rows = leaky_build(history, _bench(), rules)
    monkeypatch.setattr(pit_audit, "build", leaky_build)
    result = pit_audit.run(history, _bench(), rules, rows, cut=len(SESSIONS) - 5)
    assert result["verdict"] == "FAIL"
    assert result["features_identical"]["day_return"] is False


# -- statistics ------------------------------------------------------------------------------

def test_bucket_index_closes_the_last_bucket_on_the_right():
    edges = (0.0, 0.2, 0.5, 0.8, 0.95, 1.0)
    values = np.array([0.0, 0.19, 0.95, 1.0, 1.5, np.nan])
    index = stats.bucket_index(values, edges)
    assert index.tolist() == [0, 0, 4, 4, -1, -1]


def test_bucket_index_handles_open_ended_edges():
    edges = (None, 0.0, 0.02, None)
    values = np.array([-5.0, 0.0, 0.5])
    assert stats.bucket_index(values, edges).tolist() == [0, 1, 2]


def test_summarise_matches_numpy_on_a_known_sample():
    values = np.array([-0.06, -0.02, 0.0, 0.01, 0.04])
    block = stats.summarise(values)
    assert block["n"] == 5
    assert block["mean"] == pytest.approx(values.mean())
    assert block["median"] == pytest.approx(0.0)
    assert block["win_rate"] == pytest.approx(0.4)
    assert block["p_gap_le_minus_5pct"] == pytest.approx(0.2)
    assert block["p_gap_ge_3pct"] == pytest.approx(0.2)


def test_lift_is_a_difference_not_a_ratio():
    candidate = {"mean": 0.002, "median": 0.0, "win_rate": 0.55, "p_gap_le_minus_5pct": 0.02}
    baseline = {"mean": 0.0005, "median": 0.0, "win_rate": 0.50, "p_gap_le_minus_5pct": 0.01}
    out = stats.lift(candidate, baseline)
    assert out["mean"] == pytest.approx(0.0015)
    assert out["win_rate"] == pytest.approx(0.05)
    assert out["downside_ratio"] == pytest.approx(2.0)


def test_session_bootstrap_is_deterministic_under_its_seed():
    rng = np.random.default_rng(7)
    sessions = np.repeat(np.array(SESSIONS[:20], dtype=object), 50)
    values = rng.normal(0.0005, 0.02, sessions.size)
    candidate = values[:300] + 0.01
    first = stats.session_bootstrap(candidate, sessions[:300], values, sessions,
                                    resamples=200, seed=11)
    second = stats.session_bootstrap(candidate, sessions[:300], values, sessions,
                                     resamples=200, seed=11)
    assert first == second
    assert first.mean_lift_ci[0] < first.mean_lift < first.mean_lift_ci[1]


def test_session_bootstrap_recovers_a_planted_lift():
    rng = np.random.default_rng(3)
    sessions = np.repeat(np.array(SESSIONS, dtype=object), 100)
    values = rng.normal(0.0, 0.01, sessions.size)
    candidate = values[::4] + 0.02
    result = stats.session_bootstrap(candidate, sessions[::4], values, sessions,
                                     resamples=400, seed=5)
    assert result.mean_lift == pytest.approx(0.02, abs=0.004)
    assert result.mean_lift_ci[0] > 0


def test_symbol_concentration_measures_excess_not_level():
    values = np.array([0.10, 0.0, 0.0, 0.0])
    tickers = np.array(["AAA", "BBB", "CCC", "DDD"], dtype=object)
    out = stats.symbol_concentration(values, tickers, 0.0, top=1)
    assert out["top_contributors"][0]["ticker"] == "AAA"
    assert out["top_contributors"][0]["share_of_total_excess"] == pytest.approx(1.0)
    assert out["after_removing_top"]["mean"] == pytest.approx(0.0)


def test_extreme_removal_drops_the_largest_outcomes():
    values = np.concatenate([np.zeros(99), np.array([1.0])])
    rows = {r["removal"]: r for r in stats.extreme_removal(values, 0.0)}
    assert rows["drop_top_1"]["n"] == 99
    assert rows["drop_top_1"]["mean"] == pytest.approx(0.0)
    assert rows["drop_top_1pct"]["removed"] == 1


# -- minute ----------------------------------------------------------------------------------

def test_et_conversion_crosses_a_dst_boundary_correctly():
    edges, offsets = minute.et_offsets(datetime(2025, 1, 1, tzinfo=timezone.utc),
                                       datetime(2026, 1, 1, tzinfo=timezone.utc))
    winter = int(datetime(2025, 2, 3, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)  # 09:30 EST
    summer = int(datetime(2025, 7, 3, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)  # 09:30 EDT
    days, minutes = minute.to_et_fields(np.array([winter, summer]), edges, offsets)
    assert minutes.tolist() == [minute.REGULAR_OPEN_MIN, minute.REGULAR_OPEN_MIN]
    assert days[0] == minute.ordinal(date(2025, 2, 3))
    assert days[1] == minute.ordinal(date(2025, 7, 3))


def _tape(day: date, closes: dict[int, float], *, symbol: str = "AAA") -> minute.SymbolTape:
    minutes = np.array(sorted(closes), dtype=np.int32)
    values = np.array([closes[m] for m in minutes], dtype=float)
    return minute.SymbolTape(
        symbol=symbol, et_day=np.full(minutes.size, minute.ordinal(day), dtype=np.int64),
        minute=minutes, open=values.copy(), high=values * 1.001, low=values * 0.999,
        close=values, volume=np.full(minutes.size, 100.0), vwap=values.copy(),
        sources={}, overlap_sessions=0)


def test_session_features_ignore_bars_at_or_after_the_close():
    bars = {m: 100.0 for m in range(minute.REGULAR_OPEN_MIN, minute.REGULAR_CLOSE_MIN)}
    bars[minute.REGULAR_CLOSE_MIN] = 999.0        # 16:00 ET, must be invisible
    bars[minute.REGULAR_CLOSE_MIN + 30] = 999.0
    features = minute.session_features(_tape(date(2025, 3, 5), bars))
    block = features[minute.ordinal(date(2025, 3, 5))]
    assert block["minute_close"] == pytest.approx(100.0)
    assert block["regular_bars"] == 390.0


def test_closing_strength_is_measured_over_the_declared_windows():
    bars = {m: 100.0 for m in range(minute.REGULAR_OPEN_MIN, minute.LAST30_MIN)}
    bars.update({m: 110.0 for m in range(minute.LAST30_MIN, minute.REGULAR_CLOSE_MIN)})
    block = minute.session_features(_tape(date(2025, 3, 5), bars))[minute.ordinal(date(2025, 3, 5))]
    assert block["return_1530_to_close"] == pytest.approx(0.0)
    assert block["last30m_volume"] == pytest.approx(30 * 100.0)
    assert block["last15m_volume"] == pytest.approx(15 * 100.0)
    assert block["last30m_high_break"] == 1.0


def test_pair_sessions_walks_the_grid_not_the_tape():
    """A missing session must break the pair rather than become a two-day overnight return."""
    day_a, day_c = SESSIONS[0], SESSIONS[2]
    features = {
        minute.ordinal(day_a): {"minute_close": 100.0},
        minute.ordinal(day_c): {"next_open": 120.0, "first_1m_close": 121.0,
                                "first_5m_close": 122.0, "first_15m_close": 123.0,
                                "first_5m_high": 124.0, "first_5m_low": 119.0,
                                "first_15m_high": 125.0, "first_15m_low": 118.0,
                                "first_15m_bars": 15.0},
    }
    assert minute.pair_sessions(features, [day_a, SESSIONS[1], day_c]) == []
    rows = minute.pair_sessions(features, [day_a, day_c])
    assert len(rows) == 1
    assert rows[0]["minute_overnight"] == pytest.approx(0.2)


def test_minute_masks_require_the_daily_rule_too():
    h2 = np.array([True, True, False])
    closing = {"return_1530_to_close": np.array([0.01, -0.01, 0.01]),
               "return_1545_to_close": np.array([0.01, 0.01, 0.01]),
               "close_to_day_high": np.array([-0.001, -0.001, -0.001])}
    assert evaluate.minute_mask("H4", h2, closing).tolist() == [True, False, False]
    assert evaluate.minute_mask("H5", h2, closing).tolist() == [True, True, False]


# -- gate ------------------------------------------------------------------------------------

def _candidate(**overrides):
    block = {
        "name": "X",
        "summary": {"n": 5_000, "mean": 0.003, "median": 0.001, "win_rate": 0.55,
                    "p_gap_le_minus_5pct": 0.009},
        "lift": {"mean": 0.0025, "median": 0.0008, "win_rate": 0.05, "downside_ratio": 1.1},
        "quarterly": [{"quarter": f"2025Q{i}", "n": 10, "lift": {"mean": 0.002}} for i in range(1, 9)],
        "extreme_removal": [{"removal": "drop_top_1pct", "mean_lift": 0.001}],
        "symbol_concentration": {"top_share_of_total_excess": 0.2},
        "bootstrap": {"mean_lift_ci": [0.0005, 0.004]},
    }
    block.update(overrides)
    return block


def test_gate_passes_only_when_every_declared_criterion_holds():
    rules = load_rules()
    result = gate.evaluate(_candidate(), rules)
    assert result["verdict"] == gate.PASS
    assert result["failed"] == []


@pytest.mark.parametrize("field, value", [
    ("summary", {"n": 10, "mean": 0.003, "median": 0.001, "win_rate": 0.55,
                 "p_gap_le_minus_5pct": 0.009}),
    ("lift", {"mean": 0.0001, "median": 0.0008, "win_rate": 0.05, "downside_ratio": 1.1}),
    ("symbol_concentration", {"top_share_of_total_excess": 0.9}),
    ("extreme_removal", [{"removal": "drop_top_1pct", "mean_lift": -0.01}]),
])
def test_gate_refuses_when_one_criterion_fails(field, value):
    rules = load_rules()
    result = gate.evaluate(_candidate(**{field: value}), rules)
    assert result["verdict"] != gate.PASS
    assert result["failed"]


def test_gate_calls_a_negative_bootstrap_interval_fail_not_inconclusive():
    rules = load_rules()
    result = gate.evaluate(_candidate(bootstrap={"mean_lift_ci": [-0.002, 0.004]},
                                      lift={"mean": 0.0001, "median": 0.0,
                                            "win_rate": 0.0, "downside_ratio": 1.0}), rules)
    assert result["verdict"] == gate.FAIL


def test_gate_says_inconclusive_when_the_interval_clears_zero_but_a_check_fails():
    rules = load_rules()
    result = gate.evaluate(_candidate(summary={"n": 100, "mean": 0.003, "median": 0.001,
                                               "win_rate": 0.55, "p_gap_le_minus_5pct": 0.009}),
                           rules)
    assert result["verdict"] == gate.INCONCLUSIVE


def test_empty_candidate_is_a_fail_not_a_crash():
    rules = load_rules()
    result = gate.evaluate({"name": "empty", "summary": {"n": 0}}, rules)
    assert result["verdict"] == gate.FAIL


def test_combine_reports_the_strongest_outcome():
    assert gate.combine([{"verdict": "FAIL"}, {"verdict": "INCONCLUSIVE"}]) == gate.INCONCLUSIVE
    assert gate.combine([{"verdict": "FAIL"}, {"verdict": "PASS"}]) == gate.PASS
    assert gate.combine([{"verdict": "FAIL"}]) == gate.FAIL
