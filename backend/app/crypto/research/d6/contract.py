"""The frozen D6-A contract, loaded as the single source of every fixed value.

Nothing in this package may carry a second copy of a threshold. The contract file is the
authority, and a loaded contract whose sha256 does not match the freeze record is refused.

Two of the contract's fixed values live inside formula and threshold *strings* rather than in
numeric fields (`exit.risk.formula`, most `hard_filters[].threshold`). Parsing English is not a
defensible way to read a preregistration, so instead this module binds each of them by exact
string equality: the literal the contract must contain is written here next to the number it
stands for, and a mismatch raises `ContractMismatch` instead of silently using a stale constant.
That makes drift loud. It also means this file must be re-read, not patched, if the contract is
ever re-frozen as V2.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_JSON = REPO_ROOT / "data/research/crypto/d6/strategy_contract_v1.json"
CONTRACT_MD = REPO_ROOT / "docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md"
FREEZE_JSON = REPO_ROOT / "data/runtime/crypto/d6/contract_freeze_v1.json"

STRATEGY_ID = "FDN-V1"
STRATEGY_VERSION = "V1"
CANDIDATE_ID = "CAND-1"

#: Exact strings the contract must contain, paired with the value this package reads from them.
#: Left side is quoted from the frozen contract; right side is what the code is allowed to use.
STRING_BINDINGS: tuple[tuple[str, str, str, Any], ...] = (
    # (section path, label, literal that must appear in the contract, bound value)
    ("exit.risk.formula", "stop", "entry * (1 - clip(2.5 * f_rv24h * sqrt(240), 0.01, 0.10))",
     {"sigma_multiplier": 2.5, "horizon_bars": 240, "dist_min": 0.01, "dist_max": 0.10}),
    ("sizing.formula", "sizing",
     "notional = equity * 0.005 / stop_dist; capped at equity * 1.0; capped at SAFE_MAX qty",
     {"risk_budget": 0.005, "notional_cap_over_equity": 1.0}),
    ("hard_filters.H1", "data_stale", "last 1m bar close older than 90s", {"max_age_ms": 90_000}),
    ("hard_filters.H2", "oi_stale", "last OI record older than 15min", {"max_age_ms": 900_000}),
    ("hard_filters.H3", "insufficient_history", "bucket window valid fraction < 0.5",
     {"min_valid_fraction": 0.5}),
    ("hard_filters.H4", "feature_nan", "any of the 4 features is NaN", {"feature_count": 4}),
    ("hard_filters.H5", "vol_low", "volatility label == LOW", {"blocked_label": "LOW"}),
    ("hard_filters.H6", "spread_wide", "(ask-bid)/mid > 5bp", {"max_spread_bp": 5.0}),
    ("hard_filters.H7", "depth_short", "qty > SAFE_MAX from sizing.max_entry()", {}),
    ("hard_filters.H8", "cooldown", "< 60min since last exit", {"cooldown_ms": 3_600_000}),
    ("hard_filters.H9", "position_open", "auto position already open", {}),
    ("hard_filters.H10", "daily_loss_guard", "UTC-day realized pnl <= -2.0% of capital",
     {"guard_pct": 2.0}),
    ("hard_filters.H11", "consecutive_loss", ">= 4 consecutive losing trades -> 24h block",
     {"max_consecutive": 4, "block_ms": 86_400_000}),
    ("hard_filters.H12", "funding_window", "< 5min to next funding settlement",
     {"min_lead_ms": 300_000}),
    ("hard_filters.H13", "ledger_divergence", "recovery refused -> EMERGENCY_STOP", {}),
)

#: Numeric fields that must agree with the value bound from a string, so the two never drift.
CROSS_CHECKS: tuple[tuple[str, float, str], ...] = (
    ("entry.cooldown_min", 60.0, "cooldown"),
    ("account_risk.cooldown_min", 60.0, "cooldown"),
    ("account_risk.daily_loss_guard_pct", 2.0, "daily_loss_guard"),
    ("account_risk.consecutive_loss_guard", 4.0, "consecutive_loss"),
    ("account_risk.consecutive_loss_block_hours", 24.0, "consecutive_loss"),
    ("bucketing.min_valid_fraction", 0.5, "insufficient_history"),
    ("sizing.max_notional_over_equity", 1.0, "sizing"),
    ("exit.max_hold_min", 240.0, "stop"),
)


class ContractError(RuntimeError):
    """Base class so callers can refuse to run rather than fall back to a default."""

    code = "CONTRACT_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class ContractHashMismatch(ContractError):
    code = "CONTRACT_HASH_MISMATCH"


class ContractMismatch(ContractError):
    code = "CONTRACT_MISMATCH"


class ContractIncomplete(ContractError):
    code = "CONTRACT_INCOMPLETE"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dig(doc: dict[str, Any], path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        if isinstance(node, list):
            match = [item for item in node if item.get("id") == part]
            if not match:
                raise ContractIncomplete(f"{path}: no entry with id {part}")
            node = match[0]
            continue
        if not isinstance(node, dict) or part not in node:
            raise ContractIncomplete(f"missing field: {path}")
        node = node[part]
    return node


@dataclass(frozen=True)
class Contract:
    """A validated view of the frozen contract. Every fixed value in this package comes from here."""

    doc: dict[str, Any]
    sha256: str
    bound: dict[str, dict[str, Any]]

    # --- identity -------------------------------------------------------------------------
    @property
    def strategy_id(self) -> str:
        return STRATEGY_ID

    @property
    def strategy_version(self) -> str:
        return STRATEGY_VERSION

    @property
    def strategy_name(self) -> str:
        return self.candidate["name"]

    @property
    def candidate(self) -> dict[str, Any]:
        return _dig(self.doc, f"candidates.{CANDIDATE_ID}")

    # --- score ----------------------------------------------------------------------------
    @property
    def score_components(self) -> list[dict[str, Any]]:
        return self.doc["score"]["long_components"]

    @property
    def scale_max(self) -> int:
        return int(self.doc["score"]["scale_max"])

    @property
    def long_entry_min_score(self) -> int:
        return int(self.doc["score"]["thresholds"]["LONG_ENTRY_MIN_SCORE"])

    @property
    def score_separation_min(self) -> int:
        return int(self.doc["score"]["thresholds"]["SCORE_SEPARATION_MIN"])

    @property
    def mandatory_minimums(self) -> list[dict[str, Any]]:
        return self.doc["score"]["mandatory_minimums"]

    @property
    def short_enabled(self) -> bool:
        return self.candidate["sides"]["SHORT"] != "SHORT_DISABLED_FOR_V1"

    # --- features -------------------------------------------------------------------------
    @property
    def feature_ids(self) -> tuple[str, ...]:
        return tuple(f["id"] for f in self.doc["features"])

    @property
    def bucketed_features(self) -> tuple[str, ...]:
        return tuple(self.doc["bucketing"]["applies_to"])

    @property
    def bucket_quantiles(self) -> tuple[float, ...]:
        return tuple(self.doc["bucketing"]["quantiles"])

    @property
    def bucket_window_days(self) -> int:
        return int(self.doc["bucketing"]["window_days"])

    @property
    def vol_low_below(self) -> float:
        return float(self.doc["volatility_regime"]["low_below"])

    @property
    def vol_high_above(self) -> float:
        return float(self.doc["volatility_regime"]["high_above"])

    # --- exits / sizing / leverage --------------------------------------------------------
    @property
    def max_hold_min(self) -> int:
        return int(self.doc["exit"]["max_hold_min"])

    @property
    def stop(self) -> dict[str, Any]:
        return self.bound["stop"]

    @property
    def sizing(self) -> dict[str, Any]:
        return self.bound["sizing"]

    @property
    def leverage(self) -> float:
        return float(self.doc["leverage"]["auto_v1"])

    @property
    def qty_step(self) -> float:
        return float(self.doc["sizing"]["qty_step"])

    @property
    def min_order_qty(self) -> float:
        return float(self.doc["sizing"]["min_order_qty"])

    @property
    def min_notional_usdt(self) -> float:
        return float(self.doc["sizing"]["min_notional_usdt"])

    def filter_bound(self, label: str) -> dict[str, Any]:
        return self.bound[label]

    @property
    def hard_filter_ids(self) -> tuple[str, ...]:
        return tuple(f["id"] for f in self.doc["hard_filters"])

    def hard_filter(self, filter_id: str) -> dict[str, Any]:
        return _dig(self.doc, f"hard_filters.{filter_id}")


def _validate(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Structural checks plus the string bindings. Raises rather than repairing."""
    required = {
        "record", "authority", "scope", "candidates", "features", "bucketing",
        "volatility_regime", "hard_filters", "score", "entry", "exit", "leverage",
        "sizing", "account_risk", "datasets",
    }
    missing = required - set(doc)
    if missing:
        raise ContractIncomplete(f"missing top-level fields: {sorted(missing)}")

    if doc["record"] != "CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1":
        raise ContractMismatch(f"unexpected record: {doc['record']}")

    score = doc["score"]
    total = sum(int(c["max"]) for c in score["long_components"])
    if total != int(score["scale_max"]):
        raise ContractMismatch(f"score components total {total}, scale_max {score['scale_max']}")

    names = [c["name"] for c in score["long_components"]]
    inputs = [c["input"] for c in score["long_components"]]
    if len(set(names)) != len(names) or len(set(inputs)) != len(inputs):
        raise ContractMismatch("duplicate score component name or input")

    declared = {f["id"] for f in doc["features"]}
    unknown = set(inputs) - declared
    if unknown:
        raise ContractMismatch(f"score reads undeclared features: {sorted(unknown)}")

    threshold = score["thresholds"]["LONG_ENTRY_MIN_SCORE"]
    if not 0 < threshold < score["scale_max"]:
        raise ContractMismatch(f"entry threshold out of range: {threshold}")
    mandatory = sum(int(m["min"]) for m in score["mandatory_minimums"])
    if mandatory >= threshold:
        raise ContractMismatch("mandatory minimums alone reach the entry threshold")

    if score["thresholds"]["SHORT_ENTRY_MIN_SCORE"] is not None:
        raise ContractMismatch("SHORT is disabled in V1 but carries an entry threshold")

    regime = doc["volatility_regime"]
    if not regime["low_below"] < regime["high_above"]:
        raise ContractMismatch("volatility cutoffs are not ordered")
    if regime.get("recomputed_for_d6") is not False:
        raise ContractMismatch("volatility cutoffs must be inherited, not refitted")

    if doc["scope"]["maker_allowed"] or doc["costs"]["maker_used"]:
        raise ContractMismatch("maker execution is banned in V1")
    if doc["scope"]["liquidation_feature_allowed"]:
        raise ContractMismatch("liquidation data is banned as a V1 feature")
    if doc["scope"]["ml_allowed"] or doc["scope"]["llm_decision_allowed"]:
        raise ContractMismatch("ML and LLM decisions are banned")
    if doc["leverage"]["auto_v1"] > doc["account_risk"]["max_leverage"]:
        raise ContractMismatch("auto leverage exceeds the account risk ceiling")
    if not doc["leverage"]["fixed"]:
        raise ContractMismatch("auto leverage must be fixed")
    if doc["exit"]["other_exits_allowed"]:
        raise ContractMismatch("only the three contracted exits are allowed")

    bound: dict[str, dict[str, Any]] = {}
    for path, label, literal, values in STRING_BINDINGS:
        actual = _dig(doc, path)
        if isinstance(actual, dict):
            actual = actual.get("threshold", actual.get("formula"))
        if actual != literal:
            raise ContractMismatch(
                f"{path}: contract says {actual!r}, this build is bound to {literal!r}")
        bound[label] = dict(values)

    for path, expected, label in CROSS_CHECKS:
        actual = float(_dig(doc, path))
        if actual != expected:
            raise ContractMismatch(f"{path}: expected {expected}, contract says {actual}")
        if label not in bound:
            raise ContractIncomplete(f"cross-check {path} references unbound label {label}")

    # The two independent statements of the same numbers must agree.
    if bound["cooldown"]["cooldown_ms"] != int(doc["entry"]["cooldown_min"]) * 60_000:
        raise ContractMismatch("cooldown stated twice with different values")
    if bound["sizing"]["risk_budget"] * 100 != float(doc["sizing"]["risk_budget_pct_of_equity"]):
        raise ContractMismatch("risk budget stated twice with different values")
    if bound["stop"]["horizon_bars"] != int(doc["exit"]["max_hold_min"]):
        raise ContractMismatch("stop horizon and max hold disagree")
    if bound["daily_loss_guard"]["guard_pct"] != float(doc["account_risk"]["daily_loss_guard_pct"]):
        raise ContractMismatch("daily loss guard stated twice with different values")
    return bound


def load(path: Path | None = None, *, freeze_path: Path | None = None,
         expected_sha256: str | None = None) -> Contract:
    """Load, hash-check and validate. Any disagreement raises; there is no fallback."""
    path = path or CONTRACT_JSON
    if not path.exists():
        raise ContractIncomplete(f"contract file not found: {path}")
    digest = sha256_file(path)

    if expected_sha256 is None:
        freeze_path = freeze_path or FREEZE_JSON
        if not freeze_path.exists():
            raise ContractIncomplete(f"freeze record not found: {freeze_path}")
        freeze = json.loads(freeze_path.read_text())
        expected_sha256 = freeze["machine_readable_sha256"]
        md_expected = freeze["sha256"]
        if CONTRACT_MD.exists() and sha256_file(CONTRACT_MD) != md_expected:
            raise ContractHashMismatch(
                f"{CONTRACT_MD.name}: {sha256_file(CONTRACT_MD)} != frozen {md_expected}")

    if digest != expected_sha256:
        raise ContractHashMismatch(f"{path.name}: {digest} != frozen {expected_sha256}")

    doc = json.loads(path.read_text())
    return Contract(doc=doc, sha256=digest, bound=_validate(doc))
