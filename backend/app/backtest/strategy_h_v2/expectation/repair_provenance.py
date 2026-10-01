"""H-V2-D4-BR: the repair provenance lock (brief §11/§12).

A bounded repair exists to correct a defect in an answer, not to look for a different answer. The
distinction matters because the two are indistinguishable from the outside: both produce a changed
payload that validates. Tier B's SPSC rounds are the shape to watch - a comparison was refused, and
the next round reached for a different evidence category to support a similar conclusion:

    initial   ABOVE_COMPANY_GUIDANCE on two metrics, prior-guidance bounds absent
    repair 1  both downgraded to UNKNOWN            <- a correct, conservative repair
    repair 2  the supporting prose reworded around what analysts anticipate

Repair 1 is exactly what §10 asks for. Repair 2 is the move this module forbids in general: when a
thesis is refused, the repair may remove it or weaken it, and may not go looking for a new source
category to carry it. Note what the lock does and does not catch here - SPSC introduced no new
source id, so the lock would not have rejected repair 2. It is not a substitute for the fabrication
check; it closes the route where a refused thesis comes back cited to something that was never in
the initial answer, which is the route a consensus provider would open the moment one exists.

The lock is deliberately one-directional. REMOVING a source is always allowed - that is what
"remove the unsupported claim" means - and the set may shrink to empty. Only introduction is
rejected.

Category, not just id: `SEC:0001092699:...` and `CODE:D4:EB-...` are the two categories D4 has. A
repair that cited `IBES:...` or `CONSENSUS:...` would be introducing a provider, and naming the
category in the rejection says so in the one word that matters.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

REPAIR_PROVENANCE_CONTRACT_VERSION = "h_v2_d4_br_repair_provenance_v1"

#: Keys that carry a single id, and keys that carry a list of them. Both shapes appear in the D4
#: schema (`ClaimV2.source_id` vs `ManagementSignalChangeV1.evidence_ids`), and a lock that knew only
#: one of them would have a hole exactly where the two-document claims are.
_SINGULAR = {"source_id": "source", "evidence_id": "evidence"}
_PLURAL = {"source_ids": "source", "evidence_ids": "evidence", "sources": "source"}
#: `sources` is in the plural set because the D4 schema has a top-level `sources[]` that must cover
#: every citation in the document, and `why_now[].sources` beside it. A lock that watched only the
#: per-claim keys would miss a repair that declared a new provider in the one list whose job is to
#: enumerate providers.


def _harvest(node: Any, sources: set[str], evidence: set[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in _SINGULAR and isinstance(value, str):
                (sources if _SINGULAR[key] == "source" else evidence).add(value)
            elif key in _PLURAL and isinstance(value, (list, tuple)):
                target = sources if _PLURAL[key] == "source" else evidence
                target.update(v for v in value if isinstance(v, str))
            else:
                _harvest(value, sources, evidence)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _harvest(value, sources, evidence)


def category_of(identifier: str) -> str:
    """The provider category: everything before the first colon, uppercased.

    An id with no colon is its own category rather than an error. A malformed id is the citation
    validator's complaint, and two modules reporting the same defect in different words is how a
    repair prompt gets contradictory instructions.
    """
    return identifier.split(":", 1)[0].upper() if identifier else ""


@dataclass(frozen=True)
class ProvenanceLock:
    """What the initial response was allowed to cite, frozen for the rest of the candidate."""

    source_ids: frozenset[str]
    evidence_ids: frozenset[str]
    categories: frozenset[str]

    @classmethod
    def from_content(cls, content: Mapping[str, Any]) -> "ProvenanceLock":
        sources: set[str] = set()
        evidence: set[str] = set()
        _harvest(content, sources, evidence)
        return cls(
            source_ids=frozenset(sources),
            evidence_ids=frozenset(evidence),
            categories=frozenset(category_of(i) for i in sources | evidence),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ids": sorted(self.source_ids),
            "evidence_ids": sorted(self.evidence_ids),
            "categories": sorted(self.categories),
        }


def check_repair_provenance(
    content: Mapping[str, Any],
    lock: ProvenanceLock,
    *,
    citable_source_ids: Collection[str] | None = None,
    citable_evidence_ids: Collection[str] | None = None,
) -> list[str]:
    """What the repair introduced that it had no standing to introduce.

    Two different questions, and conflating them would break §10. CATEGORY is frozen at the initial
    call: a new provider category is the substitution §11 names, and there is no legitimate reason a
    repair needs one. IDS are not frozen that way, because §10 explicitly permits a repair to "attach
    already-present valid evidence" - the usual fix for a citation defect is to cite the bundle
    evidence id the claim should have carried, which the initial answer by definition did not. So an
    id is rejected only when it is outside the code-owned citable universe, which is what §12's
    "source/evidence that does not exist" means.

    When the citable sets are not supplied, no id-level check runs. That is deliberate: guessing the
    universe and rejecting against the guess would reject honest repairs, and the citation validator
    already holds the authoritative version of this check.
    """
    found = ProvenanceLock.from_content(content)
    errors: list[str] = []
    for category in sorted(found.categories - lock.categories):
        errors.append(
            f"repair introduced evidence category {category!r}, which the initial response did not "
            "cite. A repair may remove a claim or weaken it to UNKNOWN; it may not answer a refused "
            "claim from a source category that was not in the initial evidence universe (§11/§12) "
            "@ provenance"
        )
    if citable_source_ids is not None:
        for identifier in sorted(found.source_ids - set(citable_source_ids)):
            errors.append(
                f"repair cites source_id {identifier!r}, which is not in the code-owned citable set "
                "@ provenance.source_id"
            )
    if citable_evidence_ids is not None:
        for identifier in sorted(found.evidence_ids - set(citable_evidence_ids)):
            errors.append(
                f"repair cites evidence_id {identifier!r}, which is not in the code-owned citable "
                "set @ provenance.evidence_id"
            )
    return errors
