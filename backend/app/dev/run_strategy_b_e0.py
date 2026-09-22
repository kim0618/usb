"""CLI for the B-E0 screening run.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_b_e0 verify
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_b_e0 universe [--out <path>]
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_b_e0 prepare --universe <artifact>
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_b_e0 run --universe <artifact> \
        --dataset data/runtime/strategy_b_e0 --out <run-root> --preflight-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_b_e0 identity --universe <path>
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_b_e0 run \
        --universe <artifact> --dataset <root> --out <run-root> [--preflight-only]

The default mode is STRICT and there is deliberately no flag that relaxes it. The contract
lists ``--allow-partial``, ``--ignore-checksum``, ``--skip-missing`` and ``--force`` as
forbidden, and ``verify`` asserts that this parser really does refuse them, so the prohibition
is tested rather than merely written down.
"""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import time

from app.backtest.strategy_b_e0 import identity as identity_module
from app.backtest.strategy_b_e0.contract import CONTRACT_PATH, Contract, load_contract
from app.backtest.strategy_b_e0.freeze import ContractNotFrozen, require_frozen
from app.backtest.strategy_b_e0.run import RunRefused
from app.backtest.strategy_b_e0.identity import RunMode
from app.backtest.strategy_b_e0.universe import RunUniverse, load_universe

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
RUNTIME = REPO_ROOT / "data/runtime/strategy_b_e0"
DEFAULT_UNIVERSE = RUNTIME / "b_e0_run_universe_v1.json"
FREEZE = REPO_ROOT / "data/runtime/common_hist/STRATEGY_C_RAW_FREEZE_V1.json"
GRID_START = date(2024, 9, 17)
"""First usable grouped-daily session (2024-09-16 is a NOT_AUTHORIZED stub in the freeze)."""


class DriveUnavailable(RuntimeError):
    """The Drive workspace is not mounted, so nothing about its writers can be known."""


def contract_status(checksum_path: Path) -> str:
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# status:"):
            return line.split(":", 1)[1].strip().split(".")[0].split(",")[0].strip()
    return "UNKNOWN"


def active_writer(drive_root: Path) -> dict | None:
    """The Drive workspace's live writer lock, if any other process holds one.

    A read-only mirror does not take the writer lock, but it should not silently run beside a
    writer either. Process-name checks miss writers they do not know about (the U1 minute collector
    was missed exactly that way), so the lock file, which every writer takes, is the signal.
    """
    from app.backtest.workspace.lock import parse_lock

    if not (drive_root / "state").is_dir():
        # An absent store is not a free lock. Without this, an unmounted Drive (after a reboot,
        # before Google Drive starts) reads as "no writer" and a repair would plan against nothing.
        raise DriveUnavailable(f"{drive_root} has no state/ directory: the Drive is not mounted")
    path = drive_root / "state/writer.lock.json"
    if not path.is_file():
        return None
    lock = parse_lock(json.loads(path.read_text(encoding="utf-8")))
    if lock.is_stale():
        return None
    return {"pid": lock.pid, "purpose": lock.purpose, "acquired_at": str(lock.acquired_at),
            "heartbeat_at": str(lock.heartbeat_at)}


def collector_running() -> bool:
    """True if a historical_v2 fetch process is alive. Reads /proc argv, so this process's own
    command line can never match itself (the pgrep -f trap)."""
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and argv[0].endswith(b"python") and b"app.dev.historical_v2" in argv \
                and b"fetch" in argv:
            return True
    return False


AUTHORITATIVE_BYPASS_FLAGS = (
    "--force", "--ignore-contract", "--ignore-checksum", "--ignore-dataset-digest",
    "--ignore-universe-hash", "--allow-partial", "--skip-missing", "--ignore-preflight")
