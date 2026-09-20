# EQM-V0 사전등록 V1 (EQM-P0 최종 동결)

- 선언일: 2026-09-20, **어떤 EQM 수익률도 계산하기 전**
- 상태: **FROZEN**
- 기계 판독 정본(이 문서와 충돌하면 JSON이 우선):

| 파일 | canonical sha256 |
| --- | --- |
| `eqm_v0_rules_v1.json` | `0cd09ba2bd2e03d083a46696d26af55bfb7f0b46c765e040f6d5b87d48d4840c` |

checksum은 C-E0와 같은 방식이다. `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`의 sha256.

```text
Strategy C remains CLOSED.
EQM-V0 = New thesis derived from Strategy C findings.
```

## 1. 이 선언이 동결되기 전에 본 것과 보지 않은 것

| | |
| --- | --- |
| 본 것 | 데이터 존재 여부, coverage, 결측 사유별 건수, 이벤트 클래스별 모집단, 매출 성장률 분포, 코호트 크기, ticker/날짜 집중도, block별 건수 |
| 보지 않은 것 | forward return, MFE, MAE, 승률, 통제군 대비 초과, 그 어떤 부분집합의 수익률 |

아래의 모든 숫자(임계값, 하한, 캡)는 **건수 분포만 보고** 정했다. 근거가 되는 실측은
`EQM_V0_DATA_FEASIBILITY_V1.md`와 `data/runtime/strategy_eqm/v0/features/fcdd2e5f6c6510bd/coverage.json`에 있다.

## 2. 동결된 기준선 (하나도 바꾸지 않는다)

| 자산 | 값 |
| --- | --- |
| C-M V1 후보 | run `cmsel1-855b6a0ce64e3698fc74`, 규칙 `c769aea5…`, 변형 C-M0, 6,680행 |
| C-E0 코호트 | run `ce01-234478e8f7ff27570e13`, 규칙 `48fc34cb…`, taxonomy `6ee5a16a…` |
| C-E0 이벤트 저장소 | SEC submissions, 재수집 없음 |
| PIT 계약 | `acceptanceDateTime = UTC` (저장된 40건 대조로 매 실행마다 재확인) |

EQM-V0가 새로 가져온 것은 XBRL companyfacts 저장소 하나다.
digest `fcdd2e5f6c6510bd657fa1f2635416361a3d88ad1cc20d3d6b8bd98b14a925bd`, CIK 834개, HTTP 834건 전부 200.

## 3. Magnitude 정의 (E3b 한정)

```text
revenue_growth_yoy = 이벤트 accession이 태깅한 현재기간 매출 / 같은 accession의 전년 동기 매출 - 1
```

- 사용 accession: W_PRIMARY(= [open(D-2), close(D)))에 접수된 10-Q / 10-K / 10-KT.
  여러 건이면 effective_time이 이른 것부터, 동률이면 accession 번호 순으로 시도한다
- 매출 태그 우선순위: `RevenueFromContractWithCustomerExcludingAssessedTax` >
  `…IncludingAssessedTax` > `Revenues` > `SalesRevenueNet` > `RevenuesNetOfInterestExpense`
- 기간 bucket: 분기(80~100일) > 반기 > 3분기 누계 > 연간(350~380일). 비교기간은 end가 355~375일 이전
- 전년 동기가 0 또는 음수면 성장률을 만들지 않는다(`ZERO_BASE`, `NEGATIVE_BASE`)
- **다른 accession의 fact는 읽지 않는다.** 나중에 제출된 10-Q로 8-K 2.02의 규모를 채우는 경로를
  구조적으로 차단한다

E1(계약 금액), E2(거래 금액), E3a(실적 보도자료), E5는 규모를 구조화할 수 없어 V0 Primary에서 제외한다.
8-K accession에는 XBRL fact가 하나도 붙지 않는다(표본 1.01 24건, 2.02 251건 모두 0건).

## 4. Magnitude bucket과 material 경계

사전등록 ladder(전부 보고한다):

```text
NEGATIVE (< 0)   0~5%   5~10%   10~25%   25~50%   50%+
     172          166     155      288      146     140      (EM 관측가능 1,067행)
```

```text
MATERIAL_EVENT = revenue_growth_yoy >= +10%
```

**경계를 +10%로 정한 이유는 표본 수 하나다.** `>= +25%`는 286행으로 §19의 material 후보 300 하한에
미달한다. `>= +10%`는 574행이다. 이 판단은 수익률을 보기 전에 건수만으로 내렸다.

