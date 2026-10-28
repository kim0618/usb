"""Memory-lean loader of the frozen V1 / V2 / V2.1 code for the Strategy I paper (same rules, same numbers).

The frozen V1 module builds its price panel by collecting every grouped-daily row into a Python list
(``rows``, ~2.7 M tuples) and a long DataFrame (``px``, ~330 MB with string columns) before pivoting.
Both stay alive as module globals, and the import peaks at ~1.06 GB. The rules never read them: they
read the pivoted ``C`` and ``DV = C * V``.

This loader executes the frozen statements themselves, taken from the sha-checked files by line
number (``ast``), in a fresh module namespace. V1 lines 28-40 (``rows`` ... ``DV``) are replaced
with a streaming fill of the same matrices: same sessions (files with at least one kept row, sorted),
same columns (pivot order = sorted tickers), same float64 values, NaN where a ticker has no row.
CAf is evaluated per requested ticker with the same pandas ffill, and freed JSON/history buffers
are returned to the allocator before derived matrices. Everything else (R1, ``ranked``/``get_rank``,
V2's continuity table code, V2.1 ``simulate21``) is the frozen text, executed unchanged.
Equality with the frozen import is asserted in the tests (matrices, ranks, trades/NAV bytes, paper events).
"""

from __future__ import annotations

import ast
import collections
import gzip
import hashlib
import json
import os
import types
from pathlib import Path

import numpy as np
import pandas as pd

from . import view as V

# statement line ranges of each frozen file that are executed verbatim (inclusive, by first line)
V1_KEEP = [(7, 9), (12, 20), (26, 27), (41, 44), (54, 56), (63, 63), (65, 93)]
V1_REPLACED = (28, 40)
#: V1 lines 21-25 (``snaps``) are replaced by ``_snaps``: the same dict of dicts, keeping only the row
#: fields any I code path reads (V1 ``ranked``: primary_exchange; V2 TRANS, V2.1 SIC diagnostics and the
#: overlay: cik). Both keys are always present (value None when absent in the file), so a row dict is
#: never empty and ``snap.get(tk) or <fallback>`` takes the same branch as with the full row.
V1_SNAPS_REPLACED = (21, 25)
V1_CAF_REPLACED = (54, 54)
V1_R1_REPLACED = (55, 55)
SNAP_FIELDS = ("primary_exchange", "cik")
#: sha256 of the exact source lines each replacement stands for; a different frozen file is refused
REPLACED_SHA = {(21, 25): "a4ea8ac709a49d416cde9d96b5632f03325e27ee73f1c13f294c5380bd8e19fb",
                (28, 40): "7a2a4ea702eba94aeccff795c509fa5b3ff660589d964d30ce503d299e44e86c",
                (45, 53): "0f806d13fb08fa3fde35bf65cf89acaef6c2e5f36cdb5745602e6cebde9b716b",
                (54, 54): "ccf06151462510f3aa29bd1705dd5df7f3170007d844d71f247414948c3dfb2c",
                (55, 55): "9ddd09afb1482e28ed350bc9f1bccfb20d6fc25d904b3fe3a36a27846f229546"}
V2_KEEP = [(7, 9), (18, 21), (24, 44)]
V21_KEEP = [(8, 10), (19, 23), (25, 171)]
# sha256 of the exact source lines replaced (V1 28-40), so a different file can never be patched
V1_REPLACED_SHA = None   # filled at first use from the hash-pinned file and recorded in the manifest


def _statements(path: Path, keep: list[tuple[int, int]]) -> ast.Module:
    tree = ast.parse(path.read_text())
    body = [n for n in tree.body if any(a <= n.lineno <= b for a, b in keep)]
    for n in body:   # a kept statement must lie entirely inside its range
        a, b = next((a, b) for a, b in keep if a <= n.lineno <= b)
        assert n.end_lineno <= b, (path.name, n.lineno, n.end_lineno)
    return ast.Module(body=body, type_ignores=[])


