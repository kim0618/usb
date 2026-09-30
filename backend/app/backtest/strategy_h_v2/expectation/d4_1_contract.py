"""H-V2-D4.1: the live-pilot execution contract, frozen before any D4 call exists.

Nothing in this module executes anything. It is the preregistration - sample, budget, gates and
verdict rule - decided while the D4 contract was being written and before a single D4 output was
seen, exactly as `research/d3_3_contract.py` was for D3.3. Changing a value here after D4.1 has run
would defeat the point; a change lands as a new contract version, never as an edit.

THE SAMPLE PROBLEM, stated rather than quietly resolved.

D4 brief §33 prefers new, disjoint issuers over reusing D3.3's 12, because D3.3's outputs were read
closely while designing this stage and a "clean" test on them is not clean. That preference is
right and is followed - but it has a cost the brief does not mention: D4 consumes a D3 research
output as immutable input, and only those same 12 issuers HAVE a D3 V2 output. A genuinely disjoint
D4.1 issuer needs a D3 run first, chained, at D3 prices.

So D4.1 is split in two, and the split is the honest resolution rather than a compromise:

  TIER A - CONTRACT SHAKEDOWN, on 3 of the D3.3 issuers whose D3 output already exists. This tier
  answers only mechanical questions: does the prompt assemble, does the schema validate, do the
  frozen rules fire where they should, is the ledger linkage intact. It is NOT evidence about
  qualitative quality, because the inputs are not unseen, and its results must never be reported as
  if it were.

  TIER B - QUALITATIVE VALIDATION, on 6 issuers disjoint from all 48 CIKs any Opus call has ever
  touched (D3's 12-issuer pilot, D3.1's Batch 2 of 24, and D3.3's 12). Each needs a chained D3-then-
  D4 run. This is the tier the E-gates are actually adjudicated on.

Reporting Tier A's numbers as the pilot's quality result would be the single easiest way to make
this stage look better than it is, which is why the two tiers carry different gate sets below.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path

from app.backtest.strategy_h_v2.research.d3_3_contract import (
    D3_3_SAMPLE,
    EXCLUDED_CIKS as D3_3_EXCLUDED_CIKS,
    PACKAGES_DIR,
)

D4_1_CONTRACT_VERSION = "h_v2_d4_1_pilot_contract_v1"

#: Every CIK any live Opus research call has ever touched: D3's pilot (12) + D3.1's Batch 2 (24)
#: + D3.3's confirmation sample (12). CIK-based, never ticker-based, for the reason
#: `d3_3_contract.EXCLUDED_CIKS` states: one issuer under two symbols would silently break
#: disjointness.
D3_3_SAMPLE_CIKS: frozenset[str] = frozenset(entry.cik for entry in D3_3_SAMPLE)
EXCLUDED_CIKS: frozenset[str] = D3_3_EXCLUDED_CIKS | D3_3_SAMPLE_CIKS
assert len(EXCLUDED_CIKS) == 48, "36 previously-touched CIKs plus D3.3's 12 must be disjoint"


@dataclass(frozen=True)
class SampleEntry:
    ticker: str
    cik: str
    depth: str
    priority: str


# ---------------------------------------------------------------------------------------------
# Tier A - contract shakedown (D3 output already exists; NOT a quality result)
# ---------------------------------------------------------------------------------------------

TIER_A_SEED = "H_V2_D4_1_TIER_A_V1"
TIER_A_N = 3
TIER_A_TICKERS: tuple[str, ...] = ("SCCO", "GOOG", "BSY")
TIER_A_CHECKSUM = "d47cc4fd30fd3d6f04738b811e401547abedf57c195f0c4c2f1b65af34e2f6ab"


# ---------------------------------------------------------------------------------------------
# Tier B - qualitative validation on unseen issuers (needs a chained D3 run first)
# ---------------------------------------------------------------------------------------------

TIER_B_SEED = "H_V2_D4_1_SAMPLE_V1"
TIER_B_N_FULL = 3
TIER_B_N_CORE = 3

#: Frozen 2026-09-29, before any D4 call. `regenerate_tier_b_from_universe()` re-derives it from
#: the D2.1 snapshot so this literal can be checked against a fresh computation rather than only
#: against a hash of itself - a hardcoded list checksummed by hashing itself proves nothing.
TIER_B_SAMPLE: tuple[SampleEntry, ...] = (
    SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),
    SampleEntry("DORM", "0000868780", "FULL", "E3_P1_HIGH"),
    SampleEntry("FRPT", "0001611647", "FULL", "E3_P1_HIGH"),
    SampleEntry("TG", "0000850429", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("CRK", "0000023194", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("SPSC", "0001092699", "CORE", "E3_P2_MEDIUM"),
)
TIER_B_CHECKSUM = "acf2da18c8792f599c2435747200625ca783dfa0eb4f65140d590b5c13c825b0"


def _checksum(entries: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join(entries).encode()).hexdigest()


def tier_a_checksum(tickers: tuple[str, ...] = TIER_A_TICKERS) -> str:
    return _checksum(tickers)


def tier_b_checksum(sample: tuple[SampleEntry, ...] = TIER_B_SAMPLE) -> str:
    return _checksum(tuple(e.ticker for e in sample))


def regenerate_tier_a_from_d3_3() -> tuple[str, ...]:
    return tuple(sorted(
        (entry.ticker for entry in D3_3_SAMPLE),
        key=lambda t: hashlib.sha256(f"{TIER_A_SEED}:{t}".encode()).hexdigest(),
    )[:TIER_A_N])


def regenerate_tier_b_from_universe(
    packages_dir: Path = PACKAGES_DIR,
) -> tuple[SampleEntry, ...]:
    """Offline, read-only, 0 model calls - the same seeded-hash convention D3.1's Batch 2 and
    D3.3's sample both used, over the issuers none of them touched."""
    rows: list[SampleEntry] = []
    for path in sorted(packages_dir.glob("*.json")):
        bundle = json.loads(path.read_text())["evidence_bundle"]
        cik = bundle["identity"]["cik"]
        if cik in EXCLUDED_CIKS:
            continue
        rows.append(SampleEntry(bundle["identity"]["ticker"], cik, bundle["collection_depth"],
                                bundle["candidate_source"]))

    def key(entry: SampleEntry) -> str:
        return hashlib.sha256(f"{TIER_B_SEED}:{entry.ticker}".encode()).hexdigest()

    full = sorted((r for r in rows if r.depth == "FULL"), key=key)
    core = sorted((r for r in rows if r.depth == "CORE"), key=key)
    return tuple(full[:TIER_B_N_FULL]) + tuple(core[:TIER_B_N_CORE])


