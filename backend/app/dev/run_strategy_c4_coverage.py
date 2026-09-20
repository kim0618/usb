"""C4-P1 read-only coverage audit: how much of the analog library can carry F1/F2/F3 context.

Reads the frozen C raw cache and the C-E0 / EQM stores. Writes no store and fetches nothing:
its only job is to say whether the declared coverage gate can be met with what already exists,
and exactly which CIKs an incremental fetch would need.
"""

from datetime import date, timedelta
import json
from pathlib import Path
import sys
import time

import numpy as np

from app.backtest.strategy_c4_analog import universe
from app.backtest.strategy_c4_analog.rules import load_rules
from app.backtest.strategy_c_selection.features import compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.rules import load_rules as load_c_rules

OUT = Path("data/runtime/strategy_c4/coverage")
CACHE = Path("data/runtime/strategy_c4/cache")
SEC_ROOT = Path("data/runtime/strategy_c/e0/raw")
XBRL_ROOT = Path("data/runtime/strategy_eqm/v0/raw")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> int:
    started = time.perf_counter()
    rules = load_rules()
    c_rules = load_c_rules()
    root = universe.C_RAW_ROOT
    sessions = universe.usable_sessions(root, universe.all_sessions(root))
    log(f"sessions {sessions[0]}..{sessions[-1]} n={len(sessions)}")
    snapshots = universe.load_reference(root, universe.SNAPSHOT_DATES, c_rules.allowed_exchanges)
    log(f"snapshots {[s.as_of.isoformat() for s in snapshots]}")

    cache = CACHE / "panel_vw.npz"
    panel, vw = universe.load_panel_and_vw(root, sessions, snapshots, universe.SPLIT_RANGE)
    log(f"panel {panel.shape} splits={len(panel.splits)} elapsed={time.perf_counter() - started:.0f}s")
    np.savez_compressed(cache, vw=vw)

    features = compute(panel, c_rules)
    labels = compute_labels(panel, (5, 10), c_rules.ca_ratio)
    base = universe.base_eligible(features, c_rules)
    library_mask = base & ~features.ca_excluded & labels.valid(5) & labels.valid(10)
    t, n = panel.shape
    idx = np.arange(t)[:, None]
    in_library_window = (idx >= rules.library_start_idx) & (idx <= rules.last_query_idx)
    library_mask = library_mask & in_library_window
    log(f"library rows (no context yet) = {int(library_mask.sum())}")

    cik_code, cik_names = universe.cik_codes(panel, snapshots)
    figi = universe.entity_codes(panel, snapshots)
    rows_i, rows_j = np.nonzero(library_mask)
    row_cik = cik_code[rows_i, rows_j]
    row_figi = figi[rows_i, rows_j]

    stored = {p.name[3:] for p in (SEC_ROOT / "submissions").glob("CIK*") if p.is_dir()}
    xbrl_stored = {p.name[3:-8] for p in (XBRL_ROOT / "companyfacts").glob("CIK*.json.gz")}
    needed = sorted({cik_names[c] for c in np.unique(row_cik) if c >= 0})
    missing_sec = sorted(set(needed) - stored)

    vw_ok = np.isfinite(vw) & (vw > 0)
    vw_share = float(vw_ok[rows_i, rows_j].mean())

    report = {
        "generated_at": date.today().isoformat(),
        "rules_checksum": rules.checksum,
        "grid": {"first": sessions[0].isoformat(), "last": sessions[-1].isoformat(), "n": len(sessions)},
        "panel": {"tickers": n, "sessions": t},
        "library_rows_before_context": int(library_mask.sum()),
        "library_unique_tickers": int(len(np.unique(rows_j))),
        "library_unique_dates": int(len(np.unique(rows_i))),
        "cik_mapping": {
            "rows_with_cik": int((row_cik >= 0).sum()),
            "rows_without_cik": int((row_cik < 0).sum()),
            "share_with_cik": float((row_cik >= 0).mean()),
            "unique_ciks_needed": len(needed),
        },
        "figi": {"rows_with_figi": int((row_figi >= 0).sum()),
                 "share_with_figi": float((row_figi >= 0).mean())},
        "sec_submissions_store": {
            "ciks_stored": len(stored),
            "ciks_needed": len(needed),
            "ciks_missing": len(missing_sec),
            "rows_covered_by_store": int(np.isin(
                row_cik, [i for i, name in enumerate(cik_names) if name in stored]).sum()),
        },
        "xbrl_store": {"ciks_stored": len(xbrl_stored)},
        "vw_coverage_share_on_library_rows": vw_share,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "coverage_stage1.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    (OUT / "missing_sec_ciks.json").write_text(json.dumps(missing_sec) + "\n", encoding="utf-8")
    (OUT / "needed_ciks.json").write_text(json.dumps(needed) + "\n", encoding="utf-8")
    np.savez_compressed(CACHE / "masks.npz", library_mask=library_mask, cik_code=cik_code,
                        figi=figi, base=base, ca_excluded=features.ca_excluded,
                        valid5=labels.valid(5), valid10=labels.valid(10))
    log(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
