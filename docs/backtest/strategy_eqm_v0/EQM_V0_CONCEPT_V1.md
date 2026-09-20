# EQM-V0 개념 동결 (EVENT_QUALITY_MOMENTUM_V0)

- 연구 ID: `EVENT_QUALITY_MOMENTUM_V0` (약칭 `EQM-V0`)
- 선언일: 2026-09-20, 어떤 EQM 수익률도 계산하기 전
- 단계: `EQM-P0` Concept freeze
- 상태: **FROZEN**. 이 문서의 가설, 금지사항, 코호트 골격은 결과를 본 뒤 바꾸지 않는다

```text
Strategy C remains CLOSED.
EQM-V0 = New thesis derived from Strategy C findings.
```

Strategy C의 판정(`GATE-C2 = FAIL`, `SHORT HORIZON = REJECTED`, `VOLATILITY = OBSERVED / DEFERRED`,
`GATE-CE0 = FAIL`, `H1 = FAIL`, `H2 = FAIL`)은 하나도 바꾸지 않는다. EQM-V0는 C를 재개하는 것이
아니라, C-E0가 남긴 반증 가능한 질문 하나를 새로 선언해 검증한다. C의 임계값, event window,
horizon, 후보 규칙은 그대로 두고 **읽기 전용으로만** 재사용한다.

## 1. C-E0가 검증한 것과 EQM-V0가 묻는 것

C-E0는 `Early Momentum + Event 존재 여부`만 검증했고, 이벤트의 **존재**는 방향성 알파를 주지
않았다(EM 5D 초과 -0.256%p, M_ONLY +0.319%p, 차이 -0.575%p). C-E0의 한계 항목에는 이렇게 적혀
있다. "이벤트는 양식 코드와 8-K item 번호로만 정의된다. 방향, 강도, 서프라이즈 크기를 읽지 않는다."

EQM-V0의 질문은 다르다.

> 이벤트의 **존재 여부**가 아니라, **경제적 규모가 측정되는** 이벤트를 동반한 Early Momentum은
> 이후 방향성 수익을 보이는가?

세 단계로 나눈다.

| | 질문 |
| --- | --- |
| **Q1** | 이벤트 중 경제적 규모를 정량화할 수 있는 이벤트가 존재하는가 (데이터 실측) |
| **Q2** | 규모가 큰 이벤트를 동반한 후보가 단순 Event Presence 후보보다 향후 수익률이 높은가 |
| **Q3** | Event Quality 필터를 거친 후보가 matched control 대비 방향성 수익을 보이는가 |

Q1에서 데이터가 부족하면 `GATE-EQM-V0 = INCONCLUSIVE`로 종료할 수 있고, 그것이 정상 종료다.

## 2. 절대 금지

이번 연구에서 금지한다.

- LLM, GPT, NLP sentiment, ML 사용
- Event text의 의미 추론 (문서 본문을 읽어 "좋은 뉴스"를 판정하는 모든 행위)
- 임의의 0~100 Score, composite score 구성
- 기존 C-M threshold 변경, 기존 C-E0 event window 변경
- 결과를 보고 Event class를 선택하거나 threshold를 옮기는 행위
- Trading Backtester 구현, Entry/Exit 최적화, Stop/Target/Trailing/VWAP/분할진입/사이징
- 없는 데이터(컨센서스 EPS, 애널리스트 기대치)의 추정 또는 대체

이번 연구가 판단하는 것은 **Selection Alpha 하나**다. Trading Alpha는 Selection Alpha가 살아남은
뒤에 별도 연구로만 다룬다.

## 3. 읽기 전용으로 재사용하는 자산

| 자산 | 값 | 사용 방식 |
| --- | --- | --- |
| C-M0 후보 풀 | run `cmsel1-855b6a0ce64e3698fc74`, 규칙 `c769aea5…` | 후보를 다시 만들지 않는다. Momentum universe로 그대로 사용 |
| C-E0 이벤트 저장소 | `data/runtime/strategy_c/e0/raw`, 2,339 CIK, 1,770,876 행 | SEC submissions 재수집 금지. 이벤트 시각과 클래스의 원천 |
| C-E0 코호트 | run `ce01-234478e8f7ff27570e13`, `candidate_status.parquet` 6,680행 | M_ONLY / EM / EM_NEGATIVE_RISK 기준선. 판정은 읽지 않는다 |
| C-E0 taxonomy | `6ee5a16a…` | 이벤트 클래스 정의를 바꾸지 않는다 |
| PIT 계약 | `acceptanceDateTime = UTC` (40/40 대조 확정) | EDGAR를 쓰는 모든 전략에 재사용되는 계약 |

새로 가져오는 데이터는 **SEC XBRL `companyfacts` 하나**이며, 그 범위와 근거는 `EQM_V0_DATA_FEASIBILITY_V1.md`에 있다.

## 4. Event Quality는 Raw Feature로만 저장한다

처음부터 `event_quality_score = 87` 같은 점수를 만들지 않는다. 이벤트별로 아래 Raw Feature를
저장하고, 코호트는 사전등록된 bucket 경계로만 나눈다.

