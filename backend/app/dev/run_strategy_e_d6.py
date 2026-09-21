"""Run the Strategy E E-D6 development trading backtest.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_d6

Read-only with respect to every shared store: no provider call, no writer lock, nothing written
under the minute or daily directories. The minute input is the E1 development tape, read through
a symlink view of exactly its bound files. Running the command again over the same input writes
a ``repeat-N`` directory and records whether the result digest was identical.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_d_analog.source import read_set_digest
from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close, load_daily_rows
from app.backtest.strategy_e1_premarket import cohort as C, dataset as D
from app.backtest.strategy_e1_premarket.config import load_rules as load_e1_rules
from app.backtest.strategy_e_d6 import dataset as DS, run as R
from app.backtest.strategy_e_d6.replay import evaluate_signals, session_frames
from app.backtest.workspace.discovery import resolve_workspace_root
from app.market.calendar import MarketCalendar

REPO_ROOT = Path(__file__).resolve().parents[3]


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy E E-D6 development trading backtest")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    started = time.time()

    # 1. frozen chain -----------------------------------------------------------------------
    try:
        checksums = R.frozen_checksums()
    except Exception as error:  # every loader fails closed with its own error type
        print(f"E-D6 BLOCKED frozen artifact: {error}", file=sys.stderr)
        return 2
    ancestry = R.chain_ancestry()
    if not all(ancestry.values()):
        print(f"E-D6 BLOCKED chain ancestry: {ancestry}", file=sys.stderr)
        return 2
    e1_rules, e0_rules = load_e1_rules(), load_e0_rules()
    if e0_rules.checksum != e1_rules.e0_rules_checksum:
        print("E-D6 BLOCKED the E0 universe declaration moved", file=sys.stderr)
        return 2
    log(f"frozen chain PASS; E-D6 rules {R.RULES_CANONICAL_SHA256[:12]}")

    # 2. bound development input ---------------------------------------------------------------
    workspace_root = resolve_workspace_root(args.workspace_root)
    manifest = DS.load_manifest()
    legacy = DS.legacy_minute_hashes(workspace_root)
    view_root = REPO_ROOT / R.VIEW_DIR / manifest.digest[:16]
    view = DS.build_view(workspace_root, manifest, view_root, legacy)
    log(f"development tape view {view['tape_digest'][:16]}: {view['tape_files']} files, "
        f"{view['tape_symbols']} symbols, {view['legacy_minute_files']} legacy files")

    daily, history = load_daily_rows(workspace_root, e0_rules)
    benchmark = load_benchmark_close(workspace_root, e0_rules.snapshot_id, history.panel.sessions)
    log(f"daily rows {len(daily)}; snapshot {history.freeze.snapshot_id}")

    cohort = C.build(manifest.symbols, view_root, decision_last=R.decision_last(),
                     workers=args.workers, progress=log)
    raw = {"symbols": cohort.symbols.astype(str), "sessions": cohort.sessions.astype(str),
           **cohort.values}
    rows = D.build(raw, daily, benchmark, e1_rules)
    frames = session_frames(rows)
    source_digest = R.canonical_sha256({"tape": manifest.digest,
                                        "daily_read_set": history.freeze.d_read_digest,
                                        "e1_rules": e1_rules.checksum})
    signals = evaluate_signals(frames, source_digest)
    reproduction = {"universe_rows": len(rows),
                    "h5_rows": sum(s.candidate_count for s in signals)}
    reproduced = reproduction == R.DOCUMENTED_DEVELOPMENT
    log(f"universe {reproduction['universe_rows']} rows, H5 {reproduction['h5_rows']} "
        f"(documented {R.DOCUMENTED_DEVELOPMENT}) -> {'MATCH' if reproduced else 'MISMATCH'}")
    if not reproduced:
        print("E-D6 BLOCKED the development input does not reproduce the E1-H5 counts",
              file=sys.stderr)
        return 2

    pit = R.pit_sample(view_root, manifest.symbols)
    log(f"PIT decision boundary {pit['verdict']} ({pit['sessions_compared']} sessions)")

    # 3. replay ----------------------------------------------------------------------------------
    wanted = R.tape_jobs(frames, signals)
    bars = R.fetch_candidate_bars(view_root, wanted, workers=args.workers)
    log(f"exact bars for {len(wanted)} candidate symbols")
    calendar = MarketCalendar("America/New_York")
    replayed = R.replay_all(frames, signals, bars, calendar)
    again = R.replay_all(frames, evaluate_signals(frames, source_digest), bars, calendar)
    deterministic = R.replay_digest(replayed) == R.replay_digest(again)
    sessions = R.timeline(list(history.panel.sessions), frames, calendar)

    final_view = DS.verify_view(view_root, manifest)
    final_read = read_set_digest(workspace_root, e0_rules.snapshot_id)
    integrity = {
        "frozen_checksums": checksums, "chain_ancestry": ancestry,
        "development_tape_bound": True, "development_tape_unchanged_after_run": final_view,
        "daily_read_set_at_load": history.freeze.d_read_digest,
        "daily_read_set_after_run": final_read,
        "daily_inputs_unchanged": final_read == history.freeze.d_read_digest,
        "e1_h5_development_counts_reproduced": reproduced,
        "pit_decision_boundary": pit["verdict"], "in_process_replay_deterministic": deterministic,
    }
    integrity_pass = (final_view and integrity["daily_inputs_unchanged"] and reproduced
                      and pit["verdict"] == "PASS" and deterministic)
    evaluation = R.evaluate(replayed, sessions, len(rows), integrity=integrity_pass)
    log(f"verdict {evaluation['primary_gate']['verdict']}")

    identity_parts = {**checksums, "tape": manifest.digest,
                      "legacy_minute": view["legacy_minute_digest"],
                      "daily_freeze": history.freeze.freeze_digest,
                      "daily_read_set": history.freeze.d_read_digest,
                      "e1_rules": e1_rules.checksum, "e0_rules": e0_rules.checksum,
                      "code": R.code_digest()}
    identity_digest = R.canonical_sha256(identity_parts)
    run_id = f"ed6-{identity_digest[:12]}"

    trades = R.trades_csv(replayed)
    daily_returns = R.daily_csv(replayed, sessions)
    result = {
        "version": R.RESULT_VERSION, "run_id": run_id, "identity": identity_parts,
        "identity_digest": identity_digest,
        "dataset": {
            "role": "DEVELOPMENT (E1 bound tape); previously used in Strategy E research; not OOS",
            "minute_tape": {"digest": manifest.digest, "files": len(manifest.files),
                            "symbols": len(manifest.symbols),
                            "manifest": "docs/backtest/strategy_e_candidate/"
                                        "strategy_e_d6_development_tape_v1.json",
                            "legacy_minute_files": view["legacy_minute_files"],
                            "legacy_minute_digest": view["legacy_minute_digest"]},
            "daily": {"snapshot_id": history.freeze.snapshot_id,
                      "freeze_digest": history.freeze.freeze_digest,
                      "read_set_digest": history.freeze.d_read_digest,
                      "grid_first": history.panel.sessions[0].isoformat(),
                      "grid_last": history.panel.sessions[-1].isoformat(),
                      "grid_sessions": len(history.panel.sessions)},
            "premarket_symbol_sessions": int(len(cohort.sessions)),
            "cohort_report": {k: v for k, v in cohort.report.items()},
            "universe_rows": len(rows), "universe_sessions": len(frames),
            "universe_symbols": int(len(set(rows.tickers.tolist()))),
            "universe_counters": rows.counters,
            "evaluation_sessions": len(sessions),
            "evaluation_first": sessions[0], "evaluation_last": sessions[-1],
            "sessions_with_universe_rows": len(frames),
            "sessions_by_year": dict(sorted(Counter(s[:4] for s in sessions).items())),
        },
        "integrity": {**integrity, "pass": integrity_pass},
        "pit_audit": pit,
        "research_reproduction": {"found": reproduction, "documented": R.DOCUMENTED_DEVELOPMENT,
                                  "match": reproduced},
        **evaluation,
        "artifacts": {"trades.csv": R.hashlib.sha256(trades).hexdigest(),
                      "daily_returns.csv": R.hashlib.sha256(daily_returns).hexdigest()},
    }
    result_bytes = R.canonical_json(result)
    result_digest = R.hashlib.sha256(result_bytes).hexdigest()
    files = {
        "result.json": result_bytes, "trades.csv": trades, "daily_returns.csv": daily_returns,
        "funnel.json": R.canonical_json(result["funnel"]),
        "cost_scenarios.json": R.canonical_json(result["scenarios"]),
        "monthly.json": R.canonical_json(result["stability"]["month"]),
        "quarterly.json": R.canonical_json(result["stability"]["quarter"]),
        "buckets.json": R.canonical_json(result["buckets"]),
        "concentration.json": R.canonical_json(result["concentration"]),
    }

    base = REPO_ROOT / R.RUNS_DIR / run_id
    complete = base / "COMPLETE.json"
    if complete.exists():
        first = json.loads(complete.read_text(encoding="utf-8"))
        attempt = 2 + sum(1 for p in base.glob("repeat-*") if p.is_dir())
        run_dir = base / f"repeat-{attempt}"
    else:
        first, run_dir = None, base
    digests = R.write_artifacts(run_dir, files)
    context = {"started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
               "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "git_head": R.git_head(), "workspace_root": str(workspace_root),
               "view_root": str(view_root), "workers": args.workers,
               "provider_calls": 0, "writer_lock_taken": False}
    (run_dir / "run_context.json").write_bytes(R.canonical_json(context))
    summary = {"run_id": run_id, "result_digest": result_digest, "artifacts": digests,
               "verdict": evaluation["primary_gate"]["verdict"]}
    if first is None:
        (run_dir / "COMPLETE.json").write_bytes(R.canonical_json(summary))
    else:
        check = {"attempt": run_dir.name, "first_result_digest": first["result_digest"],
                 "this_result_digest": result_digest,
                 "identical": first["result_digest"] == result_digest,
                 "artifacts_identical": first["artifacts"] == digests}
        (run_dir / "REPEAT_CHECK.json").write_bytes(R.canonical_json(check))
        log(f"repeat check {check}")
    log(f"artifacts {run_dir} in {time.time() - started:.0f}s")
    print(f"E-D6 {evaluation['primary_gate']['verdict']} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
