"""F0 engine regression tests on synthetic tapes. No market data is read."""

import inspect
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_f0_regular_after import (config, execution as EX, features as F, params as PR,
                                                   pit, run as R, stats as S, verdict as V)
from app.backtest.strategy_f0_regular_after.tape import Tape


@pytest.fixture(scope="module")
def p():
    return PR.from_rules(config.load_rules())


def tape(bars):
    """bars: list of (day, minute, o, h, l, c, v, vw)."""
    a = np.array(bars, dtype=float)
    return Tape("TEST", a[:, 0].astype(np.int16), a[:, 1].astype(np.int16), a[:, 2], a[:, 3], a[:, 4],
                a[:, 5], a[:, 6], a[:, 7])


def session(day, price=10.0, extra=()):
    rows = [(day, m, price, price * 1.01, price * 0.99, price, 100.0, price) for m in range(570, 960)]
    return rows + list(extra)


def test_params_come_from_frozen_rules(p):
    assert p.entry_minute == 965 and p.exit_target == 1020 and p.exit_lookback == 10
    assert p.bootstrap_seed == 2026092701 and p.bootstrap_iterations == 5000
    assert p.hypotheses["H5"][0] == ("day_return", ">=", 0.05)


def test_feature_cutoff_ignores_bars_from_1600(p):
    base = tape(session(0) + session(1))
    after = tape(session(0) + session(1, extra=[(1, 960, 99, 99, 99, 99, 1e6, 99), (1, 965, 50, 50, 50, 50, 1, 50)])
                 + session(2, price=500.0))
    a, b = F.minute_part(base, 1, p), F.minute_part(after, 1, p)
    assert F.identical(a, b)
    assert F.identical(F.finalize(a, 9.0, 0.0, p), F.finalize(b, 9.0, 0.0, p))


def test_pit1_poison_is_invisible_and_pit3_plant_is_detected(p):
    t = tape(session(0) + session(1, extra=[(1, 965, 11, 11, 11, 11, 5, 11)]) + session(2))
    pt = pit.poisoned(t, 1, p, (960, 1200))
    assert F.identical(F.minute_part(t, 1, p), F.minute_part(pt, 1, p))
    assert not F.identical(F.leaky_minute_part(t, 1, p), F.leaky_minute_part(pt, 1, p))


def test_missing_1559_is_invalid_and_not_substituted(p):
    rows = [r for r in session(0) if r[1] != 959] + [(0, 960, 12, 12, 12, 12, 9, 12)]
    part = F.minute_part(tape(rows), 0, p)
    assert np.isnan(part["c1559"])
    assert np.isnan(F.finalize(part, 9.0, 0.0, p)["day_return"])


def test_rvol_needs_five_prior_sessions_and_uses_at_most_twenty(p):
    rows = []
    for d in range(26):
        rows += session(d)
    t = tape(rows)
    assert np.isnan(F.minute_part(t, 4, p)["rvol_median"])
    assert F.minute_part(t, 5, p)["rvol_history_count"] == 5
    assert F.minute_part(t, 25, p)["rvol_history_count"] == 20


def test_alias_is_bit_identical(p):
    f = F.finalize(F.minute_part(tape(session(0)), 0, p), 9.0, 0.0, p)
    assert np.float64(f["last15m_return"]).view(np.int64) == np.float64(f["return_1545_1600"]).view(np.int64)


def bars(ms, price=10.0, high=None):
    m = np.array(ms, dtype=np.int32)
    o = np.full(m.size, price); h = np.full(m.size, price if high is None else high); l = np.full(m.size, price)
    c = np.full(m.size, price)
    return m, o, h, l, c


def test_exact_1605_entry_uses_open(p):
    m, o, h, l, c = bars([965, 1015])
    o[0] = 7.0
    out = EX.resolve(m, o, h, l, c, p.entry_minute, p.exit_target, p.exit_lookback)
    assert out.status == "RESOLVED" and out.entry == 7.0


def test_no_1605_bar_is_no_trade_even_with_later_bars(p):
    out = EX.resolve(*bars([966, 967, 1015]), p.entry_minute, p.exit_target, p.exit_lookback)
    assert out.status == "NO_TRADE"


