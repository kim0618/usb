"""Run the Strategy E E-R3 Trading V1.1 development replay under the frozen E-R2 protocol.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_r3

Read-only with respect to every shared store: no provider call, no writer lock. Every precheck
runs before the first return; a failed precheck stops the run with ``E-R3 BLOCKED — INTEGRITY``
and no performance number is computed. Running again over the same input writes ``repeat-N`` and
records whether every artifact was byte-identical.
"""

import argparse
from collections import Counter
from datetime import date
import hashlib
import io
import csv
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import numpy as np

from app.backtest.strategy_d_analog.source import read_set_digest
from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close, load_daily_rows
from app.backtest.strategy_e1_premarket import cohort as C, dataset as E1D
from app.backtest.strategy_e1_premarket.config import load_rules as load_e1_rules
from app.backtest.strategy_e_d6 import dataset as DS, run as R6
from app.backtest.strategy_e_d6.replay import evaluate_signals, session_frames
from app.backtest.strategy_e_r3 import build as B, run as R3
from app.backtest.workspace.discovery import resolve_workspace_root
from app.market.calendar import MarketCalendar
from app.strategy_e_v1_1 import context, decision, replay_protocol as RP

REPO_ROOT = Path(__file__).resolve().parents[3]
BLOCK = "E-R3 BLOCKED — INTEGRITY"
CHAIN = {**R6.CHAIN, "E-D6-RESULT": "0fb1edcf75756332c72c31e0c1e6f571b23736aa",
         "E-R1": "0b932e60e02cda20a21e459d95a4929d48ce6d6f",
         "E-R2": "fa4258e"}


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def blocked(reason: str) -> int:
    print(f"{BLOCK}: {reason}", file=sys.stderr)
    return 2


def ancestry() -> dict[str, bool]:
    return {name: subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor",
                                  commit, "HEAD"], capture_output=True).returncode == 0
            for name, commit in CHAIN.items()}


