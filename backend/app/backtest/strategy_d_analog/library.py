"""The Historical Pattern Library: every stride window a query may ever be compared against.

Layout is the whole point of this module. A quarter of a million windows cannot be Python
objects if the search is to be exact, so a library is a dense float64 matrix per representation
plus parallel metadata columns, built once per window length and sliced per query.

Rows are ordered by ``(end_idx, ticker)`` and that ordering carries the D0 tie-break: because
``Panel.tickers`` is sorted, "library end session ascending, then ticker ascending" is simply
row order, and a stable sort on the score alone applies all three keys at once (D2 §19).

The expanding library of D0 is this same matrix read as a prefix: a query's candidates are the
rows up to ``searchsorted(end_idx, D - W - h)``, so no per-query mask over the full library is
ever built and a truncated re-run sees the identical slice.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from app.backtest.strategy_d_analog import encoder, pit, universe
from app.backtest.strategy_d_analog.config import AnalogRules
from app.backtest.strategy_d_analog.models import REPRESENTATIONS, HardFail, LibraryExclusion
from app.backtest.strategy_d_analog.source import DailyHistory


class FigiCoder:
    """Interns composite FIGI strings as int32 codes; ``-1`` is "the snapshot carries none".

    D0's ``figi_rule`` compares codes only when both sides have one, so the null code must never
    equal itself in a comparison - an int the equality test skips is the cheapest way to say so.
    """

    NULL = -1

    def __init__(self) -> None:
        self._codes: dict[str, int] = {}

    def code(self, figi: str | None) -> int:
        if not figi:
            return self.NULL
        if figi not in self._codes:
            self._codes[figi] = len(self._codes)
        return self._codes[figi]

    def strings(self) -> tuple[str, ...]:
        return tuple(sorted(self._codes, key=self._codes.__getitem__))

    def name(self, code: int) -> str | None:
        return None if code < 0 else self.strings()[code]


@dataclass(frozen=True)
class PatternLibrary:
    """One window length's library. ``vectors`` holds a matrix per representation, rows aligned."""

    window: int
    horizons: tuple[int, ...]
    end_idx: np.ndarray      # (L,) int32, ascending
    ticker_col: np.ndarray   # (L,) int32, ascending inside an end index
    figi_code: np.ndarray    # (L,) int32, -1 where the snapshot has no composite FIGI
    defined: dict[str, np.ndarray]
    valid: dict[int, np.ndarray]
    vectors: dict[str, np.ndarray]
    sq_norm: dict[str, np.ndarray]
    stride_end_indices: tuple[int, ...]
    exclusions: dict[str, int]
    windows_by_end_idx: dict[int, int]
    _compact: dict = field(default_factory=dict, repr=False, compare=False)

    def __len__(self) -> int:
        return int(self.end_idx.shape[0])

    def compact_rows(self, representation: str, horizon: int) -> np.ndarray:
        """Row indices this test may ever use: eligible, vector defined, label decidable.

        None of the three depends on the query, so the filter happens once per test instead of
        once per query date, and the result stays ``end_idx`` ascending (D2 §10.4).
        """
        key = (representation, horizon)
        if key not in self._compact:
            keep = self.defined[representation] & self.valid[horizon]
            self._compact[key] = np.nonzero(keep)[0].astype(np.int64)
        return self._compact[key]

    def manifest(self) -> dict:
        return {"window": self.window, "rows": len(self),
                "stride_end_indices": len(self.stride_end_indices),
                "first_end_idx": self.stride_end_indices[0] if self.stride_end_indices else None,
                "last_end_idx": self.stride_end_indices[-1] if self.stride_end_indices else None,
                "defined": {rep: int(self.defined[rep].sum()) for rep in REPRESENTATIONS},
                "valid_by_horizon": {str(h): int(self.valid[h].sum()) for h in self.horizons},
                "rows_by_test": {f"{rep}_H{h}": int(self.compact_rows(rep, h).size)
                                 for h in self.horizons for rep in REPRESENTATIONS},
                "exclusions": dict(self.exclusions),
                "windows_by_end_idx": {str(k): v for k, v in sorted(self.windows_by_end_idx.items())}}


