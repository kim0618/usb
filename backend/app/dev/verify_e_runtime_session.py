"""E-RUNTIME-R1 independent recomputation of one E session (shadow validation gate).

    PYTHONPATH=backend python -m app.dev.verify_e_runtime_session \\
        --run-dir <run root>/<session> --universe <universe dir> --store <rvol store> \\
        --book-root <run root>/paper_state_v2/SHADOW_VALIDATION [--out report.json]

It re-derives the session from what the runner persisted as *inputs* - the merged premarket bars
(``premarket_raw.json.gz``), the D-1 universe artifact and the RVOL store as it stood at the seal
(rows collected later are ignored, rows repaired later are read with their pre-repair values) -
and compares every step with what the runner recorded: universe identity, completeness, timing,
cutoff, features, RVOL, H5, R1 / Top3, B2 and exposure, entry, exit, cost and the book. The rules
are the frozen ones; nothing here imports the runtime's decision code.

Verdict ``E-RUNTIME-R1 SHADOW PASS`` only when every check passes.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, time
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[3]
RULES = REPO / "docs/backtest/strategy_e_max/strategy_e_max_v1_rules.json"
RULES_SHA256 = "b30a3e95d6333c17d736c4c4fa23f55a7f67ba4af328317379a86a4a37e89d0e"
UNIVERSE_DIGEST_FIELDS = ("format", "target_session", "asof_session", "rules_version", "inputs", "symbols",
                          "close_d_minus_1", "dollar_volume_d_minus_1", "spy_close_d_minus_1")
PASS_LABEL, FAIL_LABEL = "E-RUNTIME-R1 SHADOW PASS", "E-RUNTIME-R1 SHADOW FAIL"


def frozen_rules() -> dict[str, Any]:
    body = json.loads(RULES.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    if digest != RULES_SHA256:
        raise SystemExit("frozen E-MAX V1 rules do not match their canonical sha256")
    d = body["definition"]
    alpha = d["alpha"]["rule"]
    expected = ("premarket_gap > 0 AND premarket_rvol >= 3.0 AND position_in_premarket_range >= 0.8 "
                "AND return_0900_0925 > 0")
    if alpha != expected:
        raise SystemExit(f"H5 text changed: {alpha}")
    return {"rvol_min": 3.0, "pos_min": 0.8, "max_selected": int(d["capacity"]["max_selected"]),
            "breadth_threshold": float(d["breadth"]["threshold"]), "breadth_min_rows": int(d["breadth"]["min_universe_rows"]),
            "exposure": {"normal": Fraction(str(d["final_session_exposure"]["normal"])),
                         "high": Fraction(str(d["final_session_exposure"]["high_breadth"]))},
            "cost_bp": Decimal(d["cost"]["primary"].replace("COST_", "").replace("BP", ""))}


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, **detail: Any) -> bool:
        self.checks.append({"check": name, "pass": bool(ok), **detail})
        return ok

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c["pass"] for c in self.checks)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _previous_session(day: date) -> date:
    from app.market.calendar import MarketCalendar
    return MarketCalendar("America/New_York").previous_trading_day(day)


# -- premarket features from the raw bars (the frozen research block, re-derived) -----------------

PREMARKET_START, DECISION_LAST = 240, 564


def premarket(bars: list[list[float]]) -> dict[str, float] | None:
    from app.backtest.strategy_e1_premarket.premarket import premarket_block
    rows = [b for b in bars if PREMARKET_START <= b[0] <= DECISION_LAST]
    if not rows:
        return None
    a = np.array([[*b, float("nan")] for b in rows], dtype=float)
    return premarket_block(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], DECISION_LAST)


# -- the RVOL store as it stood at the seal --------------------------------------------------------

def store_as_of(path: Path, seal: datetime) -> dict[str, list[tuple[str, float]]]:
    """symbol -> [(session, pm_dollar)] of staged complete rows known at ``seal``."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    reverted: dict[tuple[str, str], tuple] = {}
    try:
        for row in connection.execute(
                "SELECT symbol, session, old_pm_dollar_volume, old_staged, old_collected_at, repaired_at "
                "FROM kiwoom_repair_log"):
            symbol, session, pm, staged, collected, repaired = row
            if datetime.fromisoformat(repaired) > seal:
                reverted[(symbol, session)] = (pm, staged, collected)
    except sqlite3.OperationalError:
        pass                                              # a store from before R1 has no repair log
    out: dict[str, list[tuple[str, float]]] = {}
    for symbol, session, pm, staged, complete, collected in connection.execute(
            "SELECT symbol, session, pm_dollar_volume, staged, complete, collected_at FROM kiwoom_premarket_sessions "
            "ORDER BY symbol, session"):
        if (symbol, session) in reverted:
            pm, staged, collected = reverted[(symbol, session)]
        if datetime.fromisoformat(collected) > seal or not complete or not staged or pm is None or pm <= 0:
            continue
        out.setdefault(symbol, []).append((session, float(pm)))
    connection.close()
    return out