def _check_code() -> dict[str, str]:
    V.verify_package()
    for name, h in V.CODE_SHA.items():
        if V.sha_file(V.RESEARCH / name) != h:
            raise V.ViewError("frozen research code changed: " + name)
    lines = (V.RESEARCH / "implementation_census_v1.py").read_text().splitlines()
    for (a, b), h in REPLACED_SHA.items():
        if hashlib.sha256("\n".join(lines[a - 1:b]).encode()).hexdigest() != h:
            raise V.ViewError("replaced V1 lines %d-%d changed" % (a, b))
    return {"v1_replaced_lines_sha256": hashlib.sha256("\n".join(lines[V1_REPLACED[0] - 1:V1_REPLACED[1]]).encode()).hexdigest(),
            "v1_replaced_ranges": sorted("%d-%d" % k for k in REPLACED_SHA)}


#: sessions kept before the first paper signal: the V1 ranking reads back to index i - 252 (CA.iloc[i - 252]);
#: 8 more as margin. Sessions before that cannot reach any paper decision (asserted by event equality tests).
LOOKBACK_KEEP = 260


#: first DV row any accepted signal reads: the engine refuses a signal at position < 252, and V1 ``ranked``
#: reads ``DV.iloc[i - 19:i + 1]`` (the 20-session median ADV), so rows before 252 - 19 are never read.
DV_FIRST_POS = 252 - 19


class WindowFrame:
    """``DV`` holding only rows from position ``offset`` on. ``.iloc[a:b]`` returns the same DataFrame slice
    (same labels, same values) as the full frame; a slice that starts before ``offset`` is refused."""

    def __init__(self, frame: pd.DataFrame, offset: int):
        self.frame, self.offset = frame, offset
        self.columns = frame.columns
        self.iloc = self

    def __getitem__(self, key):
        if not isinstance(key, slice) or key.step is not None or key.start is None or key.start < self.offset:
            raise V.ViewError("DV row %r outside the kept window (from %d)" % (key, self.offset))
        stop = None if key.stop is None else key.stop - self.offset
        return self.frame.iloc[key.start - self.offset:stop]


def _snaps(ns: dict) -> None:
    """Replacement of V1 lines 21-25: same files in the same order, same keys, rows cut to SNAP_FIELDS."""
    import glob as _glob
    RAW, RT = ns["RAW"], ns["RT"]
    snaps = {}
    for p in sorted(_glob.glob(RAW + "/tickers/CS_*.json.gz") + _glob.glob(RT + "/research_universe_u1/reference/tickers/CS_*.json.gz")):
        j = json.loads(gzip.open(p).read())
        rs = j["results"] if "results" in j else [r for pg in j["pages"] for r in pg["results"]]
        snaps[j.get("as_of") or ns["os"].path.basename(p)[3:13]] = {r["ticker"]: {f: r.get(f) for f in SNAP_FIELDS} for r in rs}
        del j, rs
    ns["snaps"] = snaps


class SplitFactors:
    """Stands for V1's dense ``adj`` (S x tickers, 1.0 except before a split): only split tickers hold an
    array; ``adj[tk]`` gives the same float64 Series (index S, name tk), cached per ticker. The I code reads
    ``adj`` only as ``adj[tk].iloc[i]`` / ``adj[tk].loc[s]`` (V2.1, engine, overlay); ``dense()`` is for tests."""

    def __init__(self, index: pd.Index, columns: pd.Index, factors: dict[str, np.ndarray]):
        self.index, self.columns, self._f, self._cache = index, columns, factors, {}
        self._colset = set(columns)

    def __getitem__(self, tk: str) -> pd.Series:
        s = self._cache.get(tk)
        if s is None:
            if tk not in self._colset:
                raise KeyError(tk)
            arr = self._f.get(tk)
            s = self._cache[tk] = pd.Series(arr if arr is not None else np.ones(len(self.index)), index=self.index, name=tk)
        return s

    def dense(self) -> pd.DataFrame:
        out = pd.DataFrame(1.0, index=self.index, columns=self.columns)
        for tk, arr in self._f.items(): out[tk] = arr
        return out


