"""Was it direction, or was it only volatility?

    PYTHONPATH=backend python -m app.crypto.research.btc_p1.direction

A path-touch model can score well without knowing anything about direction. When realised
volatility is high, the chance of touching +2% and the chance of touching -2% both rise, so a
model that has learned "the tape is violent right now" ranks both labels well and looks
informative on each of them separately.

That distinction decides whether BTC-P2 could ever produce a trade, so it is measured directly
rather than inferred. This runs on the predictions the frozen pipeline already wrote; it adds no
target, changes no threshold and feeds no gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import contract as C
from . import metrics as MT

OUT_DIR = Path("data/research/crypto/btc_p1")
PREDICTIONS = OUT_DIR / "predictions_v1.parquet"


def _load() -> dict[str, np.ndarray]:
    import pyarrow.parquet as pq
    table = pq.read_table(PREDICTIONS)
    return {name: table.column(name).to_numpy(zero_copy_only=False)
            for name in table.column_names}


def analyse(model: str = "M2") -> dict[str, Any]:
    data = _load()
    column = f"p_{model.lower()}_cal"
    target = data["target"]
    out: dict[str, Any] = {}

    for horizon, thresholds in sorted(C.TARGET_GRID.items()):
        hours = horizon // 60
        for bp in thresholds:
            up_name, down_name = f"{hours}H_UP_{bp:03d}", f"{hours}H_DOWN_{bp:03d}"
            up, down = target == up_name, target == down_name
            if not up.any() or not down.any():
                continue
            # Both series are written in the same row order, so the two masks line up bar for bar.
            if not np.array_equal(data["ts_ms"][up], data["ts_ms"][down]):
                raise RuntimeError(f"{up_name}/{down_name}: prediction rows are not aligned")

            y_up, y_down = data["y"][up], data["y"][down]
            p_up, p_down = data[column][up], data[column][down]

            # --- the volatility question: will EITHER side be touched? --------------------
            y_any = ((y_up + y_down) > 0).astype(float)
            score_any = p_up + p_down
            volatility = {
                "question": f"does price touch +{bp / 100:.2f}% or -{bp / 100:.2f}% within {hours}h",
                "base_rate": float(np.mean(y_any)),
                "roc_auc": MT.roc_auc(y_any, score_any),
                "n": int(len(y_any)),
            }

            # --- the direction question: which side, when only one of them happened -------
            only_down = (y_down == 1) & (y_up == 0)
            only_up = (y_up == 1) & (y_down == 0)
            clean = only_down | only_up
            label = only_down[clean].astype(float)
            score = (p_down - p_up)[clean]
            direction = {
                "question": "given exactly one side was touched, which one",
                "n": int(clean.sum()),
                "share_of_rows": float(clean.mean()),
                "down_share": float(np.mean(label)) if clean.any() else None,
                "roc_auc": MT.roc_auc(label, score) if clean.any() else None,
                "both_touched_share": float(np.mean((y_up == 1) & (y_down == 1))),
                "neither_touched_share": float(np.mean((y_up == 0) & (y_down == 0))),
            }

            out[f"{hours}H_{bp:03d}"] = {
                "threshold_bp": bp, "horizon_hours": hours, "model": model,
                "volatility": volatility, "direction": direction,
                "up_auc": MT.roc_auc(y_up, p_up), "down_auc": MT.roc_auc(y_down, p_down),
            }
    return out


def run(write: bool = True) -> dict[str, Any]:
    payload = {
        "record": "CRYPTO_BTC_P1_DIRECTION_DIAGNOSTIC_V1",
        "status": "POST_HOC_DIAGNOSTIC",
        "contract_sha256": C.sha256(),
        "feeds_any_gate": False,
        "note": "computed from predictions_v1.parquet, which the frozen pipeline produced. No "
                "target was added, no threshold changed, no verdict altered.",
        "reading": "a high volatility AUC with a direction AUC near 0.50 means the model knows "
                   "a large move is coming but not which way, which is not tradeable as "
                   "LONG or SHORT",
        "by_model": {model: analyse(model) for model in ("M1", "M2")},
    }
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "direction_diagnostic_v1.json").write_text(json.dumps(payload, indent=1))
    return payload


def main() -> int:
    payload = run()
    for model, rows in payload["by_model"].items():
        print(f"\n{model}  volatility AUC vs direction AUC")
        print(f"  {'pair':10s} {'volAUC':>7s} {'dirAUC':>7s} {'upAUC':>7s} {'dnAUC':>7s} "
              f"{'clean n':>8s} {'both':>6s}")
        for pair, row in rows.items():
            d, v = row["direction"], row["volatility"]
            dir_auc = d["roc_auc"]
            print(f"  {pair:10s} {v['roc_auc']:7.4f} "
                  f"{(dir_auc if dir_auc is not None else float('nan')):7.4f} "
                  f"{row['up_auc']:7.4f} {row['down_auc']:7.4f} {d['n']:8d} "
                  f"{d['both_touched_share']:6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
