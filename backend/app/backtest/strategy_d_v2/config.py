"""The declared V2-A rules, read from the declaration files and nowhere else.

``docs/backtest/strategy_d_v2/d_v2a_rules_v1.json`` was declared on 2026-09-20 before any V2-A
data read, feature computation or result. The canonical checksum is not a literal in this
module either: it is read from ``d_v2a_rules_v1.sha256``, the file that recorded it, so an
abbreviated digest can never be pasted into code and quietly accepted.

Every number the study depends on - a lookback, a cap, an embargo constant, a sample threshold -
comes from the JSON. Code carries the *names* of the ten coordinates and the declaration is
checked against them, which is the one direction a mismatch must be caught in: a renamed or
reordered coordinate is a different study.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.backtest.strategy_d_v2.models import FeatureSpec, HardFail, RulesChanged, SampleGate

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS_DIR = REPO_ROOT / "docs/backtest/strategy_d_v2"
RULES_PATH = DOCS_DIR / "d_v2a_rules_v1.json"
CHECKSUM_PATH = DOCS_DIR / "d_v2a_rules_v1.sha256"
CONTRACT_DOC = DOCS_DIR / "D_V2A_SCREENING_CONTRACT_V1.md"
STRATEGY_ID = "MARKET_STRUCTURE_ANALOG_V2"
#: Seed literal of the query hash. Inherited from V1 by declaration so the two studies draw the
#: same sample; it is a string in a hash, never a date.
HASH_SEED = "20260917"

#: The ten coordinates, in declaration order. This tuple is the code side of the contract.
FEATURE_NAMES: tuple[str, ...] = (
    "return_5", "return_20", "return_60", "dist_to_20d_high", "position_in_60d_range",
    "rv_20", "atr_ratio_20_60", "tr_today_ratio", "rvol_today", "dollar_volume_ratio_20_60",
)


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def declared_checksum(path: Path = CHECKSUM_PATH) -> str:
    """The full canonical digest, read from the ``.sha256`` file that declared it."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("canonical"):
            parts = line.split()
            if len(parts) < 2 or len(parts[1]) != 64:
                raise HardFail("R1", f"{path.name}: malformed canonical line {line!r}")
            return parts[1]
    raise HardFail("R1", f"{path.name} carries no canonical checksum line")


def _one_float(pattern: str, text: str, where: str) -> float:
    match = re.search(pattern, text)
    if match is None:
        raise HardFail("R1", f"{where}: {text!r} does not match {pattern!r}")
    return float(match.group(1))


