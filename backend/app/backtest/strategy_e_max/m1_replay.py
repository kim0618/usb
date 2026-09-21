"""E-MAX-M1 replay: one ordering at a time, through the unchanged E-D2..E-D5 layers.

R0 is the E-R3 path itself. R1..R3 pass the variant's selected symbols to
``strategy_e_d6.replay.replay_session`` as the candidate set and record the remaining H5
candidates as ``NOT_SELECTED_CAPACITY`` rows. Before any variant is read, the same subset method
run with R0's order must reproduce R0 record for record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
from typing import Any

import numpy as np

from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6.replay import SCENARIOS, replay_session
from app.backtest.strategy_e_r3 import run as R3
from app.strategy_e.execution import NOT_SELECTED_CAPACITY
from app.strategy_e.exits import NO_EXIT_NO_VALID_ENTRY, NO_VALID_ENTRY
from app.strategy_e.risk import NOT_SELECTED
from app.strategy_e_max import ranking

PRIMARY = "COST_10BP"
BROAD_START = "2026-04-20"


def _features(frame, symbols: Sequence[str]) -> dict[str, dict[str, float]]:
    index = {s: k for k, s in enumerate(frame.symbols)}
    return {s: {name: float(frame.features[name][index[s]]) for name in
                ("premarket_rvol", "return_0900_0925", "position_in_premarket_range",
                 "premarket_gap")} for s in symbols}


def not_selected_row(session: str, symbol: str, rank: int, descriptors: Mapping[str, float],
                     origin: str) -> dict[str, Any]:
    """The row E-D2..E-D5 produce for an H5 candidate beyond capacity."""
    return {"session": session, "symbol": symbol, "selection_rank": rank, "selected": False,
            "entry_status": NOT_SELECTED_CAPACITY, "entry_price": None,
            "exit_status": NO_VALID_ENTRY, "exit_reason": NO_EXIT_NO_VALID_ENTRY,
            "exit_price": None, "risk_status": NOT_SELECTED, "skip_reason": NOT_SELECTED_CAPACITY,
            "weight": "0/1", "standard_pnl": False, **descriptors, "universe_origin": origin}


def variant_session(rule: str, sealed, frame, rows, origins, calendar) -> dict[str, Any]:
    signal = sealed.signal
    candidates = signal.candidate_symbols
    selected, rest = ranking.select(rule, candidates, _features(frame, candidates))
    priority = {s: k + 1 for k, s in enumerate(selected + rest)}
    subset = replace(signal, candidate_symbols=tuple(sorted(selected)),
                     candidate_count=len(selected))
    view = R3.session_frame(frame, rows)
    result = replay_session(subset, view, {s: rows[s][frame.session].bars for s in selected},
                            calendar)
    records = []
    for record in result["records"]:
        record["selection_rank"] = priority[record["symbol"]]
        record["universe_origin"] = origins[record["symbol"]]
        records.append(record)
    for symbol in rest:
        records.append(not_selected_row(frame.session.isoformat(), symbol, priority[symbol],
                                        view.descriptors[symbol], origins[symbol]))
    result["records"] = sorted(records, key=lambda r: r["symbol"])
    result["candidates"] = signal.candidate_count
    result["selected_order"] = list(selected)
    result["digests"] = {"signal": signal.decision_digest, "seal": sealed.seal_digest,
                         "selection": hashlib.sha256(f"{rule}|{'|'.join(selected)}".encode()).hexdigest(),
                         "execution": result["digests"]["execution"],
                         "exit": result["digests"]["exit"], "sizing": result["digests"]["sizing"]}
    return result


def replay_variant(rule: str, prepared) -> list[dict[str, Any]]:
    p = prepared
    return [variant_session(rule, p.seals[s], p.frames[s], p.rows, p.origins[s], p.calendar)
            for s in p.frames]


def replay_base(prepared) -> list[dict[str, Any]]:
    p = prepared
    out = []
    for s in p.frames:
        result = R3.replay_sealed(p.seals[s], p.frames[s], p.rows, p.origins[s], p.calendar)
        result["selected_order"] = list(p.seals[s].selected)
        out.append(result)
    return out


def _comparable(replayed: Sequence[Mapping[str, Any]]) -> list:
    return [(s["session"], s["candidates"], s["eligible"], s["active"], s["exposure"],
             {k: str(v) for k, v in s["returns"].items()},
             sorted((tuple(sorted((k, str(v)) for k, v in r.items())) for r in s["records"])))
            for s in replayed]


def equivalent(base: Sequence[Mapping[str, Any]], subset_r0: Sequence[Mapping[str, Any]]) -> bool:
    return _comparable(base) == _comparable(subset_r0)


def session_series(replayed, sessions: Sequence[str], scenario: str = PRIMARY) -> np.ndarray:
    by = {s["session"]: s for s in replayed}
    return np.array([float(by[s]["returns"][scenario]) if s in by else 0.0 for s in sessions])


def invariants(prepared, variants: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    checks = {"same_h5_and_universe": True, "selected_within_candidates": True,
              "at_most_three": True, "small_sessions_identical": True}
    base = {s["session"]: s for s in variants["R0"]}
    for name, replayed in variants.items():
        for s in replayed:
            sealed = prepared.seals[date.fromisoformat(s["session"])]
            cands = set(sealed.signal.candidate_symbols)
            if (s["candidates"] != len(cands) or {r["symbol"] for r in s["records"]} != cands
                    or s["eligible"] != base[s["session"]]["eligible"]):
                checks["same_h5_and_universe"] = False
            chosen = [r["symbol"] for r in s["records"] if r["selected"]]
            if not set(chosen) <= cands:
                checks["selected_within_candidates"] = False
            if len(chosen) > 3:
                checks["at_most_three"] = False
            if len(cands) <= 3 and set(chosen) != set(base[s["session"]]["selected_order"]):
                checks["small_sessions_identical"] = False
    checks["pass"] = all(checks.values())
    return checks


def selection_delta(base, variant) -> dict[str, Any]:
    b = {s["session"]: s for s in base}
    out = {"sessions_with_h5": 0, "eligible_to_change": 0, "selected_set_changed": 0,
           "ordering_only_changed": 0, "symbols_added": 0, "symbols_removed": 0,
           "trade_set_changed": 0, "entry_valid_changed": 0, "standard_pnl_changed": 0}
    for s in variant:
        base_s = b[s["session"]]
        out["sessions_with_h5"] += s["candidates"] > 0
        out["eligible_to_change"] += s["candidates"] > 3
        vs, bs = set(s["selected_order"]), set(base_s["selected_order"])
        if vs != bs:
            out["selected_set_changed"] += 1
        elif s["selected_order"] != base_s["selected_order"]:
            out["ordering_only_changed"] += 1
        out["symbols_added"] += len(vs - bs)
        out["symbols_removed"] += len(bs - vs)

        def picked(session, test):
            return {r["symbol"] for r in session["records"] if test(r)}
        out["trade_set_changed"] += picked(s, lambda r: r["selected"]) != picked(base_s, lambda r: r["selected"])
        out["entry_valid_changed"] += (picked(s, lambda r: r["entry_status"] == "EXECUTED_PROXY")
                                       != picked(base_s, lambda r: r["entry_status"] == "EXECUTED_PROXY"))
        out["standard_pnl_changed"] += (picked(s, lambda r: r["standard_pnl"])
                                        != picked(base_s, lambda r: r["standard_pnl"]))
    out["selected_set_changed_ratio_to_eligible"] = (out["selected_set_changed"] / out["eligible_to_change"]
                                                     if out["eligible_to_change"] else None)
    return {k: int(v) if isinstance(v, (bool, np.bool_)) else v for k, v in out.items()}


def extremes(replayed) -> dict[str, Any]:
    trades = [(float(r[PRIMARY]), r["symbol"], r["session"]) for s in replayed
              for r in s["records"] if r["standard_pnl"]]
    if not trades:
        return {"largest_winner": None, "largest_loser": None}
    best, worst = max(trades), min(trades)
    return {"largest_winner": {"net_10bp": best[0], "symbol": best[1], "session": best[2]},
            "largest_loser": {"net_10bp": worst[0], "symbol": worst[1], "session": worst[2]}}


def period_table(replayed, base_series: np.ndarray, sessions: Sequence[str]) -> dict[str, Any]:
    series = session_series(replayed, sessions)
    trades = {s["session"]: [float(r[PRIMARY]) for r in s["records"] if r["standard_pnl"]]
              for s in replayed}
    periods = {"FULL_DEVELOPMENT": lambda d: True,
               "LEGACY_NARROW": lambda d: d < BROAD_START,
               "BROAD_COVERAGE": lambda d: d >= BROAD_START}
    out = {}
    for name, test in periods.items():
        idx = [i for i, d in enumerate(sessions) if test(d)]
        rets = np.concatenate([np.array(trades.get(sessions[i], []), dtype=float) for i in idx]) \
            if idx else np.array([])
        sub = series[idx]
        out[name] = {"first": sessions[idx[0]], "last": sessions[idx[-1]], "sessions": len(idx),
                     "trades": int(rets.size),
                     "active_sessions": int(sum(bool(trades.get(sessions[i])) for i in idx)),
                     "mean_session_10bp": float(sub.mean()),
                     "cumulative_10bp": float(np.prod(1.0 + sub) - 1.0),
                     "profit_factor_10bp": X.profit_factor(rets) if rets.size else None,
                     "paired_mean_delta_vs_r0": float((sub - base_series[idx]).mean())}
    return out


def to_float(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value
