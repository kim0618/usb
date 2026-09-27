"""Strategy G0 - after-hours to next-session premarket pre-validation.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_g0 coverage
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_g0 build      (after the freeze)
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_g0 run        (after the freeze)

``coverage`` reads counts and traded value only and computes no return; it is the step that
comes before the rules freeze. ``build`` and ``run`` refuse to start unless the declaration hashes
to the checksum frozen in ``strategy_g0_after_premarket.config``.

Read-only with respect to every shared store: no provider call, no writer lock, nothing written
under ``market_data``. Artifacts go to ``data/runtime/strategy_g_candidate``.
"""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import time

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME = REPO_ROOT / "data/runtime/strategy_g_candidate"
CACHE = RUNTIME / "cache"


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def _json_default(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value))


def _daily(workspace_root: Path):
    from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
    from app.backtest.strategy_e0_overnight.dataset import load_daily_rows
    e0_rules = load_e0_rules()
    daily, history = load_daily_rows(workspace_root, e0_rules)
    return e0_rules, daily, history


def cmd_coverage(args) -> int:
    from app.backtest.strategy_g0_after_premarket import coverage as COV
    from app.backtest.strategy_g0_after_premarket import pairing
    from app.backtest.strategy_g0_after_premarket.tape import STAGING_REL
    from app.backtest.workspace.discovery import resolve_workspace_root
    from app.market.calendar import MarketCalendar

    workspace_root = resolve_workspace_root(args.workspace_root)
    staging_root = None if args.no_staging else REPO_ROOT / STAGING_REL
    CACHE.mkdir(parents=True, exist_ok=True)

    started = time.time()
    e0_rules, daily, history = _daily(workspace_root)
    grid = list(history.panel.sessions)
    log(f"daily rows {len(daily)} on {len(grid)} sessions ({grid[0]}..{grid[-1]})")

    calendar = MarketCalendar()
    pairs = pairing.build_pairs(grid, calendar)
    pair_audit = pairing.audit(pairs, calendar)
    # only sources that the daily PIT universe can evaluate (warmup and the last session excluded)
    usable = pairs[e0_rules.first_index:e0_rules.last_index + 1]
    log(f"pairs {len(pairs)} ({pair_audit['verdict']}), usable sources {len(usable)}")

    symbols = COV.symbols_in(workspace_root, staging_root)
    log(f"symbols on tape {len(symbols)}")
    table_path = CACHE / "coverage_day_table.npz"
    if table_path.exists() and not args.rebuild:
        table = COV.load(table_path)
        meta = json.loads((CACHE / "coverage_meta.json").read_text(encoding="utf-8"))
    else:
        built = COV.build_day_table(symbols, workspace_root, staging_root, workers=args.workers,
                                    progress=log)
        COV.save(built, table_path)
        meta = {k: built[k] for k in ("integrity", "raw_integrity", "sources", "per_symbol_issues")}
        (CACHE / "coverage_meta.json").write_text(json.dumps(meta, indent=1, default=_json_default))
        table = COV.load(table_path)
    table = {**table, "symbols": table["symbols"].astype(object), "source": table["source"].astype(object)}

    session_text = np.array([s.isoformat() for s in grid])
    ticker_text = np.array(list(daily.tickers))
    eligible = set(np.char.add(np.char.add(session_text[daily.session_idx], "|"),
                               ticker_text[daily.ticker_idx]).tolist())
    report = COV.pair_report(table, usable, eligible)
    report_all = COV.pair_report(table, pairs, None)

    out = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "workspace_root": str(workspace_root),
           "staging_root": str(staging_root) if staging_root else None,
           "grid": {"first": grid[0], "last": grid[-1], "sessions": len(grid),
                    "snapshot_id": history.freeze.snapshot_id,
                    "freeze_digest": history.freeze.freeze_digest,
                    "read_set_digest": history.freeze.d_read_digest},
           "daily_pit": {"rows": len(daily), **dict(daily.counters)},
           "pairing": pair_audit,
           "usable_source_range": [usable[0].source_session, usable[-1].source_session],
           "coverage_pit_sources": report, "coverage_all_grid_pairs": report_all,
           "integrity": meta["integrity"], "raw_integrity": meta["raw_integrity"],
           "sources": meta["sources"],
           "symbols_with_issues": len(meta["per_symbol_issues"]),
           "issue_examples": dict(list(sorted(meta["per_symbol_issues"].items()))[:15]),
           "elapsed_seconds": round(time.time() - started, 1),
           "returns_computed": False}
    path = RUNTIME / "coverage_audit.json"
    path.write_text(json.dumps(out, indent=1, default=_json_default))
    log(f"coverage written to {path}")
    return 0


