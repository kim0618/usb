"""The declared D rules, read from the declaration file and nowhere else.

``docs/backtest/strategy_d/d_analog_rules_v1.json`` was declared on 2026-09-17 before any D data
read, API call or result. Code carries no threshold of its own: every number a window, a
neighbour policy or a gate depends on comes from that file, and its canonical checksum goes into
the run identity, so an edited rule is a different study by construction (R1).

The checksum recipe is the same three lines as ``strategy_c_selection.rules.canonical_checksum``.
It is reimplemented here rather than imported: C is mid-study (C-V2 decomposition is running), D
must not pin its identity to a module that C may move, and the recipe is two statements. The D
test suite pins the reimplementation against the C function so the two can never drift.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.backtest.strategy_d_analog.models import HardFail, RulesChanged, TestId

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_d/d_analog_rules_v1.json"
#: Canonical checksum declared 2026-09-17 17:25 KST, before any D data read (D_CONCEPT_V1 §0).
DECLARED_RULES_CHECKSUM = "680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3"
STRATEGY_ID = "HISTORICAL_ANALOG_V1"
#: Seed literal of the query and N1 hash strings (D1 Pre-flight §5.2, §5.3). Not a date.
HASH_SEED = "20260917"
#: D1 Pre-flight §5.3: replicate numbering starts at 0, fixed before any label was read.
N1_REPLICATE_BASE = 0


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _trailing_int(text: str, where: str) -> int:
    match = re.search(r"=\s*(\d+)\s*$", text)
    if match is None:
        raise HardFail("R1", f"{where}: {text!r} does not end in '= <int>'")
    return int(match.group(1))


@dataclass(frozen=True)
class AnalogRules:
    """Typed access to the declaration. Every property reads the file; none holds a default."""

    raw: Mapping[str, Any]
    checksum: str

    # -- data and universe ------------------------------------------------------------------
    @property
    def allowed_exchanges(self) -> frozenset[str]:
        return frozenset(self.raw["data"]["allowed_primary_exchanges"])

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
        """61 consecutive bars on ``e-60..e``; the seasoning length plus the end session itself."""
        return self.seasoning_sessions + 1

    # -- test family ------------------------------------------------------------------------
    @property
    def pattern_windows(self) -> tuple[int, ...]:
        return tuple(int(w) for w in self.raw["pattern_windows"])

    @property
    def combinations(self) -> tuple[tuple[int, int], ...]:
        out = tuple((int(c["window"]), int(c["horizon"])) for c in self.raw["combinations"])
        if len(out) != int(self.raw["combination_count"]):
            raise HardFail("R1", "combination_count does not match the combination list")
        return out

    @property
    def tests(self) -> tuple[TestId, ...]:
        return tuple(TestId(w, h, rep) for w, h in self.combinations
                     for rep in sorted(self.raw["representations"]))

    @property
    def top_k(self) -> int:
        return int(self.raw["top_k"])

    # -- library and neighbour policy -------------------------------------------------------
    @property
    def library_stride(self) -> int:
        return int(self.raw["library"]["library_stride_sessions"])

    @property
    def max_windows_per_ticker(self) -> int:
        return int(self.raw["neighbor_concentration_policy"]["max_windows_per_neighbor_ticker"])

    @property
    def max_neighbors_per_end_date(self) -> int:
        return int(self.raw["neighbor_concentration_policy"]["max_neighbors_per_library_end_date"])

    # -- evaluation window ------------------------------------------------------------------
    @property
    def min_library_span_sessions(self) -> int:
        return int(self.raw["evaluation"]["min_library_span_sessions"])

    @property
    def eval_start_idx(self) -> int:
        """``seasoning + min_library_span + max(W + h)``, cross-checked against the declared sum."""
        computed = (self.seasoning_sessions + self.min_library_span_sessions
                    + max(w + h for w, h in self.combinations))
        declared = _trailing_int(str(self.raw["evaluation"]["eval_start_idx"]), "eval_start_idx")
        if computed != declared:
            raise HardFail("R1", f"eval_start_idx computed {computed} != declared {declared}")
        return computed

    @property
    def eval_end_offset(self) -> int:
        """``eval_end_idx = N - 1 - offset``. The declaration states the offset as an expression."""
        text = str(self.raw["evaluation"]["eval_end_idx"]).replace(" ", "")
        match = re.fullmatch(r"N-1-(\d+)", text)
        if match is None:
            raise HardFail("R1", f"eval_end_idx {text!r} is not of the form 'N - 1 - <int>'")
        return int(match.group(1))

    def eval_range(self, session_count: int) -> tuple[int, int]:
        start, end = self.eval_start_idx, session_count - 1 - self.eval_end_offset
        if end < start:
            raise HardFail("F4", f"no query dates: eval range [{start}, {end}] on N={session_count}")
        return start, end

    @property
    def queries_per_date(self) -> int:
        return int(self.raw["evaluation"]["queries_per_date"])

    @property
    def min_valid_queries_per_date(self) -> int:
        return int(self.raw["evaluation"]["min_valid_queries_per_date"])

    @property
    def query_sampling_rule(self) -> str:
        return str(self.raw["evaluation"]["query_sampling"])

    @property
    def bootstrap_seed(self) -> int:
        seed = re.search(r"seed\s+(\d+)", str(self.raw["statistics"]["bootstrap"]))
        if seed is None:
            raise HardFail("R1", "bootstrap seed is not stated in the declaration")
        return int(seed.group(1))


def load_rules(path: Path = RULES_PATH, *, require_declared: bool = True) -> AnalogRules:
    """Load and bind the declaration. A checksum mismatch is R1 and stops the caller."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared and checksum != DECLARED_RULES_CHECKSUM:
        raise RulesChanged(f"rules checksum {checksum} != declared {DECLARED_RULES_CHECKSUM}")
    rules = AnalogRules(raw, checksum)
    if raw.get("strategy_id") != STRATEGY_ID:
        raise HardFail("R1", f"strategy_id {raw.get('strategy_id')!r} != {STRATEGY_ID!r}")
    if raw.get("ai_enabled") is not False:
        raise HardFail("R1", "V1 declares ai_enabled=false; the loaded file does not")
    if HASH_SEED not in rules.query_sampling_rule:
        raise HardFail("F5", f"query sampling rule does not carry the hash seed {HASH_SEED}")
    if rules.bootstrap_seed != int(HASH_SEED):
        raise HardFail("R1", f"bootstrap seed {rules.bootstrap_seed} != hash seed {HASH_SEED}")
    _ = rules.tests, rules.eval_start_idx, rules.eval_end_offset  # fail fast on a malformed family
    return rules
