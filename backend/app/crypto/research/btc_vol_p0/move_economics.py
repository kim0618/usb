"""How big are the moves, and how big would they have to be to pay for each structure.

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_p0.move_economics

This is arithmetic on distributions that already exist, which the stage contract allows; it is
not a backtest and computes no PnL. What it produces is the break-even each route needs and the
realised move distribution to hold it against.

One distinction decides most of the answer. P1's strong targets are *path* targets: did price
touch a level at any point. A long option held to expiry pays on the *endpoint*: where price
finished. Those are different questions with very different probabilities, and reading a
path-touch forecast as if it priced an option is the main way this audit could go wrong.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset
from app.crypto.research.btc_p2 import gate as G

OUT = Path("data/research/crypto/btc_vol_p0/move_economics_v1.json")

COMBOS: tuple[tuple[int, int], ...] = ((240, 100), (720, 100), (720, 200), (1440, 200))
HIGH_STATE = 0.75

#: Round trip taker cost on the Bybit USDT perpetual, from the fee scenarios D5 already verified.
#: Used only to state what a perpetual structure must clear, never to simulate a fill.
PERP_TAKER_ROUND_TRIP_BP = 11.0

#: An at-the-money straddle costs roughly this fraction of spot, given annualised implied
#: volatility and time to expiry in years: the standard Brenner-Subrahmanyam approximation
#: (2/sqrt(2*pi) = 0.7979). Used to ask what implied volatility would make a route break even,
#: not to price a trade.
STRADDLE_COEFFICIENT = 0.7978845608


def straddle_cost_fraction(implied_vol: float, years: float) -> float:
    return STRADDLE_COEFFICIENT * implied_vol * np.sqrt(years)


def breakeven_implied_vol(move_fraction: float, years: float) -> float:
    """The implied volatility at which an ATM straddle costs exactly `move_fraction` of spot."""
    return move_fraction / (STRADDLE_COEFFICIENT * np.sqrt(years))


def _forward_stats(rows: np.ndarray, horizon: int, high: np.ndarray, low: np.ndarray,
                   close: np.ndarray) -> dict[str, np.ndarray]:
    """Endpoint return, maximum excursion each way, for each decision row."""
    offsets = np.arange(1, horizon + 1, dtype=np.int64)
    endpoint = np.empty(len(rows))
    up_excursion = np.empty(len(rows))
    down_excursion = np.empty(len(rows))
    for start in range(0, len(rows), 4000):
        idx = rows[start:start + 4000]
        block = idx[:, None] + offsets[None, :]
        reference = close[idx]
        endpoint[start:start + len(idx)] = close[idx + horizon] / reference - 1.0
        up_excursion[start:start + len(idx)] = high[block].max(axis=1) / reference - 1.0
        down_excursion[start:start + len(idx)] = 1.0 - low[block].min(axis=1) / reference
    return {"endpoint": endpoint, "up": up_excursion, "down": down_excursion}


def _describe(values: np.ndarray, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "n": int(len(values)),
        "mean": float(np.mean(values)),
        "quantiles": {q: float(np.quantile(values, q))
                      for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95)},
    }


def analyse() -> dict[str, Any]:
    grid = dataset.load()
    ts_all, high, low, close = grid["ts"], grid["high"], grid["low"], grid["close"]

    combos: dict[str, Any] = {}
    for horizon, threshold_bp in COMBOS:
        frame = G.load(horizon, threshold_bp)
        rows = np.searchsorted(ts_all, frame.ts_ms)
        stats = _forward_stats(rows, horizon, high, low, close)
        years = horizon / (365.25 * 24 * 60)

        selected = frame.large_move >= HIGH_STATE
        subsets = {"all": np.ones(len(rows), dtype=bool), "p1_high_state": selected}

        entry: dict[str, Any] = {
            "horizon_minutes": horizon,
            "threshold_bp": threshold_bp,
            "horizon_years": years,
            "subsets": {},
        }
        for name, mask in subsets.items():
            if not mask.any():
                entry["subsets"][name] = {"n": 0}
                continue
            endpoint = np.abs(stats["endpoint"][mask])
            excursion = np.maximum(stats["up"][mask], stats["down"][mask])
            target = threshold_bp / 10_000.0

            # What a long straddle held to expiry actually needs: the endpoint, not the touch.
            straddle = {
                "definition": "a long ATM straddle held to expiry pays on the endpoint move, "
                              "not on whether a level was touched",
                "absolute_endpoint_move": _describe(endpoint, "abs endpoint return"),
                "share_endpoint_beyond_threshold": float(np.mean(endpoint >= target)),
                "share_touch_beyond_threshold": float(np.mean(excursion >= target)),
                "mean_absolute_endpoint_move": float(np.mean(endpoint)),
                # Break-even implied volatility: above this the straddle costs more than the
                # average realised endpoint move, so the structure loses on average.
                "breakeven_implied_vol_vs_mean_endpoint": float(
                    breakeven_implied_vol(float(np.mean(endpoint)), years)),
                "breakeven_implied_vol_vs_median_endpoint": float(
                    breakeven_implied_vol(float(np.median(endpoint)), years)),
            }

            # A structure that closes at the touch instead captures the excursion, which is
            # larger, but only if it can be exited at that instant.
            touch_capture = {
                "definition": "an intraday structure closed at the moment of touch captures the "
                              "excursion, which requires being able to exit at that instant",
                "max_excursion": _describe(excursion, "max excursion either way"),
                "mean_max_excursion": float(np.mean(excursion)),
                "breakeven_implied_vol_vs_mean_excursion": float(
                    breakeven_implied_vol(float(np.mean(excursion)), years)),
            }

            # A two-sided stop structure pays the round trip twice whenever the first side is a
            # false start, so the cost is not one round trip but one plus the whipsaw rate.
            first_up = stats["up"][mask] >= target
            first_down = stats["down"][mask] >= target
            both = first_up & first_down
            whipsaw_rate = float(np.mean(both[first_up | first_down])) \
                if (first_up | first_down).any() else float("nan")
            dual_stop = {
                "definition": "stops on both sides, the loser cancelled; whipsaw means both "
                              "levels were reached, so the first fill was a false start",
                "share_any_side_touched": float(np.mean(first_up | first_down)),
                "share_both_sides_touched": float(np.mean(both)),
                "whipsaw_rate_given_any_touch": whipsaw_rate,
                "round_trip_cost_bp": PERP_TAKER_ROUND_TRIP_BP,
                "expected_cost_bp_per_signal": float(
                    PERP_TAKER_ROUND_TRIP_BP * (1.0 + whipsaw_rate))
                if np.isfinite(whipsaw_rate) else None,
                "note": "the entry itself is a stop order, so it is filled at or through the "
                        "trigger; the gap between trigger and fill is not modelled here and "
                        "makes the real cost higher, never lower",
            }

            entry["subsets"][name] = {
                "n": int(mask.sum()),
                "straddle_held_to_expiry": straddle,
                "closed_at_touch": touch_capture,
                "dual_stop_perpetual": dual_stop,
            }
        combos[f"{horizon // 60}H_{threshold_bp:03d}"] = entry

    return {
        "record": "CRYPTO_BTC_VOL_P0_MOVE_ECONOMICS_V1",
        "status": "READ_ONLY_ARITHMETIC",
        "no_pnl_backtest": True,
        "no_orders": True,
        "high_state_threshold": HIGH_STATE,
        "straddle_approximation": "ATM straddle premium is about 0.798 * IV * sqrt(T) of spot "
                                  "(Brenner-Subrahmanyam); used to invert for a break-even "
                                  "implied volatility, not to price a trade",
        "critical_distinction": "P1's strong targets are path-touch; a long option held to "
                                "expiry pays on the endpoint. The two probabilities differ by a "
                                "factor of roughly two in this data.",
        "combos": combos,
    }


def main() -> int:
    payload = analyse()
    for combo, entry in payload["combos"].items():
        print(f"\n=== {combo}  horizon {entry['horizon_minutes']}m "
              f"({entry['horizon_years'] * 365.25 * 24:.1f}h) ===")
        for name, sub in entry["subsets"].items():
            if not sub.get("n"):
                continue
            straddle = sub["straddle_held_to_expiry"]
            touch = sub["closed_at_touch"]
            dual = sub["dual_stop_perpetual"]
            print(f"  [{name}] n={sub['n']}")
            print(f"    touch >= threshold      {straddle['share_touch_beyond_threshold'] * 100:5.1f}%")
            print(f"    ENDPOINT >= threshold   {straddle['share_endpoint_beyond_threshold'] * 100:5.1f}%"
                  f"   <- what a held straddle needs")
            print(f"    mean |endpoint| move    {straddle['mean_absolute_endpoint_move'] * 100:5.2f}%"
                  f"   break-even IV {straddle['breakeven_implied_vol_vs_mean_endpoint'] * 100:5.1f}%")
            print(f"    mean max excursion      {touch['mean_max_excursion'] * 100:5.2f}%"
                  f"   break-even IV {touch['breakeven_implied_vol_vs_mean_excursion'] * 100:5.1f}%")
            print(f"    dual stop: any {dual['share_any_side_touched'] * 100:4.1f}%  "
                  f"both {dual['share_both_sides_touched'] * 100:4.1f}%  "
                  f"whipsaw {dual['whipsaw_rate_given_any_touch'] * 100:4.1f}%  "
                  f"expected cost {dual['expected_cost_bp_per_signal']:.1f}bp")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
