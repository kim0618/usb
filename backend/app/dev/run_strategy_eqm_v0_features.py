"""EQM-P3/P4: event quality raw features and the coverage report, with no outcome read.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_eqm_v0_features

Inputs are the frozen C-E0 status table, the C-E0 SEC submissions store and the EQM-V0 XBRL store.
Nothing here loads the price panel, so no forward return, MFE, MAE or control mean can be touched
at this stage even by accident - which is the point: the coverage floors of the pre-registration
are fixed on what this script reports, before `GATE-EQM-V0` is ever evaluated.
"""

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from app.backtest.strategy_c_e0 import audit, sec_store
from app.backtest.strategy_c_e0.events import build_cik_events, read_cik_rows
from app.backtest.strategy_c_e0.pit import build_grid, parse_acceptance
from app.backtest.strategy_c_e0.rules import load_declaration
from app.backtest.strategy_c_e0.taxonomy import Taxonomy
from app.backtest.strategy_c_selection.run import usable_sessions
from app.backtest.strategy_eqm_v0 import rows as eqm_rows
from app.backtest.strategy_eqm_v0 import quality, xbrl_store

from app.dev.fetch_strategy_c_selection_raw import sessions_between
from app.market.calendar import MarketCalendar

C_E0_RUN = Path("data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13")
COHORTS_WITH_EVENTS = ("EM", "EM_NEGATIVE_RISK")


class GridMismatch(RuntimeError):
    """The rebuilt session grid does not agree with the frozen C-E0 status table."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--raw", type=Path, default=Path("data/runtime/strategy_c/raw"))
    parser.add_argument("--events", type=Path, default=sec_store.DEFAULT_ROOT)
    parser.add_argument("--facts", type=Path, default=xbrl_store.DEFAULT_ROOT)
    parser.add_argument("--status", type=Path, default=C_E0_RUN / "candidate_status.parquet")
    parser.add_argument("--out", type=Path, default=Path("data/runtime/strategy_eqm/v0/features"))
    args = parser.parse_args()

    declaration = load_declaration()
    calendar = MarketCalendar()
    sessions, dropped = usable_sessions(args.raw, tuple(sessions_between(calendar, args.start, args.end)))
    grid = build_grid(list(sessions))
    print(f"sessions={len(sessions)} dropped={dropped}", flush=True)

    status = pd.read_parquet(args.status)
    for _, row in status.iterrows():
        if sessions[int(row["date_idx"])].isoformat() != str(row["signal_date"]):
            raise GridMismatch(f"date_idx {row['date_idx']} is {sessions[int(row['date_idx'])]} "
                               f"but the frozen table says {row['signal_date']}")
    print(f"grid verified against {len(status)} frozen status rows", flush=True)

    samples = json.loads((args.events / "timezone_audit_samples.json").read_text(encoding="utf-8"))
    zone_result = audit.timezone_audit(samples, parse=parse_acceptance)
    if not zone_result["pass"]:
        raise RuntimeError("timezone audit does not pass; the C-E0 PIT contract is not reusable")
    zone = zone_result["zone"]
    taxonomy = Taxonomy(declaration.taxonomy, declaration.addendum)
    print(f"acceptance zone={zone} agreement={zone_result['agreement']}", flush=True)

    # One CIK at a time: a companyfacts document is large, so only the current filer's document
    # is ever held in memory. Rows keep their frozen order afterwards.
    targets = status[status["status"].isin(COHORTS_WITH_EVENTS)].reset_index(drop=True)
    targets["_order"] = range(len(targets))
    built = []
    for n, (cik_value, group) in enumerate(targets.groupby("cik", dropna=False, sort=True), start=1):
        cik = str(cik_value) if cik_value else None
        view = index = document = None
        if cik:
            submissions = read_cik_rows(args.events, cik)
            view = build_cik_events(cik, submissions, taxonomy, grid, acceptance_zone=zone)
            index = eqm_rows.accession_index(submissions)
            document = xbrl_store.read_facts(args.facts, cik)
        for _, row in group.iterrows():
            built.append({"_order": int(row["_order"]), **eqm_rows.build_row(
                date_idx=int(row["date_idx"]), ticker=str(row["ticker"]), cik=cik,
                status=str(row["status"]), grid=grid,
                view=view, forms_by_accession=index or {}, facts_document=document,
                recent_dilution_20=bool(row["recent_dilution_20"])).as_dict()})
        if n % 100 == 0:
            print(f"ciks {n} rows={len(built)}", flush=True)

    frame = pd.DataFrame(built).sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)
    frame["accession_statuses"] = frame["accession_statuses"].apply(list)
    digest = xbrl_store.store_digest(args.facts)
    out = args.out / digest[:16]
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "quality_rows.parquet", index=False)

    observable = frame[frame["quality_status"] == eqm_rows.OBSERVABLE]
    em_obs = observable[observable["status"] == "EM"]
    coverage = {
        "xbrl_store_digest": digest,
        "sessions": len(sessions),
        "acceptance_zone": zone,
        "rows_built": int(len(frame)),
        "by_status": frame["status"].value_counts().to_dict(),
        "by_quality_status": frame["quality_status"].value_counts().to_dict(),
        "em_rows": int((frame["status"] == "EM").sum()),
        "em_observable": int(len(em_obs)),
        "em_observable_unique_tickers": int(em_obs["ticker"].nunique()),
        "em_observable_unique_dates": int(em_obs["date_idx"].nunique()),
        "growth_quantiles": {str(q): float(em_obs["revenue_growth_yoy"].quantile(q))
                             for q in (0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)},
        "growth_bucket_counts": _buckets(em_obs),
        "period_label_counts": em_obs["period_label"].value_counts().to_dict(),
        "revenue_tag_counts": em_obs["revenue_tag"].value_counts().to_dict(),
        "risk_counts": {
            "recent_dilution_20": int(em_obs["recent_dilution_20"].sum()),
            "financing_event_60d": int(em_obs["financing_event_60d"].sum()),
            "either": int((em_obs["recent_dilution_20"] | em_obs["financing_event_60d"]).sum()),
        },
        "em_negative_risk_observable": int((observable["status"] == "EM_NEGATIVE_RISK").sum()),
    }
    (out / "coverage.json").write_text(json.dumps(coverage, indent=1, sort_keys=True) + "\n",
                                       encoding="utf-8")
    print(json.dumps(coverage, indent=1, sort_keys=True), flush=True)
    print(f"out={out}", flush=True)


def _buckets(frame: pd.DataFrame) -> dict[str, int]:
    """The declaration's magnitude ladder, counted. Counts only - no outcome is read here."""
    edges = [(-float("inf"), 0.0, "NEGATIVE"), (0.0, 0.05, "0_5"), (0.05, 0.10, "5_10"),
             (0.10, 0.25, "10_25"), (0.25, 0.50, "25_50"), (0.50, float("inf"), "50_PLUS")]
    growth = frame["revenue_growth_yoy"]
    return {label: int(((growth >= low) & (growth < high)).sum()) for low, high, label in edges}


if __name__ == "__main__":
    main()
