"""Local CLI for the shared backtest workspace on Google Drive.

The handoff command between the home PC and the office PC: it says where the shared root
is, which stage is finished, what the manifest holds, and whether a writer lock is out.
It never calls Massive, Kiwoom, or the production database, and it never runs a git write
command - git is only read.

    PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace status
    PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace init
    PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace doctor

``--workspace-root`` overrides discovery when Google Drive is mounted somewhere the
search does not reach. No secret is read or printed by any subcommand.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

from app.backtest.workspace.discovery import resolve_workspace_root
from app.backtest.workspace.errors import WorkspaceError
from app.backtest.workspace.identity import read_identity
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import force_release, read_lock
from app.backtest.workspace.manifest import counts_by_status, manifest_connection
from app.backtest.workspace.operations import diagnose, git_snapshot, initialize
from app.backtest.workspace.safe_write import utc_now_iso
from app.backtest.workspace.state import UNCOMMITTED, CurrentState, read_current_state


# Stage P of the foundation prompt: the verified Massive spike result, seeded only when
# CURRENT_STATE.json does not exist yet. source_commit stays UNCOMMITTED until the commit
# stage records the real hash.
INITIAL_STAGE = "MASSIVE_MULTI_SYMBOL_5_SESSION_VALIDATION"
INITIAL_STATUS = "PASS_WITH_LIMITATIONS"
INITIAL_NEXT_STAGE = "MASSIVE_AAPL_1_YEAR_FEASIBILITY"
INITIAL_NOTES = (
    "AAPL/AMD/ORCL x 5 completed XNYS sessions. 15/15 HTTP 200 and CLEAN. "
    "Premarket OHLCV present. Regular 390/390 all sessions. Known limitation: premarket "
    "missing minutes semantics and volume/VWAP authority.")


def not_run(reason: str) -> SystemExit:
    return SystemExit(f"NOT RUN: {reason}")


def initial_state() -> CurrentState:
    return CurrentState(stage=INITIAL_STAGE, status=INITIAL_STATUS, provider="massive",
                        source_commit=UNCOMMITTED, last_completed_at=utc_now_iso(),
                        next_stage=INITIAL_NEXT_STAGE, notes=INITIAL_NOTES)


def _workspace(args: argparse.Namespace, *, must_exist: bool = True) -> Workspace:
    root = resolve_workspace_root(args.workspace_root, must_exist=must_exist)
    return Workspace(root)


def _print_state(state: CurrentState | None) -> None:
    if state is None:
        print("current_stage=none")
        print("current_status=none")
        print("source_commit=none")
        print("next_stage=none")
        return
    print(f"current_stage={state.stage}")
    print(f"current_status={state.status}")
    print(f"provider={state.provider}")
    print(f"source_commit={state.source_commit}")
    print(f"last_completed_at={state.last_completed_at}")
    print(f"next_stage={state.next_stage}")
    print(f"notes={state.notes}")


def command_status(args: argparse.Namespace) -> int:
    workspace = _workspace(args)
    print(f"workspace_root={workspace.root}")
    identity = read_identity(workspace)
    print("workspace_schema_version="
          f"{identity.workspace_schema_version if identity else 'none'}")
    _print_state(read_current_state(workspace))
    if workspace.manifest_path.is_file():
        with manifest_connection(workspace, create=False) as connection:
            counts = counts_by_status(connection)
        print("manifest=" + " ".join(f"{key.lower()}:{value}" for key, value in counts.items()))
    else:
        print("manifest=absent")
    lock = read_lock(workspace)
    if lock is None:
        print("writer_lock=FREE")
    else:
        status = "STALE" if lock.is_stale() else "HELD"
        print(f"writer_lock={status} owner={lock.owner_id} pid={lock.pid} "
              f"purpose={lock.purpose} acquired_at={lock.acquired_at}")
    snapshot = git_snapshot()
    if snapshot.available:
        print(f"git_branch={snapshot.branch} git_head={snapshot.commit} "
              f"git_dirty_files={snapshot.dirty_files}")
    else:
        print(f"git=unavailable {snapshot.detail}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.workspace_root, must_exist=not args.create_root)
    report = initialize(root, create_root=args.create_root,
                        seed_state=None if args.no_seed_state else initial_state())
    print(f"workspace_root={report.workspace.root}")
    print(f"root_created={report.root_created}")
    print("created_directories=" + (
        ",".join(report.workspace.relative(path) for path in report.created_directories) or "none"))
    print(f"identity_created={report.identity_created}")
    print(f"manifest_created={report.manifest_created}")
    print(f"state_created={report.state_created}")
    _print_state(report.state)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    workspace = _workspace(args)
    diagnosis = diagnose(workspace)
    for check in diagnosis.checks:
        print(f"{check.name}={'OK' if check.ok else 'FAIL'} {check.detail}")
    print(f"doctor={'OK' if diagnosis.ok else 'FAIL'}")
    return 0 if diagnosis.ok else 1


def command_unlock(args: argparse.Namespace) -> int:
    workspace = _workspace(args)
    lock = force_release(workspace, require_stale=not args.even_if_live)
    if lock is None:
        print("writer_lock=FREE nothing_to_release")
        return 0
    print(f"writer_lock=RELEASED owner={lock.owner_id} purpose={lock.purpose} "
          f"acquired_at={lock.acquired_at}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="explicit 1_US-B path; skips Google Drive discovery")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="print the shared handoff state")
    init = subparsers.add_parser("init", help="create the workspace layout (idempotent)")
    init.add_argument("--create-root", action="store_true",
                      help="create the 1_US-B directory itself when its parent exists")
    init.add_argument("--no-seed-state", action="store_true",
                      help="do not seed CURRENT_STATE.json when it is absent")
    subparsers.add_parser("doctor", help="verify the workspace is usable from this PC")
    unlock = subparsers.add_parser("unlock", help="break a writer lock (explicit operator action)")
    unlock.add_argument("--force", action="store_true", required=True,
                        help="required acknowledgement that another PC may be writing")
    unlock.add_argument("--even-if-live", action="store_true",
                        help="also release a lock whose lease has not expired")
    return parser


COMMANDS = {"status": command_status, "init": command_init, "doctor": command_doctor,
            "unlock": command_unlock}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except WorkspaceError as error:
        raise not_run(str(error)) from error


if __name__ == "__main__":
    raise SystemExit(main())
