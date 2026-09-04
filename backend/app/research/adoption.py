"""Deterministic adoption triage over persisted Quant and Research truth."""

from dataclasses import dataclass
from enum import StrEnum
from math import floor
from typing import Any


ADOPTION_FILTER_VERSION = "adoption_filter_v0"


class AdoptionClassification(StrEnum):
    ADOPTION_CANDIDATE = "ADOPTION_CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EXCLUDED = "EXCLUDED"


#: Final human-review order. Deterministic over persisted truth only: no new score
#: is introduced and the classification itself is untouched.
RECOMMENDATION_ORDER: tuple[AdoptionClassification, ...] = (
    AdoptionClassification.ADOPTION_CANDIDATE,
    AdoptionClassification.REVIEW_REQUIRED,
    AdoptionClassification.EXCLUDED,
)


def recommendation_key(classification: str, gpt_rank: int, quant_rank: int | None, symbol: str) -> tuple[int, int, int, int, str]:
    """Sort key for `recommendation_rank`: classification, GPT rank, Quant rank, symbol."""
    order = [item.value for item in RECOMMENDATION_ORDER]
    group = order.index(classification) if classification in order else len(order)
    return (group, gpt_rank, 1 if quant_rank is None else 0, 0 if quant_rank is None else quant_rank, symbol)


@dataclass(frozen=True)
class AdoptionInput:
    symbol: str
    quant_rank: int | None
    gpt_rank: int
    quant_score: float | None
    overall_score: float
    catalyst_score: float
    momentum_score: float
    risk_score: float
    evidence_score: int
    fundamental_score: float
    rvol: float | None
    relative_strength: float | None
    unknown_fields: tuple[str, ...]
    catalyst_duration: str
    has_catalyst_source: bool
    pool_size: int


def _band(value: float, pass_at: float, marginal_at: float | None) -> str:
    if value >= pass_at:
        return "PASS"
    if marginal_at is not None and value >= marginal_at:
        return "MARGINAL"
    return "FAIL"


def classify(item: AdoptionInput) -> AdoptionClassification:
    dimensions = (
        (_band(item.overall_score, 70, 60), item.overall_score, 60),
        (_band(item.catalyst_score, 75, 60), item.catalyst_score, 60),
        (_band(item.momentum_score, 60, 40), item.momentum_score, 40),
        (_band(item.risk_score, 30, None), item.risk_score, 30),
    )
    failures = [dimension for dimension in dimensions if dimension[0] == "FAIL"]
    if not failures:
        return (AdoptionClassification.ADOPTION_CANDIDATE
                if all(dimension[0] == "PASS" for dimension in dimensions)
                else AdoptionClassification.REVIEW_REQUIRED)
    if (len(failures) == 1 and all(dimension[0] == "PASS" for dimension in dimensions if dimension is not failures[0])
            and failures[0][2] - failures[0][1] <= 5):
        return AdoptionClassification.REVIEW_REQUIRED
    return AdoptionClassification.EXCLUDED


def rank_delta(item: AdoptionInput) -> tuple[int | None, str, str]:
    if item.quant_rank is None:
        return None, "순위 없음", "NEUTRAL"
    delta = item.quant_rank - item.gpt_rank
    if delta > 0:
        return delta, f"↑{delta}", "UP"
    if delta < 0:
        return delta, f"↓{abs(delta)}", "DOWN"
    return 0, "유지", "UNCHANGED"


