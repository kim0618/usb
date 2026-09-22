"""Build the immutable B-E0 run universe from the same membership matrix the collector planned from.

The artifact has to describe exactly what was fetched, or the preflight would be checking a
different universe from the one on disk. So this module does not recompute scope its own way: it
calls ``b_universe.build`` with the collector's own grid and snapshot dates, then restricts the
resulting S(D) matrix to the run window. Two results come out of the same matrix:

* the per-session membership the artifact stores (which symbol may trade on which session);
* the planner's own symbol set and fetch ranges, via ``historical_v2.minute_window_ranges``.

``reconcile`` then requires the two to agree symbol for symbol and range for range, and refuses
to write anything if they do not. That check is what closes the 3,855-versus-3,883 question in
code rather than in prose: the artifact's members plus its exclusions must be the planner's 3,883
symbols exactly, and a request count (3,855) is never compared with a symbol count.

A symbol whose directory no Windows-backed store can create (``reserved_path_name``, i.e. CON)
is a planned symbol with no data. It goes to ``exclusions`` with its reason, so the count is
explained rather than silently one short.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from app.backtest.strategy_b_e0.universe import RunUniverse, UniverseMember, write_universe

BUILDER_VERSION = "b-e0-universe-builder-v1"


class ReconciliationFailed(RuntimeError):
    """The artifact would not describe what the collector fetched."""


@dataclass(frozen=True, slots=True)
class Derived:
    members: tuple[UniverseMember, ...]
    exclusions: Mapping[str, str]
    window_sessions: tuple[date, ...]


def derive(member: np.ndarray, names: Sequence[str], sessions: Sequence[date], *,
           scope_start: date, scope_end: date, warmup: int,
           reserved: Callable[[str], str | None]) -> Derived:
    """Per-symbol scope sessions inside [scope_start, scope_end], from the S(D) matrix alone.

    ``member[i, j]`` is S(D) for ``sessions[i]``, already decided from D-1 information by
    ``b_universe.scope_membership``. Nothing here looks at a bar, a return or any outcome; the
    only inputs are membership and the calendar, which is what keeps the artifact PIT-safe.
    """
    if member.shape != (len(sessions), len(names)):
        raise ValueError(f"membership {member.shape} does not match "
                         f"{len(sessions)} sessions x {len(names)} names")
    window = [i for i, day in enumerate(sessions) if scope_start <= day <= scope_end]
    if not window:
        raise ValueError(f"no session between {scope_start} and {scope_end}")

    members: list[UniverseMember] = []
    exclusions: dict[str, str] = {}
    for j, name in enumerate(names):
        days = [i for i in window if member[i, j]]
        if not days:
            continue
        clash = reserved(name)
        if clash is not None:
            exclusions[name] = (f"reserved DOS device name {clash}: no Windows-backed store can "
                                "hold it as a directory, so the collector skips it and no bar exists")
            continue
        members.append(UniverseMember(
            symbol=name,
            scope_sessions=tuple(sessions[i] for i in days),
            fetch_start=sessions[max(0, days[0] - warmup)],
            fetch_end=sessions[days[-1]]))
    return Derived(tuple(sorted(members, key=lambda item: item.symbol)), exclusions,
                   tuple(sessions[i] for i in window))


def reconcile(derived: Derived, planner_ranges: Mapping[str, tuple[date, date]]) -> dict[str, object]:
    """The artifact must be the planner's set, symbol for symbol and range for range."""
    artifact_symbols = {item.symbol for item in derived.members} | set(derived.exclusions)
    planned = set(planner_ranges)
    only_artifact = sorted(artifact_symbols - planned)
    only_planner = sorted(planned - artifact_symbols)
    range_mismatch = sorted(
        item.symbol for item in derived.members
        if item.symbol in planner_ranges
        and (item.fetch_start, item.fetch_end) != planner_ranges[item.symbol])
    if only_artifact or only_planner or range_mismatch:
        raise ReconciliationFailed(
            f"artifact and collector plan disagree: only in artifact {only_artifact[:10]}, "
            f"only in plan {only_planner[:10]}, range mismatch {range_mismatch[:10]}")
    return {
        "planner_symbols": len(planned),
        "artifact_members": len(derived.members),
        "artifact_exclusions": len(derived.exclusions),
        "members_plus_exclusions_equals_planner": True,
        "fetch_ranges_identical": True,
        "note": ("the collector's request count is a different quantity (one request per contiguous "
                 "missing-session run) and is deliberately not compared here"),
    }


def write(path: Path, derived: Derived, *, scope_start: date, scope_end: date,
          provenance: Mapping[str, object]) -> RunUniverse:
    return write_universe(path, scope_start=scope_start, scope_end=scope_end,
                          sessions=derived.window_sessions, members=derived.members,
                          exclusions=derived.exclusions,
                          built_from={"builder_version": BUILDER_VERSION, **provenance})


def build(root: Path, path: Path, *, scope_start: date, scope_end: date) -> tuple[RunUniverse, dict]:
    """Read the store, derive, reconcile against the planner, and write the artifact once.

    Reads grouped daily and the reference snapshots from ``root`` (the same inputs the collector
    planned from). Writes nothing until reconciliation has passed.
    """
    from app.backtest.historical_store import b_universe
    from app.backtest.historical_store.raw_fetch import reserved_path_name
    from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
    from app.dev.historical_v2 import GRID, minute_window_ranges
    from app.market.calendar import MarketCalendar
    from app.strategy_b.config import RvolConfig

    if scope_end != GRID[1]:
        raise ReconciliationFailed(
            f"scope_end {scope_end} differs from the collector grid end {GRID[1]}; the planner's "
            "window runs to the grid end, so the two would not describe the same sessions")
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, *GRID)
    snapshots = quarter_snapshot_dates(calendar, date(2024, 9, 16), GRID[1])
    body, member, names, _ = b_universe.build(root, sessions, snapshots)
    warmup = RvolConfig().lookback_sessions

    derived = derive(member, names, sessions, scope_start=scope_start, scope_end=scope_end,
                     warmup=warmup, reserved=reserved_path_name)
    planner = minute_window_ranges(member, names, sessions, scope_start, warmup)
    reconciliation = reconcile(derived, planner)

    provenance = {
        "source_universe_id": body["universe_id"],
        "source_universe_digest": body["digest"],
        "grid": body["grid"],
        "reference_snapshots": body["reference_snapshots"],
        "rule": body["rule"],
        "warmup_sessions": warmup,
        "reconciliation": reconciliation,
    }
    universe = write(path, derived, scope_start=scope_start, scope_end=scope_end,
                     provenance=provenance)
    return universe, reconciliation
