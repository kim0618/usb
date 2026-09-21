"""E-MAX-M4 replay: R1 / max 3 / B2 with the X1 (09:34) or X2 (conditional 09:44) exit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e_max import m1_replay as MR, m3_replay as M3R
from app.backtest.strategy_e_r3 import run as R3
from app.strategy_e.execution import EntryBar
from app.strategy_e_max import breadth, exit_x2, ranking

MINUTE_0944 = 9 * 60 + 44
_CTX: dict[str, Any] = {}


def _init(view_root: str) -> None:
    _CTX["root"] = Path(view_root)
    _CTX["edges"], _CTX["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def _bars_0944(job: tuple[str, tuple[str, ...]]) -> tuple[str, dict[str, list[EntryBar]]]:
    symbol, sessions = job
    tape = M.load_symbol_tape(symbol, _CTX["root"], _CTX["edges"], _CTX["offsets"])
    out: dict[str, list[EntryBar]] = {s: [] for s in sessions}
    if tape is None:
        return symbol, out
    for s in sessions:
        day = date.fromisoformat(s)
        idx = np.flatnonzero((tape.et_day == M.ordinal(day)) & (tape.minute == MINUTE_0944))
        for i in idx:
            vwap = float(tape.vwap[i])
            out[s].append(EntryBar(symbol=symbol, session_date=day,
                                   bar_start_et=exit_x2.EXTENDED_BAR_START_ET,
                                   open=float(tape.open[i]), high=float(tape.high[i]),
                                   low=float(tape.low[i]), close=float(tape.close[i]),
                                   volume=float(tape.volume[i]),
                                   vwap=vwap if np.isfinite(vwap) else None))
    return symbol, out


def selections(prepared) -> dict[date, tuple[tuple[str, ...], tuple[str, ...]]]:
    out = {}
    for d, frame in prepared.frames.items():
        cands = prepared.seals[d].signal.candidate_symbols
        out[d] = ranking.select("R1", cands, MR._features(frame, cands), 3)
    return out


def fetch_0944(view_root: Path, chosen, *, workers: int) -> dict[str, dict[str, list[EntryBar]]]:
    wanted: dict[str, set[str]] = {}
    for d, (selected, _) in chosen.items():
        for s in selected:
            wanted.setdefault(s, set()).add(d.isoformat())
    jobs = sorted((s, tuple(sorted(v))) for s, v in wanted.items())
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(view_root),)) as pool:
        return dict(pool.map(_bars_0944, jobs, chunksize=4))


def session(prepared, d: date, chosen, bars44, *, continuation: bool) -> dict[str, Any]:
    frame, sealed, rows, origins = prepared.frames[d], prepared.seals[d], prepared.rows, prepared.origins[d]
    selected, rest = chosen[d]
    priority = {s: k + 1 for k, s in enumerate(selected + rest)}
    view = R3.session_frame(frame, rows)
    bars = {s: {**rows[s][d].bars, exit_x2.EXTENDED_BAR_START_ET: bars44.get(s, {}).get(d.isoformat(), [])}
            for s in selected}
    out = exit_x2.execute(sealed.signal, selected, bars, view.descriptors, prepared.calendar,
                          capacity=3, continuation=continuation)
    records = []
    for record in out["records"]:
        record["selection_rank"] = priority[record["symbol"]]
        record["universe_origin"] = origins[record["symbol"]]
        records.append(record)
    for symbol in rest:
        records.append(MR.not_selected_row(d.isoformat(), symbol, priority[symbol],
                                           view.descriptors[symbol], origins[symbol]))
    out["records"] = sorted(records, key=lambda r: r["symbol"])
    out["candidates"] = sealed.signal.candidate_count
    out["selected_order"] = list(selected)
    out["digests"]["seal"] = sealed.seal_digest
    k = breadth.multiplier(sealed.signal.candidate_count, sealed.signal.eligible_count)
    return breadth.scale(out, k)


def replay(prepared, chosen, bars44, *, continuation: bool) -> list[dict[str, Any]]:
    return [session(prepared, d, chosen, bars44, continuation=continuation) for d in prepared.frames]


def invariants(x1, x2) -> dict[str, Any]:
    checks = {"same_selection_entries_and_exposure": True, "unextended_records_identical": True,
              "zero_delta_without_extension": True}
    for a, b in zip(x1, x2):
        extended = {d["symbol"] for d in b["exit_decisions"] if d["extended"]}
        if (a["selected_order"] != b["selected_order"] or a["breadth_multiplier"] != b["breadth_multiplier"]
                or [(r["symbol"], r["entry_status"], r["entry_price"]) for r in a["records"]]
                != [(r["symbol"], r["entry_status"], r["entry_price"]) for r in b["records"]]):
            checks["same_selection_entries_and_exposure"] = False
        if not extended:
            if a["records"] != b["records"] or a["returns"] != b["returns"]:
                checks["zero_delta_without_extension"] = False
        else:
            strip = lambda r: {k: v for k, v in r.items() if k != "weight"}
            for ra, rb in zip(a["records"], b["records"]):
                if ra["symbol"] not in extended and ra["standard_pnl"] == rb["standard_pnl"] \
                        and strip(ra) != strip(rb):
                    checks["unextended_records_identical"] = False
    checks["pass"] = all(checks.values())
    return checks


def exit_funnel(x1, x2) -> dict[str, Any]:
    d1 = [d for s in x1 for d in s["exit_decisions"]]
    d2 = [d for s in x2 for d in s["exit_decisions"]]
    valid_0934 = sum(d["x1_exit_status"] == "VALID_EXIT" for d in d2)
    extended = [d for d in d2 if d["extended"]]
    return {"x1_0934_exits": sum(d["final_exit_status"] == "VALID_EXIT" for d in d1),
            "x1_unresolved": sum(d["final_exit_status"] != "VALID_EXIT" for d in d1),
            "x2_0934_exits": sum(not d["extended"] and d["final_exit_status"] == "VALID_EXIT" for d in d2),
            "x2_0944_extended_exits": sum(d["final_exit_status"] == "VALID_EXIT" for d in extended),
            "x2_0944_unresolved": sum(d["final_exit_status"] != "VALID_EXIT" for d in extended),
            "x2_unresolved_at_0934": sum(d["x1_exit_status"] != "VALID_EXIT" for d in d2),
            "extension_attempts": len(extended),
            "extension_rate_of_valid_0934": len(extended) / valid_0934 if valid_0934 else None,
            "unresolved_0944_reasons": _count(d["final_exit_reason"] for d in extended
                                              if d["final_exit_status"] != "VALID_EXIT")}


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def continuation_outcome(x2) -> dict[str, Any]:
    rows = [d for s in x2 for d in s["exit_decisions"]
            if d["extended"] and d["final_exit_status"] == "VALID_EXIT"]

    def stats(values):
        return {"mean": float(np.mean(values)), "median": float(median(values)),
                "positive_rate": float(np.mean(np.array(values) > 0))} if values else None

    r34 = [d["x1_exit_price"] / d["entry_price"] - 1 for d in rows]
    r44 = [d["final_exit_price"] / d["entry_price"] - 1 for d in rows]
    inc = [d["final_exit_price"] / d["x1_exit_price"] - 1 for d in rows]
    return {"count": len(rows), "gross_at_0934": stats(r34), "gross_at_0944": stats(r44),
            "incremental_0934_to_0944": stats(inc),
            "note": "gross, unweighted, per extended position with a valid 09:44 exit"}


def changed_sessions(delta: np.ndarray, sessions: Sequence[str], x2,
                     blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    changed = {s["session"] for s in x2 if any(d["extended"] for d in s["exit_decisions"])}
    index = {s: i for i, s in enumerate(sessions)}

    def summarise(days):
        idx = [index[d] for d in days]
        ch = [index[d] for d in days if d in changed]
        dd = delta[ch] if ch else np.array([])
        return {"sessions": len(idx), "changed_sessions": len(ch),
                "positive_delta": int((dd > 0).sum()), "negative_delta": int((dd < 0).sum()),
                "zero_delta": int((dd == 0).sum()),
                "mean_delta_10bp": float(delta[idx].mean()) if idx else None,
                "sum_delta_10bp": float(delta[idx].sum()) if idx else 0.0}

    out = {"FULL_DEVELOPMENT": summarise(sessions),
           "LEGACY_NARROW": summarise([s for s in sessions if s < MR.BROAD_START]),
           "BROAD_COVERAGE": summarise([s for s in sessions if s >= MR.BROAD_START])}
    out["blocks"] = [{"block": b["block"], **summarise([s for s in sessions if b["first"] <= s <= b["last"]])}
                     for b in blocks]
    return out


def tail_dependence(delta: np.ndarray, sessions: Sequence[str]) -> dict[str, Any]:
    total = float(delta.sum())
    order = np.argsort(-delta, kind="stable")
    out = {"total_delta_10bp": total}
    for k in (1, 3, 5):
        top = float(delta[order[:k]].sum())
        out[f"top{k}"] = {"sessions": [sessions[i] for i in order[:k]], "sum": top,
                          "share_of_total": (top / total) if total > 0 else None}
    return out
