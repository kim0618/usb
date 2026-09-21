"""The M_ONLY source: the frozen C-E0 status table, accepted only if it matches C-E0 exactly.

Accepted A is the byte copy of `candidate_status.parquet` from C-E0 run ce01-234478e8f7ff27570e13.
Accepted B is a status table recomputed by the unmodified strategy_c_e0 code over an EDGAR store.
Either way the (date, ticker) rows must be the reproduced C-M0 primary rows and the status counts
must equal the counts C-E0 published. Anything else is BLOCKED: no outcome is computed.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path

import pandas as pd

BLOCKED = "BLOCKED_M_ONLY_SOURCE"


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    source: str | None
    sha256: str | None
    counts: dict[str, int]
    status: pd.DataFrame | None


def verify(path: Path | None, baseline_rows: pd.DataFrame, published: dict[str, int],
           source_kind: str = "A", run_id: str | None = None) -> Verdict:
    """``baseline_rows`` has signal_date and ticker of the reproduced C-M0 primary rows.

    With ``run_id`` (source A), the run's own ``summary.json`` must sit next to the table, name
    that run and carry the same status counts, so a status file of another C-E0 run is refused.
    """
    if path is None or not Path(path).exists():
        return Verdict(False, f"{BLOCKED}: status table not found at {path}", None, None, {}, None)
    body = Path(path).read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    status = pd.read_parquet(path)
    missing_cols = {"signal_date", "ticker", "status"} - set(status.columns)
    if missing_cols:
        return Verdict(False, f"{BLOCKED}: columns missing {sorted(missing_cols)}", str(path), sha, {}, None)
    counts = {k: int(v) for k, v in status["status"].value_counts().items()}
    full = {k: counts.get(k, 0) for k in published}
    extra = set(counts) - set(published)
    if extra or full != published:
        return Verdict(False, f"{BLOCKED}: status counts {counts} != published {published}", str(path), sha,
                       counts, None)
    if status.duplicated(["signal_date", "ticker"]).any():
        return Verdict(False, f"{BLOCKED}: duplicate (signal_date, ticker) rows", str(path), sha, counts, None)
    if run_id is not None:
        summary_path = Path(path).parent / "summary.json"
        if not summary_path.exists():
            return Verdict(False, f"{BLOCKED}: {summary_path} missing; copy the run's summary.json with the table",
                           str(path), sha, counts, None)
        import json
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("run_id") != run_id:
            return Verdict(False, f"{BLOCKED}: summary run_id {summary.get('run_id')} != {run_id}", str(path), sha,
                           counts, None)
        stored = {k: int(v) for k, v in (summary.get("status_counts") or {}).items()}
        if {k: stored.get(k, 0) for k in published} != published or set(stored) - set(published):
            return Verdict(False, f"{BLOCKED}: summary status_counts {stored} != published {published}", str(path),
                           sha, counts, None)
    mine = set(zip(baseline_rows["signal_date"], baseline_rows["ticker"]))
    theirs = set(zip(status["signal_date"], status["ticker"]))
    if mine != theirs:
        return Verdict(False, f"{BLOCKED}: rows differ from the reproduced C-M0 table "
                              f"(only_baseline={len(mine - theirs)}, only_status={len(theirs - mine)})",
                       str(path), sha, counts, None)
    return Verdict(True, f"ACCEPTED_{source_kind}", str(path), sha, counts,
                   status[["signal_date", "ticker", "status"]].copy())