`>= +25%`와 `>= +50%`는 **사전등록된 기술통계**로 함께 보고하지만 **게이트 입력이 아니다.**
어떤 sub-bucket이 좋게 나와도 그것만으로 PASS를 만들지 않는다. NEGATIVE bucket도 대조로 보고한다.

## 5. 코호트

| 코호트 | 정의 | 사전 측정 |
| --- | --- | --: |
| `M_ONLY` | C-E0 그대로 | 3,552 |
| `EQ0` = `EM` | C-E0 그대로, Event Presence | 2,550 |
| `EQ1` | EM + 성장률 계산 가능 | 1,067 |
| `EQ2` = `MATERIAL_EVENT` | EQ1 + 성장률 ≥ +10% | **574** |
| `EQ3` | EQ2 + negative risk 없음 | 516 |
| `EM_REST` | EM − EQ2, H2의 비교군 | 1,976 |
| `EQ-MATERIAL-RISK` | (EQ2 ∩ risk) ∪ (EM_NEGATIVE_RISK ∩ 관측가능 ∩ ≥+10%) | 80 |

negative risk = `recent_dilution_20`([D-20,D]의 E4) 또는 `financing_event_60d`([D-60,D]의 E4a~E4d).

**H2의 비교군을 `EM_REST`로 둔 이유:** EQ2는 EM의 부분집합이라 EQ2 대 EM 전체 비교는 자기 자신을
포함한 비교가 된다. 여집합과 비교하는 쪽이 더 엄격하다. EQ2 대 EM 전체도 함께 보고하지만 게이트는
`EM_REST`로 판정한다.

## 6. Outcome

- Primary: `close_return_5` = D+5 종가 / D+1 시가 - 1, 매칭 통제군 대비 초과
- Secondary: `close_return_10`
- 보조(검정 아님): 1D/3D 종가, MFE5/10, MAE5/10, 승률
- 통제군: C-M V1 매칭 셀 그대로. Event Quality 조건 때문에 통제군 정의를 바꾸지 않는다
- **MFE만 개선되는 결과는 FAIL이다.** C-M V1의 MFE Lift가 뉴스 없는 부분집합에 몰려 있었다는 것이
  C-E0의 결론이므로, 종가 게이트를 MFE로 대체하지 않는다

## 7. 가설과 다중검정 구조

| | 내용 | PASS 조건 |
| --- | --- | --- |
| **H1** | EQ2 5D 종가 초과 > 0 | 점추정 > 0 **and** 95% CI 하한 > 0 |
| **H2** | EQ2 − EM_REST > 0 | 점추정 > 0 **and** 97.5% CI 하한 > 0 |
| **H3** | EQ2 − M_ONLY > 0 | 점추정 > 0 **and** 97.5% CI 하한 > 0 |

```text
GATE PASS 요건 = H1 AND (H2 OR H3)
```

H1은 단일 검정이므로 5% 수준으로 본다. H2와 H3는 **합집합**(둘 중 하나면 충족)이므로 각각 2.5%
수준, 즉 97.5% 양측 CI 하한으로 판정한다. 합집합에 대한 Bonferroni 보정이다. H1과 합집합을
**동시에** 요구하는 intersection-union 구조이므로 그 위에 추가 보정은 하지 않는다.

Bootstrap: 240 신호일에 대한 moving block bootstrap, block 10, 10,000회, **seed `20260920`**(신규),
양측 95%/97.5%/99% 병기. C-E0의 seed `20260918`은 재사용하지 않는다.

## 8. 결과를 보기 전의 검사 (실패 시 INCONCLUSIVE)

| | 조건 | 사전 측정 |
| --- | --- | --- |
| P1 | timezone 감사 통과 | UTC 40/40 |
| P2 | PIT 위반 0 | 실행 시 판정 |
| P3 | 정기보고서 보유 EM 행 중 미해결 비율 ≤ 10% | **5.83%** (66/1,133) |
| P4 | EQ2 매칭 유효(k=5) ≥ 300 | 매칭 전 574 |
| P5 | EQ2 unique ticker ≥ 100 | 426 |
| P6 | EM_REST와 M_ONLY 매칭 유효 각각 ≥ 300 | 1,976 / 3,552 |
| P7 | 4개 block 각각 EQ2 ≥ 30 | 142 / 88 / 177 / 167 |
| P8 | primary 240일 전부 존재 | 확인됨 |

