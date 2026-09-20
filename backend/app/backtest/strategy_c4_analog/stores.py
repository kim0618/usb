"""Read-through access to the frozen C-E0 and EQM stores, with C-4's own incremental store.

The frozen stores are never written to: a CIK C-4 needs and C-E0 or EQM never fetched is written
under ``data/runtime/strategy_c4/`` instead, and every reader looks in the frozen root first and
the C-4 root second. So the frozen digests stay exactly what the closed studies recorded.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_e0 import events as c_events
from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_eqm_v0 import xbrl_store

FROZEN_SEC_ROOT = Path("data/runtime/strategy_c/e0/raw")
FROZEN_XBRL_ROOT = Path("data/runtime/strategy_eqm/v0/raw")
C4_SEC_ROOT = Path("data/runtime/strategy_c4/sec")
C4_XBRL_ROOT = Path("data/runtime/strategy_c4/xbrl")


@dataclass(frozen=True)
class StoreRoots:
    sec: tuple[Path, ...] = (FROZEN_SEC_ROOT, C4_SEC_ROOT)
    xbrl: tuple[Path, ...] = (FROZEN_XBRL_ROOT, C4_XBRL_ROOT)

    def sec_root_of(self, cik: str) -> Path | None:
        for root in self.sec:
            if (root / "submissions" / f"CIK{cik}").is_dir():
                return root
        return None

    def xbrl_root_of(self, cik: str) -> Path | None:
        for root in self.xbrl:
            path = xbrl_store.facts_path(root, cik)
            if path.exists() and sec_store.ledger_path(path).exists():
                return root
        return None


def stored_sec_ciks(roots: StoreRoots) -> set[str]:
    out: set[str] = set()
    for root in roots.sec:
        directory = root / "submissions"
        if directory.is_dir():
            out |= {p.name[3:] for p in directory.glob("CIK*") if p.is_dir()}
    return out


def stored_xbrl_ciks(roots: StoreRoots) -> set[str]:
    out: set[str] = set()
    for root in roots.xbrl:
        directory = root / "companyfacts"
        if directory.is_dir():
            out |= {p.name[3:-8] for p in directory.glob("CIK*.json.gz")}
    return out


def read_rows(roots: StoreRoots, cik: str) -> list[dict[str, Any]]:
    root = roots.sec_root_of(cik)
    return c_events.read_cik_rows(root, cik) if root is not None else []


def coverage_status(roots: StoreRoots, cik: str, required_from: date) -> str:
    """The C-E0 coverage verdict computed from cached pages only; no request is made."""
    root = roots.sec_root_of(cik)
    if root is None:
        return "NOT_FETCHED"
    return sec_store.fetch_cik(None, root, cik, required_from=required_from).status


def read_facts(roots: StoreRoots, cik: str) -> dict[str, Any] | None:
    root = roots.xbrl_root_of(cik)
    return xbrl_store.read_facts(root, cik) if root is not None else None


def digests(roots: StoreRoots) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, root in (("frozen_sec", FROZEN_SEC_ROOT), ("c4_sec", C4_SEC_ROOT)):
        if root.is_dir():
            out[name] = sec_store.store_digest(root)
    for name, root in (("frozen_xbrl", FROZEN_XBRL_ROOT), ("c4_xbrl", C4_XBRL_ROOT)):
        if (root / "companyfacts").is_dir():
            out[name] = xbrl_store.store_digest(root)
    return out


def missing(needed: Sequence[str], stored: set[str]) -> list[str]:
    return sorted(set(needed) - stored)
