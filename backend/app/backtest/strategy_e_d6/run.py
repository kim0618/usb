"""E-D6 orchestration: preflight, the bound development input, replay, metrics and artifacts.

Order matters and is enforced here, not only described:

1. Every frozen artifact (E-D0..E-D5, horizon semantics, the E-D6 protocol) is loaded through
   its own fail-closed loader, and the chain commits must be ancestors of HEAD.
2. The development tape is exposed through the verified view, and the E1 premarket cohort is
   rebuilt from it by E1's own code. The declared universe and H5 row counts must equal the
   documented E1-H5 development counts before any trade return is computed.
3. Only then are bars fetched, for H5 candidates only, and the sessions replayed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e1_premarket import pit_audit
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6.replay import (
    GROSS, PRIMARY, SCENARIOS, SCENARIO_BP, SessionFrame, exact_bars, replay_session,
)
from app.market.calendar import MarketCalendar
from app.strategy_e import costs, execution, exits, risk, signal


REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_backtest_rules_v1.json"
RULES_CANONICAL_SHA256 = "73056ac8e8cd6308c4affc343adaad75a7ea4e902e0f8c72b3bdc918e7cb4713"
HORIZON_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_horizon_semantics_v1.json"
RUNS_DIR = Path("data/runtime/strategy_e/backtest_runs")
VIEW_DIR = Path("data/runtime/strategy_e/tape_view")
RESULT_VERSION = "STRATEGY_E_D6_RESULT_V1"

CHAIN = {
    "E-D0": "89f27c6cee98e3cdf98c3e262ef3f926e319499c",
    "E-D1": "c210f6f8f5e56427aa003f29092022acc835a96d",
    "E-D2": "1ba07734bf5ad270d566a9492e432c716cbeae40",
    "HORIZON": "4c343e3223a2ddd880a457c8c80c1d046f7ae204",
    "E-D3": "605d3748fa7aed1854ba2d23bee9c606841e8ea6",
    "E-D4": "01ddb007b1a24dc9b2c0225bd1702aa5b906a902",
    "E-D5": "72e416d9094e5f09e3b48e2c92e40c6904495f2a",
    "E-D6-PROTOCOL": "e2e822c396cbd50d0f93be7dbfd9a8f781985b2e",
}
#: E1_H5_CONFIRMATION.md section C, DEVELOPMENT block: the reproduction target of step 2.
DOCUMENTED_DEVELOPMENT = {"universe_rows": 70738, "h5_rows": 1729}
TRADE_COLUMNS = ("session", "symbol", "selection_rank", "selected", "entry_status", "entry_price",
                 "exit_status", "exit_reason", "exit_price", "risk_status", "skip_reason",
                 "weight", "standard_pnl", "close_price", "previous_day_dollar_volume", "R_5m",
                 "R_5m_strict") + SCENARIOS


class PreflightError(RuntimeError):
    """A frozen artifact, the chain, or the development input failed verification."""


def canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_backtest_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = canonical_sha256(payload)
    if found != RULES_CANONICAL_SHA256:
        raise PreflightError(f"E-D6 protocol digest mismatch: {found}")
    upstream = payload["upstream"]
    expected = {
        "e_d0_rules_canonical_sha256": signal.TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": signal.SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": execution.EXECUTION_RULES_CANONICAL_SHA256,
        "e_d2_execution_version": execution.EXECUTION_VERSION,
        "e_d3_horizon_semantics_canonical_sha256": exits.HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": exits.EXIT_RULES_CANONICAL_SHA256,
        "e_d3_exit_version": exits.EXIT_VERSION,
        "e_d4_cost_rules_canonical_sha256": costs.COST_RULES_CANONICAL_SHA256,
        "e_d5_risk_rules_canonical_sha256": risk.RISK_RULES_CANONICAL_SHA256,
        "e_d5_risk_version": risk.RISK_VERSION,
    }
    moved = [key for key, value in expected.items() if upstream.get(key) != value]
    if moved:
        raise PreflightError(f"E-D6 protocol upstream differs from the code chain: {moved}")
    if (payload["costs"]["primary_scenario"] != PRIMARY
            or payload["statistics"]["bootstrap"]["seed"] != X.BOOTSTRAP_SEED
            or payload["statistics"]["bootstrap"]["replicates"] != X.BOOTSTRAP_REPLICATES
            or payload["data_quality"]["minimum_coverage"] != X.MIN_COVERAGE
            or payload["chronological_robustness"]["blocks"] != X.BLOCKS
            or payload["statistics"]["annualization_sessions"] != X.ANNUALIZATION
            or [list(b) for b in X.PRICE_BUCKETS] != payload["diagnostics"]["price_buckets_usd"]):
        raise PreflightError("E-D6 constants in code differ from the frozen protocol")
    return payload


def frozen_checksums() -> dict[str, str]:
    """Load every upstream artifact through its own fail-closed loader."""
    signal._load_frozen_rules()
    execution._load_rules()
    exits._load_rules()
    costs._load_rules()
    risk._load_rules()
    horizon = canonical_sha256(json.loads(HORIZON_PATH.read_text(encoding="utf-8")))
    if horizon != exits.HORIZON_SEMANTICS_CANONICAL_SHA256:
        raise PreflightError(f"horizon semantics digest mismatch: {horizon}")
    load_backtest_rules()
    return {
        "e_d0_trading_rules": signal.TRADING_RULES_CANONICAL_SHA256,
        "e_d2_execution_rules": execution.EXECUTION_RULES_CANONICAL_SHA256,
        "e_d3_horizon_semantics": exits.HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules": exits.EXIT_RULES_CANONICAL_SHA256,
        "e_d4_cost_rules": costs.COST_RULES_CANONICAL_SHA256,
        "e_d5_risk_rules": risk.RISK_RULES_CANONICAL_SHA256,
        "e_d6_backtest_rules": RULES_CANONICAL_SHA256,
    }


def chain_ancestry(repo_root: Path = REPO_ROOT) -> dict[str, bool]:
    out = {}
    for name, commit in CHAIN.items():
        result = subprocess.run(["git", "-C", str(repo_root), "merge-base", "--is-ancestor",
                                 commit, "HEAD"], capture_output=True)
        out[name] = result.returncode == 0
    return out


def git_head(repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() or "UNKNOWN"


def code_digest() -> str:
    """sha256 over the E-D6 package and the Strategy E trading modules it drives."""
    digest = hashlib.sha256()
    roots = [REPO_ROOT / "backend/app/backtest/strategy_e_d6", REPO_ROOT / "backend/app/strategy_e"]
    for root in roots:
        for path in sorted(root.glob("*.py")):
            digest.update(f"{path.relative_to(REPO_ROOT).as_posix()}\n".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


# -- pass 2: exact bars for H5 candidates only -------------------------------------------------

_BARS: dict[str, Any] = {}


def _bars_init(view_root: str) -> None:
    _BARS["root"] = Path(view_root)
    _BARS["edges"], _BARS["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def _bars_one(job: tuple[str, tuple[str, ...]]) -> tuple[str, dict[str, dict[str, list]]]:
    symbol, sessions = job
    tape = M.load_symbol_tape(symbol, _BARS["root"], _BARS["edges"], _BARS["offsets"])
    if tape is None:
        return symbol, {}
    found = exact_bars(tape, [date.fromisoformat(s) for s in sessions])
    return symbol, {s.isoformat(): v for s, v in found.items()}


def fetch_candidate_bars(view_root: Path, wanted: Mapping[str, Sequence[str]], *,
                         workers: int) -> dict[str, dict[str, dict[str, list]]]:
    """symbol -> session -> {"09:30": bars, "09:34": bars}, read from the bound view."""
    jobs = sorted((symbol, tuple(sorted(set(sessions)))) for symbol, sessions in wanted.items())
    out: dict[str, dict[str, dict[str, list]]] = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_bars_init,
                             initargs=(str(view_root),)) as pool:
        for symbol, found in pool.map(_bars_one, jobs, chunksize=4):
            out[symbol] = found
    return out


def replay_all(frames: Sequence[SessionFrame], signals: Sequence[signal.SignalResult],
               bars: Mapping[str, Mapping[str, Mapping[str, list]]],
               calendar: MarketCalendar) -> list[dict[str, Any]]:
    sessions = []
    for frame, result in zip(frames, signals):
        key = frame.session.isoformat()
        session_bars = {symbol: bars.get(symbol, {}).get(key, {})
                        for symbol in result.candidate_symbols}
        sessions.append(replay_session(result, frame, session_bars, calendar))
    return sessions


def replay_digest(replayed: Sequence[Mapping[str, Any]]) -> str:
    return canonical_sha256([{"session": s["session"], "digests": s["digests"],
                              "returns": {k: str(v) for k, v in s["returns"].items()}}
                             for s in replayed])


# -- evaluation ---------------------------------------------------------------------------------

def timeline(grid: Sequence[date], frames: Sequence[SessionFrame],
             calendar: MarketCalendar) -> list[str]:
    """Every XNYS grid session from the first to the last session with an eligible universe."""
    first, last = frames[0].session, frames[-1].session
    sessions = [s for s in grid if first <= s <= last]
    invalid = [s for s in sessions if calendar.session(s) is None]
    if invalid:
        raise PreflightError(f"grid sessions that are not XNYS sessions: {invalid[:3]}")
    return [s.isoformat() for s in sessions]


def funnel(eligible_rows: int, replayed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records = [r for s in replayed for r in s["records"]]
    candidates = len(records)
    selected = [r for r in records if r["selected"]]
    valid_entries = [r for r in selected if r["entry_status"] == "EXECUTED_PROXY"]
    invalid_entries = [r for r in selected if r["entry_status"] != "EXECUTED_PROXY"]
    valid_exits = [r for r in valid_entries if r["exit_status"] == exits.VALID_EXIT]
    unresolved = [r for r in valid_entries if r["exit_status"] != exits.VALID_EXIT]
    standard = [r for r in records if r["standard_pnl"]]

    def stage(count: int, parent: int | None) -> dict[str, Any]:
        return {"count": count,
                "pct_of_previous": (count / parent) if parent else None,
                "pct_of_h5_candidates": (count / candidates) if candidates else None}

    return {
        "eligible_universe_rows": {"count": eligible_rows},
        "H5_candidates": stage(candidates, eligible_rows),
        "selected_candidates": stage(len(selected), candidates),
        "capacity_skipped": stage(candidates - len(selected), candidates),
        "valid_entries": stage(len(valid_entries), len(selected)),
        "invalid_entries": {**stage(len(invalid_entries), len(selected)),
                            "reasons": dict(sorted(Counter(r["entry_status"]
                                                           for r in invalid_entries).items()))},
        "valid_exact_exits": stage(len(valid_exits), len(valid_entries)),
        "unresolved_exits": {**stage(len(unresolved), len(valid_entries)),
                             "reasons": dict(sorted(Counter(r["exit_reason"]
                                                            for r in unresolved).items()))},
        "standard_pnl_trades": stage(len(standard), len(valid_entries)),
        "standard_pnl_coverage": (len(standard) / len(valid_entries)) if valid_entries else None,
    }


def evaluate(replayed: Sequence[Mapping[str, Any]], sessions: Sequence[str], eligible_rows: int,
             *, integrity: bool) -> dict[str, Any]:
    by_session = {s["session"]: s for s in replayed}
    returns = {name: {s: float(by_session[s]["returns"][name]) if s in by_session else 0.0
                      for s in sessions} for name in SCENARIOS}
    trades = [r for s in replayed for r in s["records"] if r["standard_pnl"]]
    trades_by_session = Counter(r["session"] for r in trades)
    active = np.array([trades_by_session.get(s, 0) > 0 for s in sessions])

    scenarios = {}
    for name in SCENARIOS:
        trade_returns = np.array([float(r[name]) for r in trades])
        session_returns = np.array([returns[name][s] for s in sessions])
        scenarios[name] = {"round_trip_cost_bp": SCENARIO_BP[name],
                           "role": "DIAGNOSTIC_GROSS" if name == GROSS else (
                               "PRIMARY" if name == PRIMARY else "SENSITIVITY"),
                           **X.scenario_summary(trade_returns, session_returns, active)}

    primary_sessions = np.array([returns[PRIMARY][s] for s in sessions])
    bootstrap = X.bootstrap_mean_ci(primary_sessions)
    blocks = X.block_table(sessions, returns[PRIMARY], trades_by_session)
    flow = funnel(eligible_rows, replayed)
    decision = X.verdict(integrity=integrity, coverage=flow["standard_pnl_coverage"],
                         mean_10bp=scenarios[PRIMARY]["all_session_mean"],
                         profit_factor_10bp=scenarios[PRIMARY]["profit_factor"],
                         ci_low=bootstrap["ci_low"],
                         positive_blocks=sum(b["positive"] for b in blocks))

    price = np.array([r["close_price"] for r in trades])
    liquidity = np.array([r["previous_day_dollar_volume"] for r in trades])
    gross = np.array([float(r[GROSS]) for r in trades])
    net = np.array([float(r[PRIMARY]) for r in trades])
    weights = np.array([float(Decimal(r["weight"].split("/")[0]) / Decimal(r["weight"].split("/")[1]))
                        for r in trades])
    symbols = [r["symbol"] for r in trades]
    return {
        "funnel": flow,
        "scenarios": scenarios,
        "primary_gate": {"scenario": PRIMARY,
                         "all_session_mean": scenarios[PRIMARY]["all_session_mean"],
                         "profit_factor": scenarios[PRIMARY]["profit_factor"],
                         "bootstrap": bootstrap, "blocks": blocks,
                         "positive_blocks": sum(b["positive"] for b in blocks),
                         **decision},
        "stability": {"month": X.period_table(sessions, returns, trades_by_session, "month"),
                      "quarter": X.period_table(sessions, returns, trades_by_session, "quarter")},
        "buckets": {
            "price_basis": "previous-session close (D-1), the Research close_price",
            "price": X.bucket_table(price, gross, net, X.PRICE_BUCKETS),
            "liquidity_basis": "previous-day dollar volume; Research-reported bucket edges",
            "liquidity": X.bucket_table(liquidity, gross, net, X.LIQUIDITY_BUCKETS),
        },
        "concentration": {
            "basis": "per-symbol sum of normalized_weight x trade return (session contributions)",
            "gross": X.concentration(symbols, weights * gross),
            "cost_10bp": X.concentration(symbols, weights * net),
            "gate": "NONE; DIAGNOSTIC ONLY",
        },
        "break_even": {
            "mean_gross_trade_return_bp": float(gross.mean() * 1e4) if gross.size else None,
            "definition": "mean gross trade return expressed in round-trip bp; diagnostic only",
        },
        "implementation_delta": implementation_delta(replayed, trades),
    }


def implementation_delta(replayed: Sequence[Mapping[str, Any]],
                         trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Step from Research's flexible R_5m on every H5 row to the executed, sized, costed trade."""
    records = [r for s in replayed for r in s["records"]]

    def mean(values: Sequence[float]) -> float | None:
        array = np.array([v for v in values if np.isfinite(v)])
        return float(array.mean()) if array.size else None

    selected = [r for r in records if r["selected"]]
    strict_gap = [abs(float(r[GROSS]) - r["R_5m_strict"]) for r in trades]
    active = [s for s in replayed if s["active"]]
    return {
        "research_reference": {
            "matched_h5_lift_bp": 17.01,
            "source": "E1_H5_CONFIRMATION.md section F, development, session-demeaned matched lift",
            "note": "observational matched-control lift, not a raw return and not a target",
        },
        "steps_bp": {
            "all_h5_candidates_R_5m": _bp(mean([r["R_5m"] for r in records])),
            "selected_candidates_R_5m": _bp(mean([r["R_5m"] for r in selected])),
            "standard_trades_R_5m": _bp(mean([r["R_5m"] for r in trades])),
            "standard_trades_R_5m_strict": _bp(mean([r["R_5m_strict"] for r in trades])),
            "standard_trades_trading_gross": _bp(mean([float(r[GROSS]) for r in trades])),
            "active_session_gross": _bp(mean([float(s["returns"][GROSS]) for s in active])),
            "active_session_net_10bp": _bp(mean([float(s["returns"][PRIMARY]) for s in active])),
        },
        "counts": {"h5_candidates": len(records), "selected": len(selected),
                   "standard_trades": len(trades), "active_sessions": len(active)},
        "trading_gross_equals_research_strict_label": {
            "max_abs_difference": max(strict_gap) if strict_gap else None,
            "trades_compared": len(strict_gap),
            "note": "exact 09:30 open to exact 09:34 close is R_5m_strict by construction; any "
                    "difference would mean the two code paths read different bars",
        },
    }


