import numpy as np

from app.crypto.research.c1_exit import e2

N = e2.MAX_HOLD + 1


def _w(**over):
    w = {"open": np.full(N, 100.0), "high": np.full(N, 100.0), "low": np.full(N, 100.0), "close": np.full(N, 100.0),
         "mark_low": np.full(N, 100.0), "eval5": np.zeros(N, bool), "s1_bucket": np.zeros(N, np.int8),
         "oi_chg": np.full(N, -1.0), "rv24h": np.full(N, 0.002), "fund_sofar": np.zeros(N)}
    w.update(over)
    return w


def test_untriggered_rules_exit_at_max_hold():
    for fn in (e2.trig_b1, e2.trig_b3, e2.trig_b4, e2.trig_c1x, e2.trig_c2x, e2.trig_c3x):
        assert fn(_w(), 100.0, 0.02) == (e2.MAX_HOLD, None, None)


def test_b1_stop_fills_at_worse_of_stop_and_open():
    w = _w()
    w["mark_low"][10] = 90.0
    w["open"][10] = 92.0                       # gap below the stop at 100 * (1 - 0.05) = 95
    j, ref, k = e2.trig_b1(w, 100.0, 0.02)
    assert (j, k) == (10, 10) and abs(ref - 92.0) < 1e-9


def test_b3_needs_activation_then_giveback_and_exits_next_open():
    w = _w()
    w["high"][5] = 103.0                       # peak +3% >= 1 sigma (2%)
    w["close"][5:8] = 102.0                   # holds above half the peak until bar 8
    w["close"][8] = 101.4                      # +1.4% <= half of +3%
    assert e2.trig_b3(w, 100.0, 0.02) == (9, None, 8)
    w2 = _w(); w2["high"][5] = 101.5; w2["close"][8] = 100.1   # never activated
    assert e2.trig_b3(w2, 100.0, 0.02)[2] is None


def test_feature_exit_needs_two_consecutive_5m_evaluations():
    w = _w()
    w["eval5"][[4, 9, 14]] = True
    w["s1_bucket"][[4, 14]] = 2                # 9 breaks the run
    assert e2.trig_c1x(w, 100.0, 0.02)[2] is None
    w["s1_bucket"][9] = 3
    assert e2.trig_c1x(w, 100.0, 0.02) == (10, None, 9)


def test_c3x_single_confirmation_ignores_non_eval_bars():
    w = _w()
    w["rv24h"][3] = 0.0005                     # not an evaluation bar
    w["eval5"][7] = True
    w["rv24h"][7] = 0.0005
    assert e2.trig_c3x(w, 100.0, 0.02) == (8, None, 7)


def test_mask_after_erases_by_dtype():
    m = e2.mask_after(_w(), 3)
    assert np.isnan(m["close"][4]) and m["s1_bucket"][4] == -1 and not m["eval5"][4] and m["close"][3] == 100.0
