"""Run the MANUAL-R1 audit and write the artefacts.

    PYTHONPATH=backend python -m app.crypto.research.manual_r1.runner

Phase 1 verifies the money from the ledger. Phase 2 describes the entries against controls. The
order is not cosmetic: reading behaviour into a result before knowing whether the result exists
is how a losing habit gets written up as a strategy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import audit as A
from . import context as C
from . import controls as CTRL
from . import ledger as L

REPO_ROOT = Path(__file__).resolve().parents[5]
SNAPSHOT = REPO_ROOT / "data/research/crypto/manual_r1/snapshot"
OUT_DIR = REPO_ROOT / "data/research/crypto/manual_r1"

#: The server clock when the snapshot was taken, recorded in PROVENANCE.txt.
SNAPSHOT_TS_MS = 1_790_662_913_000

#: Features compared between entries and controls. Path and shape, never level.
COMPARED = [
    "ret_15s_bp", "ret_30s_bp", "ret_60s_bp", "ret_180s_bp", "ret_300s_bp",
    "ret_900s_bp", "ret_1800s_bp", "ret_3600s_bp",
    "accel_15s_vs_60s", "accel_60s_vs_300s",
    "pos_in_range_300s", "pos_in_range_900s", "pos_in_range_3600s",
    "range_300s_bp", "range_900s_bp", "range_3600s_bp",
    "drawdown_from_high_300s_bp", "drawdown_from_high_900s_bp", "drawdown_from_high_3600s_bp",
    "failed_high_15m_bp", "rv_300s_bp", "rv_1800s_bp", "vol_ratio_300_1800",
    "spread_bp", "basis_bp",
]


def _finite(values) -> np.ndarray:
    array = np.array([v for v in values if v is not None], dtype=float)
    return array[np.isfinite(array)]


def compare(entries: list[dict[str, Any]], controls: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive comparison only. With this sample size a p-value would be theatre."""
    out: dict[str, Any] = {}
    for name in COMPARED:
        a = _finite(row.get(name) for row in entries)
        b = _finite(row.get(name) for row in controls)
        if len(a) < 2 or len(b) < 2:
            out[name] = {"entries_n": len(a), "controls_n": len(b), "comparable": False}
            continue
        spread = float(np.std(b, ddof=0))
        out[name] = {
            "entries_n": len(a), "controls_n": len(b), "comparable": True,
            "entry_median": float(np.median(a)), "control_median": float(np.median(b)),
            "entry_mean": float(np.mean(a)), "control_mean": float(np.mean(b)),
            "difference_in_control_sd": ((float(np.mean(a)) - float(np.mean(b))) / spread
                                         if spread > 0 else None),
            "entry_share_above_control_median": float(np.mean(a > float(np.median(b)))),
        }
    return out


