"""Paper-only point-in-time ticker continuity (contract US-B-I-PAPER-CONTINUITY-PIT-V1, this docstring).

Why: V2's ``TRANS`` links a stopped ticker to a new one through the union of *all* reference snapshots
(including ones dated after the transition) and decides uniqueness after the fact. In the frozen
baseline BREA -> SLMT (2025-10-03) is only provable from ``CS_2026-01-02``. The paper book may not do
that. The frozen V2.1 code stays unchanged; only the ``TRANS`` table it reads is replaced by this one.

Availability. A reference snapshot (``strategy-c-selection-raw-v1`` CS document) carries an ``as_of``
date but no fetch time, and the stored history was back-filled in one sitting. So a snapshot is
*available* on ``avail = max(as_of, first_seen)``, where ``first_seen`` is the ET calendar day the paper
store first registered the file (``registry``; append-only, sha-pinned). A back-filled file therefore
never changes a decision dated before it was seen.

Ranking. The frozen V1 ranking picks the newest snapshot with ``as_of <= signal``. The paper restricts
the candidates to snapshots also *available* by the signal day; within that set the V1 rule is
unchanged (``ranking_snap_dates``).

Link rule. A held-or-not ticker ``old`` with last close ``lo`` (no close since) is linked to ``new`` at
session ``a`` only if every item holds:

1. ``cik_old``: the CIK of ``old`` in the newest snapshot that contains it with ``as_of <= lo`` and
   ``avail <= lo`` (identity as known while it traded);
2. an evidence snapshot ``E`` with ``as_of > lo`` and ``avail <= a`` in which ``old`` is absent and
   exactly one ticker ``new != old`` carries ``cik_old``, ``new`` is in the price panel, and its first
   close ``fn`` satisfies ``lo < fn`` and at most 5 stored sessions lie strictly between (V2 window);
3. ``a`` = first session ``>= max(fn, avail(E))`` with a valid close of ``new`` and no close of ``old``
   in ``(lo, a]``; ``a <= as-of``.

Evidence snapshots are taken in ``(avail, as_of, name)`` order; the first one that decides (one
candidate, or more than one) is final. More than one candidate -> ``AMBIGUOUS``; none ->
``NO_PIT_EVIDENCE``. Anything unlinked follows the V1 price-stop rule inside the unchanged V2.1 code
(valued at last close, sold at it when it leaves the hold zone) and is reported, with the frozen V2
link where one exists, as a recorded difference to V2.1.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_ID = "US-B-I-PAPER-CONTINUITY-PIT-V1"
WINDOW_BETWEEN = 5          # V2: pos[fn] - pos[lo] - 1 <= 5


@dataclass(frozen=True)
class Snapshot:
    name: str
    as_of: str
    avail: str
    rows: dict[str, str]                 # ticker -> 10-digit cik ('' when absent)
    by_cik: dict[str, tuple[str, ...]]


def read_snapshot(path: Path, avail: str) -> Snapshot:
    j = json.loads(gzip.open(path).read())
    rs = j["results"] if isinstance(j.get("results"), list) else [r for pg in j["pages"] for r in pg["results"]]
    rows = {}
    for r in rs:
        c = str(r.get("cik") or "").strip()
        rows[r["ticker"]] = c.zfill(10) if c.isdigit() else ""
    by: dict[str, list[str]] = {}
    for t, c in rows.items():
        if c: by.setdefault(c, []).append(t)
    as_of = j.get("as_of") or path.name[3:13]
    return Snapshot(path.name, as_of, max(as_of, avail), rows, {c: tuple(sorted(v)) for c, v in by.items()})


def ranking_snap_dates(snaps: list[Snapshot], signal: str) -> list[str]:
    """V1 snapshot keys usable for a signal: as_of <= signal and available by the signal day."""
    return sorted({s.as_of for s in snaps if s.as_of <= signal and s.avail <= signal})


def build(C, S: list[str], snaps: list[Snapshot]) -> tuple[dict[str, tuple], dict[str, dict[str, Any]]]:
    """Returns (TRANS for the frozen simulate21: old -> (new, lo, a, ratio), decisions per stopped ticker)."""
    pos = {s: i for i, s in enumerate(S)}
    last = S[-1]
    lv = {tk: C[tk].last_valid_index() for tk in C.columns}
    fv = {tk: C[tk].first_valid_index() for tk in C.columns}
    ordered = sorted(snaps, key=lambda s: (s.avail, s.as_of, s.name))
    trans, dec = {}, {}
    for old in sorted(C.columns):
        lo = lv[old]
        if lo is None or lo == last:
            continue
        ident = [s for s in ordered if s.as_of <= lo and s.avail <= lo and s.rows.get(old)]
        if not ident:
            dec[old] = {"lo": lo, "status": "UNRESOLVED", "reason": "NO_PIT_IDENTITY"}; continue
        cik = max(ident, key=lambda s: (s.as_of, s.avail, s.name)).rows[old]
        d = {"lo": lo, "cik": cik, "status": "UNRESOLVED", "reason": "NO_PIT_EVIDENCE"}
        for E in ordered:
            if not (E.as_of > lo) or old in E.rows:
                continue
            cands = []
            for t in E.by_cik.get(cik, ()):
                if t == old or t not in fv or fv[t] is None: continue
                if fv[t] > lo and pos[fv[t]] - pos[lo] - 1 <= WINDOW_BETWEEN:
                    cands.append(t)
            if not cands:
                continue
            if len(cands) > 1:
                d.update(reason="AMBIGUOUS", candidates=sorted(cands), evidence=E.name, evidence_avail=E.avail); break
            new = cands[0]
            start = max(fv[new], E.avail)
            a = next((s for s in S[pos[fv[new]]:] if s >= start and C[new].loc[s] == C[new].loc[s]), None)
            if a is None:
                d.update(reason="EVIDENCE_AFTER_AS_OF", candidate=new, evidence=E.name); break
            ratio = round(float(C[new].loc[fv[new]] / C[old].loc[lo]), 4)
            trans[old] = (new, lo, a, ratio)
            d = {"lo": lo, "cik": cik, "status": "LINKED", "new": new, "fn": fv[new], "a": a, "evidence": E.name, "evidence_avail": E.avail}
            break
        dec[old] = d
    return trans, dec


# -------------------------------------------------------------------------------------------- registry
def update_registry(path: Path, files: list[Path], today: str) -> dict[str, dict[str, str]]:
    """first_seen registry: name -> {sha256, first_seen}. A registered file whose bytes change is refused."""
    reg = json.loads(path.read_text()) if path.exists() else {}
    changed = False
    for p in sorted(files):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        r = reg.get(p.name)
        if r is None:
            reg[p.name] = {"sha256": h, "first_seen": today}; changed = True
        elif r["sha256"] != h:
            raise RuntimeError("reference snapshot %s changed after registration" % p.name)
    if changed:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n"); tmp.replace(path)
    return reg
