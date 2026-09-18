"""C-P2 point-in-time audit on real data: truncation replay and future-data mutations.

For each audited signal date D, the D row of the full computation is compared with the D row
computed after one of these changes. Every compared cell that differs is a PIT violation:

1. ``truncation``: sessions, splits and snapshots after D removed (the candidate-timestamp re-run).
2. ``future_bars``: every bar after D rewritten (highs x100, closes and volumes scrambled).
3. ``future_splits``: fake splits executed D+1..D+5 injected for many tickers.
4. ``later_disappeared_removed``: tickers with no bar in the last session of the panel deleted;
   the other tickers' D rows must not change.
5. ``positive_control_past_high`` (must change): a high inside D-251..D-1 raised x100 moves
   ``distance_52w_high``; proves the comparison can see a real dependency.

The audit also records how many D candidates belong to tickers that later disappear, which is
the survivorship evidence (they are in, not filtered out).
"""

from collections.abc import Sequence
from datetime import timedelta

import numpy as np

from app.backtest.strategy_c_selection.features import FEATURE_COLUMNS, FeatureSet, compute
from app.backtest.strategy_c_selection.panel import BENCHMARK, Panel, SplitEvent, truncate, with_changes
from app.backtest.strategy_c_selection.rules import SelectionRules

FAKE_SPLIT_TICKERS = 200


def _row_mismatches(full: FeatureSet, other: FeatureSet, i: int, rules: SelectionRules,
                    columns: np.ndarray | None = None) -> dict[str, int]:
    pick = slice(None) if columns is None else columns
    out: dict[str, int] = {}
    for name in FEATURE_COLUMNS:
        a, b = full.values[name][i][pick], other.values[name][i][pick]
        out[name] = int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum())
    for variant in rules.variants:
        out[f"eligible:{variant.name}"] = int((full.eligible(variant)[i][pick]
                                                != other.eligible(variant)[i][pick]).sum())
        out[f"candidate:{variant.name}"] = int((full.candidates(variant)[i][pick]
                                                 != other.candidates(variant)[i][pick]).sum())
    return {k: v for k, v in out.items() if v}


def audit(panel: Panel, full: FeatureSet, rules: SelectionRules, dates: Sequence[int],
          seed: int = 20260917) -> dict:
    rng = np.random.default_rng(seed)
    t, n = panel.shape
    has_bar_last = ~np.isnan(panel.close[-1])
    ever = ~np.isnan(panel.close).all(axis=0)
    disappeared = ever & ~has_bar_last
    disappeared[panel.column(BENCHMARK)] = False
    report: dict = {"dates": [], "violations": 0, "positive_control_detected": True}
    for i in dates:
        if not 0 <= i < t:
            raise ValueError(f"audit date index {i} is outside 0..{t - 1}; a negative index would "
                             "name different sessions in the full and the truncated panel")
        d = panel.sessions[i]
        entry: dict = {"date": d.isoformat(), "date_idx": int(i), "checks": {}}

        trunc = compute(truncate(panel, d), rules)
        entry["checks"]["truncation"] = _row_mismatches(full, trunc, i, rules)

        mutated = {name: getattr(panel, name).copy() for name in ("open", "high", "low", "close", "volume")}
        if i + 1 < t:
            future = slice(i + 1, t)
            mutated["high"][future] *= 100.0
            mutated["close"][future] *= rng.uniform(0.2, 5.0, size=mutated["close"][future].shape)
            mutated["volume"][future] *= rng.uniform(0.1, 10.0, size=mutated["volume"][future].shape)
            mutated["open"][future] *= rng.uniform(0.2, 5.0, size=mutated["open"][future].shape)
        entry["checks"]["future_bars"] = _row_mismatches(full, compute(with_changes(panel, **mutated), rules),
                                                         i, rules)

        members = np.nonzero(full.member[i])[0]
        chosen = rng.choice(members, size=min(FAKE_SPLIT_TICKERS, len(members)), replace=False)
        fake = tuple(SplitEvent(panel.tickers[j], d + timedelta(days=int(rng.integers(1, 8))),
                                float(rng.choice([1.0, 10.0])), float(rng.choice([2.0, 1.0])))
                     for j in chosen)
        fake = tuple(e for e in fake if e.split_from != e.split_to)
        entry["checks"]["future_splits"] = _row_mismatches(
            full, compute(with_changes(panel, splits=panel.splits + fake), rules), i, rules)

        keep_cols = ~disappeared
        removed = {name: getattr(panel, name).copy() for name in ("open", "high", "low", "close", "volume")}
        for name in removed:
            removed[name][:, disappeared] = np.nan
        entry["checks"]["later_disappeared_removed"] = _row_mismatches(
            full, compute(with_changes(panel, **removed), rules), i, rules, columns=keep_cols)

        violations = sum(sum(check.values()) for check in entry["checks"].values())
        entry["violations"] = int(violations)
        report["violations"] += int(violations)

        if i >= 251:
            raised = panel.high.copy()
            raised[i - 10] *= 100.0
            control = compute(with_changes(panel, high=raised), rules)
            a, b = full.values["distance_52w_high"][i], control.values["distance_52w_high"][i]
            changed = int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum())
            entry["positive_control_changed_cells"] = changed
            if changed == 0:
                report["positive_control_detected"] = False

        survivors = {}
        for variant in rules.variants:
            cand = full.candidates(variant)[i]
            survivors[variant.name] = {"candidates": int(cand.sum()),
                                       "later_disappeared": int((cand & disappeared).sum())}
        entry["survivorship"] = survivors
        report["dates"].append(entry)
    report["later_disappeared_tickers_in_panel"] = int(disappeared.sum())
    return report


def audit_dates(window: Sequence[int], count: int) -> list[int]:
    window = list(window)
    picks = np.linspace(0, len(window) - 1, count).round().astype(int)
    return sorted({window[p] for p in picks})