# ---------------------------------------------------------------------------------------------
# Budget - checked BEFORE starting a call that could not finish inside the ceiling
# ---------------------------------------------------------------------------------------------
#
# Anchored to D3.3's own measured costs rather than an estimate: 18 candidate attempts, $16.71
# total, $0.93 mean, $1.54 maximum. D4's prompt carries the full D3 output plus an 80,000-char
# expectation-evidence budget, against D3's 200,000-char evidence budget - smaller, but the worst
# case is set ABOVE D3's observed maximum rather than below it, because a ceiling that assumes the
# improvement is a ceiling that does not bound anything.

D4_WORST_CASE_CANDIDATE_USD = 2.00
#: A Tier B issuer needs a D3 run first, at D3's own observed worst case, then a D4 run.
D3_WORST_CASE_CANDIDATE_USD = 1.60
TIER_B_WORST_CASE_CANDIDATE_USD = D3_WORST_CASE_CANDIDATE_USD + D4_WORST_CASE_CANDIDATE_USD
D4_1_HARD_BUDGET_USD = 30.00
assert (TIER_A_N * D4_WORST_CASE_CANDIDATE_USD
        + (TIER_B_N_FULL + TIER_B_N_CORE) * TIER_B_WORST_CASE_CANDIDATE_USD
        ) <= D4_1_HARD_BUDGET_USD, "the frozen sample must fit its own worst case inside the budget"


# ---------------------------------------------------------------------------------------------
# E1-E8 frozen gates (brief §34)
# ---------------------------------------------------------------------------------------------
#
# Thresholds are frozen here, before any live result (brief §34: "정확한 thresholds는 live result
# 보기 전에 동결"). Where a threshold is not zero, the non-zero value has a stated basis from D3.3's
# measured behaviour rather than a round number chosen for comfort.

