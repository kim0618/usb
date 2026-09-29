"""Run the three confirmation candidates and write the artefacts.

    PYTHONPATH=backend python -m app.crypto.research.d5_5.runner

The contract is hash-checked first. The event detector is imported from D5.4 rather than
rewritten, so the event set is literally the same objects the earlier study froze.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..d5_4 import events as EV
from ..d6c import data as D
from ..d6c import metrics as M
from . import confirmation as CF
from . import execution as EX
from . import verdict as V

REPO_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_MD = REPO_ROOT / "docs/crypto/CRYPTO_D5_5_DIRECTION_CONFIRMATION_CONTRACT_V1.md"
CONTRACT_JSON = REPO_ROOT / "data/research/crypto/d5_5/contract_v1.json"
FREEZE = REPO_ROOT / "data/runtime/crypto/d5_5/contract_freeze_v1.json"
FEE_PATH = REPO_ROOT / "data/runtime/crypto/reference/fee_source_verification_v1.json"
RISK_LIMIT = REPO_ROOT / "data/runtime/crypto/BTCUSDT/reference/risk_limit_1790128376.json"
OUT_DIR = REPO_ROOT / "data/research/crypto/d5_5"

SENS_MARGIN = {"A": {"margin_fraction": 0.40}, "B": {"retrace_fraction": 0.618},
               "C": {"compression_fraction": 0.35}}


class ContractHashMismatch(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract() -> dict[str, Any]:
    freeze = json.loads(FREEZE.read_text())
    for path, expected, label in ((CONTRACT_MD, freeze["sha256"], "contract"),
                                  (CONTRACT_JSON, freeze["machine_readable_sha256"], "json")):
        digest = sha256_file(path)
        if digest != expected:
            raise ContractHashMismatch(f"{label}: {digest} != frozen {expected}")
    doc = json.loads(CONTRACT_JSON.read_text())
    if doc["costs"]["maker_used"] or doc["scope"]["liquidation_data_allowed"]:
        raise RuntimeError("maker and liquidation data are banned by the contract")
    if doc["exits"]["symmetric_bracket_reused"]:
        raise RuntimeError("the D5.4 symmetric bracket must not be reused")
    if doc["event_source"]["definition"] != "abs(z(disp15)) >= 10.0 AND z(oichg15) <= -2.5":
        raise RuntimeError("the event detector must stay the frozen D5.4 one")
    return doc


def detect_events(grid: dict[str, np.ndarray], contract: dict):
    """The D5.4 detector, applied symmetrically. No threshold is recomputed here."""
    features = EV.compute_features(grid)
    z = {name: EV.robust_z(features[name], grid["ts"])
         for name in ("disp15", "oichg15", "basis")}
    valid = np.isfinite(features["disp15"]) & np.isfinite(features["volratio"])
    for series in z.values():
        valid &= np.isfinite(series)
    mask = (np.abs(z["disp15"]) >= 10.0) & (z["oichg15"] <= -2.5) & valid
    events = EV.merge(mask, contract["event_source"]["merge_cooldown_bars"])
    events = [EV.Event(start=e.start, end=e.end,
                       displacement=abs(float(features["disp15"][e.start]))) for e in events]
    return events, features


def arm_settings(contract: dict, arm: str) -> dict[str, Any]:
    base = {"window_bars": contract["confirmation_common"]["window_bars"], "overrides": {}}
    if arm == "BASE":
        return base
    if arm == "SENS-MARGIN":
        return {**base, "use_margin_overrides": True}
    if arm == "SENS-WINDOW":
        return {**base, "window_bars": 30}
    if arm == "REF-120M":
        return {**base, "window_bars": 120}
    raise ValueError(f"unknown arm: {arm}")


def run_candidate(grid, contract, candidate, events, features) -> dict[str, Any]:
    folds = contract["folds"]["boundaries_utc"]
    oos_start, oos_end = EX.to_ms(folds[0]), EX.to_ms(folds[-1])
    min_margin = contract["confirmation_common"]["min_margin_bp"] / 1e4
    hold_bars = contract["exits"]["max_hold_bars"]

    runs: dict[str, EX.RunResult] = {}
    blocks: dict[str, dict[str, Any]] = {}
    for arm in contract["arms"]:
        settings = arm_settings(contract, arm)
        overrides = SENS_MARGIN[candidate["id"]] if settings.get("use_margin_overrides") else {}
        detector = CF.detector_for(candidate, grid=grid, features=features,
                                   window_bars=settings["window_bars"], min_margin=min_margin,
                                   overrides=overrides)
        result = EX.run(grid, contract, candidate, events, detector, arm=arm,
                        hold_bars=hold_bars, fee_path=FEE_PATH, risk_limit_path=RISK_LIMIT,
                        oos_start_ms=oos_start, oos_end_ms=oos_end)
        runs[arm] = result
        blocks[arm] = M.summarise(result.trades, starting_equity=result.starting_equity)

    base = runs["BASE"]
    main = blocks["BASE"]
    boot = contract["bootstrap"]
    ci = M.bootstrap_ci(base.trades, block_days=boot["block_days"],
                        iterations=boot["iterations"], seed=boot["seed"])
    fold_rows = M.by_fold(base.trades, folds, base.starting_equity)
    year_rows = M.by_year(base.trades, base.starting_equity)
    concentration = M.concentration(base.trades)
    loyo = M.leave_one_year_out(base.trades)
    scenarios = M.cost_scenarios(base.trades, {"taker_rate": contract["costs"]["taker_rate"]})
    sensitivity = {name: blocks[name] for name in blocks if name.startswith("SENS")}

    exits: dict[str, int] = {}
    for trade in base.trades:
        exits[trade.exit_reason] = exits.get(trade.exit_reason, 0) + 1
    stop_share = (exits.get(EX.STOP_EXIT, 0) / len(base.trades)) if base.trades else None

    rules = V.evaluate(contract, main=main, ci=ci, folds=fold_rows, years=year_rows,
                       concentration=concentration, loyo=loyo, scenarios=scenarios,
                       sensitivity=sensitivity, run=base, stop_share=stop_share)
    call = V.classify(rules, len(base.trades), contract)

    stops = [t for t in base.trades if t.exit_reason == EX.STOP_EXIT]
    streak = worst_streak = 0
    for trade in base.trades:
        streak = streak + 1 if trade.net_usdt < 0 else 0
        worst_streak = max(worst_streak, streak)
    net_bp = [t.net_bp for t in base.trades]

    return {
        "candidate": candidate["id"], "name": candidate["name"],
        "sides": candidate["sides"], "hypothesis": candidate["hypothesis"],
        "runs": {name: runs[name].as_dict() for name in runs},
        "main": main, "bootstrap_ci": ci, "folds": fold_rows, "years": year_rows,
        "concentration": concentration, "leave_one_year_out_net_bp": loyo,
        "cost_scenarios_bp": scenarios,
        "sensitivity": {name: blocks[name] for name in blocks if name != "BASE"},
        "exit_mix": exits, "stop_share": stop_share,
        "confirmation_quality": {
            "events_in_oos": base.events_in_oos, "confirmed": base.confirmed,
            "no_trade": base.no_trade, "confirmation_rate": base.confirmation_rate,
            "confirmed_long": base.confirmed_long, "confirmed_short": base.confirmed_short,
            "ignored_position_open": base.ignored_position_open,
            "ignored_cooldown": base.ignored_cooldown,
            "execution_rejects": len(base.rejects),
            "median_wait_min": (float(np.median([t.wait_minutes for t in base.trades]))
                                if base.trades else None),
            "median_confirm_distance_bp": (
                float(np.median([t.confirm_distance_bp for t in base.trades]))
                if base.trades else None),
            "false_confirmation_rate": stop_share,
            "median_time_to_stop_min": (float(np.median([t.hold_minutes for t in stops]))
                                        if stops else None),
        },
        "tail": {
            "worst_trade_bp": min(net_bp) if net_bp else None,
            "worst_5_bp": sorted(net_bp)[:5] if net_bp else [],
            "net_bp_p05": float(np.percentile(net_bp, 5)) if net_bp else None,
            "max_consecutive_losses": worst_streak,
        },
        "diagnostics": {
            "median_stop_distance_bp": (
                float(np.median([t.stop_distance * 1e4 for t in base.trades]))
                if base.trades else None),
            "notional_capped_trades": sum(1 for t in base.trades if t.notional_capped),
            "same_bar_target_and_stop": sum(1 for t in base.trades
                                            if t.same_bar_target_and_stop),
            "max_qty_over_depth": base.max_qty / 5.0,
            "identity_violations": base.identity_violations,
        },
        "verdict_rules": rules, "verdict": call,
        "trades": [t.as_dict() for t in base.trades],
        "confirmations": base.confirmations,
    }


def run(write: bool = True) -> dict[str, Any]:
    started = time.time()
    contract = load_contract()
    grid = D.load_inputs()
    events, features = detect_events(grid, contract)

    results = [run_candidate(grid, contract, candidate, events, features)
               for candidate in contract["candidates"]]
    survive = [r["candidate"] for r in results if r["verdict"]["verdict"] == V.SURVIVE]

    payload = {
        "record": "CRYPTO_D5_5_DIRECTION_CONFIRMATION_RESULTS_V1",
        "contract_sha256": sha256_file(CONTRACT_JSON),
        "contract_md_sha256": sha256_file(CONTRACT_MD),
        "evidence_class": contract["folds"]["evidence_class"],
        "runtime_sec": round(time.time() - started, 1),
        "events_detected": len(events),
        "candidates": [{k: v for k, v in r.items() if k not in ("trades", "confirmations")}
                       for r in results],
        "survive_count": len(survive), "survive": survive,
        "d6_v2": "AUTHORIZED" if survive else "NOT AUTHORIZED",
    }
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results_v1.json").write_text(json.dumps(payload, indent=1, default=str))
        _write_parquet(results, events, grid)
    return payload


def _write_parquet(results, events, grid) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    trades = [row for r in results for row in r["trades"]]
    if trades:
        pq.write_table(pa.Table.from_pylist(trades), OUT_DIR / "trades_v1.parquet")
    confirmations = [row for r in results for row in r["confirmations"]]
    if confirmations:
        pq.write_table(pa.Table.from_pylist(confirmations),
                       OUT_DIR / "confirmations_v1.parquet")
    rows = [{"start_index": e.start, "end_index": e.end,
             "ts_ms": int(grid["ts"][e.start]), "displacement_bp": e.displacement * 1e4}
            for e in events]
    pq.write_table(pa.Table.from_pylist(rows), OUT_DIR / "events_v1.parquet")


def main() -> int:
    payload = run()
    print(f"D5.5  SURVIVE {payload['survive_count']}  (D6-V2 {payload['d6_v2']})  "
          f"events {payload['events_detected']}")
    for row in payload["candidates"]:
        block, call, quality = row["main"], row["verdict"], row["confirmation_quality"]
        rate = quality["confirmation_rate"]
        print(f"  {row['candidate']} {row['name']:34s} conf={quality['confirmed']:4d} "
              f"({rate:5.1%}) trades={block['trades']:4d} "
              f"gross={block['gross_bp_mean']} net={block['average_trade_bp']} "
              f"stop_share={row['stop_share']} -> {call['verdict']} failed={call.get('failed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