@dataclass(frozen=True)
class V2ARules:
    """Typed access to the declaration. Every property reads the file; none holds a default.

    The property *names* deliberately match ``strategy_d_analog.config.AnalogRules`` where the
    two studies share a rule (``seasoning_sessions``, ``min_close``, ``library_stride``, ...),
    so V1's universe and stride code can be reused verbatim instead of copied.
    """

    raw: Mapping[str, Any]
    checksum: str

    # -- data ------------------------------------------------------------------------------
    @property
    def dataset(self) -> str:
        return str(self.raw["data"]["dataset"])

    @property
    def grid_count(self) -> int:
        return int(self.raw["data"]["grid_count"])

    @property
    def data_range(self) -> tuple[str, str]:
        first, last = str(self.raw["data"]["range"]).split("..")
        return first, last

    @property
    def freeze_digest(self) -> str:
        return str(self.raw["data"]["freeze_digest"])

    @property
    def source_digest(self) -> str:
        return str(self.raw["data"]["source_digest"])

    @property
    def grid_digest(self) -> str:
        return str(self.raw["data"]["grid_digest"])

    @property
    def allowed_exchanges(self) -> frozenset[str]:
        return frozenset(self.raw["data"]["allowed_primary_exchanges"])

    # -- universe (shares V1 property names on purpose) --------------------------------------
    @property
    def min_close(self) -> float:
        return float(self.raw["universe"]["min_close"])

    @property
    def min_adv20_dollar(self) -> float:
        return float(self.raw["universe"]["min_adv20_dollar"])

    @property
    def seasoning_sessions(self) -> int:
        return int(self.raw["universe"]["seasoning_sessions"])

    @property
    def ca_suspect_ratio(self) -> float:
        return float(self.raw["universe"]["corporate_action_exclusion"]["ca_suspect_ratio"])

    @property
    def min_history_bars(self) -> int:
        return self.seasoning_sessions + 1

    # -- coordinates -------------------------------------------------------------------------
    @property
    def features(self) -> tuple[FeatureSpec, ...]:
        out = tuple(FeatureSpec(int(f["n"]), str(f["name"]), str(f["family"]), str(f["formula"]),
                                int(f["lookback"]), str(f["undefined"]))
                    for f in self.raw["representation"]["features"])
        if tuple(f.name for f in out) != FEATURE_NAMES:
            raise HardFail("R1", f"declared coordinates {tuple(f.name for f in out)}"
                                 f" != code {FEATURE_NAMES}")
        if tuple(f.index for f in out) != tuple(range(1, len(FEATURE_NAMES) + 1)):
            raise HardFail("R1", "coordinate numbering is not 1..10 in declaration order")
        if len(out) != int(self.raw["representation"]["feature_count"]):
            raise HardFail("R1", "feature_count does not match the coordinate list")
        return out

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.features)

    @property
    def max_lookback(self) -> int:
        """Declared coordinate lookback bound, cross-checked against the coordinates themselves."""
        declared = int(self.raw["representation"]["max_lookback_sessions"])
        computed = max(f.lookback for f in self.features)
        if computed != declared:
            raise HardFail("R1", f"max lookback computed {computed} != declared {declared}")
        return declared

    @property
    def scaling_formula(self) -> str:
        return str(self.raw["scaling"]["formula"])

    @property
    def scaling_population(self) -> str:
        return str(self.raw["scaling"]["population"])

    # -- similarity and library ----------------------------------------------------------------
    @property
    def metric(self) -> str:
        return str(self.raw["similarity"]["metric"])

    @property
    def top_k(self) -> int:
        return int(self.raw["top_k"])

    @property
    def library_stride(self) -> int:
        return int(self.raw["library"]["library_stride_sessions"])

    @property
    def min_library_span_sessions(self) -> int:
        return int(self.raw["library"]["min_library_span_sessions"])

    @property
    def max_windows_per_ticker(self) -> int:
        return int(self.raw["neighbor_concentration_policy"]["max_windows_per_neighbor_ticker"])

    @property
    def max_neighbors_per_end_date(self) -> int:
        return int(self.raw["neighbor_concentration_policy"]["max_neighbors_per_library_end_date"])

    @property
    def embargo_lookback(self) -> int:
        """``L`` of ``d + h <= D - L``, cross-checked against the coordinate bound."""
        declared = int(self.raw["embargo_policy"]["feature_lookback_L"])
        if declared != self.max_lookback:
            raise HardFail("R1", f"embargo L {declared} != coordinate lookback {self.max_lookback}")
        return declared

    # -- horizons ------------------------------------------------------------------------------
    @property
    def primary_horizon(self) -> int:
        return int(self.raw["horizons"]["primary"])

    @property
    def secondary_horizons(self) -> tuple[int, ...]:
        return tuple(int(h) for h in self.raw["horizons"]["secondary"])

    @property
    def all_horizons(self) -> tuple[int, ...]:
        return tuple(sorted({self.primary_horizon, *self.secondary_horizons}))

    @property
    def max_horizon(self) -> int:
        declared = int(self.raw["horizons"]["max_h_for_eval_window"])
        if declared != max(self.all_horizons):
            raise HardFail("R1", f"max_h_for_eval_window {declared} != max horizon")
        return declared

    # -- evaluation window ----------------------------------------------------------------------
    @property
    def eval_start_idx(self) -> int:
        """``seasoning + min_library_span + (L + max h)``, cross-checked against the declared int."""
        computed = (self.seasoning_sessions + self.min_library_span_sessions
                    + self.max_lookback + self.max_horizon)
        declared = int(self.raw["evaluation"]["eval_start_idx"])
        if computed != declared:
            raise HardFail("R1", f"eval_start_idx computed {computed} != declared {declared}")
        return computed

    def eval_range(self, session_count: int) -> tuple[int, int]:
        start, end = self.eval_start_idx, session_count - 1 - self.max_horizon
        if end < start:
            raise HardFail("F4", f"no query dates: [{start}, {end}] on N={session_count}")
        if session_count == self.grid_count and end != int(self.raw["evaluation"]["eval_end_idx"]):
            raise HardFail("R1", f"eval_end {end} != declared {self.raw['evaluation']['eval_end_idx']}")
        return start, end

    @property
    def declared_evaluable_dates(self) -> int:
        return int(self.raw["evaluation"]["evaluable_dates"])

    @property
    def queries_per_date(self) -> int:
        return int(self.raw["evaluation"]["queries_per_date"])

    @property
    def min_valid_queries_per_date(self) -> int:
        return int(self.raw["evaluation"]["min_valid_queries_per_date"])

    @property
    def query_sampling_rule(self) -> str:
        return str(self.raw["evaluation"]["query_sampling"])

    # -- B0 --------------------------------------------------------------------------------------
    @property
    def b0_signs(self) -> dict[str, int]:
        signs = {str(k): int(v) for k, v in self.raw["baseline_B0"]["signs"].items()}
        if set(signs) != set(FEATURE_NAMES):
            raise HardFail("R1", "B0 signs do not cover exactly the ten coordinates")
        bad = {k: v for k, v in signs.items() if v not in (-1, 1)}
        if bad:
            raise HardFail("R1", f"B0 signs must be +1 or -1: {bad}")
        return signs

    @property
    def b0_sign_vector(self) -> tuple[int, ...]:
        signs = self.b0_signs
        return tuple(signs[name] for name in FEATURE_NAMES)

    @property
    def b0_strong_names(self) -> tuple[str, ...]:
        strength = self.raw["baseline_B0"]["sign_prior_strength"]
        return tuple(n for n in FEATURE_NAMES if str(strength[n]).upper() == "STRONG")

    # -- gate and power ----------------------------------------------------------------------------
    @property
    def sample_gate(self) -> SampleGate:
        """S1 parsed from the declaration text, in the order the declaration states it."""
        text = str(self.raw["pass_fail_policy"]["conditions"]["S1_sample"])
        found = re.findall(r"(>=|<=)\s*([0-9.]+)", text)
        if len(found) != 5:
            raise HardFail("R1", f"S1 does not state five thresholds: {text!r}")
        operators = tuple(op for op, _ in found)
        if operators != (">=", ">=", ">=", "<=", "<="):
            raise HardFail("R1", f"S1 threshold operators {operators} are not the declared order")
        values = [value for _, value in found]
        return SampleGate(int(values[0]), int(values[1]), int(values[2]),
                          float(values[3]), float(values[4]))

    @property
    def delta_threshold(self) -> float:
        """S5: the declared point-estimate threshold on the paired IC difference."""
        return _one_float(r">=\s*([0-9.]+)",
                          str(self.raw["pass_fail_policy"]["conditions"]["S5_delta_point"]), "S5")

    @property
    def power_inputs(self) -> dict[str, float]:
        """``sd_ic``, ``n_dates`` and the SE inflation, read out of the declared SE formula."""
        text = str(self.raw["power"]["se_formula"])
        match = re.fullmatch(
            r"SE\(delta\) = ([0-9.]+) \* sqrt\(2 \* \(1 - rho\)\) / sqrt\(([0-9]+)\) \* ([0-9.]+)",
            text)
        if match is None:
            raise HardFail("R1", f"power.se_formula is not in the declared form: {text!r}")
        return {"sd_ic": float(match.group(1)), "declared_dates": float(match.group(2)),
                "se_inflation": float(match.group(3))}

    @property
    def declared_mde(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.raw["power"]["mde_significance"].items()}

    @property
    def declared_delta80(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.raw["power"]["delta80_screening"].items()}

    @property
    def data_sufficiency(self) -> Mapping[str, Any]:
        return self.raw["data_sufficiency"]

    @property
    def prohibited_in_d0(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.raw["prohibited_in_d0"])


