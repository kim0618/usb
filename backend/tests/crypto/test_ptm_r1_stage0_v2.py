"""PTM-R1 V2 Stage 0 tests (contract docs/crypto/personal_trading_method_r1/CONTRACT_PTM_R1_V2.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data, stage0, stage0_v2, states
from app.crypto.research.ptm_r1.states import NONE, Setup

MIN = 60_000


def vols(quote, taker):
    quote = np.asarray(quote, dtype=np.int64)
    taker = np.asarray(taker, dtype=np.int64)
    n = len(quote)
    return data.Volumes(ts=data.TRAIN_START_MS + np.arange(n, dtype=np.int64) * MIN,
                        quote=quote, taker=taker, valid=np.ones(n, bool))


def random_vols(n, seed=7):
    rng = np.random.default_rng(seed)
    quote = rng.integers(1, 10_000, n) * 2
    taker = np.clip(quote // 2 + rng.integers(-3_000, 3_000, n), 0, quote)
    return vols(quote, taker)


@pytest.mark.parametrize("block", [60, 240, 1440])
def test_block_lean_is_last_completed_block_brute_force(block):
    v = random_vols(5 * 1440 + 77, seed=block)
    got = states.block_lean(v, block)
    for t in range(0, len(got), 53):
        done = (t + 1) // block
        if done == 0:
            assert got[t] == NONE
            continue
        w = slice((done - 1) * block, done * block)
        net = (2 * v.taker[w] - v.quote[w]).sum()
        assert got[t] == np.sign(net)


def test_block_lean_ignores_block_in_progress_and_changes_only_at_block_close():
    v = random_vols(3 * 240, seed=2)
    got = states.block_lean(v, 240)
    assert (got[:239] == NONE).all()
    assert got[239] != NONE                               # first block closes with minute 239
    assert len(set(got[239:479])) == 1                    # constant until the next close
    with pytest.raises(ValueError):
        states.block_lean(states_slice(v, 7), 240)


def states_slice(v, k):
    return data.Volumes(ts=v.ts[k:], quote=v.quote[k:], taker=v.taker[k:], valid=v.valid[k:])


def test_persistent_confirms_after_hold_and_sticks():
    raw = np.array([1, 1, -1, -1, -1, 1, 1, 1, 0, -1, -1, -1], dtype=np.int8)
    got = states.persistent(raw, 3)
    assert list(got) == [NONE, NONE, NONE, NONE, -1, -1, -1, 1, 1, 1, 1, -1]
    raw = np.array([1, NONE, 1, 1], dtype=np.int8)
    assert list(states.persistent(raw, 2)) == [NONE, NONE, NONE, 1]


@pytest.mark.parametrize("fn", [states.states_v2a, states.states_v2b])
def test_v2_no_future_leak(fn):
    v = random_vols(4 * 1440, seed=9)
    cut = 2 * 1440 + 311
    q, b = v.quote.copy(), v.taker.copy()
    b[cut + 1:] = q[cut + 1:]
    a, z = fn(v), fn(vols(q, b))
    for k in a:
        assert np.array_equal(a[k][:cut + 1], z[k][:cut + 1]), k
    assert ([s for s in states.detect_setups(a) if s.t <= cut]
            == [s for s in states.detect_setups(z) if s.t <= cut])


@pytest.mark.parametrize("fn", [states.states_v2a, states.states_v2b])
def test_v2_long_short_symmetry(fn):
    v = random_vols(4 * 1440, seed=13)
    a, b = fn(v), fn(vols(v.quote, v.quote - v.taker))
    for k in a:
        d = a[k] != NONE
        assert np.array_equal(d, b[k] != NONE)
        assert np.array_equal(a[k][d], -b[k][d])
    sa, sb = states.detect_setups(a), states.detect_setups(b)
    assert [(s.t, -s.direction, s.family) for s in sa] == [(s.t, s.direction, s.family) for s in sb]


def test_family_48h_dedup_merges_directions():
    s = [Setup(0, 1, "F2"), Setup(100, -1, "F2"), Setup(200, 1, "F1"), Setup(2880, -1, "F2")]
    assert states.dedup_family_48h(s) == [True, False, True, True]


def test_run_lengths_drop_edge_runs():
    s = np.array([1, 1, -1, -1, -1, 1, 0, 0, 1, 1], dtype=np.int8)
    assert list(states.run_lengths(s)) == [3, 1]          # 0-runs excluded, edge runs dropped


def test_v2_census_deterministic_and_has_no_performance_fields():
    v = random_vols(6 * 1440, seed=17)
    for fn in (states.states_v2a, states.states_v2b, states.all_states):
        c1, c2 = stage0_v2.census(fn(v)), stage0_v2.census(fn(v))
        assert c1 == c2
        assert stage0.forbidden_keys(c1) == []


def test_survival_counts_against_hand_example():
    # LONG thesis: full alignment at t=1440, I4 (big direction lost) at t = 1440 + 5h
    n = 1440 * 3
    one = np.ones(n, dtype=np.int8)
    st = {"M": one.copy(), "H1": one.copy(), "H4": one.copy(), "D": one.copy()}
    st["M"][1439] = -1                                     # F2 pullback right before
    st["H4"][1440 + 300:] = -1
    c = stage0_v2.census(st)
    surv = c["families"]["F2"]["LONG"]["invalidation"]["I4"]["survival"]
    assert surv["4h"] == {"eligible": 1, "thesis_alive": 1, "alive_share": 1.0}
    assert surv["8h"] == {"eligible": 1, "thesis_alive": 0, "alive_share": 0.0}


def test_verdict_rules():
    def fake(flips_d, regime_med, share12_alive, eligible, alive24, n48):
        inv = {"I4": {"survival": {"12h": {"eligible": eligible, "thesis_alive": share12_alive},
                                   "24h": {"eligible": eligible, "thesis_alive": alive24}}}}
        fam = {"LONG": {"invalidation": inv},
               "SHORT": {"invalidation": {"I4": {"survival": {
                   "12h": {"eligible": 0, "thesis_alive": 0},
                   "24h": {"eligible": 0, "thesis_alive": 0}}}}},
               "overlap_adjusted_48h_N": n48}
        return {"flips": {"D": {"changes_per_day": flips_d}},
                "duration": {"REGIME": {"median_min": regime_med}},
                "families": {"F1": fam, "F2": fam, "F3": fam}}
    v1 = fake(7.6, 30, 0, 0, 0, 0)
    assert stage0_v2.judge(v1, fake(1.0, 200, 300, 1000, 150, 150), True, True)["verdict"] \
        == "B_V2_RESEARCHABLE"
    assert stage0_v2.judge(v1, fake(3.0, 200, 300, 1000, 150, 150), True, True)["verdict"] \
        == "A_V2_THESIS_TOO_FAST"
    assert stage0_v2.judge(v1, fake(1.0, 200, 100, 1000, 150, 150), True, True)["verdict"] \
        == "A_V2_THESIS_TOO_FAST"
    assert stage0_v2.judge(v1, fake(1.0, 200, 300, 1000, 150, 99), True, True)["verdict"] \
        == "C_FAMILY_TOO_RARE"
    assert stage0_v2.judge(v1, fake(1.0, 200, 300, 1000, 150, 150), True, False)["verdict"] \
        == "D_CONTRACT_OR_DATA_PROBLEM"
