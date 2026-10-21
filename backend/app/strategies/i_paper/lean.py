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
import gzip
import hashlib
import json
import types
from pathlib import Path

import numpy as np
import pandas as pd

from . import view as V

# statement line ranges of each frozen file that are executed verbatim (inclusive, by first line)
V1_KEEP = [(7, 9), (12, 27), (41, 56), (63, 63), (65, 93)]
V1_REPLACED = (28, 40)
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
    return {"v1_replaced_lines_sha256": hashlib.sha256("\n".join(lines[V1_REPLACED[0] - 1:V1_REPLACED[1]]).encode()).hexdigest()}


#: sessions kept before the first paper signal: the V1 ranking reads back to index i - 252 (CA.iloc[i - 252]);
#: 8 more as margin. Sessions before that cannot reach any paper decision (asserted by event equality tests).
LOOKBACK_KEEP = 260


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
    Vdf = pd.DataFrame(Vm, index=ns["S"], columns=cols)
    ns["DV"] = ns["C"] * Vdf                                          # V1 L40
    del Vdf, Vm


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
    head = _statements(v1, [V1_KEEP[0], V1_KEEP[1]])
    exec(compile(head, str(v1), "exec"), ns)                           # contract, constants, snapshots, all_cs
    _panel(ns, start_session)
    tail = _statements(v1, V1_KEEP[2:])
    exec(compile(tail, str(v1), "exec"), ns)                           # splits/adj/CA/CAf/R1/pos, SIC, ranked/get_rank
    ns["signals"], ns["execs"] = [], []          # V1 L57-62 (contract-window schedule) are not run; the engine sets its own
    m2 = types.ModuleType("i_paper_lean_v2"); n2 = m2.__dict__
    n2.update(json=json, hashlib=hashlib, collections=collections, np=np, pd=pd, HERE=str(V.RESEARCH), m1=m1)
    exec(compile(_statements(V.RESEARCH / "integrity_census_v2.py", V2_KEEP), "integrity_census_v2.py", "exec"), n2)
    m21 = types.ModuleType("i_paper_lean_v21"); n21 = m21.__dict__
    n21.update(json=json, hashlib=hashlib, collections=collections, np=np, pd=pd, HERE=str(V.RESEARCH), m2=m2)
    exec(compile(_statements(V.V21_PATH, V21_KEEP), str(V.V21_PATH), "exec"), n21)
    n21["_lean_manifest"] = dict(man, start_session=start_session, sessions=len(ns["S"]), columns=ns["C"].shape[1])
    return m21
