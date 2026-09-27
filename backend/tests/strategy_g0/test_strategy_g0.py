"""Pairing, session classification, PIT cutoff, leakage rejection, corporate-action and rules-hash
tests for the Strategy G0 after-hours -> next premarket modules.

Every test runs on a synthetic tape, a synthetic daily panel or the real exchange calendar. None
touches the workspace, the frozen snapshot, the minute store or the network.
"""

from datetime import date
import hashlib
import json

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent
from app.backtest.strategy_d_analog.models import FreezeIdentity, SessionGrid
from app.backtest.strategy_d_analog.source import DailyHistory
from app.backtest.strategy_e0_overnight.dataset import build as e0_build
from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_g0_after_premarket import cohort, dataset, evaluate, gate, pairing, pit_audit
from app.backtest.strategy_g0_after_premarket import features as F
from app.backtest.strategy_g0_after_premarket import tape as T
from app.backtest.strategy_g0_after_premarket.config import (
    DECLARED_RULES_CHECKSUM, SHA256_PATH, RulesChanged, canonical_checksum, load_rules,
)
from app.market.calendar import MarketCalendar


# -- declaration ------------------------------------------------------------------------------

def test_declared_rules_hash_to_the_frozen_checksum_and_sha_file():
    rules = load_rules()
    assert rules.checksum == DECLARED_RULES_CHECKSUM == canonical_checksum(rules.raw)
    recorded = SHA256_PATH.read_text(encoding="utf-8").split()[0]
    assert recorded == DECLARED_RULES_CHECKSUM


def test_load_rules_refuses_an_edited_declaration(tmp_path):
    rules = load_rules()
    edited = json.loads(json.dumps(rules.raw))
    edited["pass_gate"]["mean"]["min_mean_lift"] = 0.0
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)


def test_rules_carry_every_required_section():
    raw = load_rules().raw
    for key in ("strategy", "version", "research_type", "information_cutoff", "source_session",
                "target_session", "entry_time", "exit_time", "secondary_diagnostics", "universe",
                "price_threshold", "liquidity_threshold", "feature_definitions", "H1", "H2", "H3",
                "H4", "H5", "weekend_policy", "holiday_policy", "corporate_action_policy",
                "early_close_policy", "cost_grid", "bootstrap_method",
                "neutralization_method", "extreme_removal_rules", "pass_gate"):
        assert key in raw, key
    assert "corporate_action_exclusion" in raw["universe"]["daily_pit"]
    assert raw["entry_time"] == "08:00 ET" and raw["exit_time"] == "09:25 ET"
    assert len([k for k in raw["hypotheses"] if k.startswith("H")]) == 5


# -- session pairing --------------------------------------------------------------------------

def _grid(start: date, end: date) -> list[date]:
    cal = MarketCalendar()
    out, day = [], start
    while day <= end:
        out.append(day)
        day = cal.next_trading_day(day)
    return out


def test_friday_pairs_with_monday():
    cal = MarketCalendar()
    pairs = pairing.build_pairs(_grid(date(2026, 9, 10), date(2026, 9, 16)), cal)
    friday = [p for p in pairs if p.source_session == date(2026, 9, 11)][0]
    assert friday.next_trading_session == date(2026, 9, 14)
    assert friday.kind == pairing.WEEKEND and friday.calendar_days == 3


def test_holiday_eve_pairs_with_next_session():
    cal = MarketCalendar()
    pairs = pairing.build_pairs(_grid(date(2025, 11, 24), date(2025, 12, 2)), cal)
    by_source = {p.source_session: p for p in pairs}
    thanksgiving_eve = by_source[date(2025, 11, 26)]
    assert thanksgiving_eve.next_trading_session == date(2025, 11, 28)
    assert thanksgiving_eve.kind == pairing.HOLIDAY
    assert by_source[date(2025, 11, 28)].source_early_close is True
    assert by_source[date(2025, 11, 28)].next_trading_session == date(2025, 12, 1)
    # the 2025-01-09 national day of mourning closed the exchange
    mourning = pairing.build_pairs(_grid(date(2025, 1, 7), date(2025, 1, 13)), cal)
    assert {p.source_session: p.next_trading_session for p in mourning}[date(2025, 1, 8)] == date(2025, 1, 10)


