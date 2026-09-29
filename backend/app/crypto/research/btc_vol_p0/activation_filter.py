"""What P1 would be worth as an on/off switch for a directional strategy that does not exist yet.

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_p0.activation_filter

P2 measured direction and found none, and found that P1's gate made direction *worse*. That
reads like a straightforward rejection of using P1 as an activation filter, but it is not the
whole calculation: the gate also triples the size of the move, and a strategy's break-even
accuracy falls as the move grows against a fixed cost.

So the two effects run in opposite directions and have to be compared on the same scale. This
computes the accuracy a directional strategy would need, gated and ungated, against the accuracy
P2 actually measured. No strategy is built and no PnL is simulated; the break-even identity is
arithmetic the stage contract allows.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

OUT = Path("data/research/crypto/btc_vol_p0/activation_filter_v1.json")
ECONOMICS = Path("data/research/crypto/btc_vol_p0/move_economics_v1.json")
P2_RESULTS = Path("data/research/crypto/btc_p2/results_v1.json")

#: Round trip taker cost on the Bybit USDT perpetual, in basis points.
ROUND_TRIP_BP = 11.0


def breakeven_accuracy(move_bp: float, cost_bp: float) -> float:
    """Accuracy a symmetric directional bet needs to break even.

    Capturing `move_bp` when right and losing the same when wrong, after `cost_bp` of round trip:
    EV = move * (2p - 1) - cost, so EV = 0 at p = 0.5 + cost / (2 * move).
    """
    if move_bp <= 0:
        return float("nan")
    return 0.5 + cost_bp / (2.0 * move_bp)


def analyse() -> dict[str, Any]:
    economics = json.loads(ECONOMICS.read_text())
    p2 = json.loads(P2_RESULTS.read_text())

    rows: dict[str, Any] = {}
    for combo, entry in economics["combos"].items():
        subsets = entry["subsets"]
        if "p1_high_state" not in subsets or not subsets["p1_high_state"].get("n"):
            continue

        p2_combo = p2["combos"].get(combo, {}).get("cells", {})
        gated_cell = p2_combo.get("FIRST_TOUCH|D2_ONLY|GATED", {})
        all_cell = p2_combo.get("FIRST_TOUCH|D2_ONLY|ALL", {})

        def accuracy(cell: dict[str, Any]) -> float | None:
            if cell.get("status") != "OK":
                return None
            # Balanced accuracy is the honest read for a symmetric bet: it is what fraction of
            # calls would be right if the two sides were equally common, which they are here.
            return max(cell["models"][m]["balanced_accuracy"] for m in ("M1", "M2"))

        record: dict[str, Any] = {"horizon_minutes": entry["horizon_minutes"],
                                  "threshold_bp": entry["threshold_bp"], "states": {}}
        for name, key, cell in (("ungated", "all", all_cell),
                                ("p1_high_state", "p1_high_state", gated_cell)):
            sub = subsets.get(key)
            if not sub or not sub.get("n"):
                continue
            move_bp = sub["straddle_held_to_expiry"]["mean_absolute_endpoint_move"] * 10_000
            needed = breakeven_accuracy(move_bp, ROUND_TRIP_BP)
            measured = accuracy(cell)
            record["states"][name] = {
                "n": sub["n"],
                "mean_absolute_move_bp": round(move_bp, 1),
                "round_trip_cost_bp": ROUND_TRIP_BP,
                "breakeven_accuracy": round(needed, 4),
                "measured_balanced_accuracy": round(measured, 4) if measured else None,
                "shortfall_pp": round((needed - measured) * 100, 2) if measured else None,
            }
        if len(record["states"]) == 2:
            gated = record["states"]["p1_high_state"]
            plain = record["states"]["ungated"]
            record["gate_effect"] = {
                "move_multiple": round(gated["mean_absolute_move_bp"]
                                       / plain["mean_absolute_move_bp"], 2),
                "breakeven_accuracy_change_pp": round(
                    (gated["breakeven_accuracy"] - plain["breakeven_accuracy"]) * 100, 2),
                "measured_accuracy_change_pp": round(
                    (gated["measured_balanced_accuracy"]
                     - plain["measured_balanced_accuracy"]) * 100, 2)
                if gated["measured_balanced_accuracy"] and plain["measured_balanced_accuracy"]
                else None,
                "shortfall_change_pp": round(gated["shortfall_pp"] - plain["shortfall_pp"], 2)
                if gated["shortfall_pp"] is not None and plain["shortfall_pp"] is not None
                else None,
            }
        rows[combo] = record

    return {
        "record": "CRYPTO_BTC_VOL_P0_ACTIVATION_FILTER_V1",
        "status": "READ_ONLY_ARITHMETIC",
        "no_strategy_built": True,
        "no_pnl_backtest": True,
        "identity": "a symmetric directional bet capturing M and paying cost C breaks even at "
                    "accuracy 0.5 + C / (2M)",
        "cost_source": "Bybit USDT perpetual taker round trip, the figure D5 verified",
        "caveat": "the measured accuracy comes from BTC-P2, where no cell reached "
                  "STRONG_DIRECTION; these numbers say how far short a directional strategy "
                  "would be, not that one exists",
        "combos": rows,
    }


def main() -> int:
    payload = analyse()
    print("break-even accuracy a directional bet would need, against what P2 measured\n")
    print(f"{'combo':10s} {'state':14s} {'move bp':>8s} {'need':>7s} {'have':>7s} {'short pp':>9s}")
    for combo, record in payload["combos"].items():
        for name, state in record["states"].items():
            have = state["measured_balanced_accuracy"]
            print(f"{combo:10s} {name:14s} {state['mean_absolute_move_bp']:8.0f} "
                  f"{state['breakeven_accuracy']:7.4f} "
                  f"{(have if have else float('nan')):7.4f} "
                  f"{(state['shortfall_pp'] if state['shortfall_pp'] is not None else float('nan')):9.2f}")
        effect = record.get("gate_effect")
        if effect:
            print(f"{'':10s} gate: move x{effect['move_multiple']}, "
                  f"break-even {effect['breakeven_accuracy_change_pp']:+.2f}pp, "
                  f"measured {effect['measured_accuracy_change_pp']:+.2f}pp, "
                  f"shortfall {effect['shortfall_change_pp']:+.2f}pp")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
