"""Point-in-time view of the production stores and the frozen V2.1 module loaded on it.

The view is a directory of symlinks that exposes, under the layout the frozen V1 loader globs:

* ``strategy_c/raw/grouped/<D>.json.gz`` for every session D <= as-of (Massive grouped daily,
  written by ``usb-grouped-daily``; unadjusted closes and volumes),
* ``strategy_c/raw/tickers/CS_<A>.json.gz`` and ``research_universe_u1/reference/tickers`` for every
  reference snapshot whose as-of A <= as-of,
* ``strategy_c/raw/splits/*`` unchanged. Split records whose execution date is after the last visible
  session are dropped by the V1 loader itself (``pre.all()``), so a forward announcement never
  adjusts a past price.

Nothing after the as-of session can be read by the frozen code. Completeness is checked against the
XNYS calendar before loading: the V1 ranking indexes sessions by position (``i - 252``, ``i - 21``), so
a missing day would silently shift every window; the view is refused instead.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.collector.range import sessions_between
from app.market.calendar import MarketCalendar

REPO_ROOT = Path(__file__).resolve().parents[4]
RESEARCH = Path(__file__).resolve().parent / "frozen"
PACKAGE_MANIFEST_SHA = "4530fc7c3b057fb1a70312ab651482d6cb8418e2496371d80b522a45932e923f"
V21_PATH = RESEARCH / "cash_integrity_census_v2_1.py"
CODE_SHA = {
    "implementation_census_v1.py": "6f8cf6730f75d97296fcc659e629ad8473f35e88d99a36dd466b195fdc60b910",
    "integrity_census_v2.py": "6597cfd2bfd97e0eac1e13c6a60d41213731425f37c71819a9d25a3d03e750b1",
    "cash_integrity_census_v2_1.py": "c6b78d9cbd5f3b30523850bb2b581630ae3045952657373335950d074f43fef6",
}
V21_CANONICAL = "34d8fa7591cbafb753470c02847e00d66fe2a3f53b9e32ae17ff11464ee24e4d"
# D17-R import inputs (R1 frozen contract 3.1): placeholder fund.parquet (no output effect) and the
# content-equivalent SIC substitute that only feeds V2.1's own diagnostic columns.
D17_FUND = RESEARCH / "risk_overlay_d17r_exec/inputs/fund.parquet"
D17_SHA = {"fund.parquet": "27b5ec72d567768dd23e5024b9d97647710af333acbc9f45e8b197ecf6c3f277",
           "fund_sic.parquet": "97b0659128f375b83df57cd14824f025c5e603856e3e4f1d1aaa0c3a7f4cdbc0"}
#: lookback the V1 ranking needs before a signal: index i - 252 must exist
LOOKBACK_SESSIONS = 253


class ViewError(RuntimeError):
    """The stores cannot give a complete point-in-time view; nothing is computed."""


@dataclass(frozen=True)
class Stores:
    runtime: Path                     # data/runtime root (production layout)
    #: extra reference snapshot directories used only as continuity evidence (read-only). Default: H's
    #: daily reference store, written by H's own refresh when that runs; absent is fine.
    evidence: tuple[Path, ...] | None = None
    #: I-owned read-only grouped history (sessions the shared production store does not hold, e.g. the
    #: 2025-10-17..2026-08-10 package). Kept out of ``strategy_c/raw/grouped`` because H reads that
    #: directory and would take the extra files as its own input. A session in both must be the same bytes.
    history: tuple[Path, ...] = ()

    @property
    def evidence_dirs(self) -> list[Path]:
        if self.evidence is not None: return list(self.evidence)
        return [self.runtime / "strategy_h_v2/d7/auto/reference"]

    def snapshot_files(self) -> list[Path]:
        out = []
        for d in self.tickers + self.evidence_dirs:
            if d.is_dir(): out += sorted(d.glob("CS_*.json.gz"))
        return out

    @property
    def grouped(self) -> Path: return self.runtime / "strategy_c/raw/grouped"
    @property
    def tickers(self) -> list[Path]:
        return [self.runtime / "strategy_c/raw/tickers", self.runtime / "research_universe_u1/reference/tickers"]
    @property
    def splits(self) -> Path: return self.runtime / "strategy_c/raw/splits"


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_package() -> None:
    """Verify the pinned manifest and every frozen input before executing any code."""
    manifest = RESEARCH / "manifest.json"
    if sha_file(manifest) != PACKAGE_MANIFEST_SHA:
        raise ViewError("frozen package manifest changed")
    for entry in json.loads(manifest.read_text())["files"]:
        p = RESEARCH / entry["path"]
        if p.stat().st_size != entry["bytes"] or sha_file(p) != entry["sha256"]:
            raise ViewError("frozen package artifact changed: " + entry["path"])


def _snapshot_as_of(p: Path) -> str:
    j = json.loads(gzip.open(p).read())
    return j.get("as_of") or p.name[3:13]


@dataclass
class View:
    as_of: date
    root: Path
    sessions: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)
    ranking_files: list[Path] = field(default_factory=list)
    evidence_files: list[Path] = field(default_factory=list)


def build_view(stores: Stores, as_of: date, root: Path, calendar: MarketCalendar | None = None) -> View:
    cal = calendar or MarketCalendar()
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ViewError("view directory must be new or empty: %s" % root)
    pat = re.compile(r"\d{4}-\d{2}-\d{2}\.json\.gz")
    by_name = {p.name: p for p in stores.grouped.glob("*.json.gz") if pat.fullmatch(p.name)}
    for h in stores.history:
        for p in sorted(h.glob("*.json.gz")) if h.is_dir() else []:
            if not pat.fullmatch(p.name): continue
            q = by_name.get(p.name)
            if q is None:
                by_name[p.name] = p
            elif sha_file(q) != sha_file(p):
                raise ViewError("grouped %s differs between %s and %s" % (p.name, q.parent, p.parent))
    g = [by_name[n] for n in sorted(by_name)]
    vis = [p for p in g if p.name[:10] <= as_of.isoformat()]
    if not vis:
        raise ViewError("no grouped session on or before as-of")
    first = date.fromisoformat(vis[0].name[:10])
    want = [w.session_date.isoformat() for w in sessions_between(cal, first, as_of)]
    have = [p.name[:10] for p in vis]
    missing = sorted(set(want) - set(have)); extra = sorted(set(have) - set(want))
    if missing or extra:
        raise ViewError("grouped store not calendar-complete: missing %s extra %s" % (missing[:5], extra[:5]))
    if have[-1] != as_of.isoformat():
        raise ViewError("as-of session %s has no grouped file (latest %s)" % (as_of, have[-1]))
    for sub in ("strategy_c/raw/grouped", "strategy_c/raw/tickers", "strategy_c/raw/splits", "research_universe_u1/reference/tickers"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    man: dict[str, Any] = {"as_of": as_of.isoformat(), "grouped": {"first": have[0], "last": have[-1], "count": len(have),
                                                                  "from_history": sum(1 for p in vis if p.parent != stores.grouped)},
                           "tickers": {}, "splits": {}}
    for p in vis:
        os.symlink(p.resolve(), root / "strategy_c/raw/grouped" / p.name)
    ranking: list[Path] = []; evidence: list[Path] = []
    for src, dst in zip(stores.tickers, ("strategy_c/raw/tickers", "research_universe_u1/reference/tickers")):
        for p in sorted(src.glob("CS_*.json.gz")):
            a = _snapshot_as_of(p)
            if a <= as_of.isoformat() and p.name[3:13] <= as_of.isoformat():
                os.symlink(p.resolve(), root / dst / p.name); man["tickers"][p.name] = sha_file(p)
                ranking.append(root / dst / p.name)
    (root / "evidence").mkdir()
    for d in stores.evidence_dirs:
        for p in sorted(d.glob("CS_*.json.gz")) if d.is_dir() else []:
            if _snapshot_as_of(p) <= as_of.isoformat() and p.name[3:13] <= as_of.isoformat() and not (root / "evidence" / p.name).exists():
                os.symlink(p.resolve(), root / "evidence" / p.name); man.setdefault("evidence", {})[p.name] = sha_file(p)
                evidence.append(root / "evidence" / p.name)
    for p in sorted(stores.splits.glob("splits_*.json.gz")):
        os.symlink(p.resolve(), root / "strategy_c/raw/splits" / p.name); man["splits"][p.name] = sha_file(p)
    man["grouped_hash"] = hashlib.sha256("\n".join(sha_file(p) + " " + p.name for p in vis[-LOOKBACK_SESSIONS - 30:]).encode()).hexdigest()
    return View(as_of=as_of, root=root, sessions=have, manifest=man, ranking_files=ranking, evidence_files=evidence)


def load_v21(view: View, scratch: Path):
    """Import the frozen V2.1 module (which imports V2, which imports V1) on the view. Returns the module."""
    verify_package()
    for name, h in CODE_SHA.items():
        if sha_file(RESEARCH / name) != h:
            raise ViewError("frozen research code changed: " + name)
    for name, h in D17_SHA.items():
        if sha_file(D17_FUND.parent / name) != h:
            raise ViewError("D17-R import input changed: " + name)
    saved = sys.argv
    sys.argv = ["i_paper_v21", str(view.root), str(D17_FUND), str(scratch)]
    try:
        spec = importlib.util.spec_from_file_location("i_paper_census_v21_%s" % view.as_of.isoformat().replace("-", ""), V21_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except AssertionError as ex:
        raise ViewError("frozen module refused the view: %r" % (ex,)) from ex
    finally:
        sys.argv = saved
    return mod