def trades_csv(replayed) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=R3.TRADE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for session in replayed:
        for record in session["records"]:
            writer.writerow({k: ("" if record.get(k) is None else
                                 (repr(record[k]) if isinstance(record[k], float) else record[k]))
                             for k in R3.TRADE_COLUMNS})
    return buffer.getvalue().encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy E E-R3 V1.1 development replay")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    started = time.time()
    calendar = MarketCalendar("America/New_York")

    # 1. frozen chain -----------------------------------------------------------------------
    try:
        checksums = R6.frozen_checksums()
        decision.load_rules()
        context.load_contract()
        protocol = RP.load_rules()
    except Exception as error:
        return blocked(f"frozen artifact: {error}")
    chain = ancestry()
    if not all(chain.values()):
        return blocked(f"chain ancestry {chain}")
    e1_rules, e0_rules = load_e1_rules(), load_e0_rules()
    if (e0_rules.checksum != e1_rules.e0_rules_checksum
            or e1_rules.checksum != protocol["upstream"]["e1_premarket_rules_canonical_sha256"]
            or e0_rules.checksum != protocol["upstream"]["e0_overnight_rules_canonical_sha256"]):
        return blocked("E0/E1 declarations moved")
    checksums.update(trading_v1_1=decision.RULES_CANONICAL_SHA256,
                     forward_context=context.CONTRACT_CANONICAL_SHA256,
                     replay_protocol=RP.RULES_CANONICAL_SHA256)
    log(f"frozen chain PASS; protocol {RP.RULES_CANONICAL_SHA256[:12]}")

    # 2. bound development input --------------------------------------------------------------
    data = protocol["dataset"]
    workspace_root = resolve_workspace_root(args.workspace_root)
    manifest = DS.load_manifest()
    legacy = DS.legacy_minute_hashes(workspace_root)
    view_root = REPO_ROOT / R6.VIEW_DIR / manifest.digest[:16]
    view = DS.build_view(workspace_root, manifest, view_root, legacy)
    last_file = max(max(re.findall(r"\d{4}-\d{2}-\d{2}", n)) for _, n, _ in manifest.files)
    dataset_ok = (view["tape_digest"] == data["minute_tape_digest"]
                  and view["tape_files"] == data["minute_files"]
                  and view["tape_symbols"] == data["minute_symbols"]
                  and view["legacy_minute_files"] == data["legacy_minute_files"]
                  and view["legacy_minute_digest"] == data["legacy_minute_digest"]
                  and date.fromisoformat(last_file) < RP.FORWARD_HOLDOUT_START
                  and "SPY" in manifest.symbols)
    if not dataset_ok:
        return blocked(f"dataset binding {view} last file {last_file}")
    daily, history = load_daily_rows(workspace_root, e0_rules)
    benchmark = load_benchmark_close(workspace_root, e0_rules.snapshot_id, history.panel.sessions)
    grid = list(history.panel.sessions)
    timeline = RP.timeline(calendar)
    if (history.freeze.freeze_digest != data["daily_freeze_digest"]
            or history.freeze.d_read_digest != data["daily_read_set_digest"]
            or [grid[0].isoformat(), grid[-1].isoformat(), len(grid)]
            != [data["grid"]["first"], data["grid"]["last"], data["grid"]["sessions"]]
            or grid[21:] != timeline or any(s >= RP.FORWARD_HOLDOUT_START for s in grid)):
        return blocked("daily snapshot or timeline differs from the protocol")
    log(f"dataset PASS: tape {manifest.digest[:16]}, {len(timeline)} sessions")

    # 3. V1 reproduction (E1 unchanged) -----------------------------------------------------
    cohort = C.build(manifest.symbols, view_root, decision_last=R6.decision_last(),
                     workers=args.workers, progress=log)
    raw = {"symbols": cohort.symbols.astype(str), "sessions": cohort.sessions.astype(str),
           **cohort.values}
    v1_rows = E1D.build(raw, daily, benchmark, e1_rules)
    v1_source = R6.canonical_sha256({"tape": manifest.digest,
                                     "daily_read_set": history.freeze.d_read_digest,
                                     "e1_rules": e1_rules.checksum})
    v1_signals = evaluate_signals(session_frames(v1_rows), v1_source)
    reproduction = {"universe_rows": len(v1_rows),
                    "h5_rows": sum(s.candidate_count for s in v1_signals)}
    if reproduction != R6.DOCUMENTED_DEVELOPMENT:
        return blocked(f"V1 reproduction {reproduction} != {R6.DOCUMENTED_DEVELOPMENT}")
    log(f"V1 reproduction MATCH {reproduction}")

    # 4. V1.1 universe -----------------------------------------------------------------------
    rows = B.build_rows(view_root, manifest.symbols, [s.isoformat() for s in timeline],
                        workers=args.workers, progress=log)
    panel = history.panel
    columns = {t: j for j, t in enumerate(panel.tickers)}
    index = {s: i for i, s in enumerate(grid)}
    dailies = B.daily_sessions(panel, grid, [index[s] for s in timeline], calendar)
    frames, origins = {}, {}
    for session in timeline:
        frame = B.frame_for(session, rows, dailies[session], columns,
                            spy_close_previous=float(benchmark[index[session] - 1]))
        if not frame.symbols:
            continue
        frames[session] = frame
        daily_s = dailies[session]
        origins[session] = {s: B.origin(bool(daily_s.has_open_d[columns[s]]),
                                        rows[s][session].has_0930_open) for s in frame.symbols}
    universe_rows = sum(len(f.symbols) for f in frames.values())
    log(f"V1.1 universe {universe_rows} rows over {len(frames)} sessions")

    # 5. attribution and structural delta (no returns) -----------------------------------------
    attribution = R3.attribute(v1_rows, frames, origins)
    delta = R3.structural_delta(frames, origins)
    v1_variant = delta["variants"]["V1"]
    attribution["v1_variant_matches_e_d6"] = (
        v1_variant["universe_rows"] == R6.DOCUMENTED_DEVELOPMENT["universe_rows"]
        and v1_variant["h5_candidates"] == R6.DOCUMENTED_DEVELOPMENT["h5_rows"]
        and v1_variant["selected_candidates"] == R3.E_D6_SELECTED)
    if not (attribution["pass"] and attribution["v1_variant_matches_e_d6"]):
        return blocked(f"V1.1 differs from V1 beyond corrections A/B: {attribution}")
    log(f"attribution PASS; rows by origin {delta['rows_by_origin']}")

    # 6. PIT poison ------------------------------------------------------------------------------
    source_digest = R6.canonical_sha256({"tape": manifest.digest,
                                         "daily_read_set": history.freeze.d_read_digest,
                                         "trading_v1_1": decision.RULES_CANONICAL_SHA256})
    seals = {s: decision.seal(f, source_digest=source_digest) for s, f in frames.items()}
    sample = B.pit_sample(manifest.symbols)
    poisoned = B.poison_rows(view_root, sample, [s.isoformat() for s in timeline],
                             workers=args.workers)
    poison_daily = B.daily_sessions(panel, grid, [index[s] for s in timeline], calendar,
                                    poison=True)
    pit = {"sample_symbols": len(sample), "modes": {}, "daily_rows_after_d_poisoned": True}
    daily_same = all(np.array_equal(poison_daily[s].eligible, dailies[s].eligible) for s in timeline)
    for mode in ("noise", "delete"):
        swapped = dict(rows)
        for symbol in sample:
            swapped[symbol] = {d: B.SymbolRow(row, False, {}, {})
                               for d, row in poisoned[symbol][mode].items() if row is not None}
        moved, compared = [], 0
        for session in timeline:
            frame = B.frame_for(session, swapped, poison_daily[session], columns,
                                spy_close_previous=float(benchmark[index[session] - 1]))
            base = seals.get(session)
            if not frame.symbols and base is None:
                continue
            compared += 1
            again = decision.seal(frame, source_digest=source_digest)
            if base is None or again.seal_digest != base.seal_digest:
                moved.append(session.isoformat())
        rows_compared = sum(len(v) for s in sample for v in [poisoned[s][mode]])
        pit["modes"][mode] = {"sessions_compared": compared, "seal_digests_moved": len(moved),
                              "examples": moved[:5], "sample_rows": rows_compared}
    pit["daily_poison_eligibility_unchanged"] = daily_same
    pit["verdict"] = "PASS" if daily_same and all(
        m["seal_digests_moved"] == 0 for m in pit["modes"].values()) else "FAIL"
    if pit["verdict"] != "PASS":
        return blocked(f"PIT poison moved the decision: {pit}")
    log(f"PIT poison PASS {pit['modes']}")

    # 7. replay (first return computed here) ------------------------------------------------------
    replayed = [R3.replay_sealed(seals[s], frames[s], rows, origins[s], calendar) for s in frames]
    reseals = {s: decision.seal(f, source_digest=source_digest) for s, f in frames.items()}
    again = [R3.replay_sealed(reseals[s], frames[s], rows, origins[s], calendar) for s in frames]
    deterministic = R6.replay_digest(replayed) == R6.replay_digest(again)
    sessions = [s.isoformat() for s in timeline]
    final_view = DS.verify_view(view_root, manifest)
    final_read = read_set_digest(workspace_root, e0_rules.snapshot_id)
    integrity = {"frozen_checksums": checksums, "chain_ancestry": chain,
                 "dataset_binding": dataset_ok, "tape_unchanged_after_run": final_view,
                 "daily_inputs_unchanged": final_read == history.freeze.d_read_digest,
                 "v1_reproduced": True, "attribution": attribution["pass"],
                 "pit_poison": pit["verdict"], "in_process_replay_deterministic": deterministic}
    integrity_pass = (final_view and integrity["daily_inputs_unchanged"] and deterministic)
    evaluation = R6.evaluate(replayed, sessions, universe_rows, integrity=integrity_pass)
    e_d6_label = evaluation["primary_gate"]["verdict"]
    label = protocol["verdict_labels"][e_d6_label]
    log(f"verdict {label}")

    identity_parts = {**checksums, "tape": manifest.digest,
                      "legacy_minute": view["legacy_minute_digest"],
                      "daily_freeze": history.freeze.freeze_digest,
                      "daily_read_set": history.freeze.d_read_digest,
                      "e1_rules": e1_rules.checksum, "e0_rules": e0_rules.checksum,
                      "code": R3.code_digest()}
    identity_digest = R6.canonical_sha256(identity_parts)
    run_id = f"er3-{identity_digest[:12]}"
    entry_invalid = R3.entry_invalid_diagnostics(replayed, {s.isoformat(): v for s, v in seals.items()})
    comparison = R3.v1_comparison(evaluation, delta)
    trades = trades_csv(replayed)
    daily_returns = R6.daily_csv(replayed, sessions)
    result = {
        "version": R3.RESULT_VERSION, "run_id": run_id, "identity": identity_parts,
        "identity_digest": identity_digest,
        "dataset": {"role": protocol["declaration"]["dataset_role"],
                    "minute_tape": {"digest": manifest.digest, "files": len(manifest.files),
                                    "symbols": len(manifest.symbols),
                                    "legacy_minute_files": view["legacy_minute_files"],
                                    "legacy_minute_digest": view["legacy_minute_digest"],
                                    "last_bound_file_date": last_file},
                    "daily": {"snapshot_id": history.freeze.snapshot_id,
                              "freeze_digest": history.freeze.freeze_digest,
                              "read_set_digest": history.freeze.d_read_digest,
                              "grid_first": grid[0].isoformat(), "grid_last": grid[-1].isoformat(),
                              "grid_sessions": len(grid)},
                    "evaluation_sessions": len(sessions), "evaluation_first": sessions[0],
                    "evaluation_last": sessions[-1],
                    "sessions_with_universe_rows": len(frames),
                    "v1_1_universe_rows": universe_rows,
                    "v1_1_universe_symbols": len({s for f in frames.values() for s in f.symbols}),
                    "sessions_by_year": dict(sorted(Counter(s[:4] for s in sessions).items()))},
        "prechecks": {"v1_reproduction": {"found": reproduction,
                                          "documented": R6.DOCUMENTED_DEVELOPMENT, "match": True},
                      "attribution": attribution, "pit_poison": pit},
        "integrity": {**integrity, "pass": integrity_pass},
        "structural_delta": delta,
        "entry_invalid": entry_invalid,
        **evaluation,
        "verdict": {"e_d6_function_output": e_d6_label, "label": label},
        "v1_vs_v1_1": comparison,
        "evidence_layers": R3.evidence_layers(evaluation, label),
        "artifacts": {"trades.csv": hashlib.sha256(trades).hexdigest(),
                      "daily_returns.csv": hashlib.sha256(daily_returns).hexdigest()},
    }
    result_bytes = R6.canonical_json(result)
    result_digest = hashlib.sha256(result_bytes).hexdigest()
    files = {
        "result.json": result_bytes, "trades.csv": trades, "daily_returns.csv": daily_returns,
        "funnel.json": R6.canonical_json(result["funnel"]),
        "structural_delta.json": R6.canonical_json({"structural_delta": delta,
                                                    "entry_invalid": entry_invalid,
                                                    "attribution": attribution}),
        "cost_scenarios.json": R6.canonical_json(result["scenarios"]),
        "bootstrap.json": R6.canonical_json(result["primary_gate"]["bootstrap"]),
        "monthly.json": R6.canonical_json(result["stability"]["month"]),
        "quarterly.json": R6.canonical_json(result["stability"]["quarter"]),
        "buckets.json": R6.canonical_json(result["buckets"]),
        "concentration.json": R6.canonical_json(result["concentration"]),
    }
    base = REPO_ROOT / RP.RUNTIME_ROOT / run_id
    complete = base / "COMPLETE.json"
    if complete.exists():
        first = json.loads(complete.read_text(encoding="utf-8"))
        run_dir = base / f"repeat-{2 + sum(1 for p in base.glob('repeat-*') if p.is_dir())}"
    else:
        first, run_dir = None, base
    digests = R6.write_artifacts(run_dir, files)
    manifest_out = {"started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                    "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "git_head": R6.git_head(), "workspace_root": str(workspace_root),
                    "view_root": str(view_root), "workers": args.workers,
                    "provider_calls": 0, "writer_lock_taken": False, "artifacts": digests,
                    "result_digest": result_digest}
    (run_dir / "run_manifest.json").write_bytes(R6.canonical_json(manifest_out))
    summary = {"run_id": run_id, "result_digest": result_digest, "artifacts": digests,
               "verdict": label}
    if first is None:
        (run_dir / "COMPLETE.json").write_bytes(R6.canonical_json(summary))
    else:
        check = {"attempt": run_dir.name, "first_result_digest": first["result_digest"],
                 "this_result_digest": result_digest,
                 "identical": first["result_digest"] == result_digest,
                 "artifacts_identical": first["artifacts"] == digests}
        (run_dir / "REPEAT_CHECK.json").write_bytes(R6.canonical_json(check))
        log(f"repeat check {check}")
    log(f"artifacts {run_dir} in {time.time() - started:.0f}s")
    print(f"{label} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