def test_exit_ten_minute_rule(p):
    assert EX.resolve(*bars([965, 1009, 1020, 1030]), 965, 1020, 10).status == "UNRESOLVED_EXIT"
    m, o, h, l, c = bars([965, 1012, 1015, 1020])
    c[2] = 12.0
    out = EX.resolve(m, o, h, l, c, 965, 1020, 10)
    assert out.exit == 12.0 and out.exit_minute == 1015 and out.exit_age_seconds == 240.0
    assert EX.resolve(*bars([965, 1019]), 965, 1020, 10).exit_age_seconds == 0.0


def test_path_stops_at_exit_bar(p):
    m, o, h, l, c = bars([965, 1019, 1040])
    h[2] = 100.0; l[2] = 0.01
    out = EX.resolve(m, o, h, l, c, 965, 1020, 10)
    assert out.mfe == 0.0 and out.mae == 0.0


def test_baseline_requires_resolved_execution():
    rows = [{"status": "RESOLVED", "masks": {"H1": True}}, {"status": "NO_TRADE", "masks": {"H1": True}},
            {"status": "UNRESOLVED_EXIT", "masks": {"H1": True}}, {"status": "FEATURE_INVALID_NO_1559"}]
    assert len(R.select_baseline(rows)) == 1


def test_bootstrap_is_deterministic(p):
    rng = np.random.default_rng(1)
    day = rng.integers(0, 104, 3000); g = rng.normal(0, 0.01, 3000); m = np.abs(rng.normal(0, 0.03, 3000))
    c1, c2 = S.draw_counts(p, 104), S.draw_counts(p, 104)
    assert np.array_equal(c1, c2)
    h = day % 3 == 0
    a = S.bootstrap(c1, day[h], g[h], m[h], day, g, m, p)
    b = S.bootstrap(c2, day[h], g[h], m[h], day, g, m, p)
    assert a == b


def cells(**over):
    base = {g: "PASS" for g in V.GATE_ORDER}
    base.update(over)
    return base


def test_verdict_function(p):
    ok = dict(pit_pass=True, integrity_critical=False, resolved_sessions=104, p=p)
    assert V.verdict({"H1": cells(), "H2": cells(Tail="FAIL")}, **ok)["verdict"] == "PASS"
    assert V.verdict({"H1": cells(Tail="FAIL")}, **ok)["verdict"] == "FAIL"
    assert V.verdict({"H1": cells(Sample="INSUFFICIENT_SAMPLE")}, **ok)["verdict"] == "INCONCLUSIVE"
    assert V.verdict({"H1": cells(Statistical="UNDERPOWERED")}, **ok)["verdict"] == "INCONCLUSIVE"
    assert V.verdict({"H1": cells(Statistical="UNDERPOWERED", Cost="FAIL")}, **ok)["verdict"] == "FAIL"
    assert V.verdict({"H1": cells()}, **{**ok, "integrity_critical": True})["verdict"] == "INCONCLUSIVE"
    assert V.verdict({"H1": cells(Tail="FAIL")}, **{**ok, "resolved_sessions": 10})["verdict"] == "INCONCLUSIVE"
    assert "BORDERLINE" not in {V.verdict({"H1": cells(Tail="FAIL")}, **ok)["verdict"]}


def test_verdict_takes_no_diagnostic_input():
    params = set(inspect.signature(V.verdict).parameters)
    assert params == {"matrix", "pit_pass", "integrity_critical", "resolved_sessions", "p"}


def test_verdict_is_written_before_any_diagnostic():
    src = Path(R.__file__).read_text()
    verdict_at = src.index('dump(out / "verdict.json"')
    for later in ("secondary_diagnostics.json", "matched_control.json", "event_diagnostic.json",
                  "feature_buckets.json", "auction_diagnostic.json"):
        assert src.index(later) > verdict_at


def test_outcomes_are_read_only_after_pit(p):
    src = Path(R.__file__).read_text()
    assert src.index("PitFailure(f\"F0 PIT FAIL") < src.index("EX.resolve(")
