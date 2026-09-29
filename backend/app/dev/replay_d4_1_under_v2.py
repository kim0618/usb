"""Offline V2 counterfactual replay of D4.1's stored raw responses. Zero live calls, zero cost.

Brief §22 asks one question: do the two impossible-contract failures D4.1 hit disappear under the
repaired validator? It is answered by re-running the V2 validator over the EXACT bytes the model
returned in D4.1 - the full untruncated initial and repair responses the ledger stored - against
the same package, the same code-owned expectation bundle and the same immutable D3 output.

Two things this is not, stated here because they are the easy mistakes:

  It does not modify D4.1's verdict. D4.1 ran V1 and failed V1; that record stands. The output of
  this module is a separate artifact labelled V2_COUNTERFACTUAL_REPLAY.

  It is not evidence that V2 succeeds live. The stored responses were produced against the V1
  PROMPT, which showed the model `{"type": "string"}` for `direction`. A response that chose
  `INCREASED` under a schema that listed no members will still be rejected here, correctly, and
  that rejection says nothing about what a model shown the enum would answer. Only the live Tier A
  re-run can answer that, which is why §30 puts it behind a separate authorization.

So the finding this module can legitimately produce is narrow and worth exactly what it is: whether
the consensus rule still fires on a sentence that honestly reports consensus evidence as absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.consensus_language import (
    ConsensusVerdict,
    classify_sentence,
    split_sentences,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import D3_3_ATTEMPTS_ROOT, D4_2_ROOT
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.validate import (
    VALIDATION_CONTRACT_VERSION,
    assemble_and_validate_d4,
)
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR

D4_1_ANALYSES_ROOT = Path("data/runtime/strategy_h_v2/d4_1/analyses")
REPLAY_ROOT = D4_2_ROOT / "replay"

#: The V1 error text that could not be repaired. A replayed response whose V2 errors no longer
#: contain it is the Defect 1 repair, observed rather than asserted.
V1_IMPOSSIBLE_CONSENSUS_ERROR = "text asserts a consensus expectation"
#: V1's direction rejection. Expected to SURVIVE the replay - see the module docstring.
V1_DIRECTION_ERROR = "direction must be one of"


@dataclass
class ResponseReplay:
    ticker: str
    role: str
    attempt: int
    v1_validation: str
    """The V1 verdict on these exact bytes, read from the stored record: OK or FAILED."""
    v1_errors: list[str]
    """V1's error list, where the ledger stored one. The LAST repair round's errors were never
    stored - D4.1's record keeps each round's `failure_details_before`, so the errors produced by
    the final response have no home in the schema. The verdict for those bytes is still known from
    `validation_after`, which is why this class carries both and does not infer one from the
    other."""
    v2_errors: list[str]
    v2_validated: bool
    consensus_absence_sentences: list[str] = field(default_factory=list)
    consensus_asserted_sentences: list[str] = field(default_factory=list)

    @property
    def v1_rejected(self) -> bool:
        return self.v1_validation != "OK"

    @property
    def v1_rejected_and_v2_accepts(self) -> bool:
        return self.v1_rejected and self.v2_validated

    @property
    def v1_hit_impossible_consensus_rule(self) -> bool:
        return any(V1_IMPOSSIBLE_CONSENSUS_ERROR in e for e in self.v1_errors)

    @property
    def v2_hit_consensus_rule(self) -> bool:
        return any("attributes an expectation to analysts or the market" in e
                   for e in self.v2_errors)

    @property
    def v1_hit_direction_rule(self) -> bool:
        return any(V1_DIRECTION_ERROR in e for e in self.v1_errors)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "role": self.role, "attempt": self.attempt,
            "v1_validation": self.v1_validation,
            "v1_errors_stored": bool(self.v1_errors),
            "v1_error_count": len(self.v1_errors), "v1_errors": self.v1_errors,
            "v2_error_count": len(self.v2_errors), "v2_errors": self.v2_errors,
            "v2_validated": self.v2_validated,
            "v1_hit_impossible_consensus_rule": self.v1_hit_impossible_consensus_rule,
            "v2_hit_consensus_rule": self.v2_hit_consensus_rule,
            "v1_hit_direction_rule": self.v1_hit_direction_rule,
            "v1_rejected_and_v2_accepts": self.v1_rejected_and_v2_accepts,
            "consensus_absence_sentences": self.consensus_absence_sentences,
            "consensus_asserted_sentences": self.consensus_asserted_sentences,
        }


def _d3_output_by_attempt_id(attempts_root: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for path in sorted(attempts_root.rglob("*.json")):
        record = json.loads(path.read_text())
        if record.get("attempt_id"):
            index[record["attempt_id"]] = record
    return index


def _consensus_scan(raw_text: str) -> tuple[list[str], list[str]]:
    """Every sentence in the raw response the classifier calls an absence or an assertion.

    Run over the raw text rather than over the parsed fields on purpose: a response that failed to
    parse has no fields, and the consensus question is still answerable about what it wrote.
    """
    absence, asserted = [], []
    for sentence in split_sentences(raw_text):
        finding = classify_sentence(sentence)
        if finding.verdict == ConsensusVerdict.ABSENCE:
            absence.append(sentence)
        elif finding.verdict == ConsensusVerdict.ASSERTED:
            asserted.append(sentence)
    return absence, asserted


def replay_analysis(
    analysis_path: Path, *, packages_dir: Path = PACKAGES_DIR,
    d3_attempts_root: Path = D3_3_ATTEMPTS_ROOT,
) -> list[ResponseReplay]:
    """Every stored response for one D4.1 analysis, re-validated under the V2 contract."""
    record = json.loads(analysis_path.read_text())
    ticker = record["ticker"]
    bundle = ExpectationEvidenceBundleV1.model_validate_json(
        (analysis_path.parent / "expectation_evidence.json").read_text())
    package = AIResearchInputV1.model_validate_json(
        (packages_dir / f"{ticker}.json").read_text())
    d3_record = _d3_output_by_attempt_id(d3_attempts_root)[record["research_input_id_attempt"]] \
        if "research_input_id_attempt" in record else None
    if d3_record is None:
        d3_record = _find_d3_output(d3_attempts_root, ticker)
    research_output = d3_record["final_output"]
    code_facts = build_code_fact_index(
        bundle, research_facts=package.evidence_bundle.fundamental_changes)
    created_at = datetime.now(timezone.utc)

    def validate(raw_text: str):
        return assemble_and_validate_d4(
            raw_text, package=package, bundle=bundle, research_output=research_output,
            code_facts=code_facts, analysis_id=record["analysis_id"], version=1,
            model_name=record["model_requested"], model_version=record["canonical_model"],
            prompt_version=record["prompt_version"], created_at=created_at,
        )

    #: Each stored response with V1's own verdict on it. The verdict is read, never recomputed:
    #: V1's validator is not installed any more, and re-deriving a historical verdict from current
    #: code is how a replay quietly becomes a rewrite.
    rounds = record.get("repair_rounds") or []
    attempts: list[tuple[str, int, dict, str, list[str]]] = [
        ("initial", 0, record["initial_raw_response"], record["initial_validation_status"],
         record.get("initial_failure_details") or []),
    ]
    for index, round_record in enumerate(rounds):
        raw = round_record["raw_repair_response"]
        #: A repair response's V1 errors are the NEXT round's `failure_details_before`. The last
        #: round has no next round, so its errors were never stored - `v1_errors` is empty there
        #: and `v1_validation` carries the verdict instead.
        following = (rounds[index + 1]["failure_details_before"]
                     if index + 1 < len(rounds) else [])
        attempts.append(("repair", raw["attempt"], raw, round_record["validation_after"],
                         list(following)))

    replays: list[ResponseReplay] = []
    for role, attempt, raw, v1_validation, v1_errors in attempts:
        raw_text = raw.get("raw_text", "")
        output, v2_errors = validate(raw_text)
        absence, asserted = _consensus_scan(raw_text)
        replays.append(ResponseReplay(
            ticker=ticker, role=role, attempt=attempt, v1_validation=v1_validation,
            v1_errors=list(v1_errors),
            v2_errors=list(v2_errors), v2_validated=output is not None,
            consensus_absence_sentences=absence[:10],
            consensus_asserted_sentences=asserted[:10],
        ))
    return replays


def _find_d3_output(attempts_root: Path, ticker: str) -> dict:
    best: dict | None = None
    for path in sorted(attempts_root.rglob("*.json")):
        record = json.loads(path.read_text())
        if record.get("ticker") != ticker or record.get("final_status") != "OK":
            continue
        if best is None or (record.get("completed_at") or "") > (best.get("completed_at") or ""):
            best = record
    if best is None:
        raise RuntimeError(f"no OK D3 output stored for {ticker} under {attempts_root}")
    return best


def replay_all(
    *, analyses_root: Path = D4_1_ANALYSES_ROOT, out_root: Path = REPLAY_ROOT,
    packages_dir: Path = PACKAGES_DIR, d3_attempts_root: Path = D3_3_ATTEMPTS_ROOT,
) -> dict:
    replays: list[ResponseReplay] = []
    for path in sorted(analyses_root.rglob("*.json")):
        if path.name == "expectation_evidence.json":
            continue
        replays.extend(replay_analysis(path, packages_dir=packages_dir,
                                       d3_attempts_root=d3_attempts_root))
    report = {
        "schema": "H_V2_D4_2_V2_COUNTERFACTUAL_REPLAY_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_calls": 0, "cost_usd": 0.0,
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
        "source": str(analyses_root),
        "note": (
            "A V2 counterfactual over D4.1's stored bytes. It does not modify the D4.1 verdict and "
            "it is not evidence that the V2 contract succeeds live: these responses were produced "
            "against the V1 prompt, whose schema showed no direction members."
        ),
        "responses_replayed": len(replays),
        "v1_impossible_consensus_failures": sum(
            1 for r in replays if r.v1_hit_impossible_consensus_rule),
        "still_failing_consensus_under_v2": sum(
            1 for r in replays if r.v1_hit_impossible_consensus_rule and r.v2_hit_consensus_rule),
        "v1_direction_failures": sum(1 for r in replays if r.v1_hit_direction_rule),
        "responses_valid_under_v2": sum(1 for r in replays if r.v2_validated),
        "v1_rejected_v2_accepts": sum(1 for r in replays if r.v1_rejected_and_v2_accepts),
        "candidates_whose_final_response_validates_under_v2": sorted({
            r.ticker for r in replays if r.role == "repair" and r.v2_validated}),
        "replays": [r.to_dict() for r in replays],
    }
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_root / f"v2_counterfactual_replay-{stamp}.json").write_text(
        json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    report = replay_all()
    print(json.dumps({k: v for k, v in report.items() if k != "replays"}, indent=2))


if __name__ == "__main__":
    main()