def _tape_digest(workspace_root: Path, staging_root: Path | None, symbols) -> dict:
    """sha256 over (form, symbol, file name, size) of every minute file the study can read."""
    import hashlib
    from app.backtest.strategy_e0_overnight import minute as M
    digest = hashlib.sha256()
    files = 0
    for form, root, rel in (("raw", workspace_root, M.RAW_DIR), ("legacy", workspace_root, M.LEGACY_DIR),
                            ("staging", staging_root, M.RAW_DIR)):
        if root is None:
            continue
        for symbol in symbols:
            folder = root / rel / symbol
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                digest.update(f"{form}/{symbol}/{path.name}\t{path.stat().st_size}\n".encode())
                files += 1
    return {"digest": digest.hexdigest(), "files": files}


def _prepare(args):
    from app.backtest.strategy_g0_after_premarket import pairing
    from app.backtest.strategy_g0_after_premarket.config import (
        G0HardFail, RulesChanged, check_daily_loader, load_rules)
    from app.backtest.workspace.discovery import resolve_workspace_root
    from app.market.calendar import MarketCalendar
    try:
        rules = load_rules()
    except (RulesChanged, G0HardFail) as error:
        print(f"G0 STOP {error}", file=sys.stderr)
        raise SystemExit(2)
    workspace_root = resolve_workspace_root(args.workspace_root)
    e0_rules, daily, history = _daily(workspace_root)
    check_daily_loader(rules, e0_rules)
    grid = list(history.panel.sessions)
    calendar = MarketCalendar()
    pairs = pairing.build_pairs(grid, calendar)
    return rules, workspace_root, e0_rules, daily, history, grid, calendar, pairs


def cmd_build(args) -> int:
    from app.backtest.strategy_g0_after_premarket import cohort as C
    from app.backtest.strategy_g0_after_premarket import coverage as COV
    from app.backtest.strategy_g0_after_premarket.dataset import ordinal
    from app.backtest.strategy_g0_after_premarket.tape import STAGING_REL
    rules, workspace_root, e0_rules, daily, history, grid, calendar, pairs = _prepare(args)
    log(f"rules {rules.checksum[:12]} frozen; daily rows {len(daily)}")
    staging_root = REPO_ROOT / STAGING_REL
    symbols = COV.symbols_in(workspace_root, staging_root)
    binding = {"symbols": list(symbols), **_tape_digest(workspace_root, staging_root, symbols)}
    log(f"tape binding {binding['digest'][:16]} ({binding['files']} files, {len(symbols)} symbols)")
    params = rules.raw["feature_parameters"]
    arrays = C.build(symbols, workspace_root, staging_root, C.target_specs(rules.raw),
                     [ordinal(s) for s in grid], rvol_window=int(params["after_rvol_window_sessions"]),
                     rvol_minimum=int(params["after_rvol_min_sessions"]), workers=args.workers,
                     progress=log)
    CACHE.mkdir(parents=True, exist_ok=True)
    C.save(arrays, CACHE / "cohort.npz")
    (CACHE / "tape_binding.json").write_text(json.dumps(binding))
    log(f"cohort rows {arrays['days'].size} saved")
    return 0


