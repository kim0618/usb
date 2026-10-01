"""H-V2-D4-BR: the convergence / abstention confirmation preregistration.

Frozen before any D4-BR confirmation call exists. It proposes a sample and a success rule; it does
not authorize spending. §1 of the brief forbids live calls in this step, so nothing here runs.

Why a NEW sample, and why reusing the frozen six would prove nothing. The six were the denominator
that produced the FAIL, and D4-BR's repairs were designed by reading their raw attempts - the
`unknown_fields` exemption exists because SPSC tripped on it, and the pre-scan exists because FRPT
and IDCC did. Re-running those six would measure how well a fix fits the cases it was cut from. The
confirmation question is the generalization one: on issuers nobody has looked at, does a candidate
whose evidence does not support a comparison now reach a valid abstention instead of nothing?

The exclusion set is every CIK any H live call has ever touched - D3.1 Batch 2, D3.3, Tier A and
Tier B - so a confirmation issuer cannot be one whose behaviour is already in the design.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    EXCLUDED_CIKS,
    TIER_B_SAMPLE,
    SampleEntry,
)
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR

D4_BR_CONTRACT_VERSION = "h_v2_d4_br_convergence_confirmation_v1"

#: Every CIK H has spent a live call on. Tier B's six are added to D4.1's 48.
D4_BR_EXCLUDED_CIKS: frozenset[str] = EXCLUDED_CIKS | {e.cik for e in TIER_B_SAMPLE}
assert len(D4_BR_EXCLUDED_CIKS) == 54, (
    "48 previously-excluded CIKs plus Tier B's 6 - a different count means a sample overlapped"
)

D4_BR_SEED = "H_V2_D4_BR_CONFIRMATION_V1"
D4_BR_N_FULL = 2
D4_BR_N_CORE = 2
D4_BR_N = D4_BR_N_FULL + D4_BR_N_CORE

#: Frozen by `regenerate_confirmation_sample()` over the D2.1 snapshot, same seeded-hash convention
#: D3.1 Batch 2, D3.3 and Tier B all used. The literal is checked against a fresh computation rather
#: than against a hash of itself.
D4_BR_CONFIRMATION_SAMPLE: tuple[SampleEntry, ...] = (
    SampleEntry("AEYE", "0001362190", "FULL", "E3_P1_HIGH"),
    SampleEntry("COLL", "0001267565", "FULL", "E3_P1_HIGH"),
    SampleEntry("FG", "0001934850", "CORE", "E3_P2_MEDIUM"),
    SampleEntry("VRRM", "0001682745", "CORE", "E3_P2_MEDIUM"),
)
D4_BR_CONFIRMATION_CHECKSUM = (
    "69598e04ad0e99e6b438567301be46b71340a43382cf5d3e3a5c2503775aa442"
)


def regenerate_confirmation_sample(
    packages_dir: Path = PACKAGES_DIR,
) -> tuple[SampleEntry, ...]:
    """Offline, read-only, 0 model calls."""
    rows: list[SampleEntry] = []
    for path in sorted(packages_dir.glob("*.json")):
        bundle = json.loads(path.read_text())["evidence_bundle"]
        cik = bundle["identity"]["cik"]
        if cik in D4_BR_EXCLUDED_CIKS:
            continue
        rows.append(SampleEntry(bundle["identity"]["ticker"], cik, bundle["collection_depth"],
                                bundle["candidate_source"]))

    def key(entry: SampleEntry) -> str:
        return hashlib.sha256(f"{D4_BR_SEED}:{entry.ticker}".encode()).hexdigest()

    full = sorted((r for r in rows if r.depth == "FULL"), key=key)
    core = sorted((r for r in rows if r.depth == "CORE"), key=key)
    return tuple(full[:D4_BR_N_FULL]) + tuple(core[:D4_BR_N_CORE])


def confirmation_checksum(sample: tuple[SampleEntry, ...] | None = None) -> str:
    sample = D4_BR_CONFIRMATION_SAMPLE if sample is None else sample
    return hashlib.sha256("|".join(e.ticker for e in sample).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# Success rule (brief §22)
# ---------------------------------------------------------------------------------------------
#
# E1's threshold is NOT changed and is not restated as a different number: it stays the frozen
# >= 95% from `d4_1_contract`. On four candidates, >= 95% and 4/4 are the same requirement, because
# 3/4 is 75%. The rule is written as 4/4 because that is what it means at n=4, and recording it as a
# percentage on a denominator of four would invite someone to read 75% as a near miss.

D4_BR_E1_REQUIRED_FINAL_VALID = D4_BR_N
"""All four. Derived from the frozen E1 threshold at n=4, not a new threshold."""


def e1_confirmation_passes(final_valid: int, attempted: int = D4_BR_N) -> bool:
    from app.backtest.strategy_h_v2.expectation.d4_1_contract import E1_MIN_SCHEMA_VALID_RATE
    if attempted <= 0:
        return False
    return (final_valid / attempted) >= E1_MIN_SCHEMA_VALID_RATE


#: The correctness gates a confirmation run must also satisfy, by reference, never restated:
#: `d4_1_contract.evaluate_d4_1_gates` for E2-E8 and `state_fidelity.GATE_ID` for SF1. A
#: confirmation that passed E1 while leaking a fabricated consensus would not be a pass.
D4_BR_APPLIES_EXISTING_CORRECTNESS_GATES = True
