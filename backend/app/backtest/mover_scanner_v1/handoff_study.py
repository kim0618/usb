"""The V1.1 handoff validation: 83 sessions, three arms, no replay and no PnL.

The question is narrow. V1 already resolved the structural starvation; V1.1 claims only that the
same discovery, handed off through the actionability mask, wastes fewer GPT calls without
undoing what V1 fixed. So this module measures four things and nothing else:

1. **Sizes.** Discovery pool, actionable pool, and the handoff that comes out of it, per session.
2. **Slot recovery.** How many V1 slots the mask refuses, and how many of them are refilled from
   discovery ranks below V1's own cut. That is the GPT budget question, stated as a count.
3. **Quality, three arms side by side.** The legacy trade-value scanner, V1's raw TOP8 and V1.1's
   actionable TOP8, all described by one premarket engine and judged by the deployed gate's own
   arithmetic, exactly as V1's comparison already does.
4. **Diversity.** Whether masking has quietly walked the output back toward the legacy arm's
   fixed mega-cap list, which would be a failure even if every other number improved.

The decision thresholds in ``DECISION`` are declared here, above the run, so the verdict is a
measurement against a stated bar rather than a reading of whatever came out. No return is
computed anywhere in this file, no replay is run, and nothing outside the V1.1 report directory
is written.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from app.backtest.mover_scanner_v1 import actionability as A
from app.backtest.mover_scanner_v1 import compare as C
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.run import StudyHardFail, StudyResult, run_study
from app.backtest.mover_scanner_v1.scan import MoverCandidate, SessionScan
from app.strategy.config import StrategyConfig

REPORT_DIR = "data/runtime/research_reports/mover_scanner_v1_1"

#: The bar for READY_FOR_FORWARD_GPT, declared before the run (section O).
#:
#: ``starvation_*`` keep V1's achievement: the legacy arm produced 0.80 gate-pass candidates a
#: session and had one at all on 45.8% of sessions, so V1.1 must stay a multiple above that,
#: not merely ahead of it. ``waste_*`` is the new claim and is absolute: the mask exists to make
#: the unactionable slot rate zero.
#:
#: ``actionable_pool_median_min`` is 5 rather than 8, and the reason is structural rather than
#: empirical: the handoff contract fixes discovery at a 25-member pool and forbids widening it,
#: and a 25-name pool intersected with a 13-point-wide gap band cannot be expected to hold
#: eight survivors. An 8 bar would therefore measure ``pool_size``, not the handoff. 5 is the
#: contract's own number: section F enumerates 8 / 5 / 2 / 0 as correct outputs and section O
#: asks for "mostly 5-8". The full distribution is reported beside the check so the margin is
#: visible rather than hidden behind a boolean.
#:
#: The diversity bar is set well inside V1's measured 0.304 repeat ratio and 0.941 turnover, so
#: only a real collapse toward the legacy structure trips it.
DECISION: Mapping[str, Any] = {
    "starvation_both_pass_per_session_min": 2.0,
    "starvation_sessions_with_any_both_pass_share_min": 0.90,
    "waste_invalid_slot_rate_max": 0.0,
    "waste_must_beat_v1": True,
    "actionable_pool_median_min": 5,
    "output_at_least_five_share_min": 0.80,
    "diversity_repeat_ratio_max": 0.50,
    "diversity_turnover_min": 0.60,
    "diversity_unique_symbols_min": 100,
    "diversity_top_repeated_share_max": 0.25,
}


def described_from(candidate: MoverCandidate) -> C.Described:
    """A pool candidate as ``compare``'s description, with no second computation.

    Both the V1 and the V1.1 arm are made of discovery-pool candidates, so describing them from
    the candidate itself keeps the two arms on bit-identical numbers instead of re-reading the
    panel through a second path. ``covered`` is True by construction: an uncovered symbol never
    becomes a candidate.
    """
    return C.Described(
        symbol=candidate.symbol, session_date=candidate.session_date, covered=True,
        gap_pct=candidate.gap_pct, pm_bars=candidate.pm_bars, pm_volume=candidate.pm_volume,
        pm_dollar_volume=candidate.pm_dollar_volume, pm_rvol=candidate.pm_rvol,
        addv20_dollar=candidate.addv20_dollar, gate=candidate.gate)


@dataclass(frozen=True)
class SessionRow:
    """One session of the handoff stage, as the report prints it."""

    session_date: date
    eligible: int
    discovery_pool_size: int
    discovery_top_size: int
    actionable_pool_size: int
    handoff_size: int
    invalid_removed: int
    replacement_added: int
    replacement_ranks: tuple[int, ...]
    rejection_counts: Mapping[str, int]
    discovery_top_symbols: tuple[str, ...]
    handoff_symbols: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = {key: value for key, value in self.__dict__.items()}
        payload["session_date"] = self.session_date.isoformat()
        payload["replacement_ranks"] = list(self.replacement_ranks)
        payload["rejection_counts"] = dict(self.rejection_counts)
        payload["discovery_top_symbols"] = list(self.discovery_top_symbols)
        payload["handoff_symbols"] = list(self.handoff_symbols)
        return payload


def _slot_quality(selections: Sequence[tuple[date, tuple[str, ...]]],
                  described: Mapping[tuple[date, str], C.Described],
                  rule: A.HandoffRule) -> dict[str, Any]:
    """Per-slot readings the arm summary does not carry: the mask's own compliance and waste.

    ``invalid_slot_rate`` is the share of handed-off slots the current contract cannot admit at
    all, measured by the same mask on the same 09:15 gap, which is the number this stage exists
    to move. The gate figures beside it are the 04:00-09:30 reading and belong to the gate.
    """
    rows = [described[(session, symbol)] for session, symbols in selections
            for symbol in symbols if (session, symbol) in described]
    verdicts: list[A.Actionability] = []
    for row in rows:
        verdicts.append(A.Actionability.DIRECTION_NOT_ACTIONABLE if row.gap_pct is None
                        else rule.verdict(row.gap_pct))
    slots = len(rows)
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[str(verdict)] = counts.get(str(verdict), 0) + 1
    passes = sum(1 for verdict in verdicts if verdict is A.Actionability.PASS)
    sessions = len(selections)
    picked = [symbol for _, symbols in selections for symbol in symbols]
    repeats: dict[str, int] = {}
    for symbol in picked:
        repeats[symbol] = repeats.get(symbol, 0) + 1
    top_symbol = max(repeats.items(), key=lambda kv: (kv[1], kv[0])) if repeats else (None, 0)
    return {
        "slots": slots,
        "gap_band_compliance_rate": None if not slots else passes / slots,
        "invalid_slot_rate": None if not slots else 1.0 - passes / slots,
        "verdict_counts": dict(sorted(counts.items())),
        "volume_gate_pass_rate_per_slot": None if not slots else
        sum(1 for row in rows if row.gate.volume_pass) / slots,
        "gap_gate_pass_rate_per_slot": None if not slots else
        sum(1 for row in rows if row.gate.gap_pass) / slots,
        "full_gate_pass_rate_per_slot": None if not slots else
        sum(1 for row in rows if row.gate.both_pass) / slots,
        "full_gate_pass_per_session": None if not sessions else
        sum(1 for row in rows if row.gate.both_pass) / sessions,
        "most_repeated_symbol": top_symbol[0],
        "most_repeated_symbol_sessions": top_symbol[1],
        "most_repeated_symbol_share_of_sessions": None if not sessions else
        top_symbol[1] / sessions,
    }


@dataclass(frozen=True)
class HandoffStudyResult:
    config: MoverScannerConfig
    rule: A.HandoffRule
    study: StudyResult
    selections: tuple[A.SessionHandoff, ...]
    rows: tuple[SessionRow, ...]
    arms: Mapping[str, C.ArmSummary]
    slot_quality: Mapping[str, Mapping[str, Any]]

    # -- sizes (section I) ---------------------------------------------------------------
    @property
    def sizes(self) -> dict[str, Any]:
        discovery = [row.discovery_pool_size for row in self.rows]
        actionable = [row.actionable_pool_size for row in self.rows]
        handoff = [row.handoff_size for row in self.rows]
        maximum = self.rule.handoff_maximum
        return {
            "sessions": len(self.rows),
            "discovery_pool_average": float(np.mean(discovery)) if discovery else None,
            "discovery_pool_median": float(median(discovery)) if discovery else None,
            "discovery_pool_minimum": int(min(discovery)) if discovery else None,
            "actionable_pool_average": float(np.mean(actionable)) if actionable else None,
            "actionable_pool_median": float(median(actionable)) if actionable else None,
            "actionable_pool_minimum": int(min(actionable)) if actionable else None,
            "actionable_pool_maximum": int(max(actionable)) if actionable else None,
            "handoff_average": float(np.mean(handoff)) if handoff else None,
            "handoff_median": float(median(handoff)) if handoff else None,
            "handoff_minimum": int(min(handoff)) if handoff else None,
            "sessions_output_full": sum(1 for size in handoff if size == maximum),
            "sessions_output_five_to_seven": sum(1 for size in handoff if 5 <= size < maximum),
            "sessions_output_one_to_four": sum(1 for size in handoff if 1 <= size <= 4),
            "sessions_output_zero": sum(1 for size in handoff if size == 0),
            "sessions_output_at_least_five": sum(1 for size in handoff if size >= 5),
            "sessions_output_at_least_five_share": None if not handoff else
            sum(1 for size in handoff if size >= 5) / len(handoff),
            "sessions_output_at_least_four_share": None if not handoff else
            sum(1 for size in handoff if size >= 4) / len(handoff),
            "handoff_size_histogram": {str(size): sum(1 for value in handoff if value == size)
                                       for size in range(0, maximum + 1)},
        }

    # -- slot recovery (section K) -------------------------------------------------------
    @property
    def slot_recovery(self) -> dict[str, Any]:
        removed = sum(row.invalid_removed for row in self.rows)
        added = sum(row.replacement_added for row in self.rows)
        v1_slots = sum(row.discovery_top_size for row in self.rows)
        v11_slots = sum(row.handoff_size for row in self.rows)
        ranks = [rank for row in self.rows for rank in row.replacement_ranks]
        rejections: dict[str, int] = {}
        for row in self.rows:
            for reason, count in row.rejection_counts.items():
                rejections[reason] = rejections.get(reason, 0) + count
        return {
            "v1_slots": v1_slots,
            "v1_invalid_slots_removed": removed,
            "v1_invalid_slot_rate": None if not v1_slots else removed / v1_slots,
            "v11_slots": v11_slots,
            "replacement_added": added,
            "replacement_rate_of_v11_slots": None if not v11_slots else added / v11_slots,
            "replacement_discovery_rank_median": float(median(ranks)) if ranks else None,
            "replacement_discovery_rank_maximum": int(max(ranks)) if ranks else None,
            "sessions_with_a_removal": sum(1 for row in self.rows if row.invalid_removed),
            "sessions_fully_refilled": sum(1 for row in self.rows if row.invalid_removed
                                           and row.handoff_size == row.discovery_top_size),
            "pool_rejections_total": dict(sorted(rejections.items())),
        }

    # -- the decision (section O) --------------------------------------------------------
    @property
    def decision(self) -> dict[str, Any]:
        v11 = self.arms["V11_ACTIONABLE_TOP8"]
        v11_slots = self.slot_quality["V11_ACTIONABLE_TOP8"]
        v1_slots = self.slot_quality["V1_RAW_TOP8"]
        legacy = self.arms["CURRENT_SCANNER"]
        sizes, recovery = self.sizes, self.slot_recovery
        v11_invalid = v11_slots["invalid_slot_rate"]
        v1_invalid = v1_slots["invalid_slot_rate"]
        checks = {
            "structural_starvation_resolved":
                v11.both_pass_per_session >= DECISION["starvation_both_pass_per_session_min"]
                and v11.sessions_with_any_both_pass_share
                >= DECISION["starvation_sessions_with_any_both_pass_share_min"],
            "gpt_waste_resolved":
                v11_invalid is not None and v1_invalid is not None
                and v11_invalid <= DECISION["waste_invalid_slot_rate_max"]
                and (v11_invalid < v1_invalid if DECISION["waste_must_beat_v1"] else True),
            "actionable_pool_sufficient":
                sizes["actionable_pool_median"] is not None
                and sizes["actionable_pool_median"] >= DECISION["actionable_pool_median_min"],
            "output_mostly_five_to_eight":
                sizes["sessions_output_at_least_five_share"] is not None
                and sizes["sessions_output_at_least_five_share"]
                >= DECISION["output_at_least_five_share_min"],
            "diversity_held":
                v11.repeat_ratio is not None
                and v11.repeat_ratio <= DECISION["diversity_repeat_ratio_max"]
                and v11.turnover is not None
                and v11.turnover >= DECISION["diversity_turnover_min"]
                and v11.unique_symbols >= DECISION["diversity_unique_symbols_min"]
                and v11_slots["most_repeated_symbol_share_of_sessions"] is not None
                and v11_slots["most_repeated_symbol_share_of_sessions"]
                <= DECISION["diversity_top_repeated_share_max"],
            "no_regression_to_legacy_structure":
                v11.unique_symbols > legacy.unique_symbols
                and (legacy.repeat_ratio is None or v11.repeat_ratio < legacy.repeat_ratio)
                and (legacy.turnover is None or v11.turnover > legacy.turnover),
            "handoff_ranks_contiguous_and_unique": all(
                [item.rank for item in selection.handoff]
                == list(range(1, len(selection.handoff) + 1))
                and len({item.symbol for item in selection.handoff}) == len(selection.handoff)
                and len(selection.handoff) <= self.rule.handoff_maximum
                for selection in self.selections),
        }
        return {
            "thresholds": dict(DECISION),
            "checks": checks,
            "verdict": ("READY_FOR_FORWARD_GPT" if all(checks.values())
                        else "NEEDS_SCANNER_HANDOFF_REVISION"),
            "structural_starvation": ("RESOLVED" if checks["structural_starvation_resolved"]
                                      else "NOT_RESOLVED"),
            "gpt_budget_waste": ("RESOLVED" if checks["gpt_waste_resolved"]
                                 else "NOT_RESOLVED"),
            "note": "The GPT handoff render test (section M) is asserted in "
                    "backend/tests/test_mover_scanner_v1_1.py, not here.",
        }

    def report(self) -> dict[str, Any]:
        strategy = StrategyConfig()
        return {
            "contract_version": self.rule.contract_version,
            "rules_checksum": self.rule.checksum,
            "actionability_rule": self.rule.declaration(),
            "discovery_contract_version": self.config.contract_version,
            "discovery_rules_checksum": self.config.checksum,
            "discovery_rules_changed": False,
            "network_calls": 0,
            "replays": 0,
            "pnl_computed": False,
            "entry_changes": 0,
            "risk_changes": 0,
            "gpt_prompt_changes": 0,
            "production_changes": 0,
            "real_orders": 0,
            "minute_cache": {"directory": str(self.study.cache.directory),
                             "cache_digest": self.study.cache.digest,
                             "sessions": self.study.cache.sessions,
                             "symbols": self.study.cache.symbols,
                             "rows": self.study.cache.rows},
            "scanned_sessions": [day.isoformat() for day in self.study.comparison_sessions],
            "window": {
                "sessions": len(self.study.comparison_sessions),
                "first": self.study.comparison_sessions[0].isoformat(),
                "last": self.study.comparison_sessions[-1].isoformat(),
            },
            "sizes": self.sizes,
            "slot_recovery": self.slot_recovery,
            "arms": {name: arm.as_dict() for name, arm in self.arms.items()},
            "slot_quality": {name: dict(value) for name, value in self.slot_quality.items()},
            "v1_as_published": self.study.new_arm.as_dict(),
            "entry_gate_measured": {
                "gap_min_pct": float(strategy.premarket_gap_min_pct),
                "gap_max_pct": float(strategy.premarket_gap_max_pct),
                "volume_ratio_min": float(strategy.premarket_volume_ratio_min),
                "direction": str(strategy.premarket_gap_direction),
                "window": "04:00-09:30 ET, the gate's own window, measurement only",
            },
            "decision": self.decision,
            "sessions": [row.as_dict() for row in self.rows],
        }


def _session_row(scan: SessionScan, selection: A.SessionHandoff) -> SessionRow:
    return SessionRow(
        session_date=scan.session_date, eligible=scan.eligible,
        discovery_pool_size=selection.discovery_pool_size,
        discovery_top_size=len(selection.discovery_top),
        actionable_pool_size=selection.actionable_pool_size,
        handoff_size=selection.handoff_size, invalid_removed=selection.invalid_removed,
        replacement_added=selection.replacement_added,
        replacement_ranks=selection.replacement_ranks,
        rejection_counts=selection.rejection_counts,
        discovery_top_symbols=tuple(item.symbol for item in selection.discovery_top),
        handoff_symbols=tuple(item.symbol for item in selection.handoff))


def run_handoff_study(repo: Path, *, config: MoverScannerConfig | None = None,
                      strategy: StrategyConfig | None = None,
                      cache_directory: Path | None = None,
                      limit_sessions: int | None = None, progress=None) -> HandoffStudyResult:
    """Run V1's own study, then the mask over its pools. Discovery is not re-implemented."""
    config = config or MoverScannerConfig()
    strategy = strategy or StrategyConfig()
    rule = A.HandoffRule.current(config, strategy)
    study = run_study(repo, config=config, cache_directory=cache_directory,
                      limit_sessions=limit_sessions, progress=progress)
    if study.config.checksum != config.checksum:
        raise StudyHardFail("the discovery rules under measurement are not the declared ones")

    selections: list[A.SessionHandoff] = []
    rows: list[SessionRow] = []
    described: dict[tuple[date, str], C.Described] = {}
    for scan in study.scans:
        selection = A.select(scan.session_date, scan.pool, rule)
        # V1.1 attributes a replacement to the slot V1 would have filled, so the reproduced
        # discovery order must be V1's own output, symbol for symbol.
        if tuple(item.symbol for item in selection.discovery_top) != \
                tuple(item.symbol for item in scan.top):
            raise StudyHardFail(
                f"the reproduced discovery order does not match V1's output on "
                f"{scan.session_date.isoformat()}")
        selections.append(selection)
        rows.append(_session_row(scan, selection))
        for candidate in scan.pool:
            described[(scan.session_date, candidate.symbol)] = described_from(candidate)

    v1_selections = [(row.session_date, row.discovery_top_symbols) for row in rows]
    v11_selections = [(row.session_date, row.handoff_symbols) for row in rows]
    arms = {
        "CURRENT_SCANNER": study.current_arm,
        "V1_RAW_TOP8": C.summarize_arm("V1_RAW_TOP8", v1_selections, described,
                                       study.addv_percentiles, study.market_caps),
        "V11_ACTIONABLE_TOP8": C.summarize_arm("V11_ACTIONABLE_TOP8", v11_selections, described,
                                               study.addv_percentiles, study.market_caps),
    }
    slot_quality = {
        "V1_RAW_TOP8": _slot_quality(v1_selections, described, rule),
        "V11_ACTIONABLE_TOP8": _slot_quality(v11_selections, described, rule),
    }
    return HandoffStudyResult(config=config, rule=rule, study=study,
                              selections=tuple(selections), rows=tuple(rows), arms=arms,
                              slot_quality=slot_quality)