def _bp(value: float | None) -> float | None:
    return None if value is None else value * 1e4


# -- artifacts ------------------------------------------------------------------------------------

def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"{type(value)!r} is not JSON serialisable")


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False,
                       default=json_default) + "\n").encode("utf-8")


def trades_csv(replayed: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=TRADE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for session in replayed:
        for record in session["records"]:
            writer.writerow({k: ("" if record.get(k) is None else
                                 (repr(record[k]) if isinstance(record[k], float) else record[k]))
                             for k in TRADE_COLUMNS})
    return buffer.getvalue().encode("utf-8")


def daily_csv(replayed: Sequence[Mapping[str, Any]], sessions: Sequence[str]) -> bytes:
    by_session = {s["session"]: s for s in replayed}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("session", "eligible", "h5_candidates", "standard_trades", "active",
                     "exposure") + SCENARIOS)
    for s in sessions:
        row = by_session.get(s)
        if row is None:
            writer.writerow((s, 0, 0, 0, False, "0") + ("0",) * len(SCENARIOS))
            continue
        writer.writerow((s, row["eligible"], row["candidates"],
                         sum(r["standard_pnl"] for r in row["records"]), row["active"],
                         row["exposure"]) + tuple(str(row["returns"][k]) for k in SCENARIOS))
    return buffer.getvalue().encode("utf-8")


