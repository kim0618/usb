"""The V1.1 09:25 decision seal and the post-seal hand-off to the unchanged E-D2 entry layer.

Order, enforced rather than described:

1. ``seal`` takes a ``DecisionFrame`` (09:25 PIT universe), evaluates H5 through
   ``evaluate_h5_signal`` (Research's mask), and fixes the candidate list and the canonical
   max-3 selection. The digest covers all of it.
2. ``execute`` accepts 09:30 bars only for a verified seal, calls ``build_entry_records``
   unchanged, and refuses the result if its selected set differs from the sealed one. A selected
   symbol without a valid 09:30 bar stays selected and becomes a no-trade; candidate #4 is never
   promoted, because E-D2 selects before it looks at a bar.

No post-09:25 input reaches step 1: the frame carries only 09:25 features, and ``seal`` accepts
nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from app.market.calendar import MarketCalendar
from app.strategy_e import costs, execution, exits, risk
from app.strategy_e.execution import EntryBar, ExecutionBatch, build_entry_records
from app.strategy_e.signal import (
    DECISION_TIME_ET, SIGNAL_VERSION, TRADING_RULES_CANONICAL_SHA256, SignalFrame, SignalResult,
    evaluate_h5_signal,
)
from app.strategy_e_v1_1.universe import UNIVERSE_VERSION, DecisionFrame

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_trading_v1_1_rules.json"
RULES_CANONICAL_SHA256 = (
    "b90573bde8809b0eadd03c9f6c5cae59bf115254b792237df92e0f6eaa1304b6"
)
TRADING_VERSION = "STRATEGY_E_TRADING_V1_1"
SEAL_FORMAT = "strategy-e-v1_1-decision-seal-v1"


class DecisionContractError(ValueError):
    """Fail-closed rejection of the V1.1 rules, a seal, or a post-seal execution."""


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def load_rules(path: Path = RULES_PATH) -> Mapping:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DecisionContractError(f"cannot load Strategy E Trading V1.1 rules: {error}") from error
    found = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if found != RULES_CANONICAL_SHA256:
        raise DecisionContractError(
            f"Strategy E Trading V1.1 rules digest mismatch: {found} != {RULES_CANONICAL_SHA256}")
    declaration = payload.get("declaration", {})
    if (declaration.get("contract_id") != "STRATEGY_E_TRADING_RULES_V1_1"
            or declaration.get("version") != TRADING_VERSION):
        raise DecisionContractError("unsupported Strategy E Trading V1.1 rules version")
    upstream = payload.get("upstream", {})
    expected = {
        "e_d0_rules_canonical_sha256": TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": execution.EXECUTION_RULES_CANONICAL_SHA256,
        "e_d3_horizon_semantics_canonical_sha256": exits.HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": exits.EXIT_RULES_CANONICAL_SHA256,
        "e_d4_cost_rules_canonical_sha256": costs.COST_RULES_CANONICAL_SHA256,
        "e_d5_risk_rules_canonical_sha256": risk.RISK_RULES_CANONICAL_SHA256,
    }
    moved = [key for key, value in expected.items() if upstream.get(key) != value]
    if moved:
        raise DecisionContractError(f"V1.1 upstream differs from the frozen chain: {moved}")
    universe = payload.get("universe", {})
    if (universe.get("version") != UNIVERSE_VERSION
            or universe.get("premarket", {}).get("requires_open_bar_0930") is not False
            or universe.get("daily", {}).get("requires_session_d_daily_open") is not False):
        raise DecisionContractError("V1.1 universe must exclude every post-09:25 availability test")
    selection = payload.get("selection", {})
    if (selection.get("max_selected_candidates") != 3
            or selection.get("priority") != "CANONICAL_SYMBOL_ASCENDING"
            or selection.get("replacement_policy") != "NONE"
            or selection.get("selection_before_entry_validation") is not True):
        raise DecisionContractError("V1.1 selection differs from the frozen E-D2 selection")
    return MappingProxyType(payload)


@dataclass(frozen=True)
class DecisionSeal:
    session: str
    trading_version: str
    universe_version: str
    rules_digest: str
    decision_time_et: str
    signal: SignalResult
    candidates: tuple[str, ...]
    selected: tuple[str, ...]
    not_selected: tuple[str, ...]
    seal_digest: str


def _seal_payload(signal: SignalResult, selected: Sequence[str],
                  not_selected: Sequence[str]) -> dict:
    return {
        "format": SEAL_FORMAT,
        "session": signal.session.isoformat(),
        "trading_version": TRADING_VERSION,
        "universe_version": UNIVERSE_VERSION,
        "rules_digest": RULES_CANONICAL_SHA256,
        "alpha_rules_digest": signal.rules_digest,
        "decision_time_et": signal.decision_time_et,
        "source_digest": signal.source_digest,
        "eligible_count": signal.eligible_count,
        "signal_decision_digest": signal.decision_digest,
        "candidates": list(signal.candidate_symbols),
        "selected": list(selected),
        "not_selected": list(not_selected),
    }


def seal(frame: DecisionFrame, *, source_digest: str) -> DecisionSeal:
    """H5, candidates and the canonical max-3 selection, fixed at 09:25."""
    load_rules()
    limit = int(execution._load_rules()["selection"]["max_selected_candidates"])
    signal = evaluate_h5_signal(SignalFrame(session=frame.session, symbols=frame.symbols,
                                            features=frame.features, source_digest=source_digest))
    candidates = tuple(signal.candidate_symbols)
    if list(candidates) != sorted(candidates):
        raise DecisionContractError("candidate order is not canonical symbol ascending")
    selected, not_selected = candidates[:limit], candidates[limit:]
    digest = hashlib.sha256(_canonical_bytes(_seal_payload(signal, selected, not_selected)))
    return DecisionSeal(frame.session.isoformat(), TRADING_VERSION, UNIVERSE_VERSION,
                        RULES_CANONICAL_SHA256, DECISION_TIME_ET, signal, candidates, selected,
                        not_selected, digest.hexdigest())


def verify(sealed: DecisionSeal) -> None:
    """Refuse a seal whose fields no longer hash to its digest."""
    signal = sealed.signal
    expected = hashlib.sha256(_canonical_bytes(
        _seal_payload(signal, sealed.selected, sealed.not_selected))).hexdigest()
    if (expected != sealed.seal_digest or sealed.session != signal.session.isoformat()
            or sealed.candidates != tuple(signal.candidate_symbols)
            or sealed.selected + sealed.not_selected != sealed.candidates
            or sealed.rules_digest != RULES_CANONICAL_SHA256):
        raise DecisionContractError("decision seal was altered after 09:25")


def execute(sealed: DecisionSeal, entry_bars: Sequence[EntryBar], *,
            calendar: MarketCalendar | None = None) -> ExecutionBatch:
    """Bind 09:30 bars to the sealed selection through E-D2, and nothing else."""
    verify(sealed)
    batch = build_entry_records(sealed.signal, entry_bars, calendar=calendar)
    chosen = tuple(sorted(record.symbol for record in batch.records if record.selected))
    if chosen != sealed.selected:
        raise DecisionContractError(
            f"entry layer selected {chosen}, the 09:25 seal fixed {sealed.selected}")
    return batch
