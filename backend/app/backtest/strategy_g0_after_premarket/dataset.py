"""The G0 row table: one row per (source session T, ticker) that passes the declared universe.

The spine is the E0 daily PIT row of session T, imported unchanged: CS membership in the latest
reference snapshot on or before T, primary exchange, close(T) >= $5, a 20-session median dollar
volume strictly before T, an open on T+1 and no split executing in (T, T+1]. G adds, in this
order and with a counter each: the source session is not an early close, both tape days exist,
neither carries conflicting duplicate minutes, the stable identity (FIGI) of the ticker is the
same at T and at T+1, the declared after-hours activity minimum, and an entry print on T+1.

T+1 is always ``pairs[T].next_trading_session`` from the calendar pairing; this module never
adds a day to a date.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight.dataset import DailyRows
from app.backtest.strategy_g0_after_premarket import features as F

EPOCH = date(1970, 1, 1)


def ordinal(day: date) -> int:
    return (day - EPOCH).days


@dataclass(frozen=True)
class GRows:
    source_sessions: np.ndarray      # object array of date (T)
    next_sessions: np.ndarray        # object array of date (T+1)
    tickers: np.ndarray
    kinds: np.ndarray                # normal_overnight / weekend_gap / holiday_gap
    staging: np.ndarray              # bool, T or T+1 came from the V2 local staging
    features: dict[str, np.ndarray]
    labels: dict[str, np.ndarray]    # "<set>:<exit>:R|MFE|MAE|after_close_to_entry"
    target: dict[str, np.ndarray]    # raw target-side levels, for the execution diagnostics
    has_entry: np.ndarray            # bool, a primary entry print exists (the evaluated rows)
    counters: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.tickers.size)


def _spy_day_return(benchmark_close: np.ndarray) -> np.ndarray:
    out = np.full(benchmark_close.shape, np.nan)
    out[1:] = benchmark_close[1:] / benchmark_close[:-1] - 1.0
    return out


def build(cohort: Mapping[str, np.ndarray], daily: DailyRows, history: Any,
          benchmark_close: np.ndarray, pairs: Sequence[Any], rules: Any) -> GRows:
    grid = list(daily.sessions)
    pair_of = {p.source_session: p for p in pairs}
    sym = cohort["symbols"].astype(str)
    key = np.char.add(np.char.add(sym, "|"), cohort["days"].astype(str))
    order = np.argsort(key)
    sorted_key = key[order]

    def lookup(tickers: np.ndarray, days: np.ndarray) -> np.ndarray:
        wanted = np.char.add(np.char.add(tickers.astype(str), "|"), days.astype(str))
        pos = np.clip(np.searchsorted(sorted_key, wanted), 0, sorted_key.size - 1)
        hit = sorted_key[pos] == wanted
        return np.where(hit, order[pos], -1)

    sessions_t = np.array([grid[i] for i in daily.session_idx], dtype=object)
    tickers = np.array([daily.tickers[j] for j in daily.ticker_idx], dtype=object)
    counters: dict[str, Any] = {"daily_pit_rows": int(tickers.size)}

    has_pair = np.array([s in pair_of for s in sessions_t])
    counters["no_calendar_pair"] = int((~has_pair).sum())
    early = np.array([pair_of[s].source_early_close if s in pair_of else False for s in sessions_t])
    counters["early_close_source"] = int((has_pair & early).sum())
    keep = has_pair & ~early

    next_t = np.array([pair_of[s].next_trading_session if s in pair_of else s for s in sessions_t],
                      dtype=object)
    t_ord = np.array([ordinal(s) for s in sessions_t])
    n_ord = np.array([ordinal(s) for s in next_t])
    src_i = lookup(tickers, t_ord)
    tgt_i = lookup(tickers, n_ord)
    counters["no_tape_source"] = int((keep & (src_i < 0)).sum())
    keep &= src_i >= 0
    counters["no_tape_target"] = int((keep & (tgt_i < 0)).sum())
    keep &= tgt_i >= 0
    conflict = np.zeros(keep.shape, dtype=bool)
    conflict[keep] = cohort["conflicting"][src_i[keep]] | cohort["conflicting"][tgt_i[keep]]
    counters["conflicting_duplicates"] = int((keep & conflict).sum())
    keep &= ~conflict

    # stable identity across the pair (ticker reuse / change guard)
    index_of = {s: i for i, s in enumerate(grid)}
    changed = np.zeros(keep.shape, dtype=bool)
    for i in np.flatnonzero(keep):
        a = history.stable_identity(str(tickers[i]), index_of[sessions_t[i]])
        b = history.stable_identity(str(tickers[i]), index_of[next_t[i]])
        changed[i] = (a.composite_figi is not None and b.composite_figi is not None
                      and a.composite_figi != b.composite_figi)
    counters["identity_changed_between_t_and_t1"] = int(changed.sum())
    keep &= ~changed

    uni = rules.raw["universe"]["after_hours_requirements"]
    bars = np.full(keep.shape, np.nan)
    value = np.full(keep.shape, np.nan)
    bars[keep] = cohort["source|after_bars"][src_i[keep]]
    value[keep] = cohort["source|after_dollar_volume"][src_i[keep]]
    enough = (bars >= float(uni["min_after_bars_1601_1959"])) & (value >= float(uni["min_after_dollar_volume"]))
    counters["rejected_after_activity"] = int((keep & ~enough).sum())
    keep &= enough

    primary_set = rules.raw["primary"]["label_set"]
    entry = np.full(keep.shape, np.nan)
    entry[keep] = cohort[f"target|{primary_set}:entry_price"][tgt_i[keep]]
    has_entry = np.isfinite(entry) & (entry > 0)
    # rows without an entry print stay in the table (flag ``has_entry``) so that a hypothesis's
    # fill rate can be measured; every return statistic reads only rows with an entry
    counters["signal_rows_no_primary_entry_print"] = int((keep & ~has_entry).sum())
    counters["signal_rows"] = int(keep.sum())

    rows = np.flatnonzero(keep)
    s_i, t_i = src_i[rows], tgt_i[rows]
    daily_idx = rows
    close_t = daily.features["close_price"][daily_idx]
    src = {name: cohort[f"source|{name}"][s_i] for name in F.SOURCE_FIELDS}
    feats = F.after_features(src, close_t, cohort["after_rvol_denominator"][s_i])
    spy = _spy_day_return(benchmark_close)
    t_index = np.array([index_of[s] for s in sessions_t[rows]])
    with np.errstate(invalid="ignore", divide="ignore"):
        feats.update({
            "regular_day_return": daily.features["day_return"][daily_idx],
            "regular_rvol": daily.features["rvol"][daily_idx],
            "regular_dollar_volume": daily.features["dollar_volume"][daily_idx],
            "close_vs_regular_vwap": np.where(src["regular_vwap"] > 0,
                                              close_t / src["regular_vwap"] - 1.0, np.nan),
            "position_in_regular_range": daily.features["clv"][daily_idx],
            "spy_day_return": spy[t_index],
            "relative_strength_vs_spy": daily.features["day_return"][daily_idx] - spy[t_index],
            "close_price": close_t,
        })

    labels: dict[str, np.ndarray] = {}
    target: dict[str, np.ndarray] = {}
    for set_name, spec in rules.raw["label_sets"].items():
        tgt = {k.split(":", 1)[1]: v[t_i] for k, v in cohort.items()
               if k.startswith(f"target|{set_name}:")}
        tgt = {k: v for k, v in tgt.items()}
        for k, v in tgt.items():
            target[f"{set_name}:{k}"] = v
        for tag in spec["exit_last_bar_minute_et"]:
            for name, series in F.labels(tgt, feats["after_last_price"], tag).items():
                labels[f"{set_name}:{tag}:{name}"] = series

    kinds = np.array([pair_of[s].kind for s in sessions_t[rows]], dtype=object)
    staging = cohort["staging"][s_i] | cohort["staging"][t_i]
    entry_flag = has_entry[rows]
    counters["rows"] = int(entry_flag.sum())
    counters["sessions"] = int(np.unique(t_ord[rows]).size)
    counters["tickers"] = int(np.unique(tickers[rows].astype(str)).size)
    counters["rows_from_staging"] = int(staging.sum())
    for name in ("after_rvol", "position_in_after_range", "after_peak_retention",
                 "relative_strength_vs_spy", "close_vs_regular_vwap"):
        counters[f"undefined_{name}"] = int((~np.isfinite(feats[name])).sum())
    return GRows(source_sessions=sessions_t[rows], next_sessions=next_t[rows],
                 tickers=tickers[rows].astype(str).astype(object), kinds=kinds, staging=staging,
                 features=feats, labels=labels, target=target, has_entry=entry_flag,
                 counters=counters)
