"""Run every candidate and arm, then write the artefacts.

    PYTHONPATH=backend python -m app.crypto.research.d5_4.runner

The contract is hash-checked before anything runs. E2 is not here at all: it was rejected at the
contract stage by the cost-first screen, and running it anyway would quietly turn a screening
rule into a suggestion.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..d6c import data as D
from ..d6c import metrics as M
from . import events as EV
from . import execution as EX
from . import verdict as V

REPO_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_MD = REPO_ROOT / "docs/crypto/CRYPTO_D5_4_COST_FIRST_EVENT_CONTRACT_V1.md"
CONTRACT_JSON = REPO_ROOT / "data/research/crypto/d5_4/contract_v1.json"
FREEZE = REPO_ROOT / "data/runtime/crypto/d5_4/contract_freeze_v1.json"
FEE_PATH = REPO_ROOT / "data/runtime/crypto/reference/fee_source_verification_v1.json"
RISK_LIMIT = REPO_ROOT / "data/runtime/crypto/BTCUSDT/reference/risk_limit_1790128376.json"
OUT_DIR = REPO_ROOT / "data/research/crypto/d5_4"

LADDER = [4.0, 5.0, 6.0, 7.0, 8.0, 10.0]


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
    if doc["families"]["E2"]["status"] != "REJECTED_AT_CONTRACT":
        raise RuntimeError("E2 must stay rejected: the cost-first screen is not advisory")
    if doc["costs"]["maker_used"] or doc["scope"]["liquidation_data_allowed"]:
        raise RuntimeError("maker and liquidation data are banned by the contract")
    return doc


def relax_threshold(candidate: dict) -> dict[str, float] | None:
    """One ladder step easier on the displacement z, for the SENS-THRESH arm."""
    condition = candidate["condition"]
    for key in ("z_disp15_max", "z_disp15_min"):
        if key in condition:
            magnitude = abs(condition[key])
            easier = max((v for v in LADDER if v < magnitude), default=None)
            if easier is None:
                return None
            return {key: -easier if key.endswith("_max") else easier}
    return None


def arm_settings(contract: dict, candidate: dict, arm: str) -> dict[str, Any] | None:
    base = {"hold_bars": contract["exits"]["X3_max_hold_bars"],
            "bracket_multiple": 0.5, "threshold_override": None}
    if arm == "BASE":
        return base
    if arm == "SENS-THRESH":
        override = relax_threshold(candidate)
        return None if override is None else {**base, "threshold_override": override}
    if arm == "SENS-TARGET":
        return {**base, "bracket_multiple": 0.75}
    if arm == "SENS-HOLD":
        return {**base, "hold_bars": 120}
    if arm == "REF-8H":
        return {**base, "hold_bars": 480}
    raise ValueError(f"unknown arm: {arm}")


def run_candidate(grid, contract: dict, candidate: dict) -> dict[str, Any]:
    cooldown = contract["event_merge"]["cooldown_bars"]
    folds = contract["folds"]["boundaries_utc"]
    oos_start = EX.to_ms(folds[0])
    oos_end = EX.to_ms(folds[-1])

    runs: dict[str, EX.RunResult] = {}
    blocks: dict[str, dict[str, Any]] = {}
    for arm in contract["arms"]:
        settings = arm_settings(contract, candidate, arm)
        if settings is None:
            continue
        signals = EV.build(grid, candidate, cooldown_bars=cooldown,
                           threshold_override=settings["threshold_override"])
        result = EX.run(grid, contract, signals, arm=arm, hold_bars=settings["hold_bars"],
                        bracket_multiple=settings["bracket_multiple"], fee_path=FEE_PATH,
                        risk_limit_path=RISK_LIMIT, oos_start_ms=oos_start,
                        oos_end_ms=oos_end)
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

    rules = V.evaluate(contract, main=main, ci=ci, folds=fold_rows, years=year_rows,
                       concentration=concentration, loyo=loyo, scenarios=scenarios,
                       sensitivity=sensitivity)
    call = V.classify(rules, main.get("trades", 0))

    exits = {}
    for trade in base.trades:
        exits[trade.exit_reason] = exits.get(trade.exit_reason, 0) + 1
    worst = min((t.net_bp for t in base.trades), default=None)
    streak = best = 0
    for trade in base.trades:
        streak = streak + 1 if trade.net_usdt < 0 else 0
        best = max(best, streak)

    return {
        "candidate": candidate["id"], "family": candidate["family"], "side": candidate["side"],
        "condition": candidate["condition"], "hypothesis": candidate["hypothesis"],
        "runs": {name: runs[name].as_dict() for name in runs},
        "main": main, "bootstrap_ci": ci, "folds": fold_rows, "years": year_rows,
        "concentration": concentration, "leave_one_year_out_net_bp": loyo,
        "cost_scenarios_bp": scenarios,
        "sensitivity": {name: blocks[name] for name in blocks if name != "BASE"},
        "exit_mix": exits,
        "tail": {"worst_trade_bp": worst,
                 "max_consecutive_losses": best,
                 "net_bp_p05": (float(np.percentile([t.net_bp for t in base.trades], 5))
                                if base.trades else None),
                 "net_bp_p01": (float(np.percentile([t.net_bp for t in base.trades], 1))
                                if base.trades else None)},
        "diagnostics": {
            "displacement_bp_median": (float(np.median([t.displacement_bp for t in base.trades]))
                                       if base.trades else None),
            "bracket_distance_bp_median": (
                float(np.median([t.bracket_distance * 1e4 for t in base.trades]))
                if base.trades else None),
            "notional_capped_trades": sum(1 for t in base.trades if t.notional_capped),
            "same_bar_target_and_stop": sum(1 for t in base.trades
                                            if t.same_bar_target_and_stop),
            "max_qty_over_depth": base.max_qty / 5.0,
            "identity_violations": base.identity_violations,
        },
        "verdict_rules": rules, "verdict": call,
        "trades": [t.as_dict() for t in base.trades],
        "events": [{"start_index": e.start, "end_index": e.end,
                    "displacement_bp": e.displacement * 1e4}
                   for e in EV.build(grid, candidate, cooldown_bars=cooldown).events],
    }


def run(write: bool = True) -> dict[str, Any]:
    started = time.time()
    contract = load_contract()
    grid = D.load_inputs()

    results = [run_candidate(grid, contract, candidate)
               for candidate in contract["candidates"]]
    survive = [r["candidate"] for r in results if r["verdict"]["verdict"] == V.SURVIVE]

    payload = {
        "record": "CRYPTO_D5_4_COST_FIRST_EVENT_RESULTS_V1",
        "contract_sha256": sha256_file(CONTRACT_JSON),
        "contract_md_sha256": sha256_file(CONTRACT_MD),
        "evidence_class": contract["folds"]["evidence_class"],
        "runtime_sec": round(time.time() - started, 1),
        "rejected_at_contract": {"E2": contract["families"]["E2"]},
        "candidates": [{k: v for k, v in r.items() if k not in ("trades", "events")}
                       for r in results],
        "survive_count": len(survive),
        "survive": survive,
        "d6_reentry": "AUTHORIZED" if survive else "NOT AUTHORIZED",
    }

    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results_v1.json").write_text(json.dumps(payload, indent=1, default=str))
        _write_parquet(results)
    return payload


def _write_parquet(results: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    trades = [row for r in results for row in r["trades"]]
    if trades:
        pq.write_table(pa.Table.from_pylist(trades), OUT_DIR / "trades_v1.parquet")
    events = [{"candidate": r["candidate"], **row} for r in results for row in r["events"]]
    if events:
        pq.write_table(pa.Table.from_pylist(events), OUT_DIR / "events_v1.parquet")


def main() -> int:
    payload = run()
    print(f"D5.4  SURVIVE {payload['survive_count']}  (D6 re-entry {payload['d6_reentry']})")
    for row in payload["candidates"]:
        block, call = row["main"], row["verdict"]
        print(f"  {row['candidate']} {row['side']:5s} n={block['trades']:4d} "
              f"gross={block['gross_bp_mean']} net={block['average_trade_bp']} "
              f"-> {call['verdict']}  failed={call.get('failed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