def cmd_run(args) -> int:
    import hashlib
    from app.backtest.strategy_d_analog.source import read_set_digest
    from app.backtest.strategy_e0_overnight import minute as M
    from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close
    from app.backtest.strategy_g0_after_premarket import cohort as C
    from app.backtest.strategy_g0_after_premarket import dataset as D
    from app.backtest.strategy_g0_after_premarket import evaluate, pit_audit
    from app.backtest.strategy_g0_after_premarket import run as R
    from app.backtest.strategy_g0_after_premarket import tape as TP
    from app.backtest.strategy_g0_after_premarket.tape import STAGING_REL
    started = time.time()
    rules, workspace_root, e0_rules, daily, history, grid, calendar, pairs = _prepare(args)
    staging_root = REPO_ROOT / STAGING_REL
    binding = json.loads((CACHE / "tape_binding.json").read_text())
    cohort = C.load(CACHE / "cohort.npz")
    benchmark = load_benchmark_close(workspace_root, e0_rules.snapshot_id, grid)
    signal = D.build(cohort, daily, history, benchmark, pairs, rules)
    log(f"signal rows {len(signal)}, evaluated rows {int(signal.has_entry.sum())}")

    code = R.code_digest(Path(R.__file__).parent)
    parts = [rules.checksum, history.freeze.freeze_digest, history.freeze.d_read_digest,
             history.freeze.grid_digest, binding["digest"], code]
    identity_digest = hashlib.sha256("\t".join(parts).encode()).hexdigest()
    run_id = f"g0-{identity_digest[:12]}"
    log(f"run {run_id}")

    results = R.study(signal, rules, daily, history.panel)
    log(f"study verdict {results['gate']['verdict']}")

    # PIT audits: cutoff on a spread sample of real tapes, pairing on every row
    edges, offsets = M.et_offsets(datetime(2024, 1, 1, tzinfo=timezone.utc),
                                  datetime(2027, 1, 1, tzinfo=timezone.utc))
    names = sorted(set(signal.tickers.astype(str).tolist()))
    sample = names[::max(1, len(names) // args.pit_sample)][:args.pit_sample]
    tapes = []
    for symbol in sample:
        merged = TP.load(symbol, workspace_root, staging_root, edges, offsets)
        if merged is not None:
            tapes.append(TP.drop_identical_duplicates(merged.tape))
    grid_days = np.array([D.ordinal(s) for s in grid], dtype=np.int64)
    params = rules.raw["feature_parameters"]
    specs = C.target_specs(rules.raw)

    def builder(tape):
        return C.symbol_rows(tape, specs, grid_days, int(params["after_rvol_window_sessions"]),
                             int(params["after_rvol_min_sessions"]))

    def masks(feats):
        return {n: evaluate.mask(n, feats, rules.raw) for n in evaluate.HYPOTHESES}

    audits = [pit_audit.cutoff_audit(tapes, builder, masks),
              pit_audit.pairing_audit(signal.source_sessions, signal.next_sessions, calendar)]
    verdict = results["gate"]["verdict"]
    if any(a["verdict"] != "PASS" for a in audits):
        verdict = "FAIL"
    log(f"pit audits {[a['verdict'] for a in audits]} -> verdict {verdict}")

    final_binding = _tape_digest(workspace_root, staging_root, binding["symbols"])
    final_read = read_set_digest(workspace_root, e0_rules.snapshot_id)
    run_dir = RUNTIME / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "run_identity.json": {"run_id": run_id, "identity_digest": identity_digest,
                              "rules_checksum": rules.checksum, "code_digest": code,
                              "snapshot_id": history.freeze.snapshot_id,
                              "freeze_digest": history.freeze.freeze_digest,
                              "grid_digest": history.freeze.grid_digest,
                              "read_set_digest_at_load": history.freeze.d_read_digest,
                              "tape_digest": binding["digest"], "tape_files": binding["files"]},
        "universe.json": {"counters": signal.counters},
        "results.json": results,
        "pit_audit.json": audits,
        "gate_results.json": {"verdict": verdict, **results["gate"]},
    }
    digests = {}
    for name, payload in payloads.items():
        body = json.dumps(payload, indent=1, sort_keys=True, default=_json_default).encode()
        (run_dir / name).write_bytes(body)
        digests[name] = hashlib.sha256(body).hexdigest()
    context = {"finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "elapsed_seconds": round(time.time() - started, 1),
               "workspace_root": str(workspace_root),
               "daily_inputs_unchanged": final_read == history.freeze.d_read_digest,
               "tape_unchanged": final_binding["digest"] == binding["digest"],
               "provider_calls": 0, "writer_lock_taken": False}
    (run_dir / "run_context.json").write_text(json.dumps(context, indent=1))
    (run_dir / "COMPLETE.json").write_text(json.dumps({"run_id": run_id, "verdict": verdict,
                                                       "artifacts": digests, **context}, indent=1))
    log(f"artifacts {run_dir}")
    print(f"G0 {verdict} run {run_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy G0 after-hours -> premarket pre-validation")
    sub = parser.add_subparsers(dest="command", required=True)
    cov = sub.add_parser("coverage", help="returns-free coverage, pairing and integrity audit")
    cov.add_argument("--workspace-root", type=Path, default=None)
    cov.add_argument("--workers", type=int, default=5)
    cov.add_argument("--rebuild", action="store_true")
    cov.add_argument("--no-staging", action="store_true")
    bld = sub.add_parser("build", help="per symbol-day blocks from the tape (after the freeze)")
    bld.add_argument("--workspace-root", type=Path, default=None)
    bld.add_argument("--workers", type=int, default=5)
    run = sub.add_parser("run", help="the G0 study and gate (after the freeze)")
    run.add_argument("--workspace-root", type=Path, default=None)
    run.add_argument("--pit-sample", type=int, default=40)
    args = parser.parse_args(argv)
    if args.command == "coverage":
        return cmd_coverage(args)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "run":
        return cmd_run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