class ForwardFilledColumns:
    """Frozen CA.ffill(), evaluated only for columns the portfolio actually reads.

    Every runtime read is CAf[tk].iloc[i]. The source CA is immutable after load;
    pandas Series.ffill uses the same float64 fill as DataFrame.ffill. dense() is
    reserved for parity tests and never used by the paper runtime.
    """

    def __init__(self, source: pd.DataFrame):
        self.source = source
        self.index, self.columns = source.index, source.columns
        self._cache: dict[str, pd.Series] = {}

    def __getitem__(self, ticker: str) -> pd.Series:
        if ticker not in self._cache:
            self._cache[ticker] = self.source[ticker].ffill()
        return self._cache[ticker]

    def dense(self) -> pd.DataFrame:
        src = self.source.dense() if hasattr(self.source, "dense") else self.source
        return src.ffill()


def _positions(columns: pd.Index, labels) -> np.ndarray:
    j = columns.get_indexer(labels)
    if (j < 0).any():
        raise KeyError([t for t, x in zip(labels, j) if x < 0])
    return j


class SplitAdjusted:
    """Stands for V1 ``CA = C * adj`` without a second full matrix: CA differs from C only in split columns
    (every other column is C times 1.0, the same float64). The I code reads CA only as ``CA[tk]`` (V2.1
    ``rebase``, ``CAf``) and ``CA.iloc[k][cols]`` (V1 ``ranked``); split values are computed as in the dense
    product (``C[:, j] * factor``). Any other access is refused. ``dense()`` is for tests."""

    def __init__(self, C: pd.DataFrame, factors: dict[str, np.ndarray]):
        self.C, self._f = C, factors
        self.index, self.columns = C.index, C.columns
        self._vals = C.to_numpy()                                      # a view of C's buffer, never written
        self._split = [(int(j), factors[tk]) for j, tk in zip(_positions(C.columns, list(factors)), factors)]
        self._cache: dict[str, pd.Series] = {}
        self.iloc = _RowAccess(self)

    def __getitem__(self, tk: str) -> pd.Series:
        if not isinstance(tk, str):
            raise V.ViewError("CA[%r]: only a single column is supported" % (tk,))
        s = self._cache.get(tk)
        if s is None:
            col = self.C[tk]
            f = self._f.get(tk)
            s = self._cache[tk] = col if f is None else pd.Series(col.to_numpy() * f, index=self.index, name=tk)
        return s

    def block(self, a: int, b: int, cols: np.ndarray) -> np.ndarray:
        """CA values of rows a..b-1 and column positions ``cols`` (a new array)."""
        x = self._vals[a:b][:, cols]
        where = {j: k for k, j in enumerate(cols)}
        for j, f in self._split:
            k = where.get(j)
            if k is not None: x[:, k] = x[:, k] * f[a:b]
        return x

    def dense(self) -> pd.DataFrame:
        return pd.DataFrame(self.block(0, len(self.index), np.arange(len(self.columns))), index=self.index, columns=self.columns)


class _RowAccess:
    """``CA.iloc[k]``: row k of CA as the Series the dense frame returns (index = columns, name = session)."""

    def __init__(self, ca: SplitAdjusted):
        self.ca = ca

    def __getitem__(self, k):
        n = len(self.ca.index)
        if isinstance(k, (bool, np.bool_)) or not isinstance(k, (int, np.integer)) or not 0 <= k < n:
            raise V.ViewError("CA.iloc[%r]: only a row number in [0, %d) is supported" % (k, n))
        row = self.ca._vals[k].copy()
        for j, f in self.ca._split: row[j] = row[j] * f[k]
        return pd.Series(row, index=self.ca.columns, name=self.ca.index[k])