기준 미달이면 기준을 낮추지 않고 `GATE-EQM-V0 = INCONCLUSIVE`로 멈춘다.

## 9. PIT 감사

- 사용된 모든 accession은 그 후보 행의 W_PRIMARY 이벤트여야 한다
- 사용된 모든 accession의 effective_time < close(D)
- 사용된 모든 fact의 `accn`이 그 accession과 일치해야 한다(다른 제출물의 fact 사용 0건)
- effective_time을 전부 +1세션 옮기면 window 경계 행만 바뀌어야 한다
- close(D)+1초 합성 accession 주입 시 감사가 잡아내야 한다(양성대조)

수익률 계산 후에 PIT 위반이 발견되면 이 선언의 게이트는 FAIL이다.

## 10. GATE-EQM-V0

**PASS: 아래 9개를 모두 충족.**

1. EQ2 5D 종가 초과 점추정 > 0
2. H1 95% CI 하한 > 0
3. (EQ2 − EM_REST) 또는 (EQ2 − M_ONLY) 점추정 > 0
4. 그 비교의 97.5% CI 하한 > 0
5. (EQ2 MAE5 초과) − (EM_REST MAE5 초과) ≥ -0.02
6. 4개 block 중 3개 이상에서 EQ2 초과 > 0, 그리고 게이트를 통과시킨 차이도 3개 이상에서 > 0
7. P4~P7 충족
8. 집중도: 단일 ticker ≤ 5%, 상위 5 ticker ≤ 15%, 단일 날짜 ≤ **8%**, 상위 10일 ≤ **40%**,
   그리고 상위 5 ticker 제외 / 상위 10일 제외 / 기간유형(Q, Y) 각각 제외 후에도 조건 1과 3의
   점추정이 > 0
9. PIT 위반 = 0

**날짜 집중도 캡을 C-E0(5% / 25%)에서 8% / 40%로 올린 이유를 명시한다.** 실적 공시는 연 4회
시즌으로 뭉친다. EQ2는 240일 중 123일에 분포하고 단일 날짜 최대 5.4%, 상위 10일 33.5%다(사전 측정).
C-E0의 캡을 그대로 쓰면 가설과 무관하게 구조적으로 FAIL이 된다. 대신 leave-out 요건을
**추가**했다(상위 10일 제외, 기간유형별 제외). 완화가 아니라 교체다.

**FAIL:** P는 통과했으나 조건 1~9 중 하나라도 실패. MATERIAL도 종가 우위 없음, EM_REST와 차이 없음,
MFE만 증가, 한 시기·한 ticker·한 기간유형에만 의존, 결과 확인 후 PIT 문제 발견이 여기 해당한다.

**INCONCLUSIVE:** P1~P8 중 하나라도 실패. 결과를 보기 전에 판정하며 PASS로 해석하지 않는다.

## 11. 범위 선언 (결론 문장에 반드시 붙인다)

```text
SCOPE = periodic-report revenue magnitude
NOT   = event quality in general
```

EQM-V0가 검증하는 것은 정기보고서가 동반된 급등의 매출 규모다. 계약 규모, 실적 서프라이즈,
FDA 승인 같은 정보는 이 연구가 다루지 않는다(데이터가 없다). PASS든 FAIL이든 이 범위를 확대해
쓰지 않는다.

## 12. 종료 규칙

`GATE-EQM-V0 = FAIL`이면 이 형태의 Event Quality 가설을 종료한다. 이후 금지: 임계값 재조정,
window 변경, horizon 교체, 새 score 구성, 사후 event class 선별, sub-bucket 승격.
다른 가설은 **새 연구 ID와 새 사전등록**으로만 시작한다.

## 13. 실행 순서

1. 선언 checksum과 XBRL 저장소 digest 확인
2. C-M0 후보를 행 단위로 재현(불일치면 중단)
3. C-E0 코호트 재현 및 quality feature 조인
4. PIT 감사와 P1~P8 → 실패 시 INCONCLUSIVE로 종료
5. 수익률과 H1/H2/H3, ablation, 집중도, 안정성 계산
6. 이 파일만 보고 GATE-EQM-V0 판정

산출물은 `data/runtime/strategy_eqm/v0/runs/<run_id>/`에 둔다. run_id 입력은 rules checksum,
C-E0 run id, snapshot digest, XBRL store digest, code digest, bootstrap 설정이다.