def load_rules(path: Path = RULES_PATH, *, require_declared: bool = True) -> V2ARules:
    """Load and bind the declaration. A checksum mismatch is R1 and stops the caller."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared:
        expected = declared_checksum()
        if checksum != expected:
            raise RulesChanged(f"rules checksum {checksum} != declared {expected}")
    rules = V2ARules(raw, checksum)
    if raw.get("strategy_id") != STRATEGY_ID:
        raise HardFail("R1", f"strategy_id {raw.get('strategy_id')!r} != {STRATEGY_ID!r}")
    if raw.get("ai_enabled") is not False:
        raise HardFail("R1", "V2-A declares ai_enabled=false; the loaded file does not")
    if raw.get("study_class") != "SCREENING":
        raise HardFail("R1", f"study_class {raw.get('study_class')!r} is not SCREENING")
    if HASH_SEED not in rules.query_sampling_rule:
        raise HardFail("F5", f"query sampling rule does not carry the hash seed {HASH_SEED}")
    # Fail fast on a malformed declaration rather than half-way through a run.
    _ = (rules.features, rules.max_lookback, rules.embargo_lookback, rules.eval_start_idx,
         rules.max_horizon, rules.b0_sign_vector, rules.sample_gate, rules.power_inputs)
    return rules


def sign_vector(rules: V2ARules, names: Sequence[str] = FEATURE_NAMES) -> tuple[int, ...]:
    signs = rules.b0_signs
    return tuple(signs[name] for name in names)
