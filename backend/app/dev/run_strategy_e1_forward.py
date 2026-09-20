"""Operate the Strategy E1-H5 forward holdout.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward estimate
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward status
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward rehearse
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward seal  --session 2026-09-21
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward label --session 2026-09-21

This tool never fetches. It reads the forward tape if it is there, refuses clearly if it is not,
and takes no writer lock, so it cannot disturb the running historical collector.
"""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

from app.backtest.strategy_e1_forward import (
    audit, checkpoint, labels as labels_mod, layout, plan, registry, seal as seal_mod, tape,
)
from app.backtest.strategy_e1_forward.layout import ForwardViolation
from app.backtest.strategy_e1_h5_confirm.config import load_rules as load_confirm_rules
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e1_premarket.config import load_rules as load_e1_rules
from app.backtest.workspace.discovery import resolve_workspace_root

REPO_ROOT = Path(__file__).resolve().parents[3]


def _dump(payload) -> int:
    print(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return 0


def cmd_estimate(args) -> int:
    universes = {"full_eligible_universe": 2557, "current_covered_symbols": 1612}
    out = {"basic_calls_per_minute": plan.BASIC_CALLS_PER_MINUTE,
           "fetcher_spacing_seconds": plan.FETCHER_SPACING_SECONDS,
           "measured_inputs": plan.MEASURED, "scenarios": {}}
    for name, symbols in universes.items():
        out["scenarios"][name] = {"session_plan": plan.session_plan(symbols).to_dict(),
                                  "accumulation": plan.accumulation(symbols)}
    return _dump(out)


def cmd_status(args) -> int:
    path = layout.registry_path(REPO_ROOT)
    rows = registry.read(path)
    h5 = sum(1 for r in rows if r.get("h5"))
    sessions = sorted({r["session_date"] for r in rows})
    return _dump({
        "forward_holdout_start": layout.FORWARD_HOLDOUT_START.isoformat(),
        "registry_path": str(path), "registry_exists": path.exists(),
        "observations": len(rows), "h5_observations": h5,
        "sessions_recorded": len(sessions),
        "session_range": [sessions[0], sessions[-1]] if sessions else None,
        "checkpoint": checkpoint.status(h5, REPO_ROOT / layout.CHECKPOINTS_DIR),
        "verdict": "INCONCLUSIVE - PROMISING",
        "promotion_status": "STRATEGY E = NOT PROMOTED",
    })


def _features_for(workspace_root: Path, session: date, *, poisoned: bool):
    """Build the sealed feature columns for one forward session from the forward tape."""
    symbols = tape.require_session_data(workspace_root, session)
    edges, offs = tape.offsets_for(session)
    names, columns = [], {name: [] for name in seal_mod.SEALED_FEATURES}
    for symbol in symbols:
        loaded = tape.load(workspace_root, symbol, session, edges, offs)
        if loaded is None:
            continue
        if poisoned:
            fields = audit.poison_tape_after_cutoff(
                loaded.minute, {"open": loaded.open, "high": loaded.high, "low": loaded.low,
                                "close": loaded.close, "volume": loaded.volume,
                                "vwap": loaded.vwap})
            loaded = type(loaded)(symbol=loaded.symbol, et_day=loaded.et_day,
                                  minute=loaded.minute, sources=loaded.sources,
                                  overlap_sessions=loaded.overlap_sessions, **fields)
        rows = P.session_rows(loaded)
        key = P.ordinal(session)
        if key not in rows:
            continue
        block = rows[key]
        derived = P.derived_features(block.premarket)
        names.append(symbol)
        values = {**block.premarket, **derived, "pm_rvol": block.pm_rvol}
        for name in seal_mod.SEALED_FEATURES:
            columns[name].append(float(values.get(name, np.nan)))
    return names, {k: np.array(v, dtype=float) for k, v in columns.items()}


def cmd_audit(args) -> int:
    workspace_root = resolve_workspace_root(args.workspace_root)
    session = date.fromisoformat(args.session)
    layout.require_forward_session(session)
    rules = load_confirm_rules()
    results = [audit.feature_cutoff(session, lambda p: _features_for(workspace_root, session, poisoned=p),
                                    rules_digest=rules.checksum)]
    return _dump(results)


def cmd_seal(args) -> int:
    workspace_root = resolve_workspace_root(args.workspace_root)
    session = date.fromisoformat(args.session)
    layout.require_forward_session(session)
    rules = load_confirm_rules()
    names, columns = _features_for(workspace_root, session, poisoned=False)
    digest, files = tape.tape_digest(workspace_root, names, session)
    built = seal_mod.build(session, names, columns,
                           provenance=args.provenance, rules_digest=rules.checksum,
                           sources={"minute": digest, "minute_files": str(files),
                                    "daily": args.daily_digest, "reference": args.reference_digest})
    path = seal_mod.write(layout.seal_path(REPO_ROOT, session), built)
    return _dump({"sealed": str(path), "eligible_rows": built.eligible_rows,
                  "h5_rows": built.h5_rows, "decision_digest": built.decision_digest})


def cmd_label(args) -> int:
    """Attach outcomes to an already sealed session, then append it to the registry."""
    workspace_root = resolve_workspace_root(args.workspace_root)
    session = date.fromisoformat(args.session)
    layout.require_forward_session(session)
    stored = seal_mod.read(layout.seal_path(REPO_ROOT, session))
    sealed_symbols = [row["symbol"] for row in stored["rows"]]
    edges, offs = tape.offsets_for(session)
    columns = {name: [] for name in labels_mod.LABEL_COLUMNS}
    present = []
    for symbol in sealed_symbols:
        loaded = tape.load(workspace_root, symbol, session, edges, offs)
        rows = P.session_rows(loaded) if loaded is not None else {}
        block = rows.get(P.ordinal(session))
        if block is None:
            raise ForwardViolation(
                f"{symbol} was sealed for {session} but its opening bars are missing; a sealed "
                "universe must be labelled in full, so this session cannot be completed as is")
        present.append(symbol)
        values = P.labels(block.opening)
        values["open_570"] = block.opening[f"open_{P.OPEN_MIN}"]
        for name in labels_mod.LABEL_COLUMNS:
            columns[name].append(float(values.get(name, np.nan)))
    digest, files = tape.tape_digest(workspace_root, present, session)
    payload = labels_mod.build(session, stored, present,
                               {k: np.array(v, dtype=float) for k, v in columns.items()},
                               sources={"minute": digest, "minute_files": str(files)})
    labels_mod.write(layout.label_path(REPO_ROOT, session), payload)
    appended = registry.append_session(layout.registry_path(REPO_ROOT), session, stored, payload)
    h5 = registry.h5_count(layout.registry_path(REPO_ROOT))
    return _dump({"labelled": session.isoformat(), "rows": appended,
                  "label_digest": payload["label_digest"],
                  "registry_h5_rows": h5,
                  "checkpoint": checkpoint.status(h5, REPO_ROOT / layout.CHECKPOINTS_DIR)})


def cmd_rehearse(args) -> int:
    """Prove the chain end to end on synthetic rows, in a temp directory, touching nothing real."""
    session = date(2026, 9, 17)
    rows = 40
    rng = np.random.default_rng(7)
    names = [f"SYM{i:03d}" for i in range(rows)]
    columns = {name: rng.uniform(1.0, 5.0, rows) for name in seal_mod.SEALED_FEATURES}
    columns["premarket_gap"] = rng.uniform(-0.02, 0.05, rows)
    columns["premarket_rvol"] = rng.uniform(0.5, 6.0, rows)
    columns["position_in_premarket_range"] = rng.uniform(0.0, 1.0, rows)
    columns["return_0900_0925"] = rng.uniform(-0.01, 0.01, rows)
    columns["close_price"] = rng.uniform(5.0, 300.0, rows)
    columns["previous_day_dollar_volume"] = rng.uniform(5e6, 1e9, rows)
    columns["premarket_dollar_volume"] = rng.uniform(5e4, 5e7, rows)
    steps = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        built = seal_mod.build(session, names, columns, provenance=seal_mod.RECONSTRUCTED,
                               sources={"minute": "rehearsal"}, rules_digest="rehearsal")
        seal_path = root / "seals" / f"{session.isoformat()}.json"
        seal_mod.write(seal_path, built)
        steps.append({"step": "seal", "h5_rows": built.h5_rows,
                      "digest": built.decision_digest[:16]})
        try:
            seal_mod.write(seal_path, built)
            steps.append({"step": "reseal", "refused": False})
        except ForwardViolation:
            steps.append({"step": "reseal", "refused": True})
        stored = seal_mod.read(seal_path)
        label_values = {name: rng.normal(0, 0.005, rows) for name in labels_mod.LABEL_COLUMNS}
        payload = labels_mod.build(session, stored, names, label_values,
                                   sources={"minute": "rehearsal"})
        labels_mod.write(root / "labels" / f"{session.isoformat()}.json", payload)
        steps.append({"step": "label", "rows": len(payload["rows"]),
                      "digest": payload["label_digest"][:16]})
        registry_file = root / "registry.jsonl"
        appended = registry.append_session(registry_file, session, stored, payload)
        steps.append({"step": "registry_append", "rows": appended,
                      "h5_rows": registry.h5_count(registry_file)})
        try:
            registry.append_session(registry_file, session, stored, payload)
            steps.append({"step": "reappend", "refused": False})
        except ForwardViolation:
            steps.append({"step": "reappend", "refused": True})
        try:
            registry.append_session(registry_file, date(2026, 9, 16), stored, payload)
            steps.append({"step": "pre_boundary_session", "refused": False})
        except ForwardViolation:
            steps.append({"step": "pre_boundary_session", "refused": True})
        steps.append({"step": "checkpoint",
                      **checkpoint.status(registry.h5_count(registry_file), root / "checkpoints")})
    return _dump({"rehearsal": "synthetic, temp directory, nothing real written", "steps": steps})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy E1-H5 forward holdout operations")
    parser.add_argument("--workspace-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("estimate", help="fetch plan, volumes and accumulation rates")
    sub.add_parser("status", help="registry and checkpoint status")
    sub.add_parser("rehearse", help="end-to-end self test on synthetic rows")
    for name in ("seal", "audit", "label"):
        node = sub.add_parser(name)
        node.add_argument("--session", required=True)
        if name == "seal":
            node.add_argument("--provenance", choices=[seal_mod.LIVE, seal_mod.RECONSTRUCTED],
                              required=True)
            node.add_argument("--daily-digest", default="UNKNOWN")
            node.add_argument("--reference-digest", default="UNKNOWN")
    args = parser.parse_args(argv)
    handlers = {"estimate": cmd_estimate, "status": cmd_status, "rehearse": cmd_rehearse,
                "seal": cmd_seal, "audit": cmd_audit, "label": cmd_label}
    try:
        return handlers[args.command](args)
    except ForwardViolation as error:
        print(f"FORWARD REFUSED {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