```text
event_type / event_subtype / event_datetime / event_age_at_signal / form_type / item_type
event_period_revenue / event_period_revenue_prior_year / revenue_growth_yoy
net_income / net_income_prior_year / gross_profit / eps
period_days / period_end / fact_accession / fact_form
same_event_type_count_90d / same_event_type_count_180d
negative_risk_flag / recent_dilution_20 / financing_event_60d
```

PIT가 불확실한 Feature는 저장하지 않는다. 사용 가능 여부는 전부 실측으로 결정한다(P1).

## 5. 용어

실제 수익성이 검증되기 전까지 `HIGH_QUALITY`라는 표현을 쓰지 않는다. 이 연구의 명칭은
`MATERIAL_EVENT`이며, "경제적 규모가 사전등록된 bucket에 해당하는 이벤트"라는 뜻 이상을 담지 않는다.

## 6. 코호트 골격

| 코호트 | 정의 |
| --- | --- |
| `M_ONLY` | C-E0 그대로. W_PRIMARY에 E1~E5 이벤트 없음 |
| `EM` (= EQ0) | C-E0 그대로. E1/E2/E3/E5 있고 E4 없음. Event Presence |
| `EQ1` | EM + Quality Raw Feature를 **계산할 수 있음**(관측 가능) |
| `EQ2` | EQ1 + 경제적 규모가 사전등록된 material bucket에 해당 |
| `EQ3` | EQ2 + negative risk 없음 |
| `EQ-MATERIAL-RISK` | material 규모 + negative risk 있음. 일반 material과 섞지 않고 별도 보고 |

EQ1~EQ3는 EM의 부분집합이며, 모두 C-E0 EM 정의를 상속한다. 코호트 경계는 결과를 본 뒤 바꾸지 않는다.

## 7. Outcome

C-E0와 비교 가능하도록 동일하게 유지한다.

- Primary: `close_return_5` = D+1 시가 → D+5 종가, matched control 대비 초과
- Secondary: `close_return_10`
- 보조(검정 아님): 1D/3D 종가, MFE5/10, MAE5/10, 승률
- Matched control: C-M V1 매칭 셀(같은 날짜 + price bucket + ATR bucket + dollar volume bucket) 그대로.
  Event Quality 조건 때문에 통제군 정의를 바꾸지 않는다

MFE는 종가 게이트를 대신할 수 없다. C-M V1의 MFE Lift가 뉴스 없는 부분집합에 몰려 있었다는 것이
C-E0의 결론이므로, MFE만 개선되는 결과는 FAIL이다.

## 8. 가설 (구조 동결, 임계값은 사전등록 파일에서 확정)

| | 내용 |
| --- | --- |
| **H1** | MATERIAL_EVENT 후보의 5D 종가 초과 > 0 |
| **H2** | MATERIAL_EVENT 5D 초과 > EM(Event Presence) 5D 초과 |
| **H3** | MATERIAL_EVENT 5D 초과 > M_ONLY 5D 초과 |

PASS 조건의 정확한 형태(교집합 구조, 다중검정 보정, bootstrap seed, coverage 하한)는
`EQM_V0_PREREGISTRATION_V1.md`와 `eqm_v0_rules_v1.json`에서 확정하며, **어떤 수익률도 계산하기
전에** 확정하고 checksum으로 동결한다.

## 9. 사전등록 전에 허용되는 관측과 금지되는 관측

| | |
| --- | --- |
| 허용 | 데이터 존재 여부, coverage, 결측률, 이벤트 건수, feature 분포(성장률 분위수), 코호트 크기, ticker/날짜 집중도 |
| 금지 | 모든 forward return, MFE/MAE, 승률, 통제군 대비 초과, 그리고 이들의 부분집합 통계 |

coverage 하한과 material bucket 경계는 **표본 수 기준으로만** 정한다. 사전등록 파일은 그 숫자가
어떤 관측에서 나왔는지 명시한다. 수익률을 본 뒤 정해진 숫자는 하나도 없다.

## 10. 실행 순서

```text
EQM-P0  Concept freeze                      (이 문서)
EQM-P1  Data feasibility                    EQM_V0_DATA_FEASIBILITY_V1.md
EQM-P2  PIT audit
EQM-P3  Quality raw feature extraction
EQM-P4  Coverage gate
EQM-P5  EQ0 / EQ1 / EQ2 / EQ3 cohort
EQM-P6  Selection Alpha
EQM-P7  Ablation / concentration / stability
EQM-P8  Final Gate                          GATE-EQM-V0 = PASS | FAIL | INCONCLUSIVE
```

P8 이후 Trading 코드로 넘어가지 않는다. 결과가 나쁘면 종료한다. 결과를 보고 threshold, event type,
horizon, score를 조정해 전략을 살리는 것은 이 선언에서 금지된다.

## 11. AI 정책

이번 연구는 AI 없이 수행한다. Rule-Based Selection Alpha가 Trading Alpha, OOS, Paper, Live까지
살아남은 뒤에만 LLM을 별도 Variant로 검토할 수 있다. 그때의 후보 역할(event novelty, strategic
importance, one-off vs structural, already priced)도 이 V0에서는 전부 금지다.