class LazyReturns:
    """Stands for V1 line 55 ``R1 = CA / CA.shift(1) - 1`` without the matrix: the I code reads R1 only as
    ``R1.iloc[a:b][cols]`` (V1 ``ranked``), so exactly those rows and columns are computed, with the same
    float64 ops (row k / row k - 1, then - 1; row 0 divides by the NaN fill of ``shift``). Any other access
    is refused. ``dense()`` is for tests."""

    def __init__(self, ca: SplitAdjusted):
        self.ca = ca
        self.index, self.columns = ca.index, ca.columns
        self.iloc = self

    def __getitem__(self, key):
        n = len(self.index)
        if (not isinstance(key, slice) or key.step is not None or key.start is None or key.stop is None
                or not 0 <= key.start <= key.stop <= n):
            raise V.ViewError("R1.iloc[%r]: only a row slice inside [0, %d] is supported" % (key, n))
        return _ReturnRows(self, key.start, key.stop)

    def values(self, a: int, b: int, cols: np.ndarray) -> np.ndarray:
        lo = max(a - 1, 0)
        x = self.ca.block(lo, b, cols)
        r = np.empty((b - a, len(cols)))
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            if a == 0 and b > 0:
                np.divide(x[:1], np.nan, out=r[:1])
                np.divide(x[1:], x[:-1], out=r[1:])
            else:
                np.divide(x[1:], x[:-1], out=r)
            np.subtract(r, 1, out=r)
        return r

    def dense(self) -> pd.DataFrame:
        return pd.DataFrame(self.values(0, len(self.index), np.arange(len(self.columns))), index=self.index, columns=self.columns)


class _ReturnRows:
    def __init__(self, r1: LazyReturns, a: int, b: int):
        self.r1, self.a, self.b = r1, a, b

    def __getitem__(self, cols):
        if not isinstance(cols, list):
            raise V.ViewError("R1.iloc[a:b][%r]: only a list of columns is supported" % type(cols).__name__)
        j = _positions(self.r1.columns, cols)
        return pd.DataFrame(self.r1.values(self.a, self.b, j), index=self.r1.index[self.a:self.b], columns=self.r1.columns.take(j))


def _adj(ns: dict) -> None:
    """Replacement of V1 lines 45-53: the same split loop (same order, same in-place products on a 1.0
    column), kept per split ticker; ``CA = C * adj`` as ``SplitAdjusted``: C with only the split columns
    multiplied (x * 1.0 == x for every float64, NaN stays NaN)."""
    S, C, sp = ns["S"], ns["C"], ns["sp"]
    cols = set(C.columns)
    factors: dict[str, np.ndarray] = {}
    split_pos = collections.defaultdict(list)
    for r in sp.itertuples():
        if r.ticker not in cols or r.split_to == 0: continue
        pre = S < r.execution_date
        if pre.all() or not pre.any(): continue
        f = factors.get(r.ticker)
        if f is None: f = factors[r.ticker] = np.ones(len(S))
        f[pre] *= r.split_from / r.split_to
        split_pos[r.ticker].append(int(np.argmax(~pre)))
    ns["adj"] = SplitFactors(C.index, C.columns, factors)
    ns["split_pos"] = split_pos
    ns["CA"] = SplitAdjusted(C, factors)


def _read_grouped(path: str, sha: dict[str, str]) -> dict:
    """``json.loads(gzip.open(path).read())`` (same bytes). The file's sha256 is recorded in ``sha`` (by
    name, for the manifest ``grouped_hash``) and its pages are dropped from the page cache: the panel is
    the last reader of a grouped file in a run, so this costs no extra IO."""
    with open(path, "rb") as fh:
        raw = fh.read()
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    sha[os.path.basename(path)] = hashlib.sha256(raw).hexdigest()
    return json.loads(gzip.decompress(raw))