"""Flags an authoritative run must never accept. None exists on the parser, abbreviations are
off, and a test asserts each of these is refused, so the prohibition is checked, not just stated."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_strategy_b_e0", description=__doc__,
                                     allow_abbrev=False)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    _no_abbrev = {"allow_abbrev": False}

    frz = sub.add_parser("freeze", **_no_abbrev,
                         help="evaluate the 15 preconditions and, only if all pass, freeze")
    frz.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    frz.add_argument("--dataset", type=Path, default=RUNTIME)
    frz.add_argument("--expected-draft", required=True,
                     help="the draft canonical checksum the user approved")
    frz.add_argument("--dry-run", action="store_true", help="evaluate and write nothing")
    sub.add_parser("verify", **_no_abbrev, help="load and validate the contract, print its checksum")

    build = sub.add_parser("universe", **_no_abbrev, help="build the immutable run universe (reads the store)")
    build.add_argument("--out", type=Path, default=DEFAULT_UNIVERSE)
    build.add_argument("--workspace-root", type=Path, default=None)

    prepare = sub.add_parser("prepare", **_no_abbrev, help="mirror Drive to local and build the session cache")
    prepare.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    prepare.add_argument("--dataset", type=Path, default=RUNTIME)
    prepare.add_argument("--workspace-root", type=Path, default=None)
    prepare.add_argument("--workers", type=int, default=8)
    prepare.add_argument("--allow-concurrent-writer", action="store_true",
                         help="mirror while another process holds the Drive writer lock. The mirror "
                              "only reads, pins every file's sha256 at planning time and fails on a "
                              "mismatch, so a concurrent writer can stop it but cannot corrupt it. "
                              "Never affects an authoritative run.")

    for name in ("identity", "run"):
        child = sub.add_parser(name, **_no_abbrev)
        child.add_argument("--universe", type=Path, required=True)
        child.add_argument("--label", default=None,
                           help="which pre-registered run; defaults to the authoritative one")
        if name == "run":
            child.add_argument("--dataset", type=Path, required=True)
            child.add_argument("--out", type=Path, required=True)
            mode = child.add_mutually_exclusive_group()
            mode.add_argument("--preflight-only", action="store_true",
                              help="check readiness and stop; writes no run artifacts")
            mode.add_argument("--smoke-sessions", type=int, default=None, metavar="N",
                              help="SMOKE mode: replay N sessions chosen by the deterministic "
                                   "rule in smoke_sessions(). Never feeds the verdict and has "
                                   "its own run id.")
    return parser


def _load(args: argparse.Namespace) -> tuple[Contract, RunUniverse]:
    contract = load_contract(args.contract)
    universe = load_universe(args.universe)
    if (universe.scope_start.isoformat(), universe.scope_end.isoformat()) != (
            contract.scope_start, contract.scope_end):
        raise SystemExit(f"universe window {universe.scope_start}..{universe.scope_end} does not "
                         f"match the contract window {contract.scope_start}..{contract.scope_end}")
    return contract, universe


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    contract = load_contract(args.contract)

    if args.command == "verify":
        print(json.dumps({
            "contract_id": contract.contract_id,
            "canonical_checksum": contract.canonical_checksum,
            "initial_capital_usd": contract.initial_capital_usd,
            "authoritative_label": contract.authoritative_label,
            "runs": [spec.label for spec in contract.runs],
            "cost_levels": {name: level.all_in_bps_per_side
                            for name, level in contract.cost_levels.items()},
            "gate_states": list(contract.gate_states),
        }, indent=1, ensure_ascii=False))
        return 0

    if args.command == "universe":
        from datetime import date

        from app.backtest.strategy_b_e0.build_universe import build
        from app.backtest.workspace.discovery import resolve_workspace_root

        root = resolve_workspace_root(args.workspace_root)
        universe, reconciliation = build(
            root, args.out, scope_start=date.fromisoformat(contract.scope_start),
            scope_end=date.fromisoformat(contract.scope_end))
        print(json.dumps({
            "universe_artifact": str(universe.path),
            "universe_sha256": universe.sha256,
            "universe_symbol_count": universe.symbol_count,
            "exclusions": dict(universe.exclusions),
            "sessions": len(universe.sessions),
            "reconciliation": reconciliation,
        }, indent=1, ensure_ascii=False))
        return 0

    if args.command == "prepare":
        return _prepare(args, contract)

    if args.command == "freeze":
        return _freeze(args, contract)

    contract, universe = _load(args)
    spec = contract.run(args.label or contract.authoritative_label)

    if args.command == "identity":
        lines = identity_module.identity_lines(
            contract, spec, universe, dataset_identity="<dataset>", dataset_digest="<digest>",
            code=identity_module.engine_code_digest(BACKEND_ROOT), mode=RunMode("STRICT"))
        print("\n".join(lines))
        return 0

    if args.preflight_only:
        return _preflight(args, contract, universe)
    return _execute(args, contract, universe, spec)


def inputs(universe: RunUniverse, dataset: Path, *, verify: bool = True):
    """Mirror manifest, cache, market inputs and facts. Local reads only; Drive is not touched."""
    from app.backtest.strategy_b_e0 import mirror, session_cache
    from app.backtest.strategy_b_e0.dataset_facts import LocalDatasetFacts
    from app.backtest.strategy_b_e0.market_inputs import load_grouped_daily, load_splits
    from app.dev.fetch_strategy_c_selection_raw import sessions_between
    from app.market.calendar import MarketCalendar

    calendar = MarketCalendar()
    mirror_root = dataset / "mirror"
    manifest = mirror.load_manifest(mirror_root)
    if manifest["universe_sha256"] != universe.sha256:
        raise SystemExit("the mirror was built for a different universe artifact")
    problems = mirror.verify(mirror_root, manifest) if verify else []
    cache = session_cache.SessionCache(
        session_cache.cache_dir(dataset / "cache", manifest["dataset_digest"]),
        expected_source_digest=manifest["dataset_digest"])
    grid = sessions_between(calendar, GRID_START, universe.scope_end)
    grouped = load_grouped_daily(mirror_root, grid, universe.symbols)
    splits = load_splits(mirror_root)
    facts = LocalDatasetFacts(universe=universe, cache=cache, grouped=grouped, splits=splits,
                              calendar=calendar, manifest=manifest, mirror_problems=problems)
    return manifest, cache, facts


def _prepare(args: argparse.Namespace, contract: Contract) -> int:
    from app.backtest.strategy_b_e0 import mirror, session_cache
    from app.backtest.workspace.discovery import resolve_workspace_root
    from app.market.calendar import MarketCalendar

    if collector_running():
        raise SystemExit("a historical_v2 collector is running; the mirror waits until it stops")
    universe = load_universe(args.universe)
    drive = resolve_workspace_root(args.workspace_root)
    writer = active_writer(drive)
    if writer is not None:
        if not args.allow_concurrent_writer:
            raise SystemExit(f"another process holds the Drive writer lock: {writer}. Wait for it, "
                             "or pass --allow-concurrent-writer if its writes cannot touch B's files.")
        print(f"proceeding beside an active writer: {writer}", flush=True)
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    log = lambda message: print(message, flush=True)  # noqa: E731
    started = time.monotonic()
    plan = mirror.plan(drive, universe, freeze, workers=args.workers)
    log(f"plan: {len(plan.files)} files, {len(plan.members_without_ledgers)} members without "
        f"ledgers, {len(plan.ledger_schema_problems)} ledger schema problems "
        f"({time.monotonic() - started:.0f}s)")
    manifest = mirror.run(drive, args.dataset / "mirror", plan, universe=universe,
                          freeze_digest=freeze["freeze_digest"], workers=args.workers,
                          progress=lambda d, n: log(f"mirror {d}/{n}"))
    mirrored = time.monotonic()
    log(f"mirror: {manifest['file_count']} files, {manifest['bytes'] / 1e9:.2f} GB, "
        f"{manifest['copied_this_run']} copied, digest {manifest['dataset_digest']} "
        f"({mirrored - started:.0f}s)")
    directory = session_cache.build(args.dataset / "mirror", args.dataset / "cache",
                                    manifest=manifest, universe=universe, calendar=MarketCalendar(),
                                    progress=lambda d, n: log(f"cache {d}/{n}"))
    meta = json.loads((directory / "cache_meta.json").read_text(encoding="utf-8"))
    log(json.dumps({"dataset_digest": manifest["dataset_digest"], "cache": str(directory),
                    "cache_digest": meta["cache_digest"], "rows": meta["rows"],
                    "uncovered_symbols": len(meta["uncovered_sessions"]),
                    "overlap_symbols": len(meta["overlap_sessions"]), "sanitation": meta["sanitation"],
                    "mirror_seconds": round(mirrored - started, 1),
                    "cache_seconds": round(time.monotonic() - mirrored, 1)}, indent=1))
    return 0


def _preflight(args: argparse.Namespace, contract: Contract, universe: RunUniverse) -> int:
    """Readiness only: no SessionSource call, no engine, no trade."""
    import resource

    from app.backtest.strategy_b_e0.preflight import run_preflight
    from app.strategy_b.config import StrategyBConfig

    started = time.monotonic()
    manifest, cache, facts = inputs(universe, args.dataset)
    loaded = time.monotonic()
    planned = {session: universe.symbols_for(session) for session in universe.sessions}
    report = run_preflight(contract, universe, facts, planned_symbols_by_session=planned,
                           contract_path=args.contract, config=StrategyBConfig())
    quality = facts.quality_exclusions()
    policy = facts.policy_exclusions()
    body = {
        "contract_canonical_checksum": contract.canonical_checksum,
        "rules_canonical_checksum": contract.rules_checksum,
        "universe_artifact": universe.path.name,
        "universe_sha256": universe.sha256,
        "dataset_digest": manifest["dataset_digest"],
        "mirror_digest": manifest["dataset_digest"],
        "cache_digest": cache.digest,
        "required_pairs": len(universe.required_pairs()),
        "covered_pairs": report.completeness.get("covered_pairs"),
        "available_pairs": len(universe.required_pairs()) - len(quality),
        "policy_exclusions": len(policy),
        "ipo_proxy_exclusion_count": len(policy),
        "ipo_proxy_symbols": len({e.symbol for e in policy}),
        "data_quality_exclusions": [e.as_dict() for e in quality],
        "gap_suspect_count": len(facts.gap_days()),
        "quality_exclusion_ratio": report.gate.overall_ratio,
        "max_session_quality_exclusion_ratio": report.gate.max_session_ratio,
        "warmup_gaps_in_use": list(facts.warmup_gaps_in_use()),
        "warmup_partial_count": facts.warmup_partial_pairs(),
        "warmup_below_minimum_count": len(facts.warmup_below_minimum()),
        "split_conflicts_total": len(facts.splits.conflicts),
        "split_conflicts_in_universe": list(facts.split_conflicts_in_universe()),
        "split_invalid_records": len(facts.splits.invalid),
        "early_close_sessions": [d.isoformat() for d in facts.early_close_sessions()],
        "cache_sanitation": cache.meta["sanitation"],
        "limitations": list(facts.limitations()),
        "preflight": report.as_dict(),
        "verdict": report.status,
    }
    out = args.dataset / "preflight"
    out.mkdir(parents=True, exist_ok=True)
    (out / "dataset_preflight.json").write_text(
        json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "ipo_warmup_exclusions.jsonl").write_text(
        "".join(json.dumps(e.as_dict(), sort_keys=True) + "\n" for e in policy), encoding="utf-8")
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    summary = {k: body[k] for k in (
        "verdict", "dataset_digest", "cache_digest", "required_pairs", "covered_pairs",
        "available_pairs", "policy_exclusions", "ipo_proxy_symbols", "gap_suspect_count",
        "quality_exclusion_ratio", "max_session_quality_exclusion_ratio", "warmup_partial_count",
        "warmup_below_minimum_count", "split_conflicts_total", "split_conflicts_in_universe",
        "early_close_sessions")}
    summary.update(warnings=report.warnings, failed=[c.name for c in report.failures],
                   load_seconds=round(loaded - started, 1),
                   preflight_seconds=round(time.monotonic() - loaded, 1), peak_rss_mb=round(peak_mb))
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0 if report.ready else 2


def smoke_sessions(universe: RunUniverse, facts, count: int) -> tuple[date, ...]:
    """The SMOKE session choice, fixed before looking at any result.

    Eligible: a universe session that is not an early close and holds no data-quality exclusion
    (so a CUB / TCI withheld pair cannot be what the smoke exercises). From the eligible list,
    take ``count`` consecutive sessions starting at its middle index. Nothing about bars, signals
    or trades enters the choice, and a zero-trade result does not move it.
    """
    if count < 1:
        raise SystemExit("--smoke-sessions needs at least one session")
    early = set(facts.early_close_sessions())
    quality = {e.session for e in facts.quality_exclusions()}
    eligible = [d for d in universe.sessions if d not in early and d not in quality]
    start = len(eligible) // 2
    chosen = tuple(eligible[start:start + count])
    if len(chosen) != count:
        raise SystemExit(f"only {len(chosen)} eligible sessions from the middle of the window")
    return chosen


def check_binding(bound, universe: RunUniverse, manifest: dict) -> None:
    """The frozen identity, compared with what is on disk now. Any mismatch refuses the run."""
    problems = []
    if universe.sha256 != bound.universe_sha256:
        problems.append(f"universe sha256 {universe.sha256} != frozen {bound.universe_sha256}")
    if len(universe.sessions) != bound.scope_sessions:
        problems.append(f"universe sessions {len(universe.sessions)} != {bound.scope_sessions}")
    if len(universe.required_pairs()) != bound.required_pairs:
        problems.append(f"required pairs {len(universe.required_pairs())} != {bound.required_pairs}")
    if manifest["dataset_digest"] != bound.dataset_digest:
        problems.append(f"dataset digest {manifest['dataset_digest']} != frozen "
                        f"{bound.dataset_digest}")
    if manifest["universe_sha256"] != bound.universe_sha256:
        problems.append("the mirror was built for another universe")
    if problems:
        raise RunRefused("RUN_REFUSED: " + "; ".join(problems))


FREEZE_LEDGER = RUNTIME / "freeze/b_e0_freeze_ledger.jsonl"
DIFFERENTIAL_ARTIFACT = RUNTIME / "execution_differential_v1.json"


def _freeze(args: argparse.Namespace, contract: Contract) -> int:
    """All 15 preconditions from what is on disk now; FROZEN only if every one passes."""
    from datetime import datetime
    import hashlib
    import subprocess
    from zoneinfo import ZoneInfo

    from app.backtest.strategy_b_e0 import code_identity, execution_differential, freeze
    from app.backtest.strategy_b_e0.contract import RULES_PATH
    from app.backtest.strategy_b_e0.preflight import config_agreement
    from app.backtest.strategy_c_selection.rules import canonical_checksum
    from app.strategy_b.config import StrategyBConfig

    universe = load_universe(args.universe)
    preflight_path = args.dataset / "preflight/dataset_preflight.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight_sha = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
    config = StrategyBConfig()
    differential = execution_differential.run(contract, contract.run(
        contract.authoritative_label).cost_level, config)
    body = differential.as_dict()
    body["contract_canonical_checksum"] = contract.canonical_checksum
    diff_bytes = (json.dumps(body, indent=1, sort_keys=True) + "\n").encode()
    rules = canonical_checksum(json.loads(RULES_PATH.read_text(encoding="utf-8")))
    checks = freeze.preconditions(
        contract, universe_sha256=universe.sha256, universe_sessions=len(universe.sessions),
        preflight=preflight, preflight_sha256=preflight_sha,
        config_agreement_passed=config_agreement(contract, config).passed,
        differential_verdict=differential.verdict,
        strategy_logic_changed=rules != contract.rules_checksum)
    summary = {"preconditions": [c.as_dict() for c in checks],
               "passed": sum(c.passed for c in checks), "total": len(checks),
               "execution_differential": {"verdict": differential.verdict,
                                          **differential.maxima()}}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    if args.dry_run or not all(c.passed for c in checks):
        return 0 if all(c.passed for c in checks) else 2
    DIFFERENTIAL_ARTIFACT.write_bytes(diff_bytes)
    code = code_identity.identity(BACKEND_ROOT)
    head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    frozen = freeze.freeze(
        args.contract, args.contract.parent / f"{args.contract.stem}.sha256", FREEZE_LEDGER,
        checks=checks, frozen_at=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        source_commit=head, pre_freeze_checksum=args.expected_draft,
        preflight_sha256=preflight_sha,
        execution={"verdict": differential.verdict, "version": execution_differential.VERSION,
                   "artifact": str(DIFFERENTIAL_ARTIFACT.relative_to(REPO_ROOT)),
                   "digest": hashlib.sha256(diff_bytes).hexdigest(), **differential.maxima()},
        code=code, config_fingerprint=config.fingerprint())
    print(json.dumps({"contract_state": frozen.raw["contract_state"],
                      "frozen_checksum": frozen.canonical_checksum,
                      "freeze": frozen.raw["freeze"]}, indent=1, ensure_ascii=False))
    return 0


def _ledger_records(contract_sha256: str) -> list[dict]:
    if not FREEZE_LEDGER.is_file():
        return []
    rows = [json.loads(line) for line in FREEZE_LEDGER.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return [row for row in rows if row.get("contract_sha256") == contract_sha256]


def bound_code_digest(bound) -> str | None:
    """The code the frozen contract is bound to: the latest code record for this contract in the
    append-only freeze ledger (the freeze writes the first), else the contract's own freeze block.
    A record never replaces the contract; it is how a later, documented engine-defect fix could be
    bound without editing a frozen file. No such record exists and no command writes one yet."""
    records = [row for row in _ledger_records(bound.contract_sha256) if row.get("code_digest")]
    return records[-1]["code_digest"] if records else bound.code_digest


def _changed_code(code: dict) -> list[str]:
    """Which files moved since the freeze, from the freeze ledger's recorded file map."""
    from app.backtest.strategy_b_e0.code_identity import changed_files
    if not FREEZE_LEDGER.is_file():
        return ["<no freeze ledger>"]
    records = [json.loads(line) for line in FREEZE_LEDGER.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    last = records[-1] if records else {}
    return changed_files(last.get("code_files") or {}, code["files"])[:20]


def _execute(args: argparse.Namespace, contract: Contract, universe: RunUniverse, spec) -> int:
    """STRICT (all sessions) or SMOKE (a few): the same path, the same gates, different run ids."""
    from app.backtest.strategy_b_e0 import code_identity, execution_differential
    from app.backtest.strategy_b_e0.run import execute
    from app.backtest.strategy_b_e0.session_source import LocalSessionSource
    from app.strategy_b.config import StrategyBConfig

    mode = RunMode("SMOKE" if args.smoke_sessions else "STRICT")
    bound = require_frozen(contract, args.contract.parent / f"{args.contract.stem}.sha256")
    if universe.sha256 != bound.universe_sha256:  # before any data is read
        raise RunRefused(f"RUN_REFUSED: universe sha256 {universe.sha256} != frozen "
                         f"{bound.universe_sha256}")
    code = code_identity.identity(BACKEND_ROOT)
    expected_code = bound_code_digest(bound)
    if expected_code is None or code["code_digest"] != expected_code:
        raise RunRefused(f"RUN_REFUSED: working-tree code digest {code['code_digest']} != frozen "
                         f"{expected_code}; changed: {_changed_code(code)}")
    config = StrategyBConfig()
    if config.fingerprint() != contract.raw["freeze"].get("config_fingerprint"):
        raise RunRefused("RUN_REFUSED: StrategyBConfig fingerprint differs from the frozen one")
    differential = execution_differential.run(contract, spec.cost_level, config)
    if differential.verdict != execution_differential.PASS:
        raise RunRefused(f"AUTHORITATIVE_RUN_NOT_READY: execution differential "
                         f"{differential.verdict} {differential.failing}")

    manifest, cache, facts = inputs(universe, args.dataset, verify=True)
    check_binding(bound, universe, manifest)
    if facts.mirror_problems:
        raise RunRefused(f"RUN_REFUSED: mirror problems {list(facts.mirror_problems)[:5]}")
    sessions = smoke_sessions(universe, facts, args.smoke_sessions) if mode.name == "SMOKE" else None
    # Smoke and authoritative output never share a directory, so a smoke artifact cannot be
    # mistaken for a research result.
    out_root = args.out / ("smoke" if mode.name == "SMOKE" else "authoritative")
    source = LocalSessionSource(facts, dataset_digest=manifest["dataset_digest"], config=config)
    outcome = execute(
        contract, spec, universe, source, facts, out_root=out_root, config=config, mode=mode,
        code_identity=code,
        backend_root=BACKEND_ROOT, repo_root=REPO_ROOT, contract_path=args.contract,
        sessions=sessions, expected_warnings=bound.warnings,
        manifest_extra={
            "frozen_contract_sha256": bound.contract_sha256,
            "dataset_digest_verified": manifest["dataset_digest"],
            "cache_digest": cache.digest,
            "dataset_preflight": {"artifact": contract.raw["data_adapter"]["evidence"][
                "preflight_artifact"], "sha256": bound.preflight_artifact_sha256,
                "status": bound.preflight_status},
            "data_quality_exclusions": [e.as_dict() for e in facts.quality_exclusions()],
            "policy_exclusion_count": len(facts.policy_exclusions()),
            "warmup_gaps_in_use": list(facts.warmup_gaps_in_use()),
            "warmup_partial_pairs": facts.warmup_partial_pairs(),
            "dataset_limitations": list(facts.limitations()),
            "execution_differential": {"version": execution_differential.VERSION,
                                       "verdict": differential.verdict},
        })
    print(json.dumps({"run_id": outcome.run_id, "run_mode": mode.name, "verdict": outcome.verdict,
                      "root": str(outcome.root),
                      "sessions": [d.isoformat() for d in (sessions or universe.sessions)][:5],
                      "trades": len(outcome.trades), "candidates": len(outcome.candidates)},
                     indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