def last_library_end_idx(eval_end_idx: int, window: int, horizons: Sequence[int]) -> int:
    """The latest end index any query of the study can reach: ``eval_end - W - min(h)`` (D2 §13)."""
    return eval_end_idx - window - min(horizons)


def build(history: DailyHistory, rules: AnalogRules, *, window: int, horizons: Sequence[int],
          validity: Mapping[int, np.ndarray], eligibility: Callable[[int], universe.Eligibility],
          eval_end_idx: int, figi: FigiCoder,
          log: Callable[[str], None] = lambda _: None) -> PatternLibrary:
    """Encode every eligible stride window of one window length, oldest end date first.

    Each end date is read through its own as-of view, so a library row is built from the rows
    the market had produced by ``d`` and from nothing later - the property the truncation audit
    re-checks rather than assumes.
    """
    horizons = tuple(sorted({int(h) for h in horizons}))
    stride = universe.library_end_indices(rules, 0, last_library_end_idx(eval_end_idx, window, horizons))
    ends: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    prices: list[np.ndarray] = []
    valid_rows: dict[int, list[np.ndarray]] = {h: [] for h in horizons}
    exclusions = {LibraryExclusion.NOT_ELIGIBLE.value: 0}
    by_end: dict[int, int] = {}

    for end in stride:
        eligible = eligibility(end)
        column = np.nonzero(eligible.eligible)[0].astype(np.int64)
        exclusions[LibraryExclusion.NOT_ELIGIBLE.value] += int((~eligible.eligible).sum())
        by_end[end] = int(column.size)
        if column.size == 0:
            continue
        view = history.panel_view(end)
        prices.append(encoder.gather(view.price, np.full(column.size, end), column, window,
                                     as_of_idx=end))
        ends.append(np.full(column.size, end, dtype=np.int32))
        cols.append(column.astype(np.int32))
        as_of = history.snapshot_as_of(end)
        table = history.figi.get(as_of, {}) if as_of is not None else {}
        names = history.tickers
        codes.append(np.array([figi.code(table.get(names[c])) for c in column], dtype=np.int32))
        for horizon in horizons:
            valid_rows[horizon].append(validity[horizon][end, column])

    if not ends:
        raise HardFail("F4", f"no library window exists for W={window}")
    end_idx = np.concatenate(ends)
    ticker_col = np.concatenate(cols)
    pit.assert_library_index(end_idx, ticker_col, stride=rules.library_stride,
                             minimum=rules.seasoning_sessions)
    window_prices = np.concatenate(prices, axis=0)

    vectors: dict[str, np.ndarray] = {}
    defined: dict[str, np.ndarray] = {}
    sq_norm: dict[str, np.ndarray] = {}
    for representation in REPRESENTATIONS:
        encoded = encoder.encode(representation, window_prices)
        vectors[representation] = encoded.vectors
        defined[representation] = encoded.defined
        sq_norm[representation] = np.einsum("ij,ij->i", encoded.vectors, encoded.vectors)
        exclusions[f"{LibraryExclusion.VECTOR_UNDEFINED.value}_{representation}"] = \
            int((~encoded.defined).sum())
    del window_prices

    valid = {h: np.concatenate(valid_rows[h]) for h in horizons}
    for horizon in horizons:
        exclusions[f"{LibraryExclusion.LABEL_INVALID.value}_H{horizon}"] = int((~valid[horizon]).sum())
    log(f"library W{window}: {end_idx.size:,} windows over {len(stride)} stride dates "
        f"(end {stride[0]}..{stride[-1]})")
    return PatternLibrary(window, horizons, end_idx, ticker_col, np.concatenate(codes), defined,
                          valid, vectors, sq_norm, tuple(stride), exclusions, by_end)