def test_pairing_audit_passes_and_grid_hole_stops():
    cal = MarketCalendar()
    grid = _grid(date(2024, 12, 20), date(2025, 1, 10))
    pairs = pairing.build_pairs(grid, cal)
    report = pairing.audit(pairs, cal)
    assert report["verdict"] == "PASS" and not any(report["failures"].values())
    assert report["kinds"][pairing.HOLIDAY] >= 2
    with pytest.raises(ValueError):
        pairing.build_pairs(grid[:3] + grid[4:], cal)


def test_pairing_audit_flags_a_wrong_pair():
    cal = MarketCalendar()
    bad = [pairing.SessionPair(date(2026, 9, 11), date(2026, 9, 15), pairing.WEEKEND, 4, False)]
    assert pairing.audit(bad, cal)["verdict"] == "FAIL"
    rows = pit_audit.pairing_audit(np.array([date(2026, 9, 11)], dtype=object),
                                   np.array([date(2026, 9, 12)], dtype=object), cal)
    assert rows["verdict"] == "FAIL"


# -- session classification -------------------------------------------------------------------

def _arrays(bars: dict[int, float], volume: float = 100.0):
    minute = np.array(sorted(bars), dtype=np.int32)
    price = np.array([bars[m] for m in sorted(bars)], dtype=float)
    return minute, price * 1.001, price * 0.999, price, np.full(minute.size, volume), price.copy()


def test_after_session_excludes_1600_bar_and_regular_bars():
    bars = {m: 50.0 for m in range(570, 960)}          # regular
    bars[960] = 999.0                                   # closing cross bar
    bars.update({961: 51.0, 1030: 52.0, 1199: 53.0})
    minute, high, low, close, volume, vwap = _arrays(bars)
    block = F.source_block(minute, high, low, close, volume, vwap)
    assert block["after_bars"] == 3
    assert block["after_high"] < 999.0
    assert block["p2000"] == 53.0 and block["p1700"] == 51.0 and block["p1800"] == 52.0
    assert block["bar_1600_dollar"] == pytest.approx(999.0 * 100.0)
    assert block["regular_bars"] == 390


def test_premarket_classification_and_entry_window():
    bars = {300: 10.0, 470: 10.1, 485: 10.2, 500: 10.3, 564: 10.4, 565: 99.0, 570: 99.0}
    minute, high, low, close, volume, vwap = _arrays(bars)
    open_ = close.copy()
    tb = F.target_block(minute, open_, high, low, close, volume, vwap, entry_minute=480,
                        entry_window=15, exits={"0925": 564, "0900": 539})
    assert tb["entry_minute"] == 485 and tb["entry_price"] == 10.2
    assert tb["exit_0925"] == 10.4, "09:25 and 09:30 bars must not be read"
    assert tb["exit_0900"] == 10.3
    assert tb["high_0925"] < 20 and tb["pre_bars_before_entry"] == 2
    strict = F.target_block(minute, open_, high, low, close, volume, vwap, entry_minute=480,
                            entry_window=1, exits={"0925": 564})
    assert np.isnan(strict["entry_price"])


def test_features_carry_forward_and_official_close_reference():
    src = {name: np.array([np.nan]) for name in F.SOURCE_FIELDS}
    src.update({"after_bars": np.array([3.0]), "after_volume": np.array([10.0]),
                "after_dollar_volume": np.array([1000.0]), "after_high": np.array([103.0]),
                "after_low": np.array([101.0]), "p2000": np.array([102.0]),
                "p1800": np.array([103.0]), "p1930": np.array([103.0]),
                "last30_dollar": np.array([0.0])})
    feats = F.after_features(src, np.array([100.0]), np.array([100.0]))
    assert feats["after_return_1600_2000"][0] == pytest.approx(0.02)
    assert feats["after_return_1600_1700"][0] == 0.0          # nothing printed before 17:00
    assert feats["last30m_after_return"][0] == pytest.approx(102.0 / 103.0 - 1.0)
    assert feats["position_in_after_range"][0] == pytest.approx(2.0 / 3.0)
    assert feats["after_peak_retention"][0] == pytest.approx(2.0 / 3.0)
    assert feats["after_rvol"][0] == pytest.approx(10.0)


