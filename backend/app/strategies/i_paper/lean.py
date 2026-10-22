"""Memory-lean loader of the frozen V1 / V2 / V2.1 code for the Strategy I paper (same rules, same numbers).

The frozen V1 module builds its price panel by collecting every grouped-daily row into a Python list
(``rows``, ~2.7 M tuples) and a long DataFrame (``px``, ~330 MB with string columns) before pivoting.
Both stay alive as module globals, and the import peaks at ~1.06 GB. The rules never read them: they
read the pivoted ``C`` and ``DV = C * V``.

This loader executes the frozen statements themselves, taken from the sha-checked files by line
number (``ast``), in a fresh module namespace, and replaces only V1 lines 28-40 (``rows`` ... ``DV``)
with a streaming fill of the same matrices: same sessions (files with at least one kept row, sorted),
same columns (pivot order = sorted tickers), same float64 values, NaN where a ticker has no row.
Everything else (snapshots, split adjustment, CA/CAf/R1, ``ranked``/``get_rank``, V2's continuity table
code, V2.1 ``simulate21``) is the frozen text, executed unchanged. Equality with the frozen import is
asserted in the tests (matrices, ranks, trades/NAV bytes, paper events).
"""

from __future__ import annotations

import ast
import collections
import gzip
import hashlib
import json
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
SNAP_FIELDS = ("primary_exchange", "cik")
#: sha256 of the exact source lines each replacement stands for; a different frozen file is refused
REPLACED_SHA = {(21, 25): "a4ea8ac709a49d416cde9d96b5632f03325e27ee73f1c13f294c5380bd8e19fb",
                (28, 40): "7a2a4ea702eba94aeccff795c509fa5b3ff660589d964d30ce503d299e44e86c",
                (45, 53): "0f806d13fb08fa3fde35bf65cf89acaef6c2e5f36cdb5745602e6cebde9b716b"}
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


def _adj(ns: dict) -> None:
    """Replacement of V1 lines 45-53: the same split loop (same order, same in-place products on a 1.0
    column), kept per split ticker; ``CA = C * adj`` as C with only the split columns multiplied
    (x * 1.0 == x for every float64, NaN stays NaN)."""
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
    ca = C.to_numpy(copy=True)
    j = C.columns.get_indexer(list(factors))
    for k, tk in zip(j, factors): ca[:, k] = ca[:, k] * factors[tk]
    ns["adj"] = SplitFactors(C.index, C.columns, factors)
    ns["split_pos"] = split_pos
    ns["CA"] = pd.DataFrame(ca, index=C.index, columns=C.columns)


def _panel(ns: dict, start_session: str | None = None) -> None:
    """Streaming replacement of V1 lines 28-40: S, C, V -> DV, without rows / px. ``start_session`` drops
    stored sessions before it (all tickers kept)."""
    RAW, all_cs, BENCH = ns["RAW"], ns["all_cs"], ns["BENCH"]
    import glob as _glob
    files = sorted(_glob.glob(RAW + "/grouped/*.json.gz"))          # V1 L29 order
    col: dict[str, int] = {}
    per_file = []
    for p in files:
        j = json.loads(gzip.open(p).read()); b = j.get("body") or {}
        rs = b.get("results") if isinstance(b, dict) else None
        if not rs: continue
        early = bool(start_session) and j["session"] < start_session
        t_idx, c, v = [], [], []
        for r in rs:
            if r["T"] in all_cs or r["T"] in BENCH:
                # every ticker of the full store keeps its column (no ticker is dropped); only the values of
                # sessions before start_session are not kept
                t_idx.append(col.setdefault(r["T"], len(col))); c.append(r["c"]); v.append(r["v"])
        if t_idx and not early:
            per_file.append((j["session"], np.asarray(t_idx, dtype=np.int64), np.asarray(c, dtype=np.float64), np.asarray(v, dtype=np.float64)))
    sessions = sorted({s for s, *_ in per_file})                       # V1 L37: sorted(set(px.s))
    s_pos = {s: i for i, s in enumerate(sessions)}
    names = sorted(col)                                               # pivot column order
    remap = np.empty(len(col), dtype=np.int64)
    for new, name in enumerate(names): remap[col[name]] = new
    Cm = np.full((len(sessions), len(names)), np.nan); Vm = np.full((len(sessions), len(names)), np.nan)
    seen = np.zeros((len(sessions), len(names)), dtype=bool)
    for s, ti, c, v in per_file:
        i = s_pos[s]; cols = remap[ti]
        if seen[i, cols].any() or len(np.unique(cols)) != len(cols):
            raise V.ViewError("duplicate (session, ticker) row: the frozen pivot would refuse it")
        seen[i, cols] = True
        Cm[i, cols] = c; Vm[i, cols] = v
    del per_file, seen
    S = pd.Index(sessions, name="s")
    cols = pd.Index(names, name="T")
    ns["S"] = pd.Index(sessions)
    ns["C"] = pd.DataFrame(Cm, index=ns["S"], columns=cols)          # == pivot(...).reindex(S): same index values
    off = min(DV_FIRST_POS, len(sessions))                            # V1 L40, rows a ranking can read
    ns["DV"] = WindowFrame(pd.DataFrame(Cm[off:] * Vm[off:], index=ns["S"][off:], columns=cols), off)
    del Vm


def start_for(view: V.View, first_signal: str | None) -> str | None:
    """First session kept: LOOKBACK_KEEP sessions before the first paper signal (None = keep everything)."""
    if first_signal is None: return None
    ss = view.sessions
    k = next((j for j, s in enumerate(ss) if s >= first_signal), len(ss))
    return ss[max(0, k - LOOKBACK_KEEP)]


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
    exec(compile(_statements(v1, [V1_KEEP[3]]), str(v1), "exec"), ns)  # splits table sp
    _adj(ns)
    exec(compile(_statements(v1, V1_KEEP[4:]), str(v1), "exec"), ns)    # CAf/R1/pos, SIC, ranked/get_rank
    ns["signals"], ns["execs"] = [], []          # V1 L57-62 (contract-window schedule) are not run; the engine sets its own
    m2 = types.ModuleType("i_paper_lean_v2"); n2 = m2.__dict__
    n2.update(json=json, hashlib=hashlib, collections=collections, np=np, pd=pd, HERE=str(V.RESEARCH), m1=m1)
    exec(compile(_statements(V.RESEARCH / "integrity_census_v2.py", V2_KEEP), "integrity_census_v2.py", "exec"), n2)
    m21 = types.ModuleType("i_paper_lean_v21"); n21 = m21.__dict__
    n21.update(json=json, hashlib=hashlib, collections=collections, np=np, pd=pd, HERE=str(V.RESEARCH), m2=m2)
    exec(compile(_statements(V.V21_PATH, V21_KEEP), str(V.V21_PATH), "exec"), n21)
    n21["_lean_manifest"] = dict(man, start_session=start_session, sessions=len(ns["S"]), columns=ns["C"].shape[1])
    return m21
