"""Mutation audits: change what a point-in-time result must not depend on, and require no change.

Asserting that the pipeline is point-in-time is worth little; the audits here try to break it.
Two kinds of evidence are collected. Negative controls mutate the future - bars after the query
date, splits executed after it, a delisted name that resumes trading later - and demand that the
neighbour lists come back bit for bit identical. Positive controls mutate the *past* inside the
window and demand that the result does change, because an audit that can only pass is not an
audit (D0 PIT mutations #4, #6, #9).

The strongest of them is truncation: the whole search is re-run on a dataset that physically ends
at the query date, so a leak cannot hide behind a mask or a slice bound. It is also the most
expensive, which is why the run audits a sample of dates rather than all 221.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent, truncate, with_changes
from app.backtest.strategy_d_analog import d2, library, pit
from app.backtest.strategy_d_analog.config import AnalogRules
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.models import SessionGrid, TestId
from app.backtest.strategy_d_analog.source import DailyHistory, grid_digest


def truncate_history(history: DailyHistory, query_idx: int) -> DailyHistory:
    """The dataset as it physically was at the close of the query date, sessions and all.

    ``AsOfView`` already makes later rows unreachable, so this is a second, independent way of
    asking the same question: if the two disagree anywhere, one of them is wrong.
    """
    session = history.grid.session(query_idx)
    panel = with_changes(truncate(history.panel, session))
    dates = history.grid.dates[:query_idx + 1]
    snapshots = tuple(d for d in history.snapshot_dates if d <= session)
    figi = {d: table for d, table in history.figi.items() if d <= session}
    return DailyHistory(SessionGrid(dates, grid_digest(dates)), panel, history.freeze, figi,
                        snapshots, dict(history.checks))


def mutate_future_bars(history: DailyHistory, after_idx: int, seed: int = 7) -> DailyHistory:
    """D0 PIT #2: scramble every bar after the query date, keeping the dataset's shape."""
    rng = np.random.default_rng(seed)
    panel = history.panel
    shape = panel.close[after_idx + 1:].shape
    changed = with_changes(
        panel,
        open=np.concatenate([panel.open[:after_idx + 1],
                             panel.open[after_idx + 1:] * rng.uniform(0.2, 5.0, shape)]),
        high=np.concatenate([panel.high[:after_idx + 1], panel.high[after_idx + 1:] * 100.0]),
        low=np.concatenate([panel.low[:after_idx + 1], panel.low[after_idx + 1:] * 0.01]),
        close=np.concatenate([panel.close[:after_idx + 1],
                              panel.close[after_idx + 1:] * rng.uniform(0.2, 5.0, shape)]),
        volume=np.concatenate([panel.volume[:after_idx + 1],
                               panel.volume[after_idx + 1:] * rng.uniform(0.1, 10.0, shape)]))
    return _replace_panel(history, changed)


def plant_future_splits(history: DailyHistory, after_idx: int, count: int = 5) -> DailyHistory:
    """D0 PIT #3: execute large splits on sessions the query date has not reached."""
    planted = tuple(
        SplitEvent(ticker, history.grid.session(after_idx + 1 + offset), 1.0, 10.0)
        for offset in range(count)
        for ticker in history.tickers[offset::max(len(history.tickers) // 8, 1)])
    return _replace_panel(history, with_changes(history.panel,
                                                splits=history.panel.splits + planted))


def scale_window_close(history: DailyHistory, session_idx: int, ticker: str,
                       factor: float) -> DailyHistory:
    """Positive control (D0 PIT #9): a past close inside the window must move the vectors."""
    close = history.panel.close.copy()
    close[session_idx, history.panel.column(ticker)] *= factor
    return _replace_panel(history, with_changes(history.panel, close=close))


def _replace_panel(history: DailyHistory, panel: Panel) -> DailyHistory:
    return DailyHistory(history.grid, panel, history.freeze, history.figi, history.snapshot_dates,
                        dict(history.checks))


@dataclass(frozen=True)
class DateAudit:
    """What one truncated re-run found for one query date across every test of the study."""

    query_date_idx: int
    compared_queries: int
    compared_neighbors: int
    mismatched_queries: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatched_queries


def neighbors_for_date(history: DailyHistory, rules: AnalogRules, validity: Mapping[int, np.ndarray],
                       query_idx: int, tests: Sequence[TestId],
                       log: pit.InvariantLog | None = None) -> dict:
    """Run the real search for a single query date, on whatever dataset is handed in.

    Same functions as the production pass - ``library.build``, ``d2.encode_queries``,
    ``d2.run_test`` - so an audit cannot pass by exercising a simplified copy of the pipeline.
    """
    log = log or pit.InvariantLog()
    eligibility = d2.Eligibility(history, rules, log)
    figi = library.FigiCoder()
    found: dict = {}
    for window in sorted({t.window for t in tests}):
        window_tests = [t for t in tests if t.window == window]
        horizons = tuple(sorted({t.horizon for t in window_tests}))
        lib = library.build(history, rules, window=window, horizons=horizons,
                            validity=validity, eligibility=eligibility,
                            eval_end_idx=query_idx + window + min(horizons), figi=figi)
        queries = d2.encode_queries(history, eligibility, rules, window, (query_idx,), figi, log)
        for test in window_tests:
            output = d2.run_test(test=test, lib=lib, queries=queries, eval_indices=(query_idx,),
                                 rules=rules, log=log, audit_dates=(query_idx,), capture=found)
            del output
    return found


def audit_truncation(history: DailyHistory, rules: AnalogRules, query_idx: int,
                     tests: Sequence[TestId], capture: Mapping[tuple, tuple],
                     log: Callable[[str], None] = lambda _: None) -> DateAudit:
    """Re-run one query date on a physically truncated dataset and compare against the full run."""
    short = truncate_history(history, query_idx)
    horizons = tuple(sorted({t.horizon for t in tests}))
    validity = compute_validity(short.panel, horizons, rules.ca_suspect_ratio)
    found = neighbors_for_date(short, rules, validity.valid, query_idx, tests)
    expected = {key: value for key, value in capture.items() if key[1] == query_idx}
    mismatched: list[str] = []
    neighbors = 0
    for key, (ends, tickers, metric) in expected.items():
        if key not in found:
            mismatched.append(f"{key[0]}:{key[2]}:missing")
            continue
        other_ends, other_tickers, other_metric = found[key]
        neighbors += int(ends.size)
        if not (np.array_equal(ends, other_ends) and np.array_equal(tickers, other_tickers)
                and np.array_equal(metric, other_metric)):
            mismatched.append(f"{key[0]}:{key[2]}")
    for key in found:
        if key not in expected:
            mismatched.append(f"{key[0]}:{key[2]}:extra")
    log(f"  truncation audit at {query_idx}: {len(expected)} queries, {neighbors:,} neighbours, "
        f"{len(mismatched)} mismatched")
    return DateAudit(query_idx, len(expected), neighbors, tuple(sorted(mismatched)[:20]))


def audit_run(history: DailyHistory, rules: AnalogRules, audit_dates: Sequence[int],
              capture: Mapping[tuple, tuple],
              log: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """Every audited date of one D2 run, folded into the summary the gate reads."""
    results = [audit_truncation(history, rules, index, rules.tests, capture, log)
               for index in audit_dates]
    return {"dates": [r.query_date_idx for r in results],
            "compared_queries": sum(r.compared_queries for r in results),
            "compared_neighbors": sum(r.compared_neighbors for r in results),
            "mismatched": {str(r.query_date_idx): list(r.mismatched_queries)
                           for r in results if not r.passed},
            "passed": all(r.passed for r in results)}
