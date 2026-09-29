"""After the first level is touched, does price keep going or come back?

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_p0.breakout_followthrough

A two-sided stop structure is directionless only in the sense that the market picks the side. It
still needs the move to continue after the trigger, so its viability rests entirely on what
happens from the touch onward.

D5.5 already found that breakouts in this market tend to go the wrong way. This measures the same
thing directly from the touch instant, and conditions it on P1's high state, which D5.5 did not
do. Distributions only: no entries, no fills, no PnL.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset
from app.crypto.research.btc_p2 import gate as G

OUT = Path("data/research/crypto/btc_vol_p0/breakout_followthrough_v1.json")

COMBOS: tuple[tuple[int, int], ...] = ((240, 100), (720, 100), (1440, 200))
HIGH_STATE = 0.75

#: How far past the trigger to look, as a share of the remaining horizon. A stop entry has to be
#: exited eventually; this stands in for "held to the end of the original horizon".
CHUNK = 2_000


def _first_touch_and_after(rows: np.ndarray, horizon: int, threshold_bp: int,
                           high: np.ndarray, low: np.ndarray,
                           close: np.ndarray) -> dict[str, np.ndarray]:
    """For each row: which side triggered first, when, and what happened afterwards.

    `favourable` is how far price travelled in the trigger's direction after the trigger, and
    `adverse` how far it went against it. Both are measured from the trigger level, which is
    where a stop order would have been working.
    """
    move = threshold_bp / 10_000.0
    n = len(rows)
    side = np.zeros(n, dtype=np.int8)          # +1 up trigger, -1 down trigger, 0 none
    trigger_minute = np.full(n, np.nan)
    favourable = np.full(n, np.nan)
    adverse = np.full(n, np.nan)

    offsets = np.arange(1, horizon + 1, dtype=np.int64)
    for start in range(0, n, CHUNK):
        idx = rows[start:start + CHUNK]
        block = idx[:, None] + offsets[None, :]
        reference = close[idx][:, None]
        up_level = reference * (1 + move)
        down_level = reference * (1 - move)
        up_hit = high[block] >= up_level
        down_hit = low[block] <= down_level
        up_at = np.where(up_hit.any(axis=1), up_hit.argmax(axis=1), horizon)
        down_at = np.where(down_hit.any(axis=1), down_hit.argmax(axis=1), horizon)

        for j in range(len(idx)):
            first_up, first_down = int(up_at[j]), int(down_at[j])
            if first_up >= horizon and first_down >= horizon:
                continue
            if first_up == first_down:
                continue                        # same minute; the order is not in the data
            direction = 1 if first_up < first_down else -1
            at = min(first_up, first_down)
            position = start + j
            side[position] = direction
            trigger_minute[position] = at + 1

            entry_price = float(up_level[j, 0] if direction > 0 else down_level[j, 0])
            tail = slice(int(idx[j]) + at + 2, int(idx[j]) + horizon + 1)
            if tail.start > tail.stop:
                favourable[position] = 0.0
                adverse[position] = 0.0
                continue
            highs, lows = high[tail], low[tail]
            if len(highs) == 0:
                favourable[position] = 0.0
                adverse[position] = 0.0
                continue
            if direction > 0:
                favourable[position] = float(highs.max() / entry_price - 1.0)
                adverse[position] = float(1.0 - lows.min() / entry_price)
            else:
                favourable[position] = float(1.0 - lows.min() / entry_price)
                adverse[position] = float(highs.max() / entry_price - 1.0)
    return {"side": side, "trigger_minute": trigger_minute,
            "favourable": favourable, "adverse": adverse}


def _summary(favourable: np.ndarray, adverse: np.ndarray, label: str) -> dict[str, Any]:
    ok = np.isfinite(favourable) & np.isfinite(adverse)
    if not ok.any():
        return {"label": label, "n": 0}
    good, bad = favourable[ok], adverse[ok]
    return {
        "label": label,
        "n": int(ok.sum()),
        "median_favourable_pct": float(np.median(good) * 100),
        "median_adverse_pct": float(np.median(bad) * 100),
        "mean_favourable_pct": float(np.mean(good) * 100),
        "mean_adverse_pct": float(np.mean(bad) * 100),
        # The question a stop entry lives or dies on: after the trigger, which way does price
        # travel further?
        "share_favourable_exceeds_adverse": float(np.mean(good > bad)),
        "median_ratio": float(np.median(good) / np.median(bad)) if np.median(bad) > 0 else None,
        "favourable_quantiles_pct": {q: float(np.quantile(good, q) * 100)
                                     for q in (0.25, 0.50, 0.75, 0.90)},
        "adverse_quantiles_pct": {q: float(np.quantile(bad, q) * 100)
                                  for q in (0.25, 0.50, 0.75, 0.90)},
    }


def analyse() -> dict[str, Any]:
    grid = dataset.load()
    ts_all, high, low, close = grid["ts"], grid["high"], grid["low"], grid["close"]

    combos: dict[str, Any] = {}
    for horizon, threshold_bp in COMBOS:
        frame = G.load(horizon, threshold_bp)
        rows = np.searchsorted(ts_all, frame.ts_ms)
        after = _first_touch_and_after(rows, horizon, threshold_bp, high, low, close)
        triggered = after["side"] != 0
        selected = frame.large_move >= HIGH_STATE

        combos[f"{horizon // 60}H_{threshold_bp:03d}"] = {
            "horizon_minutes": horizon,
            "threshold_bp": threshold_bp,
            "rows": int(len(rows)),
            "triggered": int(triggered.sum()),
            "trigger_rate": float(triggered.mean()),
            "median_trigger_minute": float(np.nanmedian(after["trigger_minute"])),
            "all_triggers": _summary(after["favourable"], after["adverse"], "all triggers"),
            "p1_high_state": _summary(
                np.where(selected, after["favourable"], np.nan),
                np.where(selected, after["adverse"], np.nan), "P1 high state only"),
            "up_triggers": _summary(
                np.where(after["side"] == 1, after["favourable"], np.nan),
                np.where(after["side"] == 1, after["adverse"], np.nan), "up triggers"),
            "down_triggers": _summary(
                np.where(after["side"] == -1, after["favourable"], np.nan),
                np.where(after["side"] == -1, after["adverse"], np.nan), "down triggers"),
        }

    return {
        "record": "CRYPTO_BTC_VOL_P0_BREAKOUT_FOLLOWTHROUGH_V1",
        "status": "READ_ONLY_DESCRIPTIVE",
        "no_pnl_backtest": True,
        "no_orders": True,
        "measures": "from the first touch of a level to the end of the original horizon, how far "
                    "price travels with the trigger versus against it",
        "why": "a two-sided stop structure is directionless only in that the market picks the "
               "side; it still needs follow-through after the trigger",
        "prior": "D5.5 found breakout confirmation pointed the wrong way, with a 24 percent win "
                 "rate; this checks the same thing conditioned on P1's high state",
        "combos": combos,
    }


def main() -> int:
    payload = analyse()
    for combo, entry in payload["combos"].items():
        print(f"\n=== {combo} ===")
        print(f"  triggered {entry['triggered']}/{entry['rows']} "
              f"({entry['trigger_rate'] * 100:.1f}%), median trigger at minute "
              f"{entry['median_trigger_minute']:.0f}")
        for key in ("all_triggers", "p1_high_state", "up_triggers", "down_triggers"):
            row = entry[key]
            if not row.get("n"):
                continue
            print(f"  {row['label']:20s} n={row['n']:6d}  "
                  f"median fav {row['median_favourable_pct']:5.2f}%  "
                  f"median adv {row['median_adverse_pct']:5.2f}%  "
                  f"fav>adv {row['share_favourable_exceeds_adverse'] * 100:5.1f}%")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
