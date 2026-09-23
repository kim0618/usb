"""E-MAX V1 provisional paper: the session record, the evidence status and the equivalence artifact.

The strategy is frozen and nothing here re-decides anything. What this module adds is the bookkeeping
the realtime paper run needs while the Kiwoom RVOL history is still being collected:

* every canonical symbol carries one state (``availability``), and a symbol whose Kiwoom RVOL history
  is short of the frozen minimum is ``RVOL_HISTORY_INSUFFICIENT`` -> ``H5_UNKNOWN`` -> not selectable.
  No other candidate is promoted in its place (no backfill);
* the session record carries the counts the operator reads, and one ``paper_evidence_status``:
  ``PROVISIONAL_RVOL_BOOTSTRAP`` while any eligible row lacks a denominator or the bootstrap is
  unfinished, ``OFFICIAL_KIWOOM_PAPER`` only when the bootstrap is complete and nothing is missing.
  Provisional sessions are recorded in their own book and are never summed into the official one;
* the equivalence artifact stores, per eligible row, the Kiwoom premarket dollar volume, the Kiwoom
  denominator, the Kiwoom RVOL and the frozen threshold's pass/fail, so a later diagnostic can put
  the Massive RVOL of the same symbol and session beside it. The threshold is read from the frozen
  rules file, never restated here, and this stage does not re-optimize it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import json
import math
from pathlib import Path
import re
from typing import Any

from app.strategy_e_max_rt import availability as AV
from app.strategy_e_max_rt.rvol_store import RVOL_MINIMUM, RVOL_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[3]
H5_RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/e1_h5_confirmation_rules_v1.json"

PROVISIONAL = "PROVISIONAL_RVOL_BOOTSTRAP"
OFFICIAL = "OFFICIAL_KIWOOM_PAPER"


def frozen_rvol_threshold(path: Path = H5_RULES_PATH) -> float:
    """The frozen H5 RVOL threshold, read from the frozen rules rather than restated."""
    rules = json.loads(path.read_text(encoding="utf-8"))
    text = rules["hypothesis"]["thresholds_frozen"]["premarket_rvol"]
    match = re.fullmatch(r">=\s*([0-9.]+)", text.strip())
    if not match:
        raise ValueError(f"frozen premarket_rvol threshold is not a '>= x' bound: {text!r}")
    return float(match.group(1))


def rvol_state(base_state: str, denominator: float | None) -> str:
    """A served symbol without a same-source denominator is a preparation gap, not a signal answer."""
    if base_state in (AV.MARKET_DATA_UNAVAILABLE, AV.STALE):
        return base_state
    if base_state == AV.FEATURE_COMPLETE and denominator is None:
        return AV.RVOL_HISTORY_INSUFFICIENT
    return base_state


def evidence_status(*, rvol_missing_rows: int, bootstrap_complete: bool) -> str:
    """Official only when the bootstrap is finished and no eligible row lacked a denominator."""
    return OFFICIAL if (bootstrap_complete and rvol_missing_rows == 0) else PROVISIONAL


def session_record(session: date, *, states: Mapping[str, str], h5_by_symbol: Mapping[str, str],
                   eligible_rows: int, candidates: Sequence[str], selected: Sequence[str],
                   rvol_ready_rows: int, rvol_missing_rows: int, bootstrap_complete: bool,
                   coverage: Mapping[str, Any] | None = None, decision_digest: str | None = None,
                   extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The per-session record; ``paper_evidence_status`` is decided here and nowhere else."""
    status = evidence_status(rvol_missing_rows=rvol_missing_rows, bootstrap_complete=bootstrap_complete)
    body = AV.diagnostics(states, h5_by_symbol, eligible_rows, candidates) | {
        "session": session.isoformat(),
        "strategy_id": "STRATEGY_E_MAX_V1",
        "mode": "SIMULATION_VIRTUAL_ONLY",
        "rvol_threshold_frozen": frozen_rvol_threshold(),
        "rvol_window": RVOL_WINDOW,
        "rvol_minimum": RVOL_MINIMUM,
        "rvol_ready": rvol_ready_rows,
        "rvol_missing": rvol_missing_rows,
        "selected": list(selected),
        "paper_evidence_status": status,
        "bootstrap_complete": bool(bootstrap_complete),
        "promotion_rule": "PROVISIONAL and OFFICIAL books are separate; provisional sessions are "
                          "never summed into the official record",
        "decision_digest": decision_digest,
    }
    if coverage is not None:
        body["rvol_coverage"] = dict(coverage)
    if extra:
        body.update(extra)
    return body


def evidence_rows(session: date, *, features: Mapping[str, Mapping[str, float | None]],
                  denominators: Mapping[str, float | None], staged_counts: Mapping[str, int],
                  states: Mapping[str, str], threshold: float | None = None) -> list[dict[str, Any]]:
    """One row per eligible symbol for the later Kiwoom / Massive source-equivalence diagnostic.

    ``massive_rvol`` is left empty on purpose: it is reconstructed offline from the Massive store,
    against this same (symbol, session), once the bootstrap is complete.
    """
    bound = frozen_rvol_threshold() if threshold is None else threshold

    def finite(value):
        return None if value is None or not math.isfinite(float(value)) else float(value)

    rows = []
    for symbol in sorted(features):
        rvol = finite(features[symbol].get("premarket_rvol"))
        denominator = denominators.get(symbol)
        rows.append({
            "session": session.isoformat(),
            "symbol": symbol,
            "source": "KIWOOM",
            "kiwoom_pm_dollar_volume": finite(features[symbol].get("premarket_dollar_volume")),
            "kiwoom_denominator": denominator,
            "kiwoom_rvol": rvol,
            "staged_sessions_used": min(staged_counts.get(symbol, 0), RVOL_WINDOW),
            "rvol_state": states.get(symbol),
            "threshold": bound,
            "kiwoom_pass": None if rvol is None else bool(rvol >= bound),
            "massive_rvol": None,
            "massive_pass": None,
            "equivalence_note": "massive_rvol is reconstructed offline from the Massive store for the "
                                "same symbol and session; sources are never mixed inside a decision",
        })
    return rows


def write_evidence(path: Path, rows: Sequence[Mapping[str, Any]]) -> int:
    import csv
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


class FixedDecision:
    """A ``DecisionSource`` that hands the engine the decision the 09:25 cutoff already produced."""

    def __init__(self, decision, source: str = "KIWOOM_NATIVE_PAPER"):
        self.decision, self.source = decision, source

    def decide(self, session: date, decided_at: datetime):
        return self.decision
