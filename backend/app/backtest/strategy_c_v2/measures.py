"""Extra forward measurements for V2A/V2B, on the same split-normalized basis as V1 labels.

Every array is (sessions, tickers) and row i describes signal date D = i. Like V1
``labels.py`` this module reads rows after D on purpose (they are outcomes) and is never
imported by feature code.
"""

import numpy as np

from app.backtest.strategy_c_selection.labels import LabelSet, _forward
from app.backtest.strategy_c_selection.panel import Panel

RETRACE_DAYS = (1, 2, 3)


def normalized(panel: Panel) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    factor, _, _ = panel.split_arrays()
    return panel.open / factor, panel.high / factor, panel.low / factor, panel.close / factor


def compute_measures(panel: Panel, labels: LabelSet, atr_pct: np.ndarray,
                     vol_horizons: tuple[int, ...], mfe_horizon: int = 10) -> dict[str, np.ndarray]:
    o, h, lo, c = normalized(panel)
    p0 = labels.reference
    out: dict[str, np.ndarray] = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        out["overnight_gap"] = p0 / c - 1.0

        # Retracement after the 10D peak and share of the peak gain given back by D+10.
        t_mfe = labels.time_to_mfe[mfe_horizon]
        peak = (labels.mfe[mfe_horizon] + 1.0) * p0
        for j in RETRACE_DAYS:
            value = np.full(c.shape, np.nan)
            for m in range(1, mfe_horizon + 1):
                rows = t_mfe == m
                value = np.where(rows, _forward(c, m + j) / peak - 1.0, value)
            out[f"post_mfe_retrace_{j}"] = value
        close_k = labels.close_return[mfe_horizon]
        gain = labels.mfe[mfe_horizon]
        out[f"giveback_{mfe_horizon}"] = np.where(gain > 0, (gain - close_k) / gain, np.nan)

        for k in vol_horizons:
            high_k = labels.mfe[k] + 1.0
            low_k = labels.mae[k] + 1.0
            out[f"range_{k}"] = high_k - low_k
            out[f"abs_close_{k}"] = np.abs(np.clip(labels.close_return[k], -1.0, 1.0))
            out[f"max_abs_move_{k}"] = np.fmax(labels.mfe[k], -labels.mae[k])
            out[f"log_up_{k}"] = np.log(high_k)
            out[f"log_down_{k}"] = -np.log(low_k)
            out[f"log_asym_{k}"] = out[f"log_up_{k}"] - out[f"log_down_{k}"]

            closes = [_forward(c, j) for j in range(1, k + 1)]
            highs = [_forward(h, j) for j in range(1, k + 1)]
            lows = [_forward(lo, j) for j in range(1, k + 1)]
            complete = ~np.isnan(np.stack(closes)).any(axis=0) & ~np.isnan(np.stack(highs)).any(axis=0) \
                & ~np.isnan(np.stack(lows)).any(axis=0)
            log_returns = np.stack([np.log(closes[0] / p0)] + [np.log(closes[j] / closes[j - 1])
                                                                for j in range(1, k)])
            rv = np.std(log_returns, axis=0, ddof=1)
            out[f"realized_vol_{k}"] = np.where(complete, rv, np.nan)
            prev_tr = [c] + closes[:-1]  # TR of D+1 uses D's close
            true_range = np.stack([np.fmax(highs[j], prev_tr[j]) - np.fmin(lows[j], prev_tr[j])
                                   for j in range(k)])
            atr_fwd = true_range.mean(axis=0) / p0
            out[f"atr_expansion_{k}"] = np.where(complete & (atr_pct > 0), atr_fwd / atr_pct, np.nan)
    return out
