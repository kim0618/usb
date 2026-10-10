"""Q1/Q2/Q3 Forward Detector V1 (`q123-forward.v1`).

The questions are R0's F1-F4; these tests pin that the forward wrapper fires exactly where R0's
conditions say, never treats a disappearance that was not traded through as consumption, keeps
invalid layers out with a named reason, and cannot see the future.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.market_context_r0 import layers as R0L
from app.crypto.research.q123_forward_v1 import contract as K
from app.crypto.research.q123_forward_v1 import detect as D
from app.crypto.research.q123_forward_v1 import journal as J
from app.crypto.research.q123_forward_v1 import outcome as O
from app.crypto.research.q123_forward_v1 import stats as S
from app.crypto.research.q123_forward_v1.frames import SecondFrameX

PKG = Path(__file__).resolve().parents[2] / "app/crypto/research/q123_forward_v1"
T0 = K.PUBLISH_FROM_MS + 3_600_000          # minute open of the approach
LEVEL = 100_000.0
MIN = 60_000


# ----------------------------------------------------------------------------- builders
def minute_frame(side: str, *, minutes=(0,), level=LEVEL, dist=0.1, level_id=7):
    rows = []
    for k in minutes:
        r = {"ts": T0 + k * MIN, "res_price": None, "res_distance_pct": None, "res_id": None,
             "sup_price": None, "sup_distance_pct": None, "sup_id": None,
             "trend_structure": "RANGE", "range_state": "IN_RANGE", "retest_state": "NONE",
             "failed_break_state": "NONE", "atr_5m": 10.0, "active_level_count": 9}
        if side == "RES":
            r.update(res_price=level, res_distance_pct=dist, res_id=level_id)
        else:
            r.update(sup_price=level, sup_distance_pct=-dist, sup_id=level_id)
        rows.append(r)
    ts = np.asarray([r["ts"] for r in rows], dtype=np.int64)
    nan = np.full(len(rows), np.nan)
    return R0L.MinuteFrame(rows=rows, ts=ts, high=nan, low=nan, close=nan, imb_1m=nan,
                           imb_3m=nan, index_of={int(t): i for i, t in enumerate(ts)})


def wall(side: str, mid: float, bps: float):
    price = mid * (1 + bps / 1e4) if side == "ASK" else mid * (1 - bps / 1e4)
    low = round(price - 2.5, 1)
    return str(low), {"bin_low": str(low), "bin_high": str(low + 5.0), "price": str(price),
                      "distance_bps": str(bps if side == "ASK" else -bps)}


def second_frame(mids, *, wall_side="ASK", wall_secs=None, flow=0.5, ok=True, flow_ok=True,
                 band=(1e6, 2e6), gen=None, start=None, session="s" * 36, wall_at=None):
    """One sample a second from the minute close. `wall_secs` = seconds the anchor wall is present."""
    start = T0 + MIN if start is None else start
    m0 = mids[0]
    bin_low, w = wall_at or wall(wall_side, m0, 5.0)
    samples = []
    for k, mid in enumerate(mids):
        present = wall_secs is None or k in wall_secs
        bins = {bin_low: w} if present else {}
        samples.append(R0L.SecondSample(
            ms=start + k * 1000, session8=session[:8], mid=mid,
            ok=ok if not callable(ok) else ok(k),
            flow60_imb=flow if not callable(flow) else flow(k),
            flow60_complete=flow_ok if not callable(flow_ok) else flow_ok(k),
            band_bid=band[1] if wall_side == "ASK" else band[0],
            band_ask=band[0] if wall_side == "ASK" else band[1],
            ask_bins=bins if wall_side == "ASK" else {},
            bid_bins=bins if wall_side == "BID" else {}))
    fr = R0L.SecondFrame(samples=samples, mids=np.asarray(mids, dtype=float),
                         ms=np.asarray([s.ms for s in samples], dtype=np.int64),
                         session_of=np.zeros(len(samples), dtype=np.int32))
    n = len(samples)
    return SecondFrameX(frame=fr, generation=gen or [1] * n, session_id=[session] * n,
                        absorption=["NONE"] * n, liquidity_state=["LIVE"] * n,
                        flow_state=["LIVE"] * n)


def fired(records, q):
    return [r for r in records if r["question"] == q and r["fires"]]


def flat(n=3800, mid=LEVEL * 0.999):
    return [mid] * n


# ----------------------------------------------------------------------------- exact triggers
def test_q1_fires_on_held_ask_wall_buy_flow_no_progress():
    recs, _ = D.detect(minute_frame("RES"), second_frame(flat(), flow=0.4))
    assert [r["question"] for r in fired(recs, "Q1")] == ["Q1"]
    assert not fired(recs, "Q2")
    q1 = fired(recs, "Q1")[0]
    assert q1["lean"] == "SHORT" and q1["r0_family"] == "F1" and q1["validity"] == "VALID"
    assert "RESEARCH / UNVALIDATED" in q1["candidate_label"]


def test_q1_does_not_fire_on_sell_flow_or_after_clearing():
    recs, _ = D.detect(minute_frame("RES"), second_frame(flat(), flow=-0.4))
    assert not fired(recs, "Q1")
    mids = flat(60) + [LEVEL * 1.002] * 3800       # mid at the decision second cleared by > RT
    recs, _ = D.detect(minute_frame("RES"), second_frame(mids, flow=0.4))
    assert not fired(recs, "Q1")


def _consumption_mids(side="ASK"):
    base = LEVEL * 0.999
    bin_low, w = wall(side, base, 5.0)
    through = float(w["bin_high"]) + 1 if side == "ASK" else float(w["bin_low"]) - 1
    return [base] * 30 + [through] * 3800, (bin_low, w)


def test_q2_fires_only_on_true_consumption_candidate():
    mids, wa = _consumption_mids("ASK")
    sx = second_frame(mids, wall_secs=set(range(30)), flow=0.4, band=(1e6, 2e6), wall_at=wa)
    recs, _ = D.detect(minute_frame("RES"), sx)
    assert [r["question"] for r in fired(recs, "Q2")] == ["Q2"]
    q2 = fired(recs, "Q2")[0]
    assert q2["traded_through"] and not q2["wall_present_at_w"] and q2["thinner_ahead"]


def test_cancel_like_disappearance_is_not_consumption():
    sx = second_frame(flat(), wall_secs=set(range(30)), flow=0.4)   # gone, price never reached it
    recs, _ = D.detect(minute_frame("RES"), sx)
    assert not fired(recs, "Q2")


def test_rank_evicted_wall_is_not_a_disappearance():
    # A display eviction leaves the wall in R0's full V2 set (wall_r0); it is present at W.
    mids, wa = _consumption_mids("ASK")
    sx = second_frame(mids, wall_secs=None, flow=0.4, wall_at=wa)
    recs, _ = D.detect(minute_frame("RES"), sx)
    assert not fired(recs, "Q2")


def test_q2_needs_thinner_liquidity_above():
    mids, wa = _consumption_mids("ASK")
    sx = second_frame(mids, wall_secs=set(range(30)), flow=0.4, band=(3e6, 1e6), wall_at=wa)
    recs, _ = D.detect(minute_frame("RES"), sx)
    assert not fired(recs, "Q2")


def test_q3a_and_q3b_are_the_support_mirrors():
    base = LEVEL * 1.001
    recs, _ = D.detect(minute_frame("SUP"), second_frame([base] * 3800, wall_side="BID", flow=-0.4))
    assert [r["question"] for r in fired(recs, "Q3A")] == ["Q3A"]
    assert fired(recs, "Q3A")[0]["lean"] == "LONG"
    bin_low, w = wall("BID", base, 5.0)
    mids = [base] * 30 + [float(w["bin_low"]) - 1] * 3800
    sx = second_frame(mids, wall_side="BID", wall_secs=set(range(30)), flow=-0.4,
                      band=(1e6, 2e6), wall_at=(bin_low, w))
    recs, _ = D.detect(minute_frame("SUP"), sx)
    assert [r["question"] for r in fired(recs, "Q3B")] == ["Q3B"]
    assert fired(recs, "Q3B")[0]["lean"] == "SHORT"


# ----------------------------------------------------------------------------- quality gate
def test_stale_or_unsynced_anchor_is_excluded_with_reason():
    recs, census = D.detect(minute_frame("RES"), second_frame(flat(), ok=False))
    assert recs == []
    assert census["excluded"]["Q1"]["ANCHOR_NOT_OK"] == 1


def test_partial_flow_at_decision_is_excluded_with_reason():
    recs, census = D.detect(minute_frame("RES"), second_frame(flat(), flow_ok=lambda k: k < 50))
    assert recs == []
    assert census["excluded"]["Q1"]["FLOW_NOT_COMPLETE"] == 1


def test_generation_change_inside_w_is_invalid_not_fired():
    gen = [1] * 30 + [2] * 3770
    recs, _ = D.detect(minute_frame("RES"), second_frame(flat(), flow=0.4, gen=gen))
    q1 = [r for r in recs if r["question"] == "Q1"][0]
    assert q1["fires_r0_literal"] and not q1["fires"]
    assert q1["validity"] == "INVALID" and q1["invalid_reason"] == K.GENERATION_CHANGED


def test_no_journal_means_outside_journal_not_zero():
    sx = second_frame(flat(), start=T0 + 5 * 3_600_000)
    recs, census = D.detect(minute_frame("RES"), sx)
    assert recs == [] and census["excluded"]["Q1"]["OUTSIDE_JOURNAL"] == 1


# ----------------------------------------------------------------------------- episodes
def test_episode_dedup_within_60_minutes_of_same_level():
    mf = minute_frame("RES", minutes=(0, 1, 2, 30, 59, 60))
    sx = second_frame(flat(4 * 3600 + 200), flow=0.4)
    recs, census = D.detect(mf, sx)
    assert census["structural_episodes"]["RES"] == 2          # minute 0 and minute 60
    assert len({r["anchor_ts"] for r in recs}) == 2


def test_event_id_is_deterministic_and_identity_bound():
    a = D.event_id("Q1", "RES", 7, LEVEL, T0, "1", "s")
    assert a == D.event_id("Q1", "RES", 7, LEVEL, T0, "1", "s")
    assert a != D.event_id("Q2", "RES", 7, LEVEL, T0, "1", "s")
    assert a != D.event_id("Q1", "RES", 7, LEVEL, T0 + MIN, "1", "s")


# ----------------------------------------------------------------------------- outcomes
def _path_frame(path):
    return second_frame(path).frame


def test_outcomes_at_all_horizons_with_signed_normalisation_and_cost():
    p0 = 100_000.0
    path = [p0] * 61 + [p0 * (1 - 30 / 1e4)] * 3700       # price falls 30 bp after decision
    path[200] = p0 * (1 + 5 / 1e4)                        # a 5 bp adverse spike
    fr = _path_frame(path)
    for h in K.HORIZONS_MIN:
        short = O.outcome(fr, "SHORT", None, "RES", h, decision_index=60)
        long = O.outcome(fr, "LONG", None, "RES", h, decision_index=60)
        assert short["status"] == "MEASURED" and short["horizon_min"] == h
        assert short["raw_move_bp"] == pytest.approx(-30.0, abs=1e-3)
        assert short["directional_move_bp"] == pytest.approx(30.0, abs=1e-3)
        assert long["directional_move_bp"] == pytest.approx(-30.0, abs=1e-3)
        assert short["net_move_bp"] == pytest.approx(30.0 - 11.25, abs=1e-3)
        assert short["est_fee_bp"] + short["est_slippage_bp"] == K.RT_COST_BP
        assert short["mfe_bp"] == pytest.approx(30.0, abs=1e-3)     # downside is favourable
        assert short["mae_bp"] == pytest.approx(5.0, abs=1e-3)      # upside spike is adverse
        assert long["mfe_bp"] == pytest.approx(5.0, abs=1e-3)
        assert long["mae_bp"] == pytest.approx(30.0, abs=1e-3)


def test_horizon_past_data_is_pending_not_estimated():
    fr = _path_frame([100_000.0] * 400)
    assert O.outcome(fr, "SHORT", None, "RES", 15, decision_index=60)["status"] \
        == "PENDING"


# ----------------------------------------------------------------------------- forward only
def test_prefix_invariance_future_samples_change_nothing_known_earlier():
    mf = minute_frame("RES", minutes=(0, 90))
    long = second_frame(flat(3 * 3600), flow=0.4)
    cut = int(long.frame.ms[0]) + 600_000
    short_sx = second_frame(flat(600), flow=0.4)
    full, _ = D.detect(mf, long, decision_cut_ms=cut)
    trunc, _ = D.detect(mf, short_sx, decision_cut_ms=cut)
    assert [J.canonical(r) for r in full] == [J.canonical(r) for r in trunc]
    assert full and all(r["decision_known_ms"] <= cut for r in full)


def test_future_flow_cannot_change_a_decision():
    mf = minute_frame("RES")
    a, _ = D.detect(mf, second_frame(flat(), flow=lambda k: 0.4 if k <= 60 else -0.9))
    b, _ = D.detect(mf, second_frame(flat(), flow=0.4))
    assert [r["fires"] for r in a] == [r["fires"] for r in b]


# ----------------------------------------------------------------------------- journal, stats
def test_journal_is_append_only_and_refuses_a_changed_record(tmp_path):
    recs, _ = D.detect(minute_frame("RES"), second_frame(flat(), flow=0.4))
    assert J.append(tmp_path, recs, [])["episodes_new"] == len(recs)
    before = (tmp_path / J.EPISODES).read_bytes()
    assert J.append(tmp_path, recs, [])["episodes_new"] == 0
    assert (tmp_path / J.EPISODES).read_bytes() == before
    changed = [dict(recs[0], fires=not recs[0]["fires"])]
    with pytest.raises(J.JournalConflict):
        J.append(tmp_path, changed, [])


def test_two_runs_are_byte_identical(tmp_path):
    for d in ("a", "b"):
        recs, _ = D.detect(minute_frame("RES", minutes=(0, 61, 122)),
                           second_frame(flat(4 * 3600), flow=0.4))
        J.append(tmp_path / d, recs, [])
    assert (tmp_path / "a" / J.EPISODES).read_bytes() == (tmp_path / "b" / J.EPISODES).read_bytes()


def test_stage_gates_and_unmeasurable_below_r0_floor():
    assert K.stage(0) == K.stage(19) == "INSUFFICIENT"
    assert K.stage(20) == K.stage(49) == "EARLY_SIGNAL_ONLY"
    assert K.stage(50) == "ELIGIBLE_FOR_SIGNAL_CONTRACT_REVIEW"
    assert K.stage(100) == "STRONGER_EVIDENCE_AVAILABLE"
    recs, _ = D.detect(minute_frame("RES"), second_frame(flat(), flow=0.4))
    summary = S.summarise(recs, [])
    assert summary["questions"]["Q1"]["horizons"]["60"]["measurable"] is False
    assert {g["horizon_min"] for g in summary["grid"]} == set(K.TEST_GRID_HORIZONS_MIN)


# ----------------------------------------------------------------------------- isolation
FORBIDDEN = ("terminal", "live", "paper", "c1", "order", "account", "leverage", "auto", "broker",
             "execution")


def test_no_order_or_account_imports_in_source():
    for path in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for name in names:
                    parts = name.split(".")
                    assert not any(p in FORBIDDEN for p in parts), (path.name, name)


def test_no_order_or_account_module_is_loaded():
    code = ("import sys, app.crypto.research.q123_forward_v1.run as r; "
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('app'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2], check=True).stdout.split()
    bad = [m for m in out if any(p in FORBIDDEN for p in m.split("."))]
    assert bad == []


def test_absorption_is_never_a_condition():
    a, _ = D.detect(minute_frame("RES"), second_frame(flat(), flow=0.4))
    sx = second_frame(flat(), flow=0.4)
    sx.absorption = ["ABSORPTION_CANDIDATE"] * len(sx.absorption)
    b, _ = D.detect(minute_frame("RES"), sx)
    strip = lambda rs: [{k: v for k, v in r.items() if k != "diag"} for r in rs]  # noqa: E731
    assert strip(a) == strip(b)


def test_decision_is_not_taken_from_a_sample_before_the_target_when_data_stops_early():
    # Data ends 3 s before anchor + 60 s. R0's "last sample at or before the target, <= 5 s old"
    # would pick the sample 3 s early; a forward detector cannot know yet that no later one comes.
    mf = minute_frame("RES")
    early, _ = D.detect(mf, second_frame(flat(58), flow=0.4))
    assert early == []
    later, _ = D.detect(mf, second_frame(flat(62), flow=0.4))
    assert later and all(r["decision_known_ms"] >= r["anchor_ms"] + 60_000 for r in later)


def test_horizon_is_pending_until_a_sample_reaches_the_target():
    fr = _path_frame([100_000.0] * (60 + 300 - 2))
    assert O.outcome(fr, "SHORT", None, "RES", 5, decision_index=60)["status"] == "PENDING"
    fr = _path_frame([100_000.0] * (60 + 300 + 1))
    assert O.outcome(fr, "SHORT", None, "RES", 5, decision_index=60)["status"] == "MEASURED"
