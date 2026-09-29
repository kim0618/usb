"""H-V2-D3.2F: the D3.3 execution contract, frozen before any D3.3 result exists.

Everything in this module is decided and checksummed BEFORE a single live D3.3 call is made
(D3.2F brief §9/§11/§16: "결과 보기 전에 동결"). Changing any value here after D3.3 has run would
defeat the entire point of a preregistration - if a change is ever needed, it must land as a new,
separately-declared contract version, not an edit to this one.

Nothing here executes a model call. `select_d3_3_sample` and `regenerate_sample_from_universe` are
pure, offline, read-only functions over the D2.1 package snapshot already on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path

PACKAGES_DIR = Path("data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages")

# ---------------------------------------------------------------------------------------------
# Exclusion set: every CIK any real Opus call has ever touched (D3.2F brief §8)
# ---------------------------------------------------------------------------------------------
#
# CIK-based, not ticker-based, on purpose (brief §8: "동일 company 다른 ticker 금지") - the same
# issuer can be listed under more than one ticker, and a "disjoint" sample that quietly re-researched
# a pilot company under a different symbol would invalidate D3.3's whole claim about unseen issuers.
# Restated here as frozen literals rather than re-read from the manifests at import time, so this
# module's own exclusion set cannot silently drift if a manifest file is ever touched - verified
# instead, in tests, by re-deriving the same set from the manifests and asserting equality.

#: D3's original 12-issuer pilot (`H_V2_D3_AI_RESEARCH_ENGINE_V1.md`).
D3_PILOT_CIKS: frozenset[str] = frozenset({
    "0000824142", "0000320193", "0000351569", "0001814287", "0001739445", "0001113232",
    "0001090872", "0001675149", "0001158449", "0001500217", "0001703057", "0000318306",
})
#: D3.1's Batch-2, all 24 sampled (including the 5 that never reached the model and MRVI's
#: unrepaired failure - a CIK that was SAMPLED counts as touched even if the call itself failed,
#: because its evidence package was already read and its identity already exposed to this pipeline).
D3_1_BATCH2_CIKS: frozenset[str] = frozenset({
    "0001828791", "0001327688", "0001002910", "0000903651", "0001667011", "0001093557",
    "0001430306", "0000102037", "0000719413", "0000003570", "0001053507", "0001883685",
    "0000030625", "0000769520", "0000072573", "0000034782", "0001590418", "0001527753",
    "0001823239", "0001508655", "0001520697", "0001860782", "0000005272", "0001551901",
})
EXCLUDED_CIKS: frozenset[str] = D3_PILOT_CIKS | D3_1_BATCH2_CIKS
assert len(EXCLUDED_CIKS) == 36, "pilot and Batch-2 CIK sets must be disjoint (12 + 24 = 36)"


@dataclass(frozen=True)
class SampleEntry:
    ticker: str
    cik: str
    depth: str
    priority: str


#: D3.2F brief §9: "성과/회사명/유명도/sector를 이용하지 않는다" - `sha256("H_V2_D3_3_SAMPLE_V1:" +
#: ticker)` ascending, the exact convention `run_strategy_h_v2_d3_1.py`'s own `SAMPLE_SEED` already
#: used for Batch 2, so this sample is reproducible the same declared way, not a new invented method.
D3_3_SAMPLE_SEED = "H_V2_D3_3_SAMPLE_V1"
D3_3_N_FULL = 6
D3_3_N_CORE = 6

#: Frozen 2026-09-29, before any D3.3 call. Regenerable from the D2.1 snapshot by
#: `regenerate_sample_from_universe()` - `test_d3_3_contract.py` asserts the two agree, so this
#: literal list cannot silently drift from what the seed actually selects.
D3_3_SAMPLE: tuple[SampleEntry, ...] = (
    SampleEntry("BSY", "0001031308", "FULL", "E3_P1_HIGH"),
    SampleEntry("WEN", "0000030697", "FULL", "E3_P1_HIGH"),
    SampleEntry("GOOG", "0001652044", "FULL", "E3_P1_HIGH"),
    SampleEntry("SCCO", "0001001838", "FULL", "E3_P1_HIGH"),
    SampleEntry("DT", "0001773383", "FULL", "E3_P1_HIGH"),
    SampleEntry("LUV", "0000092380", "FULL", "E3_P1_HIGH"),
    SampleEntry("MOFG", "0001412665", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("GD", "0000040533", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("CWBC", "0001127371", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("SIF", "0000090168", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("BPOP", "0000763901", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("BLKB", "0001280058", "CORE", "E3_P2_MEDIUM"),
)
D3_3_SAMPLE_CHECKSUM = "b671d9889f3d550fcb20cbf8f95fe216c1604f7a90a3d574cc2be24e671ddf9c"

#: D3.2F brief §13: 3 FULL + 3 CORE of the 12, for the manual content/citation/numeric/qualifier/
#: stage audit - selected the same deterministic way, over the frozen sample above, not the whole
#: universe.
D3_3_MANUAL_AUDIT_SEED = "H_V2_D3_3_MANUAL_AUDIT_V1"
D3_3_MANUAL_AUDIT_TICKERS: tuple[str, ...] = ("LUV", "DT", "WEN", "BLKB", "CWBC", "MOFG")
D3_3_MANUAL_AUDIT_CHECKSUM = "700154df337133850e1b12cdd30b772094fd6043ff4cde6db2f4aef3ec41284e"

#: D3.2F brief §10: stop BEFORE starting a call that could not complete inside the ceiling, mirroring
#: `run_strategy_h_v2_d3_1.py`'s own `WORST_CASE_CANDIDATE_USD` logic - not a retroactive check after
#: the budget is already blown.
D3_3_HARD_BUDGET_USD = 30.00
D3_3_WORST_CASE_CANDIDATE_USD = 6.00


def _checksum(entries: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join(entries).encode()).hexdigest()


def sample_checksum(sample: tuple[SampleEntry, ...] = D3_3_SAMPLE) -> str:
    return _checksum(tuple(e.ticker for e in sample))


def manual_audit_checksum(tickers: tuple[str, ...] = D3_3_MANUAL_AUDIT_TICKERS) -> str:
    return _checksum(tickers)


def regenerate_sample_from_universe(packages_dir: Path = PACKAGES_DIR) -> tuple[SampleEntry, ...]:
    """Independently re-derives the 12-issuer sample from the D2.1 package snapshot, offline, 0
    model calls. Exists so `D3_3_SAMPLE`'s frozen literal can be checked against a fresh
    computation rather than only checksummed against itself - a hardcoded list checksummed by
    hashing itself proves nothing about whether it was actually produced the declared way.
    """
    rows: list[SampleEntry] = []
    for path in sorted(packages_dir.glob("*.json")):
        data = json.loads(path.read_text())
        bundle = data["evidence_bundle"]
        cik = bundle["identity"]["cik"]
        if cik in EXCLUDED_CIKS:
            continue
        rows.append(SampleEntry(bundle["identity"]["ticker"], cik, bundle["collection_depth"],
                                bundle["candidate_source"]))

    def key(entry: SampleEntry) -> str:
        return hashlib.sha256(f"{D3_3_SAMPLE_SEED}:{entry.ticker}".encode()).hexdigest()

    full_pool = sorted((r for r in rows if r.depth == "FULL"), key=key)
    core_pool = sorted((r for r in rows if r.depth == "CORE"), key=key)
    return tuple(full_pool[:D3_3_N_FULL]) + tuple(core_pool[:D3_3_N_CORE])


# ---------------------------------------------------------------------------------------------
# L1-L7 frozen gates (D3.2F brief §11)
# ---------------------------------------------------------------------------------------------

L1_MIN_FINAL_VALID_RATE = 0.95
L2_MIN_INITIAL_VALID_RATE = 0.80
"""Pre-repair (first-attempt) schema/contract success rate - directly what D3.2R's prompt changes
are supposed to move, since D3.1's stage-floor over-claim was overwhelmingly a first-attempt
defect (D3.2R §C)."""
L3_MAX_MATERIAL_CONTENT_DEFECTS = 0
"""From the manual audit (§13) only - not machine-derivable."""
L4_MAX_UNSUPPORTED_STAGE_ESCALATIONS = 0
L5_MAX_CITATION_DEFECT_RATE = 0.10
"""Validation Contract V2's citation precision (`validation_v2.citation_precision`) -
UNSUPPORTED + ADJACENT_RECOVERABLE as a share of material claims, D3.2 §J.2's own metric."""
L6_MAX_FABRICATED_NUMERIC_FACTS = 0
L7_MAX_DECISION_LEAKS = 0


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
    return GateResult(gate, name, threshold, observed, GateStatus.PASS if passed else GateStatus.FAIL)


