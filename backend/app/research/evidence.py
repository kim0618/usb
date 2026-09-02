"""Deterministic structural evidence coverage scoring (not truth probability)."""

from collections import defaultdict

from app.research.domain import ResearchSource, SourceType

SOURCE_QUALITY = {
    SourceType.SEC: 1.0, SourceType.IR: 1.0, SourceType.EXCHANGE: 1.0,
    SourceType.OFFICIAL: 1.0, SourceType.NEWS: 0.8, SourceType.OTHER: 0.4,
}
PRIMARY_TYPES = {SourceType.SEC, SourceType.IR, SourceType.EXCHANGE, SourceType.OFFICIAL}


def evidence_confidence(sources: list[ResearchSource], unknown_fields: list[str]) -> int:
    by_claim: dict[str, list[ResearchSource]] = defaultdict(list)
    for source in sources:
        by_claim[source.claim.strip().lower()].append(source)
    catalyst_quality = max((SOURCE_QUALITY[s.type] for s in by_claim.get("catalyst", [])), default=0.0)
    other_qualities = sorted(
        (max(SOURCE_QUALITY[s.type] for s in claim_sources)
         for claim, claim_sources in by_claim.items() if claim != "catalyst"),
        reverse=True,
    )[:4]
    score = 40 * catalyst_quality + 10 * sum(other_qualities)
    if any(source.type in PRIMARY_TYPES for source in sources):
        score += 10
    domains = {source.url.host.lower() for source in sources if source.url.host}
    if len(domains) >= 2:
        score += 10
    score -= min(len(set(unknown_fields)), 5) * 5
    result = max(0, min(100, round(score)))
    return min(result, 49) if catalyst_quality == 0 else result
