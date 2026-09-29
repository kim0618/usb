"""Run every preregistered arm, verify the signal pass against the engine, write the artefacts.

    PYTHONPATH=backend python -m app.crypto.research.d6c.runner

Order matters: the contracts are hash-checked first, the vectorised signal pass is checked
against the real decision engine second, and only then is a single trade simulated. A mismatch at
either step stops the run, because a result produced by something other than the frozen engine
under the frozen contract is not the result that was preregistered.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import data as D
from . import execution as EX
from . import gates as G
from . import metrics as M
from . import signals as S
from .contract import load as load_contracts

OUT_DIR = Path("data/research/crypto/d6")
RESULTS_JSON = OUT_DIR / "d6c_results_v1.json"
TRADES_PARQUET = OUT_DIR / "d6c_trades_v1.parquet"
FOLDS_PARQUET = OUT_DIR / "d6c_fold_metrics_v1.parquet"

#: Same lookback the D6-B runner uses: 30 bucket days, the decision day, and the 1441 closes the
#: 24 hour volatility needs.
LOOKBACK_BARS = 31 * 1440 + 1441
VERIFY_SAMPLE = 2000
VERIFY_SEED = 20260928


def _signal_cache(grid, strategy, arms) -> dict[tuple, S.SignalPass]:
    """One signal pass per distinct (threshold, stop_k, oi_window) triple across all arms."""
    cache: dict[tuple, S.SignalPass] = {}
    for name in arms:
        spec = _spec(strategy, arms, name)
        key = (spec["score_threshold"], spec["stop_k"], spec["oi_window_min"])
        if key not in cache:
            cache[key] = S.build(grid, strategy, score_threshold=key[0], stop_k=key[1],
                                 oi_window_min=key[2])
    return cache


def _spec(strategy, arms, name) -> dict[str, Any]:
    base = {"score_threshold": strategy.long_entry_min_score,
            "hold_min": strategy.max_hold_min,
            "stop_k": strategy.stop["sigma_multiplier"],
            "oi_window_min": 60, "stop_enabled": True, "judging": False}
    base.update(arms[name])
    return base


def verify(grid, strategy, signals: S.SignalPass) -> dict[str, Any]:
    """Re-run the real engine on every eligible bar and a fixed sample of the rest."""
    eligible = np.nonzero(signals.bar_eligible)[0]
    rng = np.random.default_rng(VERIFY_SEED)
    others = np.nonzero(~signals.bar_eligible)[0]
    others = others[others >= LOOKBACK_BARS]
    sample = rng.choice(others, size=min(VERIFY_SAMPLE, len(others)), replace=False)
    checked = np.concatenate([eligible[eligible >= LOOKBACK_BARS], sample])
    problems = S.verify_against_engine(grid, strategy, signals, checked,
                                       lookback_bars=LOOKBACK_BARS)
    return {"bars_checked": int(len(checked)), "eligible_checked": int((eligible >= LOOKBACK_BARS).sum()),
            "sampled_non_signals": int(len(sample)), "disagreements": problems}


def run(write: bool = True) -> dict[str, Any]:
    started = time.time()
    backtest = load_contracts()
    strategy = backtest.strategy
    grid = D.load_inputs()

    arms = backtest.arms
    cache = _signal_cache(grid, strategy, arms)
    main_key = (strategy.long_entry_min_score, strategy.stop["sigma_multiplier"], 60)
    verification = verify(grid, strategy, cache[main_key])
    if verification["disagreements"]:
        raise RuntimeError("signal pass disagrees with the decision engine: "
                           + "; ".join(verification["disagreements"][:5]))

    runs: dict[str, EX.RunResult] = {}
    for name in arms:
        spec = _spec(strategy, arms, name)
        key = (spec["score_threshold"], spec["stop_k"], spec["oi_window_min"])
        runs[name] = EX.run_arm(grid, backtest, cache[key], name)

    main_run = runs["A-MAIN"]
    boundaries = backtest.fold_boundaries_utc
    start_equity = main_run.starting_equity
    main = M.summarise(main_run.trades, starting_equity=start_equity)
    boot = backtest.bootstrap
    ci = M.bootstrap_ci(main_run.trades, block_days=boot["block_days"],
                        iterations=boot["iterations"], seed=boot["seed"])
    folds = M.by_fold(main_run.trades, boundaries, start_equity)
    years = M.by_year(main_run.trades, start_equity)
    regimes = M.by_regime(main_run.trades, grid, start_equity)
    concentration = M.concentration(main_run.trades)
    loyo = M.leave_one_year_out(main_run.trades)
    scenarios = M.cost_scenarios(main_run.trades, backtest.costs)

    sensitivity = {name: M.summarise(runs[name].trades, starting_equity=runs[name].starting_equity)
                   for name in arms if name.startswith("A-SENS")}
    nostop = M.summarise(runs["A-NOSTOP"].trades,
                         starting_equity=runs["A-NOSTOP"].starting_equity)
    reference = {name: M.summarise(runs[name].trades, starting_equity=runs[name].starting_equity)
                 for name in arms if name.startswith("A-REF")}

    gate_rows = G.evaluate(backtest, main=main, ci=ci, folds=folds, years=years,
                           regimes=regimes, concentration=concentration, loyo=loyo,
                           scenarios=scenarios, sensitivity=sensitivity, nostop=nostop)
    final = G.verdict(gate_rows)

    depth = float(backtest.fill_model["depth_btc_per_side"])
    ambiguity = {
        "same_bar_stop_and_time_expiry": sum(1 for t in main_run.trades
                                             if t.same_bar_stop_and_expiry),
        "gap_open_below_stop": sum(1 for t in main_run.trades if t.gap_open_below_stop),
    }

    payload = {
        "record": "CRYPTO_D6_C_BACKTEST_RESULTS_V1",
        "contract_sha256": backtest.sha256,
        "d6a_contract_sha256": strategy.sha256,
        "i1_sha256": backtest.i1_sha256,
        "strategy": strategy.strategy_id,
        "evidence_class": backtest.doc["window"]["evidence_class"],
        "runtime_sec": round(time.time() - started, 1),
        "engine_verification": verification,
        "arms": {name: runs[name].as_dict() for name in arms},
        "main": main,
        "bootstrap_ci": ci,
        "folds": folds,
        "years": years,
        "regimes": regimes,
        "concentration": concentration,
        "leave_one_year_out_net_bp": loyo,
        "cost_scenarios_bp": scenarios,
        "sensitivity": sensitivity,
        "nostop": nostop,
        "reference_arms": reference,
        "intrabar_ambiguity": ambiguity,
        "depth_usage": {"max_order_qty_btc": main_run.max_qty,
                        "synthetic_depth_btc_per_side": depth,
                        "max_fraction_of_depth": main_run.max_qty / depth if depth else None},
        "gates": gate_rows,
        "verdict": final,
    }

    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_JSON.write_text(json.dumps(payload, indent=1, default=str))
        _write_parquet(main_run.trades, folds)
    return payload


def _write_parquet(trades, folds) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if trades:
        rows = [t.as_dict() for t in trades]
        pq.write_table(pa.Table.from_pylist(rows), TRADES_PARQUET)
    clean = [{k: (v if not isinstance(v, str) or k in ("fold", "start_utc", "end_utc",
                                                       "profit_factor") else v)
              for k, v in row.items()} for row in folds]
    for row in clean:
        row["profit_factor"] = str(row["profit_factor"])
    pq.write_table(pa.Table.from_pylist(clean), FOLDS_PARQUET)


def main() -> int:
    payload = run()
    verdict = payload["verdict"]
    print(f"D6-C {verdict['verdict']}  (D6-D {verdict['d6d_authorization']})")
    print(f"  trades {payload['main']['trades']}  net_bp {payload['main']['average_trade_bp']}")
    for gate in payload["gates"]:
        print(f"  {gate['id']:3s} {gate['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