E1_MIN_SCHEMA_VALID_RATE = 0.95
"""Final validity after the bounded repair loop. Same level D3.3's L1 used and met (12/12)."""
E2_MAX_UNSOURCED_MATERIAL_GAP_CLAIMS = 0
"""Every material gap claim carries a resolvable citation. Zero-tolerance: D3.3 achieved
structural provenance on every material claim, so anything above zero here is a regression.

D4-S §16 froze the MEANING, leaving the threshold at its original zero:

    Every MATERIAL_SOURCE_REQUIRED expectation-gap claim must resolve to a valid source/evidence id
    or to a code-owned fact id. CODE_OWNED_FACT_EXPLANATION and META_LIMITATION_OR_UNKNOWN claims
    are not in the denominator.

The classification is `audit_strategy_h_v2_d4_1.classify_claim_sourcing`, decided from the claim's
type and its cited ids - never from its prose - and the numerator is
`material_source_required_defects`. B is excluded because M8/E5 already holds a code-owned-fact
claim to a STRICTER test (it must state that fact's value correctly), so counting it here would
double-count one obligation; C is excluded because the schema exempts UNKNOWN claims from citation
outright, and free prose in `limitations`/`unknown_fields` is not a claim and never reaches the
walker. Neither exclusion was chosen after seeing a result: both follow from contracts that predate
D4-S, and with the citation-form bug fixed the measured numerator is 0 on D4.3A and on Final Tier A
V3 alike."""
E3_MAX_FABRICATED_CONSENSUS = 0
"""Any assertion of an analyst/market/consensus expectation. Zero, and not adjustable: there is no
consensus source, so every occurrence is invention."""
E4_MAX_FUTURE_SOURCE_LEAKS = 0
"""Any use of a price bar or a source published after decision_time."""
E5_MAX_CODE_OWNED_NUMERIC_DEFECTS = 0
"""A claim citing a code-owned fact while stating a different number - i.e. the model computed."""
E6_MAX_UNKNOWN_DISCIPLINE_VIOLATIONS = 0
"""A POSITIVE/WIDE_POSITIVE gap that rule C1 or C6 should have blocked, or a confidence above the
frozen ceiling. Measured on FINAL outputs: the repair loop is allowed to catch these, the final
record is not allowed to contain them."""
E7_MAX_DECISION_LEAKS = 0
"""Any prohibited decision/valuation field or vocabulary in a final output."""
E8_MAX_UNSUPPORTED_PRICED_IN_CLAIMS = 0
"""A priced-in assessment other than UNKNOWN missing evidence_ids, confidence or limitations."""

#: Brief §34 is explicit that these are not investment-performance gates. Restated in code so a
#: reader of the gate list cannot mistake a PASS here for evidence of anything about returns.
E_GATES_PROVE = (
    "that D4 produced evidence-disciplined expectation interpretations under a frozen contract"
)
E_GATES_DO_NOT_PROVE = (
    "that any expectation gap is real, that a POSITIVE gap precedes a positive return, or that "
    "this pipeline is profitable. Alpha is D6 Decision plus D7 Forward Shadow, and neither exists."
)

#: Tier A can only be adjudicated on the mechanical gates - its issuers are not unseen, so a
#: content-quality result from it would not mean what a gate result is supposed to mean.
TIER_A_GATES = ("E1", "E4", "E5", "E7")
TIER_B_GATES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
#: Failure of any of these is always FAIL, never a reportable limitation: each is a fabrication or
#: a leakage gate, and neither is an operational inconvenience.
D4_1_CORE_GATES = ("E3", "E4", "E5", "E7")


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class GateResult:
    gate: str
    name: str
    threshold: str
    observed: str
    status: GateStatus

    def to_dict(self) -> dict:
        return {"gate": self.gate, "name": self.name, "threshold": self.threshold,
                "observed": self.observed, "status": self.status.value}


def _gate(gate: str, name: str, threshold: str, observed: str, passed: bool) -> GateResult:
    return GateResult(gate, name, threshold, observed,
                      GateStatus.PASS if passed else GateStatus.FAIL)


