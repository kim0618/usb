"""The Historical Structure Library: every stride window a query may be compared against.

One dense ``(L, 10)`` matrix plus parallel metadata columns, not one object per row. Rows are
ordered by ``(end_idx, ticker)`` and that ordering *is* the declared tie-break: with the library
in that order, a stable sort on the score alone applies "distance ascending, then library end
session ascending, then ticker ascending" in one pass, which is why nothing downstream has to
re-sort or remember a secondary key.

The expanding library of the declaration is this same matrix read as a prefix: a query's
candidates are the rows up to ``searchsorted(end_idx, D - L - h)``, so no per-query mask over
the whole library is ever built and a truncated re-run sees the identical slice.

No forward return is stored here. ``label_valid`` is a mask, and it is the only thing about a
neighbour's future this phase is allowed to know.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from app.backtest.strategy_d_analog import pit as v1_pit
from app.backtest.strategy_d_analog import universe
from app.backtest.strategy_d_analog.source import DailyHistory
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, V2ARules
from app.backtest.strategy_d_v2.models import HardFail, LibraryExclusion
from app.backtest.strategy_d_v2.structure_features import StructureFeatures

LIBRARY_CONTRACT = "d-v2a-structure-library-v1"


class FigiCoder:
    """Interns composite FIGI strings as int32 codes; ``-1`` means the snapshot carries none.

    The declared FIGI rule compares codes only when both sides have one, so the null code must
    never equal itself in a comparison - an int the equality test skips is the cheapest way to
    say so. (V1's coder is identical; it is reimplemented here because V1's ``library`` module
    also imports the raw-path encoder this study must not load.)
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


@dataclass(frozen=True)
class StructureLibrary:
    """One horizon's library: vectors, identity columns and the counters behind them."""

    horizon: int
    lookback: int
    end_idx: np.ndarray       # (L,) int32, ascending
    ticker_col: np.ndarray    # (L,) int32, ascending inside an end index
    figi_code: np.ndarray     # (L,) int32, -1 where the snapshot has no composite FIGI
    vectors: np.ndarray       # (L, 10) float64 rank coordinates
    sq_norm: np.ndarray       # (L,) float64, precomputed for the distance expansion
    stride_end_indices: tuple[int, ...]
    rows_by_end: dict[int, int]
    exclusions: dict[str, int]
    figi_strings: tuple[str, ...] = ()
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __len__(self) -> int:
        return int(self.end_idx.shape[0])

    def prefix(self, limit_idx: int) -> int:
        """How many rows a query with this embargo limit may see."""
        return int(np.searchsorted(self.end_idx, limit_idx, side="right"))

    def manifest(self) -> dict:
        return {"contract": LIBRARY_CONTRACT, "horizon": self.horizon, "lookback": self.lookback,
                "rows": len(self), "dimension": int(self.vectors.shape[1]),
                "stride_dates": len(self.stride_end_indices),
                "first_end_idx": self.stride_end_indices[0] if self.stride_end_indices else None,
                "last_end_idx": self.stride_end_indices[-1] if self.stride_end_indices else None,
                "exclusions": dict(self.exclusions),
                "distinct_figi": len(self.figi_strings),
                "figi_null_rows": int((self.figi_code < 0).sum()),
                "rows_by_end_idx": {str(k): v for k, v in sorted(self.rows_by_end.items())},
                "ordering": "end_idx ascending, then ticker ascending (the declared tie-break)"}


def last_library_end_idx(eval_end_idx: int, lookback: int, horizon: int) -> int:
    """The latest end index any query of the study can reach: ``eval_end - L - h``."""
    return eval_end_idx - lookback - horizon