def evaluate_d3_3_gates(
    *, attempted: int, final_valid: int, initial_valid: int, material_content_defects: int | None,
    unsupported_stage_escalations: int, material_claims: int, citation_defective_claims: int,
    fabricated_numeric_facts: int, decision_leaks: int,
) -> list[GateResult]:
    """L1-L7, computed mechanically. `material_content_defects=None` means the manual audit (§13)
    has not been performed yet - reported as `NOT_EVALUATED`, never a silent PASS, the same
    convention D3.1's own R10 gate used."""
    final_rate = final_valid / attempted if attempted else 0.0
    initial_rate = initial_valid / attempted if attempted else 0.0
    citation_rate = citation_defective_claims / material_claims if material_claims else 0.0
    l3 = (GateResult("L3", "material content accuracy", f"== {L3_MAX_MATERIAL_CONTENT_DEFECTS}",
                     "not performed", GateStatus.NOT_EVALUATED) if material_content_defects is None
         else _gate("L3", "material content accuracy", f"== {L3_MAX_MATERIAL_CONTENT_DEFECTS}",
                    str(material_content_defects),
                    material_content_defects <= L3_MAX_MATERIAL_CONTENT_DEFECTS))
    return [
        _gate("L1", "final validity", f">= {L1_MIN_FINAL_VALID_RATE:.0%}",
             f"{final_valid}/{attempted} = {final_rate:.1%}", final_rate >= L1_MIN_FINAL_VALID_RATE),
        _gate("L2", "initial validity (pre-repair)", f">= {L2_MIN_INITIAL_VALID_RATE:.0%}",
             f"{initial_valid}/{attempted} = {initial_rate:.1%}",
             initial_rate >= L2_MIN_INITIAL_VALID_RATE),
        l3,
        _gate("L4", "unsupported Future Business stage escalation",
             f"== {L4_MAX_UNSUPPORTED_STAGE_ESCALATIONS}", str(unsupported_stage_escalations),
             unsupported_stage_escalations <= L4_MAX_UNSUPPORTED_STAGE_ESCALATIONS),
        _gate("L5", "citation precision defect rate", f"<= {L5_MAX_CITATION_DEFECT_RATE:.0%}",
             f"{citation_defective_claims}/{material_claims} = {citation_rate:.1%}",
             citation_rate <= L5_MAX_CITATION_DEFECT_RATE),
        _gate("L6", "fabricated/mutated material numeric facts",
             f"== {L6_MAX_FABRICATED_NUMERIC_FACTS}", str(fabricated_numeric_facts),
             fabricated_numeric_facts <= L6_MAX_FABRICATED_NUMERIC_FACTS),
        _gate("L7", "investment decision leakage", f"== {L7_MAX_DECISION_LEAKS}",
             str(decision_leaks), decision_leaks <= L7_MAX_DECISION_LEAKS),
    ]


class D33Verdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_LIMITATIONS = "PASS_WITH_LIMITATIONS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


#: The "core" gates (brief §14: "핵심 factual/future-business/numeric gate") whose failure is
#: always FAIL, never merely a limitation - L1/L2/L5 are operational/prompt-quality gates that can
#: fall under "non-structural operational limitation" instead.
D33_CORE_GATES = ("L3", "L4", "L6", "L7")


def d3_3_verdict(gates: list[GateResult]) -> D33Verdict:
    """D3.2F brief §14, applied mechanically: FAIL if any core gate fails or is unevaluated (an
    unperformed manual audit is not a PASS by default - same discipline as D3.1's own R10);
    PASS if every gate passes; PASS_WITH_LIMITATIONS if only a non-core gate (L1/L2/L5) fails."""
    by_id = {g.gate: g for g in gates}
    for core in D33_CORE_GATES:
        if by_id[core].status != GateStatus.PASS:
            return D33Verdict.FAIL
    if all(g.status == GateStatus.PASS for g in gates):
        return D33Verdict.PASS
    return D33Verdict.PASS_WITH_LIMITATIONS
