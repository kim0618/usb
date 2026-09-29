"""Citable identities for D4's code-owned numbers (brief §25).

The problem this solves. D3's claim contract (`ClaimV2`) requires every FACT/INTERPRETATION/
INFERENCE to cite a `source_id` + `evidence_id` resolving to a real evidence chunk. D4's most
important numbers are not in any chunk - a benchmark-adjusted 3-day event return exists only
because `price_engine` computed it. Under D3's contract as written, a claim about that number could
only be `UNKNOWN`, which would make the entire price-reaction layer uncitable and therefore unusable.

Two ways to fix that. Loosen the claim contract for D4 - which brief §24 forbids, and rightly:
a looser claim schema is how citation discipline erodes. Or give every code-owned number a real,
resolvable identity so D3's contract applies to it UNCHANGED. This module does the second.

A code fact id looks exactly like an evidence id (`<source_id>:CHUNK:<path>`) and resolves through
exactly the same validator, so `ClaimV2` needs no D4-specific branch. The difference is what
resolution proves: an ordinary evidence_id proves the text exists, while a code fact id also
carries the computed VALUE, which lets `validate.py` check that a claim citing it states that
number and not a different one. Citing code-owned arithmetic is therefore a STRICTER contract than
citing prose, not a workaround for a weaker one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1

#: The synthetic source every code fact belongs to. Namespaced per bundle so a code fact from one
#: candidate can never resolve inside another - the same cross-candidate rejection
#: `validation_v2.ProvenanceStatus.EVIDENCE_WRONG_CANDIDATE` enforces for real chunks.
CODE_SOURCE_PREFIX = "CODE:D4"


def code_source_id(bundle_id: str) -> str:
    return f"{CODE_SOURCE_PREFIX}:{bundle_id}"


@dataclass(frozen=True)
class CodeFact:
    evidence_id: str
    path: str
    value: float | str | None
    unit: str
    description: str

    def to_dict(self) -> dict:
        return {"evidence_id": self.evidence_id, "path": self.path, "value": self.value,
                "unit": self.unit, "description": self.description}


def _fact(source: str, path: str, value: Any, unit: str, description: str) -> CodeFact:
    return CodeFact(f"{source}:CHUNK:{path}", path, value, unit, description)


#: A window whose status is not COMPLETE has no value, and gets NO code fact at all rather than a
#: fact whose value is None. A citable id for a number that does not exist is exactly how "the
#: 3-day reaction was flat" gets written about a window that never completed.
def _window_facts(source: str, prefix: str, window: dict[str, Any], label: str) -> list[CodeFact]:
    if window.get("status") != "COMPLETE" or window.get("value") is None:
        return []
    return [_fact(source, prefix, window["value"], "RETURN_FRACTION",
                  f"{label} ({window.get('from_session')} -> {window.get('to_session')})")]


def build_code_fact_index(
    bundle: ExpectationEvidenceBundleV1, *, research_facts: dict[str, Any] | None = None,
) -> dict[str, CodeFact]:
    """Every code-owned number and state a D4 claim may cite, keyed by its evidence id.

    `research_facts` is the D2.1 bundle's `fundamental_changes` block - the reality side of the
    comparison, already code-owned and already cited this way by D3. Passing it here lets a D4
    claim cite the same state D3 cited, by an id that resolves to the same value, instead of
    restating it from D3's prose (which is how a state silently mutates between stages).
    """
    source = code_source_id(bundle.bundle_id)
    facts: list[CodeFact] = []

    context = bundle.pre_event_price_context
    for key, label in (
        ("return_1m", "candidate return over the last 21 sessions"),
        ("return_3m", "candidate return over the last 63 sessions"),
        ("return_6m", "candidate return over the last 126 sessions"),
        ("relative_strength_1m", "candidate return minus benchmark over the same 21 sessions"),
        ("relative_strength_3m", "candidate return minus benchmark over the same 63 sessions"),
        ("relative_strength_6m", "candidate return minus benchmark over the same 126 sessions"),
    ):
        if isinstance(context.get(key), dict):
            facts += _window_facts(source, f"pre_event_price_context.{key}", context[key], label)
    for key, unit, label in (
        ("last_close", "USD", "last PIT-eligible close"),
        ("close_252s_high", "USD", "highest close in the last 252 sessions"),
        ("close_252s_low", "USD", "lowest close in the last 252 sessions"),
        ("drawdown_from_252s_close_high", "RETURN_FRACTION",
         "last close relative to the highest close in the last 252 sessions"),
        ("realized_volatility_60s", "ANNUALIZED_STDEV",
         "annualized stdev of daily log returns over the last 60 sessions"),
    ):
        if context.get(key) is not None:
            facts.append(_fact(source, f"pre_event_price_context.{key}", context[key], unit, label))

    for reaction in bundle.price_reaction:
        base = f"price_reaction.{reaction.source_id}"
        for key, label in (
            ("return_1d", "1-session event return"),
            ("return_3d", "3-session event return"),
            ("benchmark_adjusted_1d", "benchmark-adjusted 1-session event return"),
            ("benchmark_adjusted_3d", "benchmark-adjusted 3-session event return"),
            ("pre_event_return_5d", "5-session return ending before the event"),
            ("pre_event_return_20d", "20-session return ending before the event"),
        ):
            facts += _window_facts(source, f"{base}.{key}", getattr(reaction, key), label)

    stub = bundle.valuation_context_stub
    for key in ("market_cap", "net_debt", "enterprise_value"):
        block = stub.get(key)
        if isinstance(block, dict) and block.get("value") is not None:
            facts.append(_fact(source, f"valuation_context_stub.{key}", block["value"], "USD",
                               f"code-owned {key.replace('_', ' ')}, raw context only - not a "
                               "valuation and not a judgement about price"))

    for metric, block in (research_facts or {}).items():
        if isinstance(block, dict) and block.get("state") is not None:
            facts.append(_fact(source, f"research_facts.fundamental_changes.{metric}.state",
                               str(block["state"]), "STATE_TOKEN",
                               f"code-owned D1/D2 fundamental change state for {metric}"))
    return {fact.evidence_id: fact for fact in facts}


def format_code_facts_block(index: dict[str, CodeFact]) -> str:
    """The prompt rendering. One line per fact, id first, so the model copies an id that exists
    rather than composing one from a path it saw in prose."""
    lines = [
        f"{fact.evidence_id} = {fact.value!r} [{fact.unit}] - {fact.description}"
        for fact in sorted(index.values(), key=lambda f: f.path)
    ]
    return "\n".join(lines)
