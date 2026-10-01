"""H-V2-D4-E7R: the E7 detector replay over every stored D4 final output. Offline, 0 calls, $0.

What this answers, and why it has to be a replay rather than an argument. The repaired detector is
only an improvement if it removes the two false positives this corpus actually produced AND leaves
every genuine decision leak in place. The second half cannot be shown by fixtures, because a fixture
only contains what its author thought of. So the new semantics are run over the same bytes every
graded run was scored on - D4.1, D4-S1, Tier A V2/V3, Tier B and D4-BR-C - and the old vocabulary is
run beside it so the difference is reported per finding rather than asserted in prose.

The old detector is reconstructed here verbatim from `audit_strategy_h_v2_d4_1` as it stood at commit
2e34e36, because a comparison against a detector that no longer exists is the only way to say which
findings were removed and which are new. It is a frozen literal for comparison and is wired to
nothing.

Changes no verdict. D4-BR-C stays FAIL - `HISTORICAL` carries that as a literal and a test fails if
it moves.
"""

from __future__ import annotations

from pathlib import Path
import json
import re
import sys

from app.backtest.strategy_h_v2.research.validation_v2 import decision_leakage_findings

ANALYSES_GLOB = "data/runtime/strategy_h_v2/**/*.json"
"""Deliberately the whole runtime tree rather than a path per stage. Coverage then does not
depend on remembering where a stage stored its records - a D4 analysis record is identified by
carrying `analysis_id`, and `records_without_final_output` reports the ones E7 has nothing to
measure on rather than silently dropping them."""

#: The pre-E7R vocabulary, exactly as `audit_strategy_h_v2_d4_1.DECISION_VOCABULARY` read before this
#: step retired it. Frozen for comparison; nothing in the pipeline uses it.
OLD_DECISION_VOCABULARY = tuple(re.compile(rf"\b{p}\b", re.I) for p in (
    "approve", "reject", "buy", "sell", "overvalued", "undervalued", "fair value",
    "intrinsic value", "price target", "target price", "cheap", "expensive", "attractive entry",
    "position size", "we recommend", "recommendation",
))

HISTORICAL = {
    "d4_br_c_verdict": "FAIL",
    "d4_br_c_e7_findings": 1,
    "d4_br_c_e7_ticker": "COLL",
    "d5_authorization": "NOT READY",
}
"""Asserted, never recomputed. E7R is an offline detector repair and does not regrade a live run."""

#: Mirrors `audit_strategy_h_v2_d4_1.NON_PROSE_KEYS` by import so the replay walks exactly the
#: strings the audit walks. A replay over a different set of fields would measure a different gate.
from app.dev.audit_strategy_h_v2_d4_1 import NON_PROSE_KEYS, iter_prose  # noqa: E402


def _old_findings(output: dict) -> list[dict]:
    found: list[dict] = []
    for path, text in iter_prose(output):
        for pattern in OLD_DECISION_VOCABULARY:
            if pattern.search(text):
                found.append({"path": path, "match": pattern.pattern, "text": text[:200]})
                break
    return found


def _new_findings(output: dict) -> list[dict]:
    found: list[dict] = []
    for path, text in iter_prose(output):
        for finding in decision_leakage_findings(text):
            found.append({"path": path, "match": finding.term, "context": finding.context,
                          "text": text[:200]})
            break
    return found


def replay(glob_pattern: str = ANALYSES_GLOB) -> dict:
    records: list[tuple[Path, dict]] = []
    skipped: list[dict] = []
    for path in sorted(Path(".").glob(glob_pattern)):
        if path.name == "expectation_evidence.json":
            continue
        try:
            record = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(record, dict) or "analysis_id" not in record:
            continue
        if record.get("final_output"):
            records.append((path, record))
        else:
            skipped.append({"run_id": record.get("analysis_run_id"),
                            "ticker": record.get("ticker"),
                            "final_status": record.get("final_status")})

    rows: list[dict] = []
    for path, record in records:
        output = record["final_output"]
        old = _old_findings(output)
        new = _new_findings(output)
        old_paths = {(f["path"], f["text"]) for f in old}
        new_paths = {(f["path"], f["text"]) for f in new}
        rows.append({
            "run_id": record.get("analysis_run_id"),
            "ticker": record.get("ticker"),
            "file": str(path),
            "old_findings": len(old),
            "new_findings": len(new),
            "removed": [f for f in old if (f["path"], f["text"]) not in new_paths],
            "newly_exposed": [f for f in new if (f["path"], f["text"]) not in old_paths],
            "retained": [f for f in new if (f["path"], f["text"]) in old_paths],
        })

    return {
        "schema": "H_V2_D4_E7R_REPLAY_V1",
        "live_calls": 0,
        "live_cost_usd": 0.0,
        "total_outputs": len(rows),
        "d4_analysis_records_seen": len(rows) + len(skipped),
        "records_without_final_output": skipped,
        "runs": sorted({r["run_id"] for r in rows if r["run_id"]}),
        "old_e7_findings": sum(r["old_findings"] for r in rows),
        "new_e7_findings": sum(r["new_findings"] for r in rows),
        "false_positives_removed": sum(len(r["removed"]) for r in rows),
        "newly_exposed_violations": sum(len(r["newly_exposed"]) for r in rows),
        "retained_violations": sum(len(r["retained"]) for r in rows),
        "historical": HISTORICAL,
        "rows": rows,
    }


INITIAL_RESPONSE_DIAGNOSTIC = (
    ("Nothing here is a valuation, a price target or a decision.",
     "COLL initial response, the sentence that cost it a repair round"),
    ("The flat pre-event return suggests the guidance cut was not anticipated in the price "
     "beforehand: there was neither a run-up nor a sell-off before the event.",
     "COLL final output, the sentence that failed E7"),
)
"""§10. Diagnostic only. The repair-round history of run D4_BR_C-20261001T005758Z is what the run
recorded and this step does not retroactively reduce it: the model did spend that round, and a
detector repaired afterwards does not give the money back."""


def main() -> None:
    report = replay()
    out = Path("data/runtime/strategy_h_v2/d4_br_c/E7R_REPLAY.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2, default=str))
    print("\n--- §10 initial-response diagnostic, offline, changes no historical count ---")
    for sentence, label in INITIAL_RESPONSE_DIAGNOSTIC:
        findings = decision_leakage_findings(sentence)
        print(f"  {'ALLOWED' if not findings else 'VIOLATION'}  {label}")
    for row in report["rows"]:
        if row["removed"] or row["newly_exposed"]:
            print(f"\n{row['ticker']} ({row['run_id']})")
            for f in row["removed"]:
                print(f"  REMOVED  {f['match']}  {f['path']}")
                print(f"           {f['text'][:150]}")
            for f in row["newly_exposed"]:
                print(f"  NEW      {f['match']}  {f['path']}")
                print(f"           {f.get('context', f['text'])[:150]}")
    if report["newly_exposed_violations"]:
        print("\nNEWLY EXPOSED VIOLATIONS PRESENT", file=sys.stderr)


if __name__ == "__main__":
    main()
