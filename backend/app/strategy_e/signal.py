"""Deterministic Strategy E H5 trading-signal boundary.

There is deliberately no H5 expression in this module. Research's frozen ``mask("H5", ...)`` is
the sole evaluator, confirmation calls it, Forward calls it, and this Trading wrapper calls it.
Execution and risk eligibility happen after this result and cannot alter its Alpha mask.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import numpy as np

from app.backtest.strategy_e1_forward.seal import H5_INPUTS, SEALED_FEATURES, decision_digest
from app.backtest.strategy_e1_premarket.evaluate import mask as research_mask


REPO_ROOT = Path(__file__).resolve().parents[3]
TRADING_RULES_PATH = (
    REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_trading_rules_v1.json"
)
TRADING_RULES_CANONICAL_SHA256 = (
    "f1534f07688c801f2979491e447eefbb3e62b8045afb9c4ab302b90e4593d4b2"
)
STRATEGY_ID = "STRATEGY_E"
SIGNAL_VERSION = "STRATEGY_E_H5_SIGNAL_V1"
DECISION_TIME_ET = "09:25"
LAST_USABLE_BAR_START_ET = "09:24"


class SignalContractError(ValueError):
    """Fail-closed rejection at the Strategy E signal boundary."""


@dataclass(frozen=True)
class SignalFrame:
    """An already eligible 09:25 PIT feature frame; no execution fields are consulted."""

    session: date
    symbols: Sequence[str]
    features: Mapping[str, Sequence[float] | np.ndarray]
    source_digest: str
    decision_time_et: str = DECISION_TIME_ET
    latest_feature_bar_start_et: str = LAST_USABLE_BAR_START_ET
    rules_digest: str = TRADING_RULES_CANONICAL_SHA256


@dataclass(frozen=True)
class SignalResult:
    strategy_id: str
    signal_version: str
    session: date
    decision_time_et: str
    rules_digest: str
    source_digest: str
    eligible_count: int
    candidate_count: int
    candidate_symbols: tuple[str, ...]
    h5_mask: tuple[bool, ...]
    decision_digest: str


def _canonical_checksum(payload: Mapping) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_frozen_rules(path: Path = TRADING_RULES_PATH) -> Mapping:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SignalContractError(f"cannot load frozen Strategy E trading rules: {error}") from error
    found = _canonical_checksum(payload)
    if found != TRADING_RULES_CANONICAL_SHA256:
        raise SignalContractError(
            f"Strategy E trading rules digest mismatch: {found} != "
            f"{TRADING_RULES_CANONICAL_SHA256}"
        )
    if payload.get("declaration", {}).get("contract_id") != "STRATEGY_E_TRADING_RULES_V1":
        raise SignalContractError("unsupported Strategy E trading rules version")
    if tuple(payload.get("alpha", {}).get("allowed_fields", ())) != H5_INPUTS:
        raise SignalContractError("frozen trading Alpha fields do not match Research H5 inputs")
    return MappingProxyType(payload)


def _minute_of_day(value: str, field: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as error:
        raise SignalContractError(f"invalid {field}: {value!r}") from error
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SignalContractError(f"invalid {field}: {value!r}")
    return hour * 60 + minute


def _validated_features(frame: SignalFrame) -> dict[str, np.ndarray]:
    missing = [name for name in SEALED_FEATURES if name not in frame.features]
    if missing:
        raise SignalContractError(f"signal frame is missing required sealed features: {missing}")
    count = len(frame.symbols)
    arrays: dict[str, np.ndarray] = {}
    for name in SEALED_FEATURES:
        value = np.asarray(frame.features[name])
        if value.ndim != 1 or len(value) != count:
            raise SignalContractError(
                f"feature {name!r} must be one-dimensional with {count} rows"
            )
        if not np.issubdtype(value.dtype, np.number) or np.issubdtype(value.dtype, np.bool_):
            raise SignalContractError(f"feature {name!r} must contain numeric values")
        arrays[name] = value
    return arrays


def evaluate_h5_signal(frame: SignalFrame, *, rules_path: Path = TRADING_RULES_PATH) -> SignalResult:
    """Evaluate the frozen Research H5 and return its deterministic Trading identity.

    ``candidate_symbols`` is lexicographically canonicalized solely for stable identity. It is not
    the still-TBD multi-candidate priority policy and conveys no execution preference.
    """
    rules = _load_frozen_rules(rules_path)
    if frame.rules_digest != TRADING_RULES_CANONICAL_SHA256:
        raise SignalContractError("input rules digest does not match frozen Strategy E V1")
    if frame.decision_time_et != rules["decision"]["signal_cutoff_et"]:
        raise SignalContractError("signal decision cutoff is not the frozen 09:25 ET cutoff")
    latest = _minute_of_day(frame.latest_feature_bar_start_et, "latest feature bar start")
    cutoff = _minute_of_day(rules["decision"]["last_usable_bar_start_et"], "rules cutoff")
    if latest > cutoff:
        raise SignalContractError("signal frame contains information after the 09:25 ET cutoff")
    if not frame.source_digest:
        raise SignalContractError("source digest is required")

    symbols = tuple(str(symbol) for symbol in frame.symbols)
    if any(not symbol for symbol in symbols):
        raise SignalContractError("candidate identifiers must be non-empty")
    if len(set(symbols)) != len(symbols):
        raise SignalContractError("eligible frame contains duplicate symbol identifiers")
    features = _validated_features(frame)
    try:
        selected = research_mask("H5", features)
    except (KeyError, TypeError, ValueError) as error:
        raise SignalContractError(f"Research H5 rejected the signal frame: {error}") from error
    if selected.dtype != np.bool_ or selected.shape != (len(symbols),):
        raise SignalContractError("Research H5 returned an invalid mask")

    candidates = tuple(sorted(symbol for symbol, keep in zip(symbols, selected) if bool(keep)))
    digest = decision_digest(symbols, features, selected)
    return SignalResult(
        strategy_id=STRATEGY_ID,
        signal_version=SIGNAL_VERSION,
        session=frame.session,
        decision_time_et=DECISION_TIME_ET,
        rules_digest=frame.rules_digest,
        source_digest=frame.source_digest,
        eligible_count=len(symbols),
        candidate_count=len(candidates),
        candidate_symbols=candidates,
        h5_mask=tuple(bool(value) for value in selected),
        decision_digest=digest,
    )