def denominator(history: list[tuple[str, float]], session: str) -> float | None:
    values = [v for s, v in history if s < session]
    if len(values) < 5:
        return None
    value = statistics.median(values[-20:])
    return value if value > 0 else None


# -- the session -----------------------------------------------------------------------------------

def verify(run_dir: Path, universe_dir: Path, store: Path, book_root: Path | None) -> dict[str, Any]:
    rules = frozen_rules()
    report = Report()
    session = run_dir.name
    day = date.fromisoformat(session)
    open_et = datetime.combine(day, time(9, 30), tzinfo=ET)
    record = _json(run_dir / "session_record.json")
    run = _json(run_dir / "run.json")
    raw = json.loads(gzip.decompress((run_dir / "premarket_raw.json.gz").read_bytes()))
    gate = record.get("execution_gate") or {}
    timeline = record.get("timeline") or {}

    # universe
    art = _json(universe_dir / f"universe_{session}.json")
    digest = hashlib.sha256(json.dumps({k: art[k] for k in UNIVERSE_DIGEST_FIELDS if k in art},
                                       sort_keys=True).encode()).hexdigest()
    report.check("universe_identity", art.get("target_session") == session
                 and art.get("asof_session") == _previous_session(day).isoformat()
                 and digest == art.get("digest") == run.get("universe_digest") == record.get("universe_digest"),
                 target=art.get("target_session"), asof=art.get("asof_session"), digest=digest[:16])
    report.check("universe_rows", set(raw["symbols"]) | set(run["universe"]["unmapped"]) == set(art["symbols"]),
                 symbols=len(art["symbols"]))

    # completeness and timing
    statuses = {s: v["status"] for s, v in raw["symbols"].items()}
    stale = sorted(s for s, v in statuses.items() if v == "STALE")
    documented = set(gate.get("documented_exceptions") or [])
    report.check("universe_complete", not [s for s in stale if s not in documented],
                 stale=len(stale), documented=sorted(documented))
    seal = datetime.fromisoformat(timeline["seal"])
    t0 = datetime.fromisoformat(timeline["T0"])
    report.check("seal_before_0930", seal < open_et, seal=timeline["seal"],
                 margin_s=(open_et - seal).total_seconds())
    report.check("t0_at_0925", abs((t0 - datetime.combine(day, time(9, 25), tzinfo=ET)).total_seconds()) < 2.0,
                 T0=timeline["T0"])
    report.check("gate_authorized", bool(gate.get("authorized")), status=gate.get("status"))

    # cutoff / leakage
    future = sum(1 for v in raw["symbols"].values() for b in v["bars"] if b[0] > DECISION_LAST)
    report.check("no_future_bars", future == 0 and run["cutoff_audit"]["future_cutoff_violation_count"] == 0,
                 future_bars=future)

    # features
    recorded = {}
    with (run_dir / "feature_frame.csv").open() as fh:
        for row in csv.DictReader(fh):
            symbol = row.pop("symbol")
            recorded[symbol] = {k: float(v) for k, v in row.items()}
    spy = premarket(raw["spy"])
    spy_ret = spy["pm_last_price"] / art["spy_close_d_minus_1"] - 1 if spy else float("nan")
    history = store_as_of(store, seal)
    mine, feature_mismatch, rvol_mismatch = {}, [], []
    for symbol, body in raw["symbols"].items():
        if body["status"] != "FEATURE_COMPLETE":
            continue
        pre = premarket(body["bars"])
        if pre is None or pre["pm_bars"] < 3 or pre["pm_dollar_volume"] < 50_000.0:
            continue
        close = art["close_d_minus_1"][symbol]
        den = denominator(history.get(symbol, []), session)
        span = pre["pm_high"] - pre["pm_low"]
        mine[symbol] = {"premarket_gap": pre["pm_last_price"] / close - 1,
                        "premarket_rvol": pre["pm_dollar_volume"] / den if den else float("nan"),
                        "position_in_premarket_range": (pre["pm_last_price"] - pre["pm_low"]) / span if span > 0 else float("nan"),
                        "return_0900_0925": pre["return_0900_0925"], "premarket_dollar_volume": pre["pm_dollar_volume"],
                        "spy_premarket_return": spy_ret, "_den": den}
    report.check("feature_rows", set(mine) == set(recorded), mine=len(mine), runtime=len(recorded),
                 only_mine=sorted(set(mine) - set(recorded))[:10], only_runtime=sorted(set(recorded) - set(mine))[:10])

    def same(a: float, b: float) -> bool:
        return (math.isnan(a) and math.isnan(b)) or a == b or abs(a - b) <= 1e-12 * max(1.0, abs(a), abs(b))

    for symbol in sorted(set(mine) & set(recorded)):
        for name in ("premarket_gap", "position_in_premarket_range", "return_0900_0925", "premarket_dollar_volume"):
            if not same(mine[symbol][name], recorded[symbol][name]):
                feature_mismatch.append((symbol, name, mine[symbol][name], recorded[symbol][name]))
        if not same(mine[symbol]["premarket_rvol"], recorded[symbol]["premarket_rvol"]):
            rvol_mismatch.append((symbol, mine[symbol]["premarket_rvol"], recorded[symbol]["premarket_rvol"]))
    report.check("features_exact", not feature_mismatch, mismatches=feature_mismatch[:10], count=len(feature_mismatch))
    report.check("rvol_exact_prior_only", not rvol_mismatch, mismatches=rvol_mismatch[:10], count=len(rvol_mismatch))

    # H5, R1, Top3, B2
    def h5(f):
        return (f["premarket_gap"] > 0 and f["premarket_rvol"] >= rules["rvol_min"]
                and f["position_in_premarket_range"] >= rules["pos_min"] and f["return_0900_0925"] > 0)
    candidates = sorted(s for s, f in mine.items() if h5(f))
    order = sorted(candidates, key=lambda s: (-mine[s]["premarket_rvol"], s))
    top = order[:rules["max_selected"]]
    rows = len(mine)
    rate = len(candidates) / rows if rows else 0.0
    high = rows >= rules["breadth_min_rows"] and rate >= rules["breadth_threshold"]
    exposure = rules["exposure"]["high" if high else "normal"]
    report.check("h5_exact", len(candidates) == record["h5_true"], mine=len(candidates), runtime=record["h5_true"])
    report.check("breadth_denominator", rows == record["breadth_denominator_eligible_rows"], mine=rows,
                 runtime=record["breadth_denominator_eligible_rows"])
    report.check("top3_exact_no_backfill", top == list(record["selected"]), mine=top, runtime=record["selected"],
                 r1_head=[(s, mine[s]["premarket_rvol"]) for s in order[:5]])

    # execution
    if book_root is not None:
        state_path = book_root / "STRATEGY_E_MAX_V1" / "sessions" / f"{session}.json"
        book_path = book_root / "STRATEGY_E_MAX_V1" / "book.json"
        if state_path.exists():
            state = _json(state_path)
            decision = state.get("decision") or {}
            report.check("decision_matches_record", decision.get("selected") == record["selected"]
                         and decision.get("r1_order") == order and decision.get("candidates") == candidates,
                         decision_selected=decision.get("selected"))
            report.check("breadth_exposure_exact", bool(decision.get("high_breadth")) == high
                         and Fraction(decision.get("final_exposure", "0")) == exposure,
                         mine={"rows": rows, "h5": len(candidates), "rate": rate, "high": high, "exposure": str(exposure)},
                         runtime={k: decision.get(k) for k in ("universe_rows", "h5_count", "h5_rate", "high_breadth",
                                                               "final_exposure")})
            equity = Decimal(state["equity_at_open"])
            entries, exits = state.get("entries") or {}, state.get("exits") or {}
            executable = state.get("executable") or []
            entered = [s for s, e in entries.items() if e.get("status") == "FILLED"]
            report.check("no_symbol_outside_selection", set(entries) <= set(record["selected"])
                         and set(executable) <= set(record["selected"]), entries=sorted(entries))
            bad_entry, bad_exit, bad_cost, realized = [], [], [], Decimal(0)
            for symbol in entered:
                e = entries[symbol]
                weight = Fraction(e["weight"])
                if weight != exposure / len(executable):
                    bad_entry.append((symbol, "weight", e["weight"]))
                ref = Decimal(str(decision["reference_prices"][symbol]))
                qty = (equity * Decimal(weight.numerator) / Decimal(weight.denominator) / ref).to_integral_value(ROUND_DOWN)
                if Decimal(e["intent"]["quantity"]) != qty:
                    bad_entry.append((symbol, "quantity", e["intent"]["quantity"], str(qty)))
                fb = e["fill_bar"]
                if not fb["timestamp"].startswith(f"{session}T09:30") or Decimal(e["raw_market_price"]) != Decimal(str(fb["open"])):
                    bad_entry.append((symbol, "entry_0930_open", fb["timestamp"], e["raw_market_price"], fb["open"]))
                cost = Decimal(e["fill_cost"])
                if cost != qty * Decimal(e["raw_market_price"]) * rules["cost_bp"] / Decimal(10000):
                    bad_cost.append((symbol, str(cost)))
                x = exits.get(symbol) or {}
                if x.get("status") == "UNRESOLVED_EXIT":
                    continue
                xb = x.get("fill_bar") or {}
                if (not xb.get("timestamp", "").startswith(f"{session}T09:34") or x.get("fill_price_field") != "close"
                        or Decimal(x["raw_market_price"]) != Decimal(str(xb["close"]))):
                    bad_exit.append((symbol, xb.get("timestamp"), x.get("raw_market_price"), xb.get("close")))
                realized += qty * (Decimal(x["raw_market_price"]) - Decimal(e["raw_market_price"])) - cost
            report.check("entry_exact_0930_open", not bad_entry, problems=bad_entry)
            report.check("exit_exact_0934_close", not bad_exit, problems=bad_exit)
            report.check("cost_exact_10bp", not bad_cost, problems=bad_cost)
            summary = state.get("summary") or {}
            report.check("session_pnl_exact", Decimal(summary.get("sim_realized_pnl", "0")) == realized,
                         mine=str(realized), runtime=summary.get("sim_realized_pnl"))
            book = _json(book_path)
            chain, value = True, Decimal(book["initial_equity"])
            for _, row in sorted(book["sessions"].items()):
                value += Decimal(row["sim_realized_pnl"])
                chain &= value == Decimal(row["equity_after"])
            report.check("book_reconciles", chain and value == Decimal(book["equity"])
                         and Decimal(book["equity"]) - Decimal(book["initial_equity"]) == Decimal(book["realized_pnl"]),
                         equity=book["equity"], realized=book["realized_pnl"])
        else:
            report.check("execution_recorded", gate.get("authorized") is False, detail="no engine session in the book",
                         gate=gate.get("status"))
    verdict = PASS_LABEL if report.passed else FAIL_LABEL
    return {"verdict": verdict, "session": session, "checks": report.checks,
            "failed": [c["check"] for c in report.checks if not c["pass"]]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E-RUNTIME-R1 independent session recomputation")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--book-root", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    body = verify(args.run_dir, args.universe, args.store, args.book_root)
    text = json.dumps(body, indent=1, default=str)
    if args.out:
        args.out.write_text(text + "\n")
    print(json.dumps({"verdict": body["verdict"], "failed": body["failed"]}, indent=1))
    return 0 if body["verdict"] == PASS_LABEL else 1


if __name__ == "__main__":
    raise SystemExit(main())
