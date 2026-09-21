"""The E-R3 prechecks and V1.1 decision inputs, packaged for E-MAX stages.

This reproduces steps 1-6 of ``app.dev.run_strategy_e_r3`` (which stays unchanged so the E-R3
code identity holds): frozen chain, dataset binding, V1 reproduction, V1.1 rows and frames,
attribution, PIT poison and the V1.1 seals. Any failure raises ``PrecheckBlocked`` before a single
return is computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

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

REPO_ROOT = Path(__file__).resolve().parents[4]


class PrecheckBlocked(RuntimeError):
    """A precheck failed; the stage is BLOCKED — INTEGRITY and no return is computed."""


@dataclass
class Prepared:
    calendar: MarketCalendar
    workspace_root: Path
    view_root: Path
    timeline: list[date]
    frames: dict
    origins: dict
    rows: dict
    seals: dict
    universe_rows: int
    checksums: dict[str, str]
    identity: dict[str, str]
    prechecks: dict[str, Any]
    manifest: Any
    history: Any
    e0_rules: Any
    extra: dict[str, Any] = field(default_factory=dict)

    def verify_after(self) -> dict[str, bool]:
        return {"tape_unchanged_after_run": DS.verify_view(self.view_root, self.manifest),
                "daily_inputs_unchanged": read_set_digest(self.workspace_root, self.e0_rules.snapshot_id)
                == self.history.freeze.d_read_digest}


def _ancestry(chain: dict[str, str]) -> dict[str, bool]:
    return {name: subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor",
                                  commit, "HEAD"], capture_output=True).returncode == 0
            for name, commit in chain.items()}


def prepare(*, workspace_root: Path | None, workers: int, chain: dict[str, str],
            log: Callable[[str], None]) -> Prepared:
    calendar = MarketCalendar("America/New_York")
    try:
        checksums = R6.frozen_checksums()
        decision.load_rules()
        context.load_contract()
        protocol = RP.load_rules()
    except Exception as error:
        raise PrecheckBlocked(f"frozen artifact: {error}") from error
    ancestry = _ancestry({**R6.CHAIN, **chain})
    if not all(ancestry.values()):
        raise PrecheckBlocked(f"chain ancestry {ancestry}")
    e1_rules, e0_rules = load_e1_rules(), load_e0_rules()
    if (e0_rules.checksum != e1_rules.e0_rules_checksum
            or e1_rules.checksum != protocol["upstream"]["e1_premarket_rules_canonical_sha256"]
            or e0_rules.checksum != protocol["upstream"]["e0_overnight_rules_canonical_sha256"]):
        raise PrecheckBlocked("E0/E1 declarations moved")
    checksums.update(trading_v1_1=decision.RULES_CANONICAL_SHA256,
                     forward_context=context.CONTRACT_CANONICAL_SHA256,
                     replay_protocol=RP.RULES_CANONICAL_SHA256)
    log("frozen chain PASS")

    data = protocol["dataset"]
    root = resolve_workspace_root(workspace_root)
    manifest = DS.load_manifest()
    view_root = REPO_ROOT / R6.VIEW_DIR / manifest.digest[:16]
    view = DS.build_view(root, manifest, view_root, DS.legacy_minute_hashes(root))
    last_file = max(max(re.findall(r"\d{4}-\d{2}-\d{2}", n)) for _, n, _ in manifest.files)
    if not (view["tape_digest"] == data["minute_tape_digest"]
            and view["tape_files"] == data["minute_files"]
            and view["tape_symbols"] == data["minute_symbols"]
            and view["legacy_minute_digest"] == data["legacy_minute_digest"]
            and date.fromisoformat(last_file) < RP.FORWARD_HOLDOUT_START
            and "SPY" in manifest.symbols):
        raise PrecheckBlocked(f"dataset binding {view} last file {last_file}")
    daily, history = load_daily_rows(root, e0_rules)
    benchmark = load_benchmark_close(root, e0_rules.snapshot_id, history.panel.sessions)
    grid = list(history.panel.sessions)
    timeline = RP.timeline(calendar)
    if (history.freeze.freeze_digest != data["daily_freeze_digest"]
            or history.freeze.d_read_digest != data["daily_read_set_digest"]
            or grid[21:] != timeline or any(s >= RP.FORWARD_HOLDOUT_START for s in grid)):
        raise PrecheckBlocked("daily snapshot or timeline differs from the protocol")
    log(f"dataset PASS: {len(timeline)} sessions")

    cohort = C.build(manifest.symbols, view_root, decision_last=R6.decision_last(),
                     workers=workers, progress=None)
    raw = {"symbols": cohort.symbols.astype(str), "sessions": cohort.sessions.astype(str),
           **cohort.values}
    v1_rows = E1D.build(raw, daily, benchmark, e1_rules)
    v1_source = R6.canonical_sha256({"tape": manifest.digest,
                                     "daily_read_set": history.freeze.d_read_digest,
                                     "e1_rules": e1_rules.checksum})
    reproduction = {"universe_rows": len(v1_rows),
                    "h5_rows": sum(s.candidate_count for s in
                                   evaluate_signals(session_frames(v1_rows), v1_source))}
    if reproduction != R6.DOCUMENTED_DEVELOPMENT:
        raise PrecheckBlocked(f"V1 reproduction {reproduction}")
    log(f"V1 reproduction MATCH {reproduction}")

    rows = B.build_rows(view_root, manifest.symbols, [s.isoformat() for s in timeline],
                        workers=workers, progress=None)
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
        origins[session] = {s: B.origin(bool(dailies[session].has_open_d[columns[s]]),
                                        rows[s][session].has_0930_open) for s in frame.symbols}
    universe_rows = sum(len(f.symbols) for f in frames.values())
    attribution = R3.attribute(v1_rows, frames, origins)
    if not attribution["pass"]:
        raise PrecheckBlocked(f"attribution {attribution}")
    log(f"V1.1 universe {universe_rows}; attribution PASS")

    source_digest = R6.canonical_sha256({"tape": manifest.digest,
                                         "daily_read_set": history.freeze.d_read_digest,
                                         "trading_v1_1": decision.RULES_CANONICAL_SHA256})
    seals = {s: decision.seal(f, source_digest=source_digest) for s, f in frames.items()}
    sample = B.pit_sample(manifest.symbols)
    poisoned = B.poison_rows(view_root, sample, [s.isoformat() for s in timeline], workers=workers)
    poison_daily = B.daily_sessions(panel, grid, [index[s] for s in timeline], calendar, poison=True)
    moved = 0
    for mode in ("noise", "delete"):
        swapped = dict(rows)
        for symbol in sample:
            swapped[symbol] = {d: B.SymbolRow(row, False, {}, {})
                               for d, row in poisoned[symbol][mode].items() if row is not None}
        for session in timeline:
            frame = B.frame_for(session, swapped, poison_daily[session], columns,
                                spy_close_previous=float(benchmark[index[session] - 1]))
            base = seals.get(session)
            if not frame.symbols and base is None:
                continue
            if base is None or decision.seal(frame, source_digest=source_digest).seal_digest != base.seal_digest:
                moved += 1
    daily_same = all(np.array_equal(poison_daily[s].eligible, dailies[s].eligible) for s in timeline)
    if moved or not daily_same:
        raise PrecheckBlocked(f"PIT poison moved {moved} seals; daily unchanged {daily_same}")
    log("PIT poison PASS")

    identity = {**checksums, "tape": manifest.digest, "legacy_minute": view["legacy_minute_digest"],
                "daily_freeze": history.freeze.freeze_digest,
                "daily_read_set": history.freeze.d_read_digest,
                "e1_rules": e1_rules.checksum, "e0_rules": e0_rules.checksum}
    prechecks = {"chain_ancestry": ancestry, "dataset_binding": True,
                 "v1_reproduction": reproduction, "attribution": attribution["pass"],
                 "rows_by_origin": R3.structural_delta(frames, origins)["rows_by_origin"],
                 "pit_poison": {"sample_symbols": len(sample), "seal_digests_moved": moved,
                                "daily_unchanged": daily_same, "verdict": "PASS"}}
    return Prepared(calendar, root, view_root, timeline, frames, origins, rows, seals,
                    universe_rows, checksums, identity, prechecks, manifest, history, e0_rules)