def build(history: DailyHistory, rules: V2ARules, features: StructureFeatures,
          label_valid: np.ndarray, *, eval_end_idx: int, horizon: int,
          log: Callable[[str], None] = lambda _: None) -> StructureLibrary:
    """Assemble the library for one horizon, oldest end date first.

    A row exists when the universe rule admits it as of its own end session, its ten coordinates
    are all defined, and its label for this horizon is decidable. The three conditions are
    counted separately so the manifest says why a stride window is absent.
    """
    lookback = rules.max_lookback
    last_end = last_library_end_idx(eval_end_idx, lookback, horizon)
    stride = universe.library_end_indices(rules, 0, last_end)
    if not stride:
        raise HardFail("F4", f"no library window exists for h={horizon}, L={lookback}")

    figi = FigiCoder()
    ends: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    vectors: list[np.ndarray] = []
    exclusions = {LibraryExclusion.NOT_ELIGIBLE.value: 0,
                  LibraryExclusion.VECTOR_UNDEFINED.value: 0,
                  LibraryExclusion.LABEL_INVALID.value: 0}
    rows_by_end: dict[int, int] = {}
    names = history.tickers

    for end in stride:
        eligible_row = features.eligible[end]
        defined_row = features.defined[end]
        valid_row = label_valid[end]
        exclusions[LibraryExclusion.NOT_ELIGIBLE.value] += int((~eligible_row).sum())
        exclusions[LibraryExclusion.VECTOR_UNDEFINED.value] += int((eligible_row & ~defined_row).sum())
        exclusions[LibraryExclusion.LABEL_INVALID.value] += int(
            (eligible_row & defined_row & ~valid_row).sum())
        keep = np.nonzero(eligible_row & defined_row & valid_row)[0].astype(np.int64)
        rows_by_end[end] = int(keep.size)
        if keep.size == 0:
            continue
        session_idx = np.full(keep.size, end, dtype=np.int64)
        vectors.append(features.matrix(session_idx, keep))
        ends.append(np.full(keep.size, end, dtype=np.int32))
        cols.append(keep.astype(np.int32))
        as_of = history.snapshot_as_of(end)
        table = history.figi.get(as_of, {}) if as_of is not None else {}
        codes.append(np.array([figi.code(table.get(names[c])) for c in keep], dtype=np.int32))

    end_idx = np.concatenate(ends)
    ticker_col = np.concatenate(cols)
    v1_pit.assert_library_index(end_idx, ticker_col, stride=rules.library_stride,
                                minimum=rules.seasoning_sessions)
    matrix = np.ascontiguousarray(np.concatenate(vectors, axis=0))
    if matrix.shape[1] != len(FEATURE_NAMES):
        raise HardFail("R5", f"library vectors have {matrix.shape[1]} coordinates")
    if not np.isfinite(matrix).all():
        raise HardFail("R12", "a library vector carries a non-finite coordinate")
    if matrix.size and (matrix.min() < 0.0 or matrix.max() > 1.0):
        raise HardFail("R12", "a library coordinate is outside [0, 1]")
    sq_norm = np.einsum("ij,ij->i", matrix, matrix)
    log(f"library h={horizon}: {end_idx.size:,} rows over {len(stride)} stride dates "
        f"(end {stride[0]}..{stride[-1]})")
    return StructureLibrary(horizon, lookback, end_idx, ticker_col, np.concatenate(codes),
                            matrix, sq_norm, tuple(stride), rows_by_end, exclusions,
                            figi.strings())


def query_figi_codes(history: DailyHistory, session_idx: Sequence[int],
                     ticker_col: Sequence[int], coder_strings: Sequence[str]) -> np.ndarray:
    """Each query's composite FIGI as of its own date, coded against the library's dictionary.

    A query whose FIGI is absent from the library dictionary gets a fresh code that no library
    row carries, which is the right answer: it can equal nothing, so the FIGI rule excludes
    nothing for it. A query with no FIGI at all gets ``-1`` and the comparison is skipped.
    """
    index = {value: position for position, value in enumerate(coder_strings)}
    out = np.empty(len(session_idx), dtype=np.int32)
    spare = len(index)
    names = history.tickers
    for position, (session, column) in enumerate(zip(session_idx, ticker_col)):
        as_of = history.snapshot_as_of(int(session))
        table = history.figi.get(as_of, {}) if as_of is not None else {}
        value = table.get(names[int(column)])
        if not value:
            out[position] = FigiCoder.NULL
        elif value in index:
            out[position] = index[value]
        else:
            out[position] = spare
            index[value] = spare
            spare += 1
    return out
