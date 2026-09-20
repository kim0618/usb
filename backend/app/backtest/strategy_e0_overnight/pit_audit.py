"""Point-in-time audit: poison the future and require the present not to move.

The study builds its features from whole-panel arrays and then indexes rows out of them, which
is fast but is exactly the shape in which a look-ahead bug hides. The audit replaces every OHLCV
value after a cut session with noise, rebuilds the row table, and requires the surviving rows to
be identical bit for bit.

Which rows must survive unchanged:

* a **feature** of session ``t`` may read sessions ``<= t``, so every row with ``t < cut`` must
  match - including ``t = cut - 1``, which is the only row a feature that illegally read ``t + 1``
  would move;
* a **label** legitimately reads ``t + 1``, so labels are compared over ``t + 1 < cut`` only.

The universe filter also reads ``t + 1`` (it requires a next open, and a split test). The poison
is deliberately large but **positive and finite**, so those two tests answer the same way on the
poisoned panel and the selected row set is unchanged; the audit asserts that rather than assuming
it. Without that property the ``t = cut - 1`` row could not be compared at all, and a feature
reading one session ahead would pass unnoticed.
"""

from collections.abc import Mapping
from typing import Any

import numpy as np

from app.backtest.strategy_c_selection.panel import with_changes
from app.backtest.strategy_e0_overnight.config import E0Rules
from app.backtest.strategy_e0_overnight.dataset import DailyRows, build
from app.backtest.strategy_d_analog.source import DailyHistory


def _poisoned(history: DailyHistory, cut: int, seed: int) -> DailyHistory:
    rng = np.random.default_rng(seed)
    panel = history.panel
    changes: dict[str, np.ndarray] = {}
    for name in ("open", "high", "low", "close", "volume"):
        array = getattr(panel, name).copy()
        tail = array[cut:]
        # Large, wrong and structurally different: if any feature reads it, the row moves.
        # The values stay positive and finite, and a cell that was missing stays missing, so
        # the universe filter - which reads open(t + 1) and asks only "present and positive" -
        # answers exactly as it did on the clean panel. Replacing a missing next open with a
        # valid number would otherwise admit a row the clean build rejected, and the audit
        # would report a row-set difference that is an artifact of the poison rather than a
        # look-ahead in the study.
        noise = rng.uniform(1.0, 1000.0, size=tail.shape) * 13.0
        array[cut:] = np.where(np.isfinite(tail), noise, tail)
        changes[name] = array
    poisoned_panel = with_changes(panel, **changes)
    return DailyHistory(history.grid, poisoned_panel, history.freeze, history.figi,
                        history.snapshot_dates, dict(history.checks))


def run(history: DailyHistory, benchmark: np.ndarray, rules: E0Rules, rows: DailyRows,
        *, cut: int, seed: int = 20260920) -> dict[str, Any]:
    """Rebuild on a poisoned panel and compare the region that must not have moved."""
    poisoned_benchmark = benchmark.copy()
    rng = np.random.default_rng(seed + 1)
    poisoned_benchmark[cut:] = rng.uniform(1.0, 1000.0, size=poisoned_benchmark[cut:].shape)
    rebuilt = build(_poisoned(history, cut, seed), poisoned_benchmark, rules)

    region_a = rows.session_idx < cut
    region_b = rebuilt.session_idx < cut
    keys_a = np.stack([rows.session_idx[region_a], rows.ticker_idx[region_a]])
    keys_b = np.stack([rebuilt.session_idx[region_b], rebuilt.ticker_idx[region_b]])
    same_rows = keys_a.shape == keys_b.shape and bool(np.array_equal(keys_a, keys_b))

    checks: dict[str, Any] = {
        "cut_index": cut,
        "cut_session": rows.sessions[cut].isoformat(),
        "rows_compared": int(region_a.sum()),
        "row_set_identical": same_rows,
        "features_identical": {},
        "labels_identical": None,
    }
    if not same_rows:
        checks["detail"] = (f"{int(region_a.sum())} rows before the cut in the clean build, "
                            f"{int(region_b.sum())} in the poisoned build")
        checks["verdict"] = "FAIL"
        return checks
    for name, series in rows.features.items():
        other = rebuilt.features[name]
        a, b = series[region_a], other[region_b]
        checks["features_identical"][name] = bool(
            np.array_equal(a, b, equal_nan=True))
    label_a = rows.session_idx < cut - 1
    label_b = rebuilt.session_idx < cut - 1
    checks["labels_compared"] = int(label_a.sum())
    checks["labels_identical"] = bool(
        np.array_equal(rows.overnight[label_a], rebuilt.overnight[label_b], equal_nan=True))
    ok = (same_rows and all(checks["features_identical"].values())
          and checks["labels_identical"])
    checks["verdict"] = "PASS" if ok else "FAIL"
    return checks
