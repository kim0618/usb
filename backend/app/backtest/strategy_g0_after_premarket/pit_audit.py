"""Point-in-time audit for G0.

**A. After-hours cutoff.** Every feature of DAY T must be a function of bars that started at or
before 19:59 ET of T (and, for the RVOL denominator, of strictly earlier sessions). The audit
picks cut days, replaces every bar after the cut - the rest of the cut day beyond 19:59 and
every later day, the next premarket and the labels included - with large positive noise,
rebuilds the rows with the same builder, and requires every source field, every derived feature
and every H mask of the days up to the cut to be bit-identical.

**B. The audit must be able to fail.** ``cutoff_audit`` takes the builder as a parameter, and the
test suite hands it a deliberately leaky builder that reads DAY T+1's premarket into a DAY T
feature. The audit has to report FAIL for it; an audit that cannot fail proves nothing.

**C. Pairing.** Every row's T+1 must be the calendar's next session of its T.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight.minute import SymbolTape
from app.backtest.strategy_g0_after_premarket import features as F

LAST_FEATURE_MINUTE = F.AFTER_LAST        # 19:59


def poison_after(tape: SymbolTape, cut_day: int, seed: int) -> SymbolTape:
    """A copy whose bars after ``cut_day`` 19:59 ET carry noise instead of prices and volume."""
    rng = np.random.default_rng(seed)
    target = (tape.et_day > cut_day) | ((tape.et_day == cut_day) & (tape.minute > LAST_FEATURE_MINUTE))
    fields = {}
    for name in ("open", "high", "low", "close", "volume", "vwap"):
        array = getattr(tape, name).copy()
        values = array[target]
        noise = rng.uniform(1.0, 1000.0, size=int(target.sum())) * 13.0
        array[target] = np.where(np.isfinite(values), noise, values)
        fields[name] = array
    return SymbolTape(symbol=tape.symbol, et_day=tape.et_day.copy(), minute=tape.minute.copy(),
                      sources=dict(tape.sources), overlap_sessions=tape.overlap_sessions, **fields)


def _feature_view(rows: Mapping[str, Any], mask_fn: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]] | None,
                  ) -> dict[str, np.ndarray]:
    src = {name: np.asarray(rows["source"][name], dtype=float) for name in F.SOURCE_FIELDS}
    den = np.asarray(rows["after_rvol_denominator"], dtype=float)
    # a fixed synthetic official close: the audit is about the minute boundary, not the daily one
    close = np.where(np.isfinite(src["regular_vwap"]), src["regular_vwap"], 10.0)
    view = dict(src)
    view["after_rvol_denominator"] = den
    feats = F.after_features(src, close, den)
    # daily context (known at the T close, D's loader verifies it) is held fixed: this audit is
    # about the minute boundary, and a constant keeps every H mask evaluable
    feats["relative_strength_vs_spy"] = np.full(close.shape, 0.01)
    view.update({f"feature:{k}": v for k, v in feats.items()})
    if mask_fn is not None:
        view.update({f"mask:{k}": v.astype(float) for k, v in mask_fn(feats).items()})
    return view


def _same(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a == b) | (~np.isfinite(a) & ~np.isfinite(b))


def cutoff_audit(tapes: Sequence[SymbolTape], builder: Callable[[SymbolTape], Mapping[str, Any]],
                 mask_fn: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]] | None = None,
                 *, cuts_per_symbol: int = 3, seed: int = 20260927) -> dict[str, Any]:
    mismatches: dict[str, int] = {}
    compared = 0
    cuts_done = 0
    for n, tape in enumerate(tapes):
        days = np.unique(tape.et_day)
        if days.size < 3:
            continue
        picks = days[np.linspace(1, days.size - 2, num=min(cuts_per_symbol, days.size - 2)).astype(int)]
        clean_rows = builder(tape)
        clean = _feature_view(clean_rows, mask_fn)
        clean_days = np.asarray(clean_rows["days"])
        for j, cut in enumerate(picks):
            dirty_rows = builder(poison_after(tape, int(cut), seed + 97 * n + j))
            dirty = _feature_view(dirty_rows, mask_fn)
            dirty_days = np.asarray(dirty_rows["days"])
            upto = clean_days <= cut
            if not np.array_equal(clean_days[upto], dirty_days[dirty_days <= cut]):
                mismatches["day_set"] = mismatches.get("day_set", 0) + 1
                continue
            dirty_upto = dirty_days <= cut
            cuts_done += 1
            compared += int(upto.sum())
            for name in clean:
                bad = ~_same(clean[name][upto], dirty[name][dirty_upto])
                if bad.any():
                    mismatches[name] = mismatches.get(name, 0) + int(bad.sum())
    return {"check": "after_feature_cutoff_1959_and_future_poison", "symbols": len(tapes),
            "cuts": cuts_done, "symbol_days_compared": compared,
            "mismatched_fields": mismatches,
            "verdict": "PASS" if cuts_done and not mismatches else "FAIL"}


def pairing_audit(source_sessions: np.ndarray, next_sessions: np.ndarray, calendar: Any) -> dict[str, Any]:
    wrong = 0
    examples: list[list[str]] = []
    cache: dict[Any, Any] = {}
    for s, n in zip(source_sessions, next_sessions):
        if s not in cache:
            cache[s] = calendar.next_trading_day(s)
        if cache[s] != n or not n > s:
            wrong += 1
            if len(examples) < 5:
                examples.append([str(s), str(n), str(cache[s])])
    return {"check": "row_next_session_is_calendar_next", "rows_checked": int(len(source_sessions)),
            "wrong": wrong, "examples": examples, "verdict": "PASS" if wrong == 0 else "FAIL"}
