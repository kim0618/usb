"""What the P1 signal actually looks like in time: how often, how clustered, how soon.

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_p0.signal_shape

A probability forecast can be well calibrated and still be useless to trade, because the shape of
its firing pattern decides which instrument could carry it. Options care about when the move
arrives, not only whether it does: a 1% move that shows up four minutes before expiry pays a
straddle very differently from one that shows up in the first hour.

Read-only. Nothing here fits a model, optimises a threshold or computes PnL; the contract for
this stage forbids all three. Frequency, duration and time-to-touch are descriptive statistics of
data that already exists.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset
from app.crypto.research.btc_p2 import gate as G

OUT = Path("data/research/crypto/btc_vol_p0/signal_shape_v1.json")

#: P1's own preregistered confidence thresholds. Not re-tuned here.
STATES = (0.60, 0.65, 0.70, 0.75)

#: The combos P2 examined, so the two studies describe the same slices of the same data.
COMBOS: tuple[tuple[int, int], ...] = ((240, 100), (720, 100), (720, 200), (1440, 200))

MINUTE_MS = 60_000
HOUR_MS = 3_600_000


def _episodes(flags: np.ndarray, ts_ms: np.ndarray) -> list[tuple[int, int]]:
    """Consecutive runs of a high state, merged into episodes.

    Decision points are hourly, so an episode is a run of adjacent hours. Counting each hour as
    its own signal would multiply one event into ten.
    """
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _time_to_touch(horizon: int, threshold_bp: int, rows: np.ndarray, high: np.ndarray,
                   low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Minutes until either side is first touched, NaN when neither is inside the horizon."""
    move = threshold_bp / 10_000.0
    offsets = np.arange(1, horizon + 1, dtype=np.int64)
    out = np.full(len(rows), np.nan)
    for start in range(0, len(rows), 4000):
        idx = rows[start:start + 4000]
        block = idx[:, None] + offsets[None, :]
        reference = close[idx][:, None]
        hit = (high[block] >= reference * (1 + move)) | (low[block] <= reference * (1 - move))
        any_hit = hit.any(axis=1)
        first = np.where(any_hit, hit.argmax(axis=1) + 1, -1).astype(float)
        first[~any_hit] = np.nan
        out[start:start + len(idx)] = first
    return out


def analyse() -> dict[str, Any]:
    grid = dataset.load()
    ts_all, high, low, close = grid["ts"], grid["high"], grid["low"], grid["close"]

    combos: dict[str, Any] = {}
    for horizon, threshold_bp in COMBOS:
        frame = G.load(horizon, threshold_bp)
        rows = np.searchsorted(ts_all, frame.ts_ms)
        if not np.array_equal(ts_all[rows], frame.ts_ms):
            raise RuntimeError("P1 prediction timestamps are not on the research grid")
        large_move = frame.large_move
        span_years = (frame.ts_ms[-1] - frame.ts_ms[0]) / (365.25 * 24 * HOUR_MS)

        states: dict[str, Any] = {}
        for level in STATES:
            flags = large_move >= level
            episodes = _episodes(flags, frame.ts_ms)
            lengths = np.array([end - begin + 1 for begin, end in episodes], dtype=float)
            states[f"p>={level:.2f}"] = {
                "hours_in_state": int(flags.sum()),
                "share_of_hours": float(flags.mean()),
                "episodes": len(episodes),
                "episodes_per_year": float(len(episodes) / span_years) if span_years else None,
                "median_episode_hours": float(np.median(lengths)) if len(lengths) else None,
                "max_episode_hours": float(lengths.max()) if len(lengths) else None,
                # Hours inside an episode divided by episodes: how much one signal overlaps
                # itself, which decides whether positions would stack.
                "mean_episode_hours": float(lengths.mean()) if len(lengths) else None,
            }

        ttt = _time_to_touch(horizon, threshold_bp, rows, high, low, close)
        touched = np.isfinite(ttt)
        overall = {
            "rows": int(len(rows)),
            "touch_rate": float(touched.mean()),
            "median_minutes": float(np.median(ttt[touched])) if touched.any() else None,
            "quartiles_minutes": {
                q: float(np.quantile(ttt[touched], q)) for q in (0.10, 0.25, 0.50, 0.75, 0.90)
            } if touched.any() else None,
            "share_touched_in_first_quarter": float(
                np.mean(ttt[touched] <= horizon / 4)) if touched.any() else None,
            "share_touched_in_last_quarter": float(
                np.mean(ttt[touched] > 3 * horizon / 4)) if touched.any() else None,
        }

        high_state = large_move >= 0.75
        conditioned = None
        if high_state.any() and np.isfinite(ttt[high_state]).any():
            sub = ttt[high_state]
            sub_touched = np.isfinite(sub)
            conditioned = {
                "rows": int(high_state.sum()),
                "touch_rate": float(sub_touched.mean()),
                "median_minutes": float(np.median(sub[sub_touched])),
                "share_touched_in_first_quarter": float(
                    np.mean(sub[sub_touched] <= horizon / 4)),
            }

        combos[f"{horizon // 60}H_{threshold_bp:03d}"] = {
            "horizon_minutes": horizon,
            "threshold_bp": threshold_bp,
            "span_years": round(float(span_years), 2),
            "large_move_quantiles": {
                q: float(np.quantile(large_move, q))
                for q in (0.50, 0.75, 0.90, 0.95, 0.99)
            },
            "states": states,
            "time_to_touch_all": overall,
            "time_to_touch_high_state": conditioned,
        }

    return {
        "record": "CRYPTO_BTC_VOL_P0_SIGNAL_SHAPE_V1",
        "status": "READ_ONLY_DESCRIPTIVE",
        "source": "P1 per-fold validation predictions plus the D2 research grid",
        "no_model_fitted": True,
        "no_threshold_optimised": True,
        "no_pnl_computed": True,
        "note": "frequency and timing decide which instrument could carry the signal; they are "
                "not evidence that any instrument would profit",
        "combos": combos,
    }


def main() -> int:
    payload = analyse()
    for combo, entry in payload["combos"].items():
        print(f"\n=== {combo} (horizon {entry['horizon_minutes']}m, "
              f"{entry['span_years']} years) ===")
        for level, state in entry["states"].items():
            print(f"  {level}: {state['hours_in_state']:5d} hours "
                  f"({state['share_of_hours'] * 100:5.2f}%)  "
                  f"{state['episodes']:4d} episodes "
                  f"({state['episodes_per_year']:.0f}/yr, "
                  f"median {state['median_episode_hours']:.0f}h, "
                  f"max {state['max_episode_hours']:.0f}h)")
        ttt = entry["time_to_touch_all"]
        print(f"  time to touch: rate {ttt['touch_rate'] * 100:.1f}%  "
              f"median {ttt['median_minutes']:.0f}m  "
              f"first quarter {ttt['share_touched_in_first_quarter'] * 100:.0f}%  "
              f"last quarter {ttt['share_touched_in_last_quarter'] * 100:.0f}%")
        if entry["time_to_touch_high_state"]:
            high = entry["time_to_touch_high_state"]
            print(f"  p>=0.75 subset: n={high['rows']}  rate {high['touch_rate'] * 100:.1f}%  "
                  f"median {high['median_minutes']:.0f}m")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
