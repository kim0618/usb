"""Does P1 know something a volatility-tracking option price would not already charge for?

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_p0.realized_vs_trailing

The decisive question for any long-volatility route is not whether P1 predicts large moves. It is
whether P1 predicts them *better than the option market already prices*. A straddle bought when
everyone can see the tape is violent is expensive for exactly that reason.

Without an option chain the implied side cannot be measured. What can be measured is its main
driver: implied volatility tracks trailing realised volatility closely, so the ratio of forward
realised volatility to trailing realised volatility is a lower-bound proxy for how much of P1's
forecast is already visible in the price. A ratio near one means the move was already priced; a
ratio well above one means P1 sees something extra.

This is a proxy and is labelled as one. It cannot replace an implied volatility series, and the
audit's verdict says so.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset
from app.crypto.research.btc_p1 import windows as W
from app.crypto.research.btc_p2 import gate as G

OUT = Path("data/research/crypto/btc_vol_p0/realized_vs_trailing_v1.json")

COMBOS: tuple[tuple[int, int], ...] = ((240, 100), (720, 100), (1440, 200))
STATES = (0.60, 0.70, 0.75)
DAY = 1440


def _forward_realised_bp(rows: np.ndarray, horizon: int, returns: np.ndarray) -> np.ndarray:
    """Realised volatility over the forward horizon, annualised the same way as trailing."""
    offsets = np.arange(1, horizon + 1, dtype=np.int64)
    out = np.empty(len(rows))
    for start in range(0, len(rows), 4000):
        idx = rows[start:start + 4000]
        block = returns[idx[:, None] + offsets[None, :]]
        out[start:start + len(idx)] = np.std(block, axis=1) * np.sqrt(horizon) * 1e4
    return out


def analyse() -> dict[str, Any]:
    grid = dataset.load()
    ts_all, close = grid["ts"], grid["close"]
    ln_close = np.log(close)
    returns = np.concatenate(([0.0], np.diff(ln_close)))
    trailing_24h = W.rolling_std(returns, DAY) * np.sqrt(DAY) * 1e4

    combos: dict[str, Any] = {}
    for horizon, threshold_bp in COMBOS:
        frame = G.load(horizon, threshold_bp)
        rows = np.searchsorted(ts_all, frame.ts_ms)
        forward = _forward_realised_bp(rows, horizon, returns)
        # Put both on the same horizon so the ratio is dimensionless.
        trailing = trailing_24h[rows] * np.sqrt(horizon / DAY)
        usable = np.isfinite(forward) & np.isfinite(trailing) & (trailing > 0)
        ratio = np.full(len(rows), np.nan)
        ratio[usable] = forward[usable] / trailing[usable]

        states: dict[str, Any] = {}
        for level in (None, *STATES):
            mask = usable if level is None else (usable & (frame.large_move >= level))
            if mask.sum() < 20:
                states["unconditional" if level is None else f"p>={level:.2f}"] = {
                    "n": int(mask.sum()), "note": "too few rows"}
                continue
            states["unconditional" if level is None else f"p>={level:.2f}"] = {
                "n": int(mask.sum()),
                "median_trailing_bp": float(np.median(trailing[mask])),
                "median_forward_bp": float(np.median(forward[mask])),
                "median_ratio": float(np.median(ratio[mask])),
                "mean_ratio": float(np.mean(ratio[mask])),
                # The share of signals where the coming window was genuinely more violent than
                # the window a price-setter had just watched.
                "share_ratio_above_one": float(np.mean(ratio[mask] > 1.0)),
                "ratio_quantiles": {q: float(np.quantile(ratio[mask], q))
                                    for q in (0.25, 0.50, 0.75, 0.90)},
            }

        combos[f"{horizon // 60}H_{threshold_bp:03d}"] = {
            "horizon_minutes": horizon, "threshold_bp": threshold_bp, "states": states}

    return {
        "record": "CRYPTO_BTC_VOL_P0_REALIZED_VS_TRAILING_V1",
        "status": "READ_ONLY_PROXY",
        "proxy_warning": "implied volatility is not measured here. Trailing realised volatility "
                         "is its main driver, so this ratio bounds how much of P1's forecast a "
                         "volatility-tracking option price would already have charged for. It "
                         "is a proxy and cannot settle the question.",
        "reading": "a median ratio near 1.0 means the coming window is about as violent as the "
                   "one just observed, so a straddle would already be priced for it; a ratio "
                   "well above 1.0 means P1 sees something the recent tape does not show",
        "no_pnl_backtest": True,
        "combos": combos,
    }


def main() -> int:
    payload = analyse()
    for combo, entry in payload["combos"].items():
        print(f"\n=== {combo} ===")
        print(f"  {'state':12s} {'n':>6s} {'trail bp':>9s} {'fwd bp':>8s} "
              f"{'ratio':>7s} {'>1':>6s}")
        for name, state in entry["states"].items():
            if "median_ratio" not in state:
                print(f"  {name:12s} {state['n']:6d}  (too few rows)")
                continue
            print(f"  {name:12s} {state['n']:6d} {state['median_trailing_bp']:9.0f} "
                  f"{state['median_forward_bp']:8.0f} {state['median_ratio']:7.3f} "
                  f"{state['share_ratio_above_one'] * 100:5.1f}%")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
