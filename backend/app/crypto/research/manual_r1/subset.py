"""Recompute performance after the provenance audit, one origin group at a time.

    PYTHONPATH=backend python -m app.crypto.research.manual_r1.subset

MANUAL-R1 reported one number over seventeen trades of mixed origin. This splits the same
ledger by who made each trade and refuses to aggregate across the split. The USER group has one
completed trade, so no ratio is computed for it: a profit factor over n=1 is not a small
estimate, it is not an estimate.

MANUAL-R1.2 resolved the attribution of all seventeen from the operator's declaration, so the
third bucket is now empty and the TEST group carries the full cost of development. That moved
643.48 USDT out of "counted nowhere" and none of it into the USER column: the operator's figure
is the same +330.75 it was before the declaration, which is the point. The declaration changed
who owns the losses, not how much anyone made.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import audit as A
from . import ledger as L
from . import provenance as P

REPO_ROOT = Path(__file__).resolve().parents[5]
SNAPSHOT = REPO_ROOT / "data/research/crypto/manual_r1/snapshot"
OUT_DIR = REPO_ROOT / "data/research/crypto/manual_r1"

#: Below this many completed trades, ratios are withheld rather than reported with a caveat.
#: A caveat under a number does not stop the number being quoted.
MIN_TRADES_FOR_RATIOS = 10

#: R1.2 writes alongside the R1.1 artefacts rather than over them. The v1 files are what the
#: R1.1 reports cite, and an overlay that silently rewrites its own history is the thing this
#: whole correction exists to prevent.
RESULTS_NAME = "manual_subset_results_v2.json"
PROVENANCE_NAME = "trade_provenance_v2.parquet"


def _group_summary(trades: list[L.ManualTrade], *, label: str) -> dict[str, Any]:
    if not trades:
        return {"group": label, "trades": 0,
                "note": "no countable trades in this group"}
    totals = {
        "group": label,
        "trades": len(trades),
        "trade_indices": [t.index for t in trades],
        "gross_pnl": sum(t.gross_pnl for t in trades),
        "fees": sum(t.fees for t in trades),
        "funding": sum(t.funding for t in trades),
        "net_pnl": sum(t.net_pnl for t in trades),
        "wins": sum(1 for t in trades if t.is_win),
    }
    if len(trades) < MIN_TRADES_FOR_RATIOS:
        totals["ratios"] = "WITHHELD_INSUFFICIENT_SAMPLE"
        totals["ratios_note"] = (
            f"win rate, profit factor, drawdown and expectancy are not reported below "
            f"{MIN_TRADES_FOR_RATIOS} completed trades; with n={len(trades)} they would be "
            f"restatements of the individual trades")
    else:
        totals["ratios"] = A.summarise(trades)
    return totals


def run(write: bool = True) -> dict[str, Any]:
    events = L.load_events(SNAPSHOT / "ledger.jsonl")
    book = L.reconstruct(events)
    trades = book.trades

    rows: list[dict[str, Any]] = []
    for trade in trades:
        classification = P.classify(trade.index)
        rows.append({
            "trade_index": trade.index,
            "opened_utc": L.utc(trade.opened_ts_ms),
            "closed_utc": L.utc(trade.closed_ts_ms),
            "side": trade.side,
            "qty": trade.qty,
            "leverage": trade.leverage,
            "hold_minutes": trade.hold_ms / 60_000,
            "gross_pnl": trade.gross_pnl,
            "fees": trade.fees,
            "funding": trade.funding,
            "net_pnl": trade.net_pnl,
            "liquidated": trade.liquidated,
            "segment": trade.segment,
            **{k: v for k, v in classification.as_dict().items() if k != "trade_index"},
        })

    user = [t for t in trades if P.classify(t.index).include_in_manual_performance]
    test = [t for t in trades if P.classify(t.index).include_in_test_performance]
    nowhere = [t for t in trades
               if not P.classify(t.index).include_in_manual_performance
               and not P.classify(t.index).include_in_test_performance]

    open_position = dict(book.open_position) if book.open_position else None

    payload = {
        "record": "MANUAL_R1_MANUAL_SUBSET_RESULTS_V2",
        "supersedes_interpretation_of": "MANUAL_R1_RESULTS_V1",
        "supersedes": "MANUAL_R1_MANUAL_SUBSET_RESULTS_V1",
        "supersedes_note": "the arithmetic in results_v1.json is unchanged and still correct; "
                           "what is withdrawn is reading its aggregate as the operator's "
                           "trading performance",
        "v2_note": "v1 classified 13 trades as MEDIUM or UNKNOWN and counted them nowhere. The "
                   "operator's Q3 declaration attributes all 16 pre-17 trades to development, "
                   "so those 13 now sit in the TEST group. No trade moved into the USER group "
                   "and no ledger event was touched.",
        "q3_declaration": {
            "source": P.DECLARATION,
            "statement": P.DECLARATION_TEXT,
            "evidence_tier": P.TIER_1,
            "settles": "attribution of every completed trade",
            "does_not_settle": "which kind of development test each earlier trade was",
            "user_trades_named": {"completed": [17], "open": 1},
        },
        "aggregate_verdict": "INVALID_MIXED_PROVENANCE",
        "user_manual_verdict": "INSUFFICIENT_SAMPLE",
        "provenance_audit_verdict": "PARTIAL",
        "provenance_audit_note": "attribution is COMPLETE: no completed trade is UNKNOWN any "
                                 "more. The audit stays PARTIAL because 13 of the 16 development "
                                 "trades still have no documented test subtype, and TEST_UNSPECIFIED "
                                 "records that absence rather than filling it.",
        "attribution_verdict": "COMPLETE_BY_DECLARATION",
        "auto_readiness": "NOT_AUTHORIZED",
        "ledger_modified": False,
        "production_touched": False,
        "classification_rule": P.summary()["rule"],
        "classification_summary": P.summary(),
        "groups": {
            "USER": _group_summary(user, label="USER"),
            "TEST": _group_summary(test, label="TEST"),
        },
        "cost_of_development_on_production": {
            "net_pnl": sum(t.net_pnl for t in test),
            "note": "what verifying this system on the live paper account cost, now that all of "
                    "it is attributed. It includes the 600.56 USDT liquidation of trade 13, "
                    "which is here on the operator's word and on no document.",
        },
        "counted_nowhere": {
            "trades": len(nowhere),
            "trade_indices": [t.index for t in nowhere],
            "net_pnl": sum(t.net_pnl for t in nowhere),
            "note": "empty since R1.2: the declaration attributed every completed trade, so "
                    "no trade is excluded from both groups. A row lands here only if a future "
                    "classification is MEDIUM or UNKNOWN again",
        },
        "all_completed_trades_for_reference": {
            "trades": len(trades),
            "net_pnl": sum(t.net_pnl for t in trades),
            "note": "ledger fact. Not a performance figure for any person or strategy.",
        },
        "open_position": ({
            **open_position,
            "opened_utc": L.utc(open_position["opened_ts_ms"]),
            "provenance": P.OPEN_POSITION.as_dict(),
        } if open_position else None),
        "not_recomputed": {
            "entry_behaviour_comparisons": "the entry-versus-control comparison in "
                                           "MANUAL_R1_RESULTS_V1 is not rerun per group: the "
                                           "USER group has one entry, and a one-point "
                                           "distribution has nothing to compare",
            "h1_h2_h3": "CONTAMINATED",
            "h4": "NOT_EVALUABLE",
            "h2_status": "REMOVED_FROM_USER_FINDINGS",
            "h2_note": "'the operator has no information distinguishing LONG from SHORT' was "
                       "measured on 16 trades now confirmed to be development work. A developer "
                       "exercising an order path is not expressing a view, so the finding "
                       "describes the test harness and says nothing about the operator. It is "
                       "withdrawn as a statement about human behaviour rather than restated "
                       "with a caveat.",
            "user_behaviour_evaluable": False,
            "user_behaviour_note": "one completed trade and one open position. No entry-timing, "
                                   "sizing or direction finding is computable from n=1",
        },
    }

    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / RESULTS_NAME).write_text(
            json.dumps(payload, indent=1, default=str))
        _write_parquet(rows)
    return payload


def _write_parquet(rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if rows:
        pq.write_table(pa.Table.from_pylist(rows), OUT_DIR / PROVENANCE_NAME)


def main() -> int:
    payload = run()
    user, test = payload["groups"]["USER"], payload["groups"]["TEST"]
    print(f"MANUAL-R1.2  aggregate {payload['aggregate_verdict']}")
    print(f"  USER  {user['trades']} trade(s)  net {user.get('net_pnl', 0):+.2f} USDT"
          f"  -> {payload['user_manual_verdict']}")
    print(f"  TEST  {test['trades']} trade(s)  net {test.get('net_pnl', 0):+.2f} USDT")
    nowhere = payload["counted_nowhere"]
    print(f"  counted nowhere {nowhere['trades']} trade(s)  net {nowhere['net_pnl']:+.2f} USDT")
    print(f"  by origin: {payload['classification_summary']['by_origin']}")
    print(f"  attribution {payload['attribution_verdict']}, "
          f"audit {payload['provenance_audit_verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