def write_artifacts(run_dir: Path, files: Mapping[str, bytes]) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, body in sorted(files.items()):
        (run_dir / name).write_bytes(body)
        digests[name] = hashlib.sha256(body).hexdigest()
    return digests


def tape_jobs(frames: Sequence[SessionFrame],
              signals: Sequence[signal.SignalResult]) -> dict[str, list[str]]:
    wanted: dict[str, list[str]] = defaultdict(list)
    for frame, result in zip(frames, signals):
        for symbol in result.candidate_symbols:
            wanted[symbol].append(frame.session.isoformat())
    return wanted


def pit_sample(view_root: Path, symbols: Sequence[str], sample: int = 40) -> dict[str, Any]:
    """E1's own poison test on a deterministic symbol sample from the bound view."""
    edges, offsets = M.et_offsets(datetime(2024, 1, 1, tzinfo=timezone.utc),
                                  datetime(2027, 1, 1, tzinfo=timezone.utc))
    chosen = sorted(set(symbols))[::max(1, len(symbols) // sample)]
    tapes = [t for t in (M.load_symbol_tape(s, view_root, edges, offsets) for s in chosen)
             if t is not None]
    return pit_audit.decision_boundary(tapes)


def decision_last() -> int:
    return P.DECISION_TIMES["0925"]
