"""D-AGG-4 execution PIT audits on the real frozen panel.

Audit dates are fixed by position before any statistic (12 evenly spaced evaluation sessions).
For every valid universe row of an audit date D (setup and control alike):

    AFTER_EXIT       bars after each row's own exit session scrambled      -> trade identical
    AFTER_WINDOW     every bar after D+5 scrambled (all tickers)            -> trade identical
    SIGNAL_DAY       O/H/L/C of session D scrambled                         -> trade identical
    TP_CONTROL       TIME rows: H(D+3) raised to 1.2 x P0                   -> exit becomes TP at D+3
    SL_CONTROL       TIME rows: L(D+3) lowered to 0.8 x P0                  -> exit becomes SL at D+3
    ENTRY_CONTROL    O(D+1) x 1.05 (H raised if needed)                     -> gross moves
    RV20_AS_OF       rv_20 recomputed independently from bars D-20..D, and again with every bar after D
                     scrambled -> equal to the V2-A coordinate, buckets unchanged

Validity is D-AGG-1's frozen artifact (audited there) and is not recomputed here.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel, with_changes
from app.backtest.strategy_d_agg import backtest
from app.backtest.strategy_d_agg.control import BUCKETS


def audit_dates(eval_start: int, eval_end: int, count: int = 12) -> tuple[int, ...]:
    return tuple(int(round(v)) for v in np.linspace(eval_start, eval_end, count))


def _scramble(panel: Panel, mask: np.ndarray, seed: int) -> Panel:
    rng = np.random.default_rng(seed)
    out = {}
    for name in ("open", "high", "low", "close"):
        array = getattr(panel, name).copy()
        array[mask] = array[mask] * rng.uniform(0.2, 5.0, int(mask.sum()))
        out[name] = array
    stack = np.stack([out["open"], out["close"], out["high"], out["low"]])
    with np.errstate(invalid="ignore"):
        hi, lo = np.fmax.reduce(stack, axis=0), np.fmin.reduce(stack, axis=0)
    out["high"] = np.where(mask, hi, out["high"])
    out["low"] = np.where(mask, lo, out["low"])
    return with_changes(panel, **out)


def _same(a: backtest.Trades, b: backtest.Trades) -> bool:
    g = (np.isnan(a.gross) & np.isnan(b.gross)) | (a.gross == b.gross)
    return bool(g.all() and np.array_equal(a.reason, b.reason) and np.array_equal(a.exit_offset, b.exit_offset))


def rv20_independent(panel: Panel, date_idx: int, cols: np.ndarray) -> np.ndarray:
    factor = panel.split_arrays()[0]
    price = panel.close[date_idx - 20: date_idx + 1, cols] / factor[date_idx - 20: date_idx + 1, cols]
    return np.std(np.diff(np.log(price), axis=0), axis=0, ddof=1)


def audit(panel: Panel, date_idx: int, cols: np.ndarray, valid: np.ndarray, rv20_coord: np.ndarray,
          bucket_of: np.ndarray) -> dict[str, Any]:
    s = np.full(cols.size, date_idx, dtype=np.int64)
    prices = backtest.adjusted(panel)
    base = backtest.execute(s, cols, valid, prices)
    findings: list[str] = []
    witness: dict[str, int] = {}

    after_exit = np.zeros(panel.close.shape, dtype=bool)
    for j, k, v in zip(cols, base.exit_offset, valid):
        if v:
            after_exit[int(date_idx) + int(k) + 1:, int(j)] = True   # int8 offsets: cast before adding
    if not _same(base, backtest.execute(s, cols, valid, backtest.adjusted(_scramble(panel, after_exit, date_idx)))):
        findings.append("AFTER_EXIT:trade_changed")
    after = np.zeros(panel.close.shape, dtype=bool)
    after[date_idx + 6:] = True
    if not _same(base, backtest.execute(s, cols, valid, backtest.adjusted(_scramble(panel, after, date_idx + 1)))):
        findings.append("AFTER_WINDOW:trade_changed")
    day = np.zeros(panel.close.shape, dtype=bool)
    day[date_idx, cols] = True
    if not _same(base, backtest.execute(s, cols, valid, backtest.adjusted(_scramble(panel, day, date_idx + 2)))):
        findings.append("SIGNAL_DAY:trade_changed")

    factor = panel.split_arrays()[0]
    time_rows = np.flatnonzero((base.reason == 6) & ~np.isnan(panel.close[date_idx + 3, cols]))
    for name, field, mult, code in (("TP_CONTROL", "high", 1.2, 1), ("SL_CONTROL", "low", 0.8, 2)):
        array = getattr(panel, field).copy()
        jj = cols[time_rows]
        array[date_idx + 3, jj] = base.entry_price[time_rows] * mult * factor[date_idx + 3, jj]
        moved = backtest.execute(s, cols, valid, backtest.adjusted(with_changes(panel, **{field: array})))
        hit = (moved.reason[time_rows] == code) & (moved.exit_offset[time_rows] == 3)
        witness[name] = int(hit.sum())
        if witness[name] == 0 or not hit.all():
            findings.append(f"{name}:not_all_time_rows_switched")
    open_, high = panel.open.copy(), panel.high.copy()
    open_[date_idx + 1, cols] *= 1.05
    high[date_idx + 1, cols] = np.fmax(high[date_idx + 1, cols], open_[date_idx + 1, cols])
    moved = backtest.execute(s, cols, valid, backtest.adjusted(with_changes(panel, open=open_, high=high)))
    witness["ENTRY_CONTROL"] = int(((moved.gross != base.gross) & valid).sum())
    if witness["ENTRY_CONTROL"] == 0:
        findings.append("ENTRY_CONTROL:no_witness")

    rv_here = rv20_independent(panel, date_idx, cols)
    future = np.zeros(panel.close.shape, dtype=bool)
    future[date_idx + 1:] = True
    rv_future = rv20_independent(_scramble(panel, future, date_idx + 3), date_idx, cols)
    if not np.allclose(rv_here, rv20_coord, rtol=1e-9, atol=0):   # two implementations, ULP-level order differences
        findings.append("RV20_AS_OF:independent_value_differs")
    if not np.array_equal(rv_here, rv_future):
        findings.append("RV20_AS_OF:future_changed_rv20")
    probs = np.linspace(0.1, 0.9, BUCKETS - 1)
    b_here = np.searchsorted(np.quantile(rv_here, probs), rv_here, side="right")
    b_future = np.searchsorted(np.quantile(rv_future, probs), rv_future, side="right")
    if not np.array_equal(b_here, b_future):
        findings.append("RV20_AS_OF:bucket_changed")
    # the primary buckets come from the V2-A coordinate; an ULP-level difference between the two
    # implementations can move a value sitting exactly on an interpolated edge, which is not a PIT
    # issue, so it is counted rather than failed
    witness["RV20_bucket_vs_coordinate_mismatch"] = int((b_here != bucket_of).sum())
    return {"date_idx": int(date_idx), "rows": int(cols.size), "valid_rows": int(valid.sum()),
            "findings": findings, "witness": witness}


def run(panel: Panel, dates: Sequence[int], universe_session: np.ndarray, universe_col: np.ndarray,
        valid: np.ndarray, rv20: np.ndarray, bucket: np.ndarray) -> dict[str, Any]:
    per = []
    for d in dates:
        m = universe_session == d
        per.append(audit(panel, d, universe_col[m], valid[m], rv20[m], bucket[m]))
    return {"dates": list(dates), "per_date": per,
            "violations": sum(len(p["findings"]) for p in per)}