def run(write: bool = True) -> dict[str, Any]:
    events = L.load_events(SNAPSHOT / "ledger.jsonl")
    book = L.reconstruct(events)
    # The rotated segments carry the trading history; the live tail carries the present. Both
    # are needed: valuing the still-open position against the newest segment would price it at a
    # mark that is days stale.
    tape = C.load_tape(sorted((SNAPSHOT / "segments").glob("*.input.jsonl.gz")))
    live = C.load_tape([SNAPSHOT / "input.jsonl.gz"])

    trades = book.trades
    current = book.segment_trades(book.current_segment)
    reset_equity = book.resets[-1]["after_equity"] if book.resets else None

    # --- Phase 1: the money -------------------------------------------------------------
    lifetime = A.summarise(trades, starting_equity=None)
    segment = A.summarise(current, starting_equity=reset_equity)

    open_position = dict(book.open_position) if book.open_position else None
    if open_position:
        index = live.at_or_before(SNAPSHOT_TS_MS)
        mark = float(live.mark[index]) if index is not None else None
        mark_ts = int(live.ts_ms[index]) if index is not None else None
        qty = open_position["signed_qty"]
        if mark and np.isfinite(mark):
            unrealised = (open_position["entry_price"] - mark) * abs(qty) * \
                (1 if qty < 0 else -1)
            open_position.update({
                "snapshot_mark": mark,
                "snapshot_mark_utc": L.utc(mark_ts) if mark_ts else None,
                "unrealised_pnl": unrealised,
                "hold_hours": (SNAPSHOT_TS_MS - open_position["opened_ts_ms"]) / 3_600_000,
                "if_closed_now_before_exit_fee": unrealised
                - open_position["fees_so_far"] - open_position["funding_so_far"],
                "note": "UNREALISED. Not part of any realised figure above."})

    # --- Phase 2: the behaviour ----------------------------------------------------------
    entry_rows: list[dict[str, Any]] = []
    for trade in trades:
        features = C.entry_features(tape, trade.opened_ts_ms)
        if features is None:
            continue
        entry_rows.append({
            "index": trade.index, "side": trade.side, "segment": trade.segment,
            "leverage": trade.leverage, "net_pnl": trade.net_pnl, "net_bp": trade.net_bp,
            "gross_bp": trade.gross_bp, "is_win": trade.is_win,
            "liquidated": trade.liquidated, "hold_minutes": trade.hold_ms / 60_000,
            "opened_utc": L.utc(trade.opened_ts_ms), **features,
            **{f"out_{k}": v for k, v in
               C.outcome_path(tape, trade.opened_ts_ms, trade.side,
                              trade.entry_price).items()}})

    control_points = CTRL.sample(tape, trades,
                                 open_position_ts=(book.open_position or {}).get("opened_ts_ms"),
                                 snapshot_ts=SNAPSHOT_TS_MS)
    control_rows: list[dict[str, Any]] = []
    for point in control_points:
        features = C.entry_features(tape, point.ts_ms)
        if features is None:
            continue
        control_rows.append({"matched_entry_index": point.matched_entry_index,
                             "matched_side": point.matched_side, **features})

    shorts = [row for row in entry_rows if row["side"] == "SHORT"]
    longs = [row for row in entry_rows if row["side"] == "LONG"]
    short_controls = [row for row in control_rows if row["matched_side"] == "SHORT"]
    long_controls = [row for row in control_rows if row["matched_side"] == "LONG"]

    payload = {
        "record": "MANUAL_R1_RESULTS_V1",
        "provenance": (SNAPSHOT / "PROVENANCE.txt").read_text(),
        "source_of_truth": "paper ledger (ledger.jsonl), recomputed; terminal display not used",
        "snapshot_ts_ms": SNAPSHOT_TS_MS,
        "run_start_utc": L.utc(book.run_start_ms) if book.run_start_ms else None,
        "accounting_reconciliation": _reconcile(events, trades, book),
        "resets": book.resets,
        "counts": {"completed_trades": len(trades), "open_positions": 1 if book.open_position else 0,
                   "rejected_orders": book.rejected_orders, "liquidations": book.liquidations,
                   "leverage_changes": book.leverage_changes,
                   "unattributed_fees": book.unattributed_fees},
        "lifetime": lifetime,
        "current_segment": {"segment": book.current_segment, **segment},
        "open_position": open_position,
        "by_side": A.by_side(trades),
        "by_leverage": A.by_leverage(trades),
        "by_hour": A.by_hour(trades),
        "concentration": A.concentration(trades),
        "fee_burden": A.fee_burden(trades),
        "leverage_vs_direction": A.leverage_vs_direction(trades),
        "exposure": A.exposure(trades, book, SNAPSHOT_TS_MS),
        "tape": {
            "history_snapshots": len(tape),
            "history_span_utc": [L.utc(tape.span_ms[0]), L.utc(tape.span_ms[1])],
            "history_median_gap_ms": float(np.median(np.diff(tape.ts_ms))),
            "history_max_gap_ms": int(np.max(np.diff(tape.ts_ms))),
            "history_gap_note": "segments 3 to 9 were not pulled: no manual entry falls in them",
            "live_snapshots": len(live),
            "live_span_utc": [L.utc(live.span_ms[0]), L.utc(live.span_ms[1])],
        },
        "control_rule": CTRL.describe_rule(),
        "entry_vs_control": {
            "all": compare(entry_rows, control_rows),
            "short": compare(shorts, short_controls),
            "long": compare(longs, long_controls),
        },
        "winning_vs_losing_short": compare([r for r in shorts if r["is_win"]],
                                           [r for r in shorts if not r["is_win"]]),
        "sample_sizes": {"entries": len(entry_rows), "short_entries": len(shorts),
                         "long_entries": len(longs), "controls": len(control_rows),
                         "winning_shorts": sum(1 for r in shorts if r["is_win"]),
                         "losing_shorts": sum(1 for r in shorts if not r["is_win"])},
    }

    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results_v1.json").write_text(json.dumps(payload, indent=1, default=str))
        _write_parquet(trades, entry_rows, control_rows)
    return payload


def _reconcile(events: list[dict[str, Any]], trades, book) -> dict[str, Any]:
    """The rebuild has to agree with the ledger's own running totals, or it is not a rebuild."""
    from decimal import Decimal
    last_realized = Decimal(0)
    fees = Decimal(0)
    funding = Decimal(0)
    for event in events:
        if "realized_pnl" in event:
            last_realized = Decimal(str(event["realized_pnl"]))
        if event["event_type"] == "FEE":
            fees += Decimal(str(event.get("amount", 0)))
        if event["event_type"] == "FUNDING":
            funding += Decimal(str(event.get("amount_paid", 0)))
    open_fees = (book.open_position or {}).get("fees_so_far", 0.0)
    open_funding = (book.open_position or {}).get("funding_so_far", 0.0)
    return {
        "ledger_realized_pnl": float(last_realized),
        "rebuilt_gross_sum": sum(t.gross_pnl for t in trades),
        "gross_matches": abs(float(last_realized) - sum(t.gross_pnl for t in trades)) < 1e-6,
        "ledger_cumulative_fees": float(fees),
        "rebuilt_fees_plus_open": sum(t.fees for t in trades) + open_fees,
        "fees_match": abs(float(fees) - (sum(t.fees for t in trades) + open_fees)) < 1e-6,
        "ledger_cumulative_funding": float(funding),
        "rebuilt_funding_plus_open": sum(t.funding for t in trades) + open_funding,
        "funding_match": abs(float(funding) - (sum(t.funding for t in trades)
                                               + open_funding)) < 1e-6,
    }


def _write_parquet(trades, entry_rows, control_rows) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if trades:
        pq.write_table(pa.Table.from_pylist([t.as_dict() for t in trades]),
                       OUT_DIR / "manual_trades_v1.parquet")
    if entry_rows:
        pq.write_table(pa.Table.from_pylist(entry_rows), OUT_DIR / "entry_context_v1.parquet")
    if control_rows:
        pq.write_table(pa.Table.from_pylist(control_rows),
                       OUT_DIR / "control_context_v1.parquet")


def main() -> int:
    payload = run()
    life, seg = payload["lifetime"], payload["current_segment"]
    print(f"MANUAL-R1  completed trades {life['trades']}  "
          f"lifetime net {life['net_pnl']:+.2f} USDT  PF {life['profit_factor']}")
    print(f"  current segment {seg['segment']}: {seg['trades']} trade(s), "
          f"net {seg.get('net_pnl', 0):+.2f} USDT")
    print(f"  reconciliation: {payload['accounting_reconciliation']}")
    print(f"  sample sizes:   {payload['sample_sizes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