def test_prior_mean_is_strictly_prior_and_skips_missing_tape():
    values = [1.0, 2.0, 3.0, 100.0]
    present = [True, False, True, True]
    out = F.prior_mean(values, present, window=20, minimum=2)
    assert np.isnan(out[0]) and np.isnan(out[1]) and np.isnan(out[2])
    assert out[3] == pytest.approx(2.0)                       # (1 + 3) / 2, today excluded


# -- PIT cutoff and leakage rejection ---------------------------------------------------------

DAYS = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8),
        date(2026, 5, 11)]
SPECS = {"E0800": {"entry_minute": 480, "entry_window": 15, "exits": {"0925": 564}}}


def _tape(symbol: str = "AAA", seed: int = 1) -> SymbolTape:
    rng = np.random.default_rng(seed)
    minutes = [300, 420, 481, 500, 560] + list(range(570, 960, 13)) + [960, 975, 1030, 1100, 1180, 1195]
    rows = []
    for day in DAYS:
        for m in minutes:
            rows.append((ordinal(day), m, 20.0 + rng.normal(0, 0.2)))
    day = np.array([r[0] for r in rows], dtype=np.int64)
    minute = np.array([r[1] for r in rows], dtype=np.int32)
    price = np.array([r[2] for r in rows])
    return SymbolTape(symbol=symbol, et_day=day, minute=minute, open=price.copy(), high=price + 0.05,
                      low=price - 0.05, close=price, volume=rng.uniform(100, 1000, day.size),
                      vwap=price.copy(), sources={}, overlap_sessions=0)


GRID = np.array([ordinal(d) for d in DAYS], dtype=np.int64)


def _builder(tape: SymbolTape):
    return cohort.symbol_rows(tape, SPECS, GRID, rvol_window=20, rvol_minimum=2)


def _masks(feats):
    raw = load_rules().raw
    return {name: evaluate.mask(name, feats, raw) for name in evaluate.HYPOTHESES}


def test_after_feature_cutoff_is_invariant_to_future_poison():
    report = pit_audit.cutoff_audit([_tape("AAA", 1), _tape("BBB", 2)], _builder, _masks)
    assert report["verdict"] == "PASS", report
    assert report["symbol_days_compared"] > 0


def _leaky_builder(tape: SymbolTape):
    """Deliberately wrong: DAY T's after-hours window also reads DAY T+1's premarket bars."""
    rows = _builder(tape)
    days = rows["days"]
    for i, day in enumerate(days[:-1]):
        nxt = (tape.et_day == days[i + 1]) & (tape.minute < 570)
        if nxt.any():
            rows["source"]["p2000"][i] = float(tape.close[nxt][-1])
            rows["source"]["after_dollar_volume"][i] += float(np.sum(tape.volume[nxt]))
    return rows


def test_synthetic_future_bar_leakage_is_rejected():
    report = pit_audit.cutoff_audit([_tape("AAA", 1)], _leaky_builder, _masks)
    assert report["verdict"] == "FAIL"
    assert "p2000" in report["mismatched_fields"]


def test_poison_touches_only_bars_after_the_cut():
    tape = _tape()
    cut = ordinal(DAYS[2])
    dirty = pit_audit.poison_after(tape, cut, seed=3)
    before = tape.et_day <= cut
    assert np.array_equal(tape.close[before], dirty.close[before])
    assert not np.array_equal(tape.close[~before], dirty.close[~before])


# -- dataset: corporate action, identity, early close ------------------------------------------

