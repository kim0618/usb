"""Episodes and question firing: R0's `minute_episodes` and `joint_family`, called unchanged.

What is added is bookkeeping, not judgement: the contract section 5 generation gate, the
contract section 4 event identity, the per question record, and diagnostic columns that never feed
a condition.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import numpy as np

from ..market_context_r0 import families as R0F
from ..market_context_r0 import layers as R0L
from . import VERSION
from . import contract as K
from .frames import SecondFrameX

MINUTE_MS = 60_000


def event_id(question: str, side: str, level_id, level_price, anchor_ts: int, wall_bin_low,
             session_id: str | None) -> str:
    key = "|".join(str(x) for x in (VERSION, question, side, level_id, level_price, anchor_ts,
                                     wall_bin_low, session_id))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def utc_session(ms: int) -> str:
    """Diagnostic bucket only (contract section 1 / 2): ASIA 00-08, EUROPE 08-13, US 13-21, LATE 21-24 UTC."""
    h = datetime.fromtimestamp(ms / 1000, timezone.utc).hour
    return "ASIA" if h < 8 else "EUROPE" if h < 13 else "US" if h < 21 else "LATE_US"


def _anchor_index(sf: R0L.SecondFrame, anchor_ts: int) -> int | None:
    """The same anchor second `joint_family` uses: first sample at or after the minute close."""
    info_ms = anchor_ts + MINUTE_MS
    i = int(np.searchsorted(sf.ms, info_ms, side="left"))
    if i >= len(sf.ms) or sf.ms[i] - info_ms > 5_000:
        return None
    return i


def known_index(sf: R0L.SecondFrame, target_ms: int) -> int | None:
    """The first sample at or after `target_ms`: the moment "the last sample at or before the target"
    stops being able to change. Until it exists, a decision (or a horizon) is not yet known."""
    k = int(np.searchsorted(sf.ms, target_ms, side="left"))
    return None if k >= len(sf.ms) else k


def detect(mf: R0L.MinuteFrame, sx: SecondFrameX, *, decision_cut_ms: int | None = None
           ) -> tuple[list[dict], dict]:
    """One record per (structural episode, question of its side). Returns (records, census).

    A record exists only once its decision is known (`known_index`), and `decision_cut_ms` keeps
    records known at or before the cut, which is what makes a truncated run a prefix of a full one.
    """
    sf = sx.frame
    episodes = R0F.minute_episodes(mf)
    records: list[dict] = []
    census: dict = {"structural_episodes": {"RES": 0, "SUP": 0}, "excluded": {}}
    for e in episodes:
        census["structural_episodes"][e.side] += 1
    for question, fam in K.QUESTIONS.items():
        spec = R0F.joint_family(mf, sf, episodes, fam, K.VARIANT)
        census["excluded"][question] = dict(spec["excluded"])
        side, lean = spec["side"], spec["lean"]
        for row in spec["episodes"]:
            i = _anchor_index(sf, row["anchor_ts"])
            j = row["i_dec"]
            dec_ms = int(sf.ms[j])
            k = known_index(sf, int(sf.ms[i]) + R0F.WALL_W_MS)
            if k is None:
                continue                      # pending: a later sample could still be the decision
            known_ms = int(sf.ms[k])
            if decision_cut_ms is not None and known_ms > decision_cut_ms:
                continue
            gen_i, gen_j = sx.generation[i], sx.generation[j]
            valid = gen_i == gen_j and gen_i is not None
            sess = sx.session_id[j]
            records.append({
                "version": VERSION,
                "event_id": event_id(question, side, row["level_id"], row["level_price"],
                                     row["anchor_ts"], row["wall_bin_low"], sess),
                "question": question, "r0_family": fam, "lean": lean, "side": side,
                "candidate_label": K.CANDIDATE_LABEL[question],
                "anchor_ts": row["anchor_ts"], "anchor_ms": int(sf.ms[i]), "decision_ms": dec_ms, "decision_known_ms": known_ms,
                "session_id": sess, "level_id": row["level_id"], "level_price": row["level_price"],
                "wall_bin_low": row["wall_bin_low"], "wall_distance_bps": row["wall_distance_bps"],
                "wall_present_at_w": row["wall_present_at_w"],
                "traded_through": row["traded_through"], "thinner_ahead": row["thinner_ahead"],
                "cleared": row["cleared"], "flow60_imb": row["flow60_imb"],
                "fires": bool(row["fires"]) and valid,
                "fires_r0_literal": bool(row["fires"]),
                "validity": "VALID" if valid else "INVALID",
                "invalid_reason": None if valid else K.GENERATION_CHANGED,
                "generation_anchor": gen_i, "generation_decision": gen_j,
                "p0_mid": None if not np.isfinite(sf.mids[j]) else float(sf.mids[j]),
                "diag": {"absorption_at_decision": sx.absorption[j],
                         "liquidity_state_at_decision": sx.liquidity_state[j],
                         "flow_state_at_decision": sx.flow_state[j],
                         "utc_session": utc_session(dec_ms),
                         "oi": "NOT_COLLECTED", "funding": "NOT_COLLECTED",
                         "basis": "NOT_COLLECTED", "c1": "NOT_COLLECTED"}})
    by_ts = {r["ts"]: r for r in mf.rows}
    for rec in records:
        row = by_ts.get(rec["anchor_ts"]) or {}
        rec["structure"] = {k: row.get(k) for k in (
            "trend_structure", "range_state", "retest_state", "failed_break_state",
            "res_distance_pct", "sup_distance_pct", "atr_5m", "active_level_count")}
    records.sort(key=lambda r: (r["anchor_ts"], r["question"], r["event_id"]))
    return records, census