def _panel(ns: dict, start_session: str | None = None) -> None:
    """Streaming replacement of V1 lines 28-40: S, C, V -> DV, without rows / px. ``start_session`` drops
    stored sessions before it (all tickers kept).

    Values go straight into the final buffers (no per-file staging): columns are positions in the sorted
    ticker universe ``all_cs | BENCH`` (every kept row's ticker is in it), rows are sessions in file order.
    Sessions must arrive in increasing order (grouped files are named by session); a session seen before
    reuses its row, so a duplicate (session, ticker) is still refused. Unused universe columns are
    compacted away in place at the end; rows never used are never touched (not resident)."""
    RAW, all_cs, BENCH = ns["RAW"], ns["all_cs"], ns["BENCH"]
    import glob as _glob
    files = sorted(_glob.glob(RAW + "/grouped/*.json.gz"))          # V1 L29 order
    uni = sorted(set(all_cs) | set(BENCH))
    upos = {t: k for k, t in enumerate(uni)}
    Cb = np.empty((len(files), len(uni)))
    # Only rows a ranking can read get DV (see DV_FIRST_POS): with sessions in file order the row number is
    # final when it is assigned, and fewer than DV_FIRST_POS sessions leave DV empty (as off = len(S)).
    DVb = np.empty((max(0, len(files) - DV_FIRST_POS), len(uni)))
    seen = np.zeros((len(files), len(uni)), dtype=bool)
    used = np.zeros(len(uni), dtype=bool)
    sessions: list[str] = []
    s_row: dict[str, int] = {}
    sha = ns["grouped_sha256"] = {}
    for p in files:
        j = _read_grouped(p, sha); b = j.get("body") or {}
        rs = b.get("results") if isinstance(b, dict) else None
        if not rs: continue
        early = bool(start_session) and j["session"] < start_session
        t_idx, c, v = [], [], []
        for r in rs:
            if r["T"] in all_cs or r["T"] in BENCH:
                # every ticker of the full store keeps its column (no ticker is dropped); only the values of
                # sessions before start_session are not kept
                t_idx.append(upos[r["T"]]); c.append(r["c"]); v.append(r["v"])
        if not t_idx: continue
        cols = np.asarray(t_idx, dtype=np.int64)
        used[cols] = True
        if early: continue
        s = j["session"]; i = s_row.get(s)
        if i is None:
            if sessions and s < sessions[-1]:
                raise V.ViewError("grouped session %s after %s: files out of session order" % (s, sessions[-1]))
            i = s_row[s] = len(sessions); sessions.append(s)
            Cb[i] = np.nan
            if i >= DV_FIRST_POS: DVb[i - DV_FIRST_POS] = np.nan
        if seen[i, cols].any() or len(np.unique(cols)) != len(cols):
            raise V.ViewError("duplicate (session, ticker) row: the frozen pivot would refuse it")
        seen[i, cols] = True
        c = np.asarray(c, dtype=np.float64)
        Cb[i, cols] = c
        if i >= DV_FIRST_POS:
            DVb[i - DV_FIRST_POS, cols] = c * np.asarray(v, dtype=np.float64)
    del seen
    n = len(sessions); off = min(DV_FIRST_POS, n)
    keep = np.flatnonzero(used); k = len(keep)
    if k < len(uni):                      # in-place column compaction (keep is increasing: row[:k] never overruns)
        for i in range(n): Cb[i, :k] = Cb[i, keep]
        for i in range(n - off): DVb[i, :k] = DVb[i, keep]
    names = [uni[x] for x in keep]                                    # pivot column order = sorted tickers
    cols = pd.Index(names, name="T")
    ns["S"] = pd.Index(sessions)                                       # V1 L37: sorted(set(px.s))
    ns["C"] = pd.DataFrame(Cb[:n, :k], index=ns["S"], columns=cols)  # == pivot(...).reindex(S): same index values
    ns["DV"] = WindowFrame(pd.DataFrame(DVb[:n - off, :k], index=ns["S"][off:], columns=cols), off)


