"""E's consumer of ``snapshot_0924``: the decision, read-only, from the sealed file.

The consumer rebuilds E's caches from the sealed file and runs E-RUNTIME-R1's own decision path -
availability classification, the Kiwoom-native RVOL denominators, ``_feature_rows``, the V1.1 seal
(H5), R1, Top3 and B2 through ``decision.decide_from_frame``, and ``execution_gate`` - with the RVOL
store opened read-only. This module never executes anything and imports no paper, broker or book
code: the producer hands an authorized decision to E-RUNTIME-R1's own paper stage (SimBroker, the
``e_cost_v2_exact_0934_close`` book) and nothing else.

The same path is also run on the producer's live caches, so :func:`parity` can show that the file
gives E exactly the inputs and the decision the live cache would have.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time as dtime
from pathlib import Path
import sqlite3
from typing import Any

import numpy as np

from app.ae_common import snapshot as SS
from app.strategy_e_max_rt import availability as AV
from app.strategy_e_max_rt import finalizer as FZ
from app.strategy_e_max_rt.rvol_store import RvolStore

#: Decision fields compared between the file and the live cache. ``decided_at`` and ``digest`` are
#: instants of the two computations and are not inputs.
DECISION_FIELDS = ("universe_rows", "h5_count", "h5_rate", "high_breadth", "breadth_multiplier",
                   "global_multiplier", "final_exposure", "candidates", "r1_order", "selected",
                   "reference_prices", "features", "v1_1_seal_digest", "v1_rules_digest")


class ReadOnlyRvolStore(RvolStore):
    """``RvolStore`` over a ``mode=ro`` connection: reads work, any write fails in SQLite itself."""

    def __init__(self, path: Path, *, clock=None):  # noqa: D107 - deliberately skips the schema write
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(FZ.ET))
        if not self.path.is_file():
            raise FileNotFoundError(str(self.path))
        self.connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)


def statuses_of(caches: Mapping[str, FZ.SymbolCache], unmapped: Sequence[str], deadline: datetime) -> dict[str, str]:
    out = {sym: AV.classify(AV.outcome_from_cache(c, deadline=deadline)) for sym, c in caches.items()}
    for sym in unmapped:
        out[sym] = AV.MARKET_DATA_UNAVAILABLE
    return out


def decide(caches: Mapping[str, FZ.SymbolCache], spy: FZ.SymbolCache, art: Mapping[str, Any], *,
           unmapped: Sequence[str], session: date, deadline: datetime, store: RvolStore,
           decided_at: datetime, source: str) -> dict[str, Any]:
    """E-RUNTIME-R1's decision, execution gate and session record over ``caches``, as plain data.

    ``_decision`` (the sealed ``EDecision``) and ``_record`` (``paper.session_record``) are the two
    objects the paper stage takes; they are dropped before the body is written anywhere.
    """
    from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
    from app.dev import run_e_rt2_dryrun as RT2
    from app.strategy_e_max_rt import decision as DEC
    from app.strategy_e_max_rt import paper as PAPER
    from app.strategy_e_v1_1.universe import DecisionFrame

    statuses = statuses_of(caches, unmapped, deadline)
    denominators = store.preload(list(caches), session)
    rows, rvol_unknown = RT2._feature_rows(dict(caches), spy, statuses, dict(art), denominators)  # noqa: SLF001
    symbols = tuple(sorted(rows))
    frame = DecisionFrame(session, symbols, {n: np.array([rows[x][n] for x in symbols], dtype=float)
                                             for n in SEALED_FEATURES}, {})
    decision = DEC.decide_from_frame(frame, source_digest=art["digest"], source=source, decided_at=decided_at)
    unreachable = store.unreachable_symbols()
    pending = store.bootstrap_pending(list(caches), session)
    documented = {sym for sym, st in store.collection_state().items()
                  if st.get("last_error") in RT2.DOCUMENTED_REFUSALS}
    gate = RT2.execution_gate(decided_at=decided_at, open_et=datetime.combine(session, dtime(9, 30), tzinfo=FZ.ET),
                              statuses=statuses, documented=documented, rvol_unknown=rvol_unknown,
                              unreachable=unreachable, pending=pending)
    counts = {k: sum(v == k for v in statuses.values())
              for k in (AV.FEATURE_COMPLETE, AV.SPARSE_NO_PREMARKET, AV.STALE, AV.MARKET_DATA_UNAVAILABLE)}
    body = {"decision": decision.to_json(), "gate": gate, "status_counts": counts,
            "statuses_digest": SS.digest(dict(sorted(statuses.items()))),
            "feature_rows_digest": SS.digest({s: rows[s] for s in symbols}),
            "rvol_unknown": sorted(rvol_unknown), "eligible_rows": len(symbols)}
    # the session record exactly as E-RUNTIME-R1's runner builds it (statuses after the RVOL relabel)
    record_states = dict(statuses)
    for sym in rvol_unknown:
        record_states[sym] = AV.RVOL_HISTORY_INSUFFICIENT
    h5_by_symbol = {sym: AV.h5_status(record_states[sym], sym in decision.candidates) for sym in caches}
    coverage = store.coverage(list(caches), session) | {"unreachable": len(unreachable),
                                                         "pending_collection": len(pending),
                                                         "pending_examples": pending[:10]}
    body["_decision"] = decision
    body["_record"] = PAPER.session_record(
        session, states=record_states, h5_by_symbol=h5_by_symbol, eligible_rows=decision.universe_rows,
        candidates=decision.candidates, selected=decision.selected,
        rvol_ready_rows=len(symbols) - len(rvol_unknown), rvol_missing_rows=len(rvol_unknown),
        bootstrap_complete=not pending, coverage=coverage, decision_digest=decision.digest,
        extra={"universe_digest": art["digest"], "runtime": "AE_COMMON_PRODUCER_V1", "execution_gate": gate,
               "decision_source": source})
    return body


def public(body: Mapping[str, Any]) -> dict[str, Any]:
    """The body without the in-memory objects, for a run record."""
    return {k: v for k, v in body.items() if not k.startswith("_")}


def parity(from_file: Mapping[str, Any], from_live: Mapping[str, Any]) -> dict[str, Any]:
    a, b = from_file["decision"], from_live["decision"]
    differ = [k for k in DECISION_FIELDS if SS.canonical(a.get(k)) != SS.canonical(b.get(k))]
    same_status = from_file["statuses_digest"] == from_live["statuses_digest"]
    same_rows = from_file["feature_rows_digest"] == from_live["feature_rows_digest"]
    return {"exact": not differ and same_status and same_rows, "status_classification_equal": same_status,
            "feature_rows_equal": same_rows, "decision_fields_different": differ}


def no_decision(reason: str, detail: str) -> dict[str, Any]:
    """E's record when the producer could not give it a sealed snapshot. Never a decision, never a trade."""
    return {"status": "NO_DECISION", "reason": reason, "detail": detail, "decision": None,
            "real_orders": 0, "sim_entries": 0, "simbroker_used": False, "book_written": False}