def strengths(item: AdoptionInput) -> list[str]:
    result: list[str] = []
    if item.catalyst_score >= 90:
        result.append(f"촉매 {item.catalyst_score:g} 매우 강함")
    elif item.catalyst_score >= 75:
        result.append(f"촉매 {item.catalyst_score:g} 강함")
    if item.momentum_score >= 90:
        result.append(f"모멘텀 {item.momentum_score:g} 매우 강함")
    elif item.momentum_score >= 75:
        result.append(f"모멘텀 {item.momentum_score:g} 강함")
    if item.fundamental_score >= 90:
        result.append(f"기업 체력 {item.fundamental_score:g} 매우 강함")
    if item.risk_score >= 70:
        result.append(f"안전도 {item.risk_score:g} 매우 높음")
    if item.rvol is not None and item.rvol >= 1.5:
        result.append(f"거래량 {item.rvol:.2f}배 활발")
    if item.quant_rank == item.gpt_rank and item.gpt_rank <= 2:
        result.append(f"Quant·GPT 모두 #{item.gpt_rank}")
    if item.evidence_score >= 70:
        result.append(f"근거 신뢰도 {item.evidence_score} 높음")
    if item.relative_strength is not None and item.relative_strength > 0:
        result.append(f"시장 대비 상대강도 +{item.relative_strength * 100:.2f}%")
    return result


def warnings(item: AdoptionInput) -> list[str]:
    result: list[str] = []
    if item.evidence_score < 40:
        result.append(f"근거 신뢰도 낮음 {item.evidence_score}")
    if item.risk_score < 50:
        result.append(f"안전도 주의 {item.risk_score:g}")
    delta, _, _ = rank_delta(item)
    if delta is not None and abs(delta) >= 3:
        result.append(f"Quant/GPT 순위 괴리 {delta:+d}")
    if len(item.unknown_fields) >= 3:
        result.append(f"미확인 항목 {len(item.unknown_fields)}건")
    if item.catalyst_duration == "UNKNOWN":
        result.append("재료 지속성 확인 불가")
    if not item.has_catalyst_source:
        result.append("촉매 출처 없음")
    if abs(item.catalyst_score - item.momentum_score) >= 30:
        result.append(f"촉매·모멘텀 불일치 {item.catalyst_score:g}/{item.momentum_score:g}")
    if item.rvol is not None and item.rvol < 1.2:
        result.append(f"거래량 관심 부족 RVOL {item.rvol:.2f}")
    if item.quant_rank is not None and item.pool_size and item.quant_rank > floor(item.pool_size * 2 / 3):
        result.append(f"Quant 하위권 #{item.quant_rank}/{item.pool_size}")
    return result


def explanation(item: AdoptionInput) -> str:
    delta, label, _ = rank_delta(item)
    positives: list[str] = []
    if item.catalyst_score >= 75: positives.append(f"촉매 {item.catalyst_score:g}")
    if item.fundamental_score >= 90: positives.append(f"기업 체력 {item.fundamental_score:g}")
    if item.momentum_score >= 75: positives.append(f"모멘텀 {item.momentum_score:g}")
    cautions: list[str] = []
    if item.catalyst_score < 75: cautions.append(f"촉매 {item.catalyst_score:g}")
    if item.momentum_score < 60: cautions.append(f"모멘텀 {item.momentum_score:g}")
    if item.evidence_score < 40: cautions.append(f"근거 신뢰도 {item.evidence_score}")
    if delta is None:
        prefix = "Quant 순위가 없어 GPT 순위 변화는 비교할 수 없습니다"
    elif delta > 0:
        prefix = f"GPT가 {', '.join(positives) or '종합 점수'}을 높게 평가해 Quant 순위보다 {label[1:]}계단 상승했습니다"
    elif delta < 0:
        prefix = f"{', '.join(cautions) or 'GPT 평가 점수'}를 확인할 필요가 있어 Quant 순위보다 {label[1:]}계단 하락했습니다"
    else:
        prefix = "Quant와 GPT 순위가 일치합니다"
    return prefix + "."


def evaluate(item: AdoptionInput) -> dict[str, Any]:
    delta, label, direction = rank_delta(item)
    return {"classification": classify(item).value, "rank_delta": delta,
            "rank_delta_label": label, "rank_direction": direction,
            "strengths": strengths(item), "warnings": warnings(item),
            "rank_explanation": explanation(item)}