SESSIONS = tuple(date.fromordinal(date(2025, 1, 6).toordinal() + 7 * (i // 5) + (i % 5))
                 for i in range(30))
TICKERS = ("AAA", "BBB")


class _E0Rules:
    checksum = "test"
    allowed_exchanges = frozenset({"XNAS"})
    min_close = 5.0
    min_dollar_volume = 1_000.0
    min_present_sessions = 15
    warmup = 20
    first_index = 20
    last_index = len(SESSIONS) - 2
    snapshot_id = "TEST"


class _Calendar:
    def next_trading_day(self, day):
        return SESSIONS[SESSIONS.index(day) + 1]

    def is_early_close(self, day):
        return day == SESSIONS[24]


def _history(splits=(), figi=None):
    t, n = len(SESSIONS), len(TICKERS)
    close = np.linspace(50.0, 60.0, t)[:, None] * np.ones((1, n))
    snapshots = {SESSIONS[0]: frozenset(TICKERS), SESSIONS[23]: frozenset(TICKERS)}
    panel = Panel(SESSIONS, TICKERS, open=close.copy(), high=close * 1.02, low=close * 0.98,
                  close=close, volume=np.full((t, n), 1_000_000.0), splits=tuple(splits),
                  snapshots=snapshots)
    grid = SessionGrid(SESSIONS, "grid-digest")
    freeze = FreezeIdentity(
        snapshot_id="TEST", snapshot_sha256="0" * 64, freeze_id="TEST_FREEZE",
        freeze_digest="1" * 64, source_digest="2" * 64, d_read_digest="3" * 64,
        daily_authority="MASSIVE_GROUPED_DAILY", first_session=SESSIONS[0].isoformat(),
        last_session=SESSIONS[-1].isoformat(), session_count=len(SESSIONS), grid_digest="grid-digest")
    return DailyHistory(grid, panel, freeze, figi or {}, tuple(sorted(snapshots)), {})


def _synthetic_cohort():
    results = []
    for symbol in TICKERS:
        rows = []
        for day in SESSIONS:
            for m, p in ((300, 55.0), (481, 55.1), (500, 55.3), (560, 55.2),
                         (600, 55.0), (959, 55.0), (961, 55.2), (1100, 55.4), (1190, 55.5)):
                rows.append((ordinal(day), m, p))
        day = np.array([r[0] for r in rows], dtype=np.int64)
        minute = np.array([r[1] for r in rows], dtype=np.int32)
        price = np.array([r[2] for r in rows])
        tape = SymbolTape(symbol=symbol, et_day=day, minute=minute, open=price.copy(),
                          high=price + 0.01, low=price - 0.01, close=price,
                          volume=np.full(day.size, 500.0), vwap=price.copy(), sources={},
                          overlap_sessions=0)
        grid = np.array([ordinal(s) for s in SESSIONS], dtype=np.int64)
        params = load_rules().raw["feature_parameters"]
        out = cohort.symbol_rows(tape, cohort.target_specs(load_rules().raw), grid,
                                 params["after_rvol_window_sessions"], params["after_rvol_min_sessions"])
        out["symbol"] = symbol
        results.append(out)
    return cohort.pack(results)


def _g_rows(history):
    rules = load_rules()
    bench = np.linspace(400.0, 420.0, len(SESSIONS))
    daily = e0_build(history, bench, _E0Rules())
    pairs = pairing.build_pairs(list(SESSIONS), _Calendar())
    return dataset.build(_synthetic_cohort(), daily, history, bench, pairs, rules), daily


def test_split_between_t_and_t_plus_1_is_excluded():
    base, _ = _g_rows(_history())
    split, daily = _g_rows(_history(splits=[SplitEvent("AAA", SESSIONS[22], 1.0, 2.0)]))
    assert daily.counters["ca_excluded"] == 1
    removed = {(s, t) for s, t in zip(base.source_sessions, base.tickers)} - \
              {(s, t) for s, t in zip(split.source_sessions, split.tickers)}
    assert removed == {(SESSIONS[21], "AAA")}


def test_identity_change_and_early_close_are_excluded():
    figi = {SESSIONS[0]: {"AAA": "F1", "BBB": "F2"}, SESSIONS[23]: {"AAA": "F9", "BBB": "F2"}}
    rows, _ = _g_rows(_history(figi=figi))
    assert rows.counters["identity_changed_between_t_and_t1"] == 1          # AAA 22 -> 23
    assert (SESSIONS[22], "AAA") not in set(zip(rows.source_sessions, rows.tickers))
    assert rows.counters["early_close_source"] == 2                          # both tickers at 24
    assert SESSIONS[24] not in set(rows.source_sessions)
    for s, n in zip(rows.source_sessions, rows.next_sessions):
        assert n == SESSIONS[SESSIONS.index(s) + 1]


def test_labels_are_from_the_next_session_only():
    rows, _ = _g_rows(_history())
    r = rows.labels["E0800:0925:R"]
    assert np.allclose(r[rows.has_entry], 55.2 / 55.1 - 1.0)
    gap = rows.labels["E0800:0925:after_close_to_entry"]
    assert np.allclose(gap[rows.has_entry], 55.1 / 55.5 - 1.0)


# -- tape helpers and gate ---------------------------------------------------------------------

def test_duplicate_minutes_identical_vs_conflicting():
    tape = _tape()
    idx = np.array([0, 0, 1])
    rest = np.arange(2, tape.et_day.size)
    order = np.concatenate([idx, rest])
    dup = SymbolTape(symbol="AAA", et_day=tape.et_day[order], minute=tape.minute[order],
                     open=tape.open[order], high=tape.high[order], low=tape.low[order],
                     close=tape.close[order].copy(), volume=tape.volume[order], vwap=tape.vwap[order],
                     sources={}, overlap_sessions=0)
    assert T.duplicate_days(dup) == {int(tape.et_day[0]): True}
    assert T.drop_identical_duplicates(dup).et_day.size == tape.et_day.size
    dup.close[1] += 1.0
    assert T.duplicate_days(dup) == {int(tape.et_day[0]): False}


def test_gate_fails_empty_and_is_inconclusive_on_execution_only():
    g = load_rules().gate
    assert gate.evaluate({"name": "H1", "summary": {"n": 0}}, {}, g)["verdict"] == "FAIL"
    months = [{"period": f"2026-{m:02d}", "n": 50, "mean_lift": 0.003} for m in range(1, 10)]
    block = {
        "name": "HX", "summary": {"n": 5000, "median": 0.001}, "sessions": 200, "unique_symbols": 900,
        "lift": {"mean": 0.004, "p_gap_ge_2pct": 0.03, "p_gap_le_minus_2pct_ratio": 1.1},
        "excursion_lift": {"p_mfe_ge_3pct": 0.04},
        "extreme_removal": [{"removal": "drop_top_1pct", "lift": {"mean": 0.002}}],
        "concentration": {"top1_share_of_total_excess": 0.05, "top5_share_of_total_excess": 0.2},
        "bootstrap_session": {"mean": {"ci95": [0.001, 0.007]}, "p_ge_2pct": {"ci95": [0.01, 0.05]},
                              "p_mfe_ge_3pct": {"ci95": [0.01, 0.05]}},
        "bootstrap_symbol": {"mean": {"ci95": [0.0005, 0.007]}, "p_ge_2pct": {"ci95": [0.01, 0.05]},
                             "p_mfe_ge_3pct": {"ci95": [0.01, 0.05]}},
        "monthly": months, "matched": {"estimator_primary": {"mean_lift": 0.003, "match_rate": 0.9}},
        "cost": {"break_even_cost_bp": 80.0},
    }
    good = {"median_entry_dollar": 50_000.0, "entry_fill_rate": 0.9}
    assert gate.evaluate(block, good, g)["verdict"] == "PASS"
    thin = {"median_entry_dollar": 500.0, "entry_fill_rate": 0.9}
    assert gate.evaluate(block, thin, g)["verdict"] == "INCONCLUSIVE"
    weak = dict(block, bootstrap_symbol={"mean": {"ci95": [-0.001, 0.007]}})
    assert gate.evaluate(weak, good, g)["verdict"] != "PASS"


def test_cluster_bootstrap_is_deterministic_and_centered():
    from app.backtest.strategy_g0_after_premarket import stats as S
    rng = np.random.default_rng(0)
    clusters = np.repeat(np.arange(50), 20).astype(str)
    values = rng.normal(0, 0.01, clusters.size)
    sel = np.zeros(clusters.size, dtype=bool)
    sel[::3] = True
    a = S.cluster_bootstrap({"mean": values[sel]}, clusters[sel], {"mean": values}, clusters,
                            resamples=500, seed=7)
    b = S.cluster_bootstrap({"mean": values[sel]}, clusters[sel], {"mean": values}, clusters,
                            resamples=500, seed=7)
    assert a == b
    observed = values[sel].mean() - values.mean()
    assert a["mean"]["ci95"][0] < observed < a["mean"]["ci95"][1]
    assert a["mean"]["lift"] == pytest.approx(observed, abs=2e-4)
    assert a["clusters"] == 50
