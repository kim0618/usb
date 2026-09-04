from app.research.adoption import AdoptionClassification, AdoptionInput, classify, evaluate, recommendation_key


def item(**changes):  # type: ignore[no-untyped-def]
    values = dict(symbol="TEST", quant_rank=1, gpt_rank=1, quant_score=1.0,
        overall_score=80, catalyst_score=80, momentum_score=80, risk_score=60,
        evidence_score=60, fundamental_score=70, rvol=1.3, relative_strength=0.01,
        unknown_fields=(), catalyst_duration="ONE_TO_TWO_DAYS",
        has_catalyst_source=True, pool_size=7)
    values.update(changes)
    return AdoptionInput(**values)


def test_classification_pass_marginal_fail_and_near_miss() -> None:
    assert classify(item()) is AdoptionClassification.ADOPTION_CANDIDATE
    assert classify(item(catalyst_score=74)) is AdoptionClassification.REVIEW_REQUIRED
    assert classify(item(catalyst_score=54)) is AdoptionClassification.EXCLUDED
    assert classify(item(momentum_score=38)) is AdoptionClassification.REVIEW_REQUIRED
    assert classify(item(momentum_score=38, catalyst_score=74)) is AdoptionClassification.EXCLUDED


def test_safety_score_polarity_and_advisory_dimensions() -> None:
    assert classify(item(risk_score=70)) is AdoptionClassification.ADOPTION_CANDIDATE
    assert classify(item(risk_score=29)) is AdoptionClassification.REVIEW_REQUIRED
    assert classify(item(risk_score=20)) is AdoptionClassification.EXCLUDED
    assert classify(item(evidence_score=1, fundamental_score=1, quant_rank=7)) is AdoptionClassification.ADOPTION_CANDIDATE
    result = evaluate(item(evidence_score=20, fundamental_score=95, rvol=1.0))
    assert "근거 신뢰도 낮음 20" in result["warnings"]
    assert "기업 체력 95 매우 강함" in result["strengths"]


def test_rank_delta_strengths_warnings_and_explanation() -> None:
    result = evaluate(item(quant_rank=5, gpt_rank=2, catalyst_score=97,
        fundamental_score=98, momentum_score=82, evidence_score=35, rvol=1.07,
        unknown_fields=("a", "b", "c"), has_catalyst_source=False))
    assert (result["rank_delta"], result["rank_delta_label"], result["rank_direction"]) == (3, "↑3", "UP")
    assert "촉매 97 매우 강함" in result["strengths"]
    assert "Quant/GPT 순위 괴리 +3" in result["warnings"]
    assert "촉매 출처 없음" in result["warnings"]
    assert "상승" in result["rank_explanation"]


def test_run2_equivalent_is_three_two_two() -> None:
    rows = [
        ("TSLA",1,1,91,98,98,47,25,70,1.83), ("SPCX",2,6,68,47,99,31,31,79,1.16),
        ("AVGO",3,5,70,95,38,53,36,95,2.98), ("META",4,3,77,64,82,59,31,88,1.28),
        ("NVDA",5,2,89,97,82,67,55,98,1.07), ("AAPL",6,7,59,35,65,69,28,93,.93),
        ("MSFT",7,4,75,80,62,82,65,96,1.00),
    ]
    actual = {symbol: classify(item(symbol=symbol, quant_rank=qr, gpt_rank=gr,
        overall_score=o, catalyst_score=c, momentum_score=m, risk_score=r,
        evidence_score=e, fundamental_score=f, rvol=v)) for symbol,qr,gr,o,c,m,r,e,f,v in rows}
    assert actual == {"TSLA": AdoptionClassification.ADOPTION_CANDIDATE, "NVDA": AdoptionClassification.ADOPTION_CANDIDATE,
        "MSFT": AdoptionClassification.ADOPTION_CANDIDATE, "META": AdoptionClassification.REVIEW_REQUIRED,
        "AVGO": AdoptionClassification.REVIEW_REQUIRED, "SPCX": AdoptionClassification.EXCLUDED,
        "AAPL": AdoptionClassification.EXCLUDED}


def test_recommendation_order_is_classification_then_gpt_then_quant_then_symbol() -> None:
    rows = [
        ("TSLA",1,1,91,98,98,47,25,70,1.83), ("SPCX",2,6,68,47,99,31,31,79,1.16),
        ("AVGO",3,5,70,95,38,53,36,95,2.98), ("META",4,3,77,64,82,59,31,88,1.28),
        ("NVDA",5,2,89,97,82,67,55,98,1.07), ("AAPL",6,7,59,35,65,69,28,93,.93),
        ("MSFT",7,4,75,80,62,82,65,96,1.00),
    ]
    inputs = [item(symbol=symbol, quant_rank=qr, gpt_rank=gr, overall_score=o, catalyst_score=c,
        momentum_score=m, risk_score=r, evidence_score=e, fundamental_score=f, rvol=v)
        for symbol,qr,gr,o,c,m,r,e,f,v in rows]
    ordered = sorted(inputs, key=lambda row: recommendation_key(classify(row).value, row.gpt_rank, row.quant_rank, row.symbol))
    assert [row.symbol for row in ordered] == ["TSLA", "NVDA", "MSFT", "META", "AVGO", "SPCX", "AAPL"]
    assert sorted(inputs, key=lambda row: recommendation_key(classify(row).value, row.gpt_rank, row.quant_rank, row.symbol)) == ordered


def test_recommendation_key_tie_breaks_on_quant_rank_then_symbol() -> None:
    same = "ADOPTION_CANDIDATE"
    assert recommendation_key(same, 1, 2, "BBB") < recommendation_key(same, 1, 3, "AAA")
    assert recommendation_key(same, 1, 2, "AAA") < recommendation_key(same, 1, 2, "BBB")
    assert recommendation_key(same, 1, 9, "AAA") < recommendation_key(same, 1, None, "AAA")
    assert recommendation_key(same, 9, 1, "AAA") < recommendation_key("REVIEW_REQUIRED", 1, 1, "AAA")
    assert recommendation_key("REVIEW_REQUIRED", 9, 1, "AAA") < recommendation_key("EXCLUDED", 1, 1, "AAA")