def _returns(ns: dict) -> None:
    """Replacement of V1 line 55 ``R1 = CA / CA.shift(1) - 1`` (see ``LazyReturns``)."""
    ns["R1"] = LazyReturns(ns["CA"])


def start_for(view: V.View, first_signal: str | None) -> str | None:
    """First session kept: LOOKBACK_KEEP sessions before the first paper signal (None = keep everything)."""
    if first_signal is None: return None
    ss = view.sessions
    k = next((j for j, s in enumerate(ss) if s >= first_signal), len(ss))
    return ss[max(0, k - LOOKBACK_KEEP)]


def _release_heap() -> None:
    """Return freed history/JSON buffers before building derived matrices (glibc when available).

    This is an allocation boundary only: no live object, value or calculation changes.
    Platforms without malloc_trim keep the ordinary Python allocator behavior.
    """
    import ctypes
    import gc
    gc.collect()
    try:
        trim = ctypes.CDLL(None).malloc_trim
    except (AttributeError, OSError):
        return
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    trim(0)


def load(view: V.View, start_session: str | None = None) -> types.ModuleType:
    """Module object with the same attributes the engine reads from the frozen V2.1 import."""
    man = _check_code()
    for name, h in V.D17_SHA.items():
        if V.sha_file(V.D17_FUND.parent / name) != h:
            raise V.ViewError("D17-R import input changed: " + name)
    import collections, glob, os, sys
    m1 = types.ModuleType("i_paper_lean_v1")
    ns = m1.__dict__
    ns.update(gzip=gzip, json=json, glob=glob, os=os, sys=sys, hashlib=hashlib, collections=collections, np=np, pd=pd,
              HERE=str(V.RESEARCH), RT=str(view.root), SICP=str(V.D17_FUND).replace(".parquet", "_sic.parquet"))
    v1 = V.RESEARCH / "implementation_census_v1.py"
    exec(compile(_statements(v1, V1_KEEP[0:2]), str(v1), "exec"), ns)  # contract, constants
    _snaps(ns)
    exec(compile(_statements(v1, [V1_KEEP[2]]), str(v1), "exec"), ns)   # snap_dates, all_cs
    _panel(ns, start_session)
    _release_heap()
    exec(compile(_statements(v1, [V1_KEEP[3]]), str(v1), "exec"), ns)  # splits table sp
    _adj(ns)
    ns["CAf"] = ForwardFilledColumns(ns["CA"])
    _returns(ns)
    exec(compile(_statements(v1, [(56, 56)] + V1_KEEP[5:]), str(v1), "exec"), ns)  # pos, SIC, ranks verbatim
    ns["signals"], ns["execs"] = [], []          # V1 L57-62 (contract-window schedule) are not run; the engine sets its own
    m2 = types.ModuleType("i_paper_lean_v2"); n2 = m2.__dict__
    n2.update(json=json, hashlib=hashlib, collections=collections, np=np, pd=pd, HERE=str(V.RESEARCH), m1=m1)
    exec(compile(_statements(V.RESEARCH / "integrity_census_v2.py", V2_KEEP), "integrity_census_v2.py", "exec"), n2)
    m21 = types.ModuleType("i_paper_lean_v21"); n21 = m21.__dict__
    n21.update(json=json, hashlib=hashlib, collections=collections, np=np, pd=pd, HERE=str(V.RESEARCH), m2=m2)
    exec(compile(_statements(V.V21_PATH, V21_KEEP), str(V.V21_PATH), "exec"), n21)
    n21["_lean_manifest"] = dict(man, start_session=start_session, sessions=len(ns["S"]), columns=ns["C"].shape[1])
    return m21