def write_artifacts(repo: Path, result: HandoffStudyResult,
                    handoff_session: date | None = None) -> dict[str, str]:
    """Write the V1.1 report, the handoff rows, the pool audit and one GPT handoff sample.

    V1's directory is not touched: V1.1 writes its own, so the V1 artifacts the comparison
    rests on stay exactly as they were published.
    """
    out = repo / REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    header = {"contract_version": result.rule.contract_version,
              "rules_checksum": result.rule.checksum,
              "discovery_rules_checksum": result.config.checksum}
    payloads: dict[str, Any] = {
        "mover_handoff_v1_1_report.json": result.report(),
        "handoff_top8_rows.json": {**header, "rows": [
            row for selection in result.selections
            for row in A.handoff_rows(selection, result.config)]},
        "handoff_pool_audit_rows.json": {**header, "rows": [
            row for selection in result.selections
            for row in A.audit_rows(selection, result.config)]},
    }
    session = handoff_session or (result.rows[-1].session_date if result.rows else None)
    if session is not None:
        chosen = next(item for item in result.selections if item.session_date == session)
        payloads["gpt_handoff_sample.json"] = A.handoff_payload(
            chosen, result.config, datetime.now(timezone.utc))
    written: dict[str, str] = {}
    for name, payload in payloads.items():
        body = json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n"
        (out / name).write_text(body, encoding="utf-8")
        written[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return written