def evaluate_d4_1_gates(
    *, attempted: int, schema_valid: int, unsourced_material_gap_claims: int,
    fabricated_consensus: int, future_source_leaks: int, code_owned_numeric_defects: int,
    unknown_discipline_violations: int | None, decision_leaks: int,
    unsupported_priced_in_claims: int | None, tier: str = "B",
) -> list[GateResult]:
    """E1-E8, computed mechanically. A gate outside this tier's set is `NOT_EVALUATED` - never a
    silent PASS, the same convention D3.1's R10 and D3.3's L3 both used for an unperformed check.
    `None` for a manual-audit count means the audit has not been performed."""
    allowed = TIER_A_GATES if tier.upper() == "A" else TIER_B_GATES
    rate = schema_valid / attempted if attempted else 0.0

    def _maybe(gate: str, name: str, threshold: str, observed: str, passed: bool) -> GateResult:
        if gate not in allowed:
            return GateResult(gate, name, threshold, f"not evaluated in tier {tier.upper()}",
                              GateStatus.NOT_EVALUATED)
        return _gate(gate, name, threshold, observed, passed)

    def _manual(gate: str, name: str, threshold: str, value: int | None,
                limit: int) -> GateResult:
        if gate not in allowed:
            return GateResult(gate, name, threshold, f"not evaluated in tier {tier.upper()}",
                              GateStatus.NOT_EVALUATED)
        if value is None:
            return GateResult(gate, name, threshold, "not performed", GateStatus.NOT_EVALUATED)
        return _gate(gate, name, threshold, str(value), value <= limit)

    return [
        _maybe("E1", "schema validity", f">= {E1_MIN_SCHEMA_VALID_RATE:.0%}",
               f"{schema_valid}/{attempted} = {rate:.1%}", rate >= E1_MIN_SCHEMA_VALID_RATE),
        _maybe("E2", "material gap claims source-linked",
               f"== {E2_MAX_UNSOURCED_MATERIAL_GAP_CLAIMS}",
               str(unsourced_material_gap_claims),
               unsourced_material_gap_claims <= E2_MAX_UNSOURCED_MATERIAL_GAP_CLAIMS),
        _maybe("E3", "fabricated consensus", f"== {E3_MAX_FABRICATED_CONSENSUS}",
               str(fabricated_consensus), fabricated_consensus <= E3_MAX_FABRICATED_CONSENSUS),
        _maybe("E4", "future source / price leakage", f"== {E4_MAX_FUTURE_SOURCE_LEAKS}",
               str(future_source_leaks), future_source_leaks <= E4_MAX_FUTURE_SOURCE_LEAKS),
        _maybe("E5", "code-owned numeric integrity",
               f"== {E5_MAX_CODE_OWNED_NUMERIC_DEFECTS}", str(code_owned_numeric_defects),
               code_owned_numeric_defects <= E5_MAX_CODE_OWNED_NUMERIC_DEFECTS),
        _manual("E6", "UNKNOWN discipline", f"== {E6_MAX_UNKNOWN_DISCIPLINE_VIOLATIONS}",
                unknown_discipline_violations, E6_MAX_UNKNOWN_DISCIPLINE_VIOLATIONS),
        _maybe("E7", "investment decision leakage", f"== {E7_MAX_DECISION_LEAKS}",
               str(decision_leaks), decision_leaks <= E7_MAX_DECISION_LEAKS),
        _manual("E8", "priced-in claims fully supported",
                f"== {E8_MAX_UNSUPPORTED_PRICED_IN_CLAIMS}", unsupported_priced_in_claims,
                E8_MAX_UNSUPPORTED_PRICED_IN_CLAIMS),
    ]


class D41Verdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_LIMITATIONS = "PASS_WITH_LIMITATIONS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


def d4_1_verdict(gates: list[GateResult], *, tier: str = "B") -> D41Verdict:
    """FAIL if any core gate fails or was not evaluated in a tier that should have evaluated it;
    PASS only if every gate in this tier's set passes; PASS_WITH_LIMITATIONS otherwise.

    A Tier A run can never return PASS: its gate set excludes every content gate, so the most it
    can say is that the machinery works, which is PASS_WITH_LIMITATIONS by construction.
    """
    by_id = {gate.gate: gate for gate in gates}
    allowed = TIER_A_GATES if tier.upper() == "A" else TIER_B_GATES
    for core in D4_1_CORE_GATES:
        if core in allowed and by_id[core].status != GateStatus.PASS:
            return D41Verdict.FAIL
    in_scope = [by_id[g] for g in allowed]
    if all(g.status == GateStatus.PASS for g in in_scope) and tier.upper() != "A":
        return D41Verdict.PASS
    if any(g.status == GateStatus.FAIL for g in in_scope):
        return D41Verdict.FAIL if tier.upper() != "A" else D41Verdict.FAIL
    return D41Verdict.PASS_WITH_LIMITATIONS
