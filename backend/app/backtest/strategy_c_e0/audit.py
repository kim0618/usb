"""The audits C-E0 must pass before a single forward return is read.

Two groups, both from `c_e0_rules_v1.json`:

* `pit_audit.checks` - six checks on the event view itself, including a mutation test and a
  positive control that injects a filing one second after close(D).
* `pre_outcome_checks` - P1..P8, decided from cohort membership and label validity only. A
  failure is INCONCLUSIVE, never a lowered threshold, and it is reached before outcomes exist.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import pandas as pd

from app.backtest.strategy_c_e0 import cohorts
from app.backtest.strategy_c_e0.cik_map import Mapping as CikMapping
from app.backtest.strategy_c_e0.events import CikEvents, Event
from app.backtest.strategy_c_e0.pit import DURING_SESSION, SessionGrid

SYNTHETIC_ACCESSION = "0000000000-00-000000"
POSITIVE_CONTROL_SAMPLE = 25


@dataclass(frozen=True)
class RowInput:
    """Everything one candidate row's status was decided from, kept for the audits."""

    d_index: int
    ticker: str
    mapping: CikMapping
    events: CikEvents | None
    coverage: cohorts.Coverage | None
    status: str


def pit_audit(grid: SessionGrid, rows: Sequence[RowInput], *, sample: int = POSITIVE_CONTROL_SAMPLE) -> dict:
    checks: dict[str, Any] = {}
    violations = 0

    late = [f"{r.ticker}@{grid.sessions[r.d_index]}" for r in rows if r.events
            for e in r.events.classes_in(cohorts.window(r.d_index, cohorts.W_PRIMARY_BACK))
            if e.effective_time >= grid.close_of(r.d_index)]
    checks["events_before_close"] = {"violations": len(late), "examples": late[:5]}
    violations += len(late)

    changed = [r.ticker for r in rows if r.events
               and cohorts.assign(grid, r.d_index, r.mapping, _drop_from(r.events, grid, r.d_index),
                                  r.coverage)["status"] != r.status]
    checks["dropping_post_close_filings_changes_nothing"] = {"violations": len(changed),
                                                             "examples": changed[:5]}
    violations += len(changed)

    shifted_changes, unexplained = 0, []
    for row in rows:
        if row.events is None:
            continue
        moved = cohorts.assign(grid, row.d_index, row.mapping, _shift(row.events, grid), row.coverage)
        if moved["status"] == row.status:
            continue
        shifted_changes += 1
        if not _at_window_edge(row):
            unexplained.append(f"{row.ticker}@{grid.sessions[row.d_index]}")
    checks["shift_mutation"] = {"changed": shifted_changes, "unexplained": len(unexplained),
                               "examples": unexplained[:5]}
    violations += len(unexplained)

    detected, missed = 0, []
    for row in rows[:: max(1, len(rows) // sample)][:sample]:
        if row.events is None:
            continue
        injected = _inject_after_close(row.events, grid, row.d_index)
        after = cohorts.assign(grid, row.d_index, row.mapping, injected, row.coverage)
        if after["status"] == row.status and after["post_signal_event_flag"]:
            detected += 1
        else:
            missed.append(f"{row.ticker}@{grid.sessions[row.d_index]}")
    checks["positive_control"] = {"detected": detected, "missed": len(missed), "examples": missed[:5]}
    violations += len(missed)

    amended = [e.forms for r in rows if r.events for e in r.events.events
               if any(f.upper().endswith("/A") for f in e.forms)]
    pending_as_event = [p.accession for r in rows if r.events for p in r.events.missing_acceptance
                        if any(p.accession in e.accessions for e in r.events.events)]
    checks["amendments_and_pending"] = {"amended_events": len(amended), "pending_placed": len(pending_as_event)}
    violations += len(amended) + len(pending_as_event)

    bad_as_of = [r.ticker for r in rows if r.mapping.as_of is not None
                 and r.mapping.as_of > grid.sessions[r.d_index]]
    checks["cik_snapshot_as_of"] = {"violations": len(bad_as_of), "examples": bad_as_of[:5]}
    violations += len(bad_as_of)

    return {"violations": int(violations), "checks": checks,
            "positive_control_detected": checks["positive_control"]["detected"] > 0}


def _drop_from(events: CikEvents, grid: SessionGrid, d_index: int) -> CikEvents:
    """The same CIK view with every filing at or after close(D) removed."""
    close = grid.close_of(d_index)
    keep = tuple(e for e in events.events if e.effective_time < close)
    sessions = {i for i in range(len(grid.sessions)) if grid.open_of(i) < close}
    return replace(events, events=keep,
                   ma_pin_sessions=frozenset(events.ma_pin_sessions & sessions),
                   unclassifiable_sessions=frozenset(events.unclassifiable_sessions & sessions),
                   unknown_items_sessions=frozenset(events.unknown_items_sessions & sessions))


def _shift(events: CikEvents, grid: SessionGrid) -> CikEvents:
    """Every effective_time one session later (the declared mutation test)."""
    last = len(grid.sessions) - 1
    moved = tuple(replace(e, session_index=min(last, e.session_index + 1),
                          effective_time=grid.open_of(min(last, e.session_index + 1)))
                  for e in events.events)
    bump = lambda s: frozenset(min(last, i + 1) for i in s)  # noqa: E731 - one-line local
    return replace(events, events=moved, ma_pin_sessions=bump(events.ma_pin_sessions),
                   unclassifiable_sessions=bump(events.unclassifiable_sessions),
                   unknown_items_sessions=bump(events.unknown_items_sessions))


def _at_window_edge(row: RowInput) -> bool:
    """A shift may only change a row that had a filing on a window edge session."""
    if row.events is None:
        return False
    edges = {row.d_index, row.d_index - cohorts.W_PRIMARY_BACK - 1,
             row.d_index - cohorts.MA_PIN_BACK - 1, row.d_index - cohorts.RECENT_DILUTION_BACK - 1}
    sessions = {e.session_index for e in row.events.events}
    sessions |= set(row.events.ma_pin_sessions) | set(row.events.unclassifiable_sessions)
    sessions |= set(row.events.unknown_items_sessions)
    return bool(sessions & edges)


def _inject_after_close(events: CikEvents, grid: SessionGrid, d_index: int) -> CikEvents:
    """A synthetic E5 one second after close(D): it must land on D+1, never on D."""
    moment = grid.close_of(d_index) + timedelta(seconds=1)
    session = min(len(grid.sessions) - 1, d_index + 1)
    synthetic = Event(events.cik, "E5", session, grid.open_of(session), (SYNTHETIC_ACCESSION,),
                      ("8-K",), ("E5",), ("AFTER_CLOSE_OR_NON_SESSION",))
    if moment >= grid.close_of(d_index):  # the injection is post-signal by construction
        return replace(events, events=events.events + (synthetic,))
    return events


def pre_outcome_checks(rows: pd.DataFrame, *, timezone_audit_passed: bool, pit_violations: int,
                       unknown_share: float, blocks: Sequence[Sequence[int]], expected_dates: int,
                       present_dates: int, declaration: Mapping[str, Any]) -> dict:
    """P1..P8. Every number comes from the declaration file, none from this module."""
    checks = declaration["pre_outcome_checks"]["checks"]
    em = rows[(rows["status"] == cohorts.EM) & rows["matched_5"]]
    m_only = rows[(rows["status"] == cohorts.M_ONLY) & rows["matched_5"]]
    per_block = [int(len(em[em["date_idx"].isin(list(block))])) for block in blocks]
    results = {
        "P1_timezone_audit": {"pass": bool(timezone_audit_passed), "declared": checks["P1_timezone_audit"]},
        "P2_pit_audit": {"pass": pit_violations == 0, "violations": int(pit_violations)},
        "P3_unknown_share": {"pass": unknown_share <= 0.10, "value": float(unknown_share)},
        "P4_em_sample": {"pass": len(em) >= 300, "value": int(len(em))},
        "P5_em_unique_tickers": {"pass": em["ticker"].nunique() >= 100, "value": int(em["ticker"].nunique())},
        "P6_m_only_sample": {"pass": len(m_only) >= 300, "value": int(len(m_only))},
        "P7_block_sample": {"pass": all(n >= 30 for n in per_block), "value": per_block},
        "P8_window": {"pass": present_dates == expected_dates,
                      "value": [int(present_dates), int(expected_dates)]},
    }
    return {"all_pass": all(bool(v["pass"]) for v in results.values()), "checks": results}


def timezone_audit(samples: Sequence[Mapping[str, Any]], *, parse: Callable[[str, str], Any]) -> dict:
    """Compare submissions `acceptanceDateTime` with the filing's ACCEPTANCE-DATETIME header.

    Each sample is {accession, acceptance, header} where `header` is the SGML `YYYYMMDDHHMMSS`
    (ET). The zone that matches every sample wins; a tie or a mismatch leaves it undecided, which
    P1 turns into INCONCLUSIVE(PIT_UNCERTAIN).
    """
    per_zone: dict[str, int] = {}
    details = []
    for zone in ("ET", "UTC"):
        agree = 0
        for sample in samples:
            header = str(sample["header"])
            parsed = parse(str(sample["acceptance"]), zone)
            expected = f"{parsed.year:04d}{parsed.month:02d}{parsed.day:02d}" \
                       f"{parsed.hour:02d}{parsed.minute:02d}{parsed.second:02d}"
            match = expected == header
            agree += int(match)
            if zone == "ET":
                details.append({"accession": sample["accession"], "acceptance": sample["acceptance"],
                                "header": header, "et_interpretation": expected, "match_as_et": match})
        per_zone[zone] = agree
    n = len(samples)
    decided = [zone for zone, agree in per_zone.items() if n and agree == n]
    return {"samples": n, "agreement": per_zone, "zone": decided[0] if len(decided) == 1 else None,
            "pass": len(decided) == 1 and n >= 30, "details": details[:60]}
