"""E-MAX-M2 replay: R1 ordering at capacity 3 (C1) and 5 (C2) through ``capacity.execute``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e_max import m1_replay as MR
from app.backtest.strategy_e_r3 import run as R3
from app.strategy_e_max import capacity, ranking

ORDERING = "R1"


def capacity_session(cap: int, sealed, frame, rows, origins, calendar) -> dict[str, Any]:
    candidates = sealed.signal.candidate_symbols
    selected, rest = ranking.select(ORDERING, candidates, MR._features(frame, candidates), cap)
    priority = {s: k + 1 for k, s in enumerate(selected + rest)}
    view = R3.session_frame(frame, rows)
    out = capacity.execute(sealed.signal, selected,
                           {s: rows[s][frame.session].bars for s in selected},
                           view.descriptors, calendar, capacity=cap)
    records = []
    for record in out["records"]:
        record["selection_rank"] = priority[record["symbol"]]
        record["universe_origin"] = origins[record["symbol"]]
        records.append(record)
    for symbol in rest:
        records.append(MR.not_selected_row(frame.session.isoformat(), symbol, priority[symbol],
                                           view.descriptors[symbol], origins[symbol]))
    out["records"] = sorted(records, key=lambda r: r["symbol"])
    out["candidates"] = sealed.signal.candidate_count
    out["selected_order"] = list(selected)
    out["digests"]["seal"] = sealed.seal_digest
    return out


def replay(cap: int, prepared) -> list[dict[str, Any]]:
    p = prepared
    return [capacity_session(cap, p.seals[s], p.frames[s], p.rows, p.origins[s], p.calendar)
            for s in p.frames]


def invariants(prepared, c1, c2) -> dict[str, Any]:
    checks = {"same_h5_and_universe": True, "c1_at_most_three": True, "c2_at_most_five": True,
              "c2_extends_c1_prefix": True, "small_sessions_identical": True}
    for a, b in zip(c1, c2):
        sealed = prepared.seals[date.fromisoformat(a["session"])]
        cands = set(sealed.signal.candidate_symbols)
        for s in (a, b):
            if s["candidates"] != len(cands) or {r["symbol"] for r in s["records"]} != cands:
                checks["same_h5_and_universe"] = False
        checks["c1_at_most_three"] &= len(a["selected_order"]) <= 3
        checks["c2_at_most_five"] &= len(b["selected_order"]) <= 5
        checks["c2_extends_c1_prefix"] &= b["selected_order"][:len(a["selected_order"])] == a["selected_order"]
        if len(cands) <= 3:
            checks["small_sessions_identical"] &= a["selected_order"] == b["selected_order"]
    checks["pass"] = all(checks.values())
    return checks


def opportunity(c1, c2, broad_start: str = MR.BROAD_START) -> dict[str, Any]:
    out = {}
    for name, test in (("FULL_DEVELOPMENT", lambda d: True),
                       ("LEGACY_NARROW", lambda d: d < broad_start),
                       ("BROAD_COVERAGE", lambda d: d >= broad_start)):
        pairs = [(a, b) for a, b in zip(c1, c2) if test(a["session"])]
        out[name] = {
            "sessions_with_h5": sum(a["candidates"] > 0 for a, _ in pairs),
            "sessions_ge_4_candidates": sum(a["candidates"] >= 4 for a, _ in pairs),
            "sessions_ge_5_candidates": sum(a["candidates"] >= 5 for a, _ in pairs),
            "selected_set_changed": sum(set(a["selected_order"]) != set(b["selected_order"])
                                        for a, b in pairs),
            "additional_selected_positions": sum(len(b["selected_order"]) - len(a["selected_order"])
                                                 for a, b in pairs),
            "additional_standard_trades": sum(sum(r["standard_pnl"] for r in b["records"])
                                              - sum(r["standard_pnl"] for r in a["records"])
                                              for a, b in pairs),
        }
    return out


def block_returns(series: np.ndarray, sessions: Sequence[str], blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    index = {s: i for i, s in enumerate(sessions)}
    out = []
    for block in blocks:
        idx = [index[s] for s in sessions if block["first"] <= s <= block["last"]]
        out.append({"block": block["block"], "first": block["first"], "last": block["last"],
                    "sessions": len(idx), "trades": block["trades"], "mean_10bp": block["mean"],
                    "compounded_10bp": float(np.prod(1.0 + series[idx]) - 1.0)})
    return out
