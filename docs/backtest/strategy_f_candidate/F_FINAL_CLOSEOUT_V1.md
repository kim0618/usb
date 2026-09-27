# Strategy F Final Closeout (F0 REGULAR -> AFTER-HOURS)

작성 2026-09-27. 이 문서는 새 판정을 내리지 않는다. 이미 동결된 F0 판정을 한곳에 모으고, 종결의 이유와 범위, 재개 조건을 고정한다.
F0 사전등록 문서, rules JSON, 결과 문서, run artifact는 수정하지 않는다.

```text
STRATEGY F FINAL STATUS

Strategy F   (REGULAR_TO_AFTER_HOURS_V0)     CLOSED
F0           (F0_REGULAR_AFTER_PREVALIDATION) FAIL
F-D0+                                         NOT AUTHORIZED
```

---

## A. Final Status

- Strategy F = **CLOSED**
- F0 = **FAIL**
- F-D0+ = **NOT AUTHORIZED**

## B. Research Question

Regular-session 정보(16:00 ET 이전에 시작한 봉)와 D-1까지의 일봉·reference만으로, 같은 날 After-hours에서 공격적인 상승(upside) alpha를 보이는 종목을 선별할 수 있는가.

## C. Frozen Contract

| 항목 | 값 |
| --- | --- |
| rules | `f0_regular_after_rules_v1.json`, canonical `3b980aa46f27f1a138478c87cfcd7c510ef9e710044e1e76ff6374e2a0118fd0` (frozen 2026-09-27 15:24 KST, `frozen_before = F0_RESULT_EXECUTION`) |
| official run | `f0-3a7971269e2a` (repeat-2 산출물 19개 바이트 동일) |
| research window | 2026-04-20 ~ 2026-09-16, XNYS 104세션 |
| universe | `STRATEGY_E_TRADING_UNIVERSE_V1_1`, 수정 없이 재사용 |
| feature cutoff | 15:59:59 ET. 16:00 봉은 POSTMARKET이라 제외 |
| primary entry | 16:05 정각 봉 OPEN. 없으면 NO_TRADE(다음 봉 대체 없음) |
| primary exit | 16:50~16:59 안의 마지막 봉 CLOSE. 없으면 UNRESOLVED_EXIT |
| primary endpoint | P(MFE >= +3%) |

H1~H5 (`>=` 포함 비교, 동결):

| H | 조건 |
| --- | --- |
| H1 | day_return 0.03, position_in_day_range 0.80 |
| H2 | return_1530_1600 0.01, close_vs_VWAP 0.005 |
| H3 | regular_RVOL 2.0, position_in_day_range 0.80 |
| H4 | relative_strength_vs_SPY 0.02, return_1530_1600 0.005, regular_dollar_volume 20M |
| H5 | day_return 0.05, regular_RVOL 2.0, position_in_day_range 0.90, return_1530_1600 0.01 |

주요 gate:

| Gate | 통과 조건 |
| --- | --- |
| Sample | 거래 300, 종목 100, 세션 60 이상 |
| Statistical | 세션 단위 bootstrap 5000회(seed 2026092701), tail lift 99% CI 하한 > 0 |
| Tail | 절대 +2%p 이상, 배수 1.5x 이상 |
| Mean/Median | mean lift > 0, median >= -0.25% |
| Downside | P(r<=-2%)가 +2%p 이상 나빠지거나 1.5배를 넘으면 FAIL |
| Extreme | top1% 이익 거래 제거 후 mean lift > 0 그리고 tail lift > 0 |
| Concentration | top1 <= 15%, top5 <= 40%, leave-top-10 mean lift > 0 |
| Time | 4블록 중 mean lift 양수 3개 이상, tail lift 양수 3개 이상 |
| Cost | break-even >= 50bp (COST STRESS ASSUMPTION) |

BORDERLINE은 없다.

## D. Official Result

**F0 FAIL.** PIT-1/2/3은 모두 PASS했고 critical 무결성 실패는 0건이다. baseline은 16,077 symbol-session(1,224종목, 104세션)이고, P(MFE>=3%)는 5.30%다.

| Gate | H1 | H2 | H3 | H4 | H5 |
| --- | --- | --- | --- | --- | --- |
| Sample | PASS | PASS | PASS | PASS | INSUFFICIENT_SAMPLE |
| Statistical | FAIL | PASS | PASS | PASS | PASS |
| Tail | FAIL | PASS | PASS | PASS | PASS |
| Mean/Median | FAIL | PASS | FAIL | PASS | FAIL |
| Downside | PASS | FAIL | FAIL | PASS | FAIL |
| Extreme | FAIL | FAIL | FAIL | FAIL | FAIL |
| Concentration | FAIL | FAIL | FAIL | FAIL | FAIL |
| Time Consistency | FAIL | PASS | FAIL | FAIL | PASS |
| Cost | FAIL | FAIL | FAIL | FAIL | FAIL |
| PIT | PASS | PASS | PASS | PASS | PASS |
| 판정 | H_FAIL | H_FAIL | H_FAIL | H_FAIL | INSUFFICIENT_SAMPLE |

| 지표 | H1 | H2 | H3 | H4 | H5 |
| --- | --- | --- | --- | --- | --- |
| N | 2,102 | 1,621 | 658 | 1,443 | 147 |
| P(MFE>=3%) | 6.23% | 9.25% | 11.70% | 7.97% | 11.56% |
| tail lift | +0.93%p | +3.95%p | +6.40%p | +2.67%p | +6.27%p |
| tail lift 99% CI | [-0.60, +2.46] | [+1.53, +7.45] | [+3.38, +9.68] | [+0.81, +4.72] | [+0.12, +14.00] |
| mean lift (gross) | -0.025%p | +0.128%p | -0.089%p | +0.046%p | -0.028%p |
| P(r<=-2%) (base 3.49%) | 4.71% | 5.55% | 8.36% | 4.78% | 9.52% |
| top1% 제거 후 mean lift | -0.21%p | -0.12%p | -0.25%p | -0.14%p | -0.25%p |
| leave-top-10 mean lift | -0.16%p | -0.06%p | -0.30%p | -0.10%p | -0.50%p |
| break-even | 2.6bp | 17.9bp | -3.8bp | 9.7bp | 2.3bp |

상세는 `F0_REGULAR_AFTER_RESULTS_V1.md`와 `data/runtime/strategy_f_candidate/runs/f0-3a7971269e2a/`에 있다.

## E. Why F Failed

1. 모든 gate를 통과한 H가 없다.
2. Cost gate는 H1~H5 전부 FAIL이다.
3. Extreme-removal gate는 H1~H5 전부 FAIL이다.
4. Concentration gate는 H1~H5 전부 FAIL이다.
5. H2/H3/H4는 P(MFE>=3%) tail lift가 통계적으로 존재했다(99% CI 하한 > 0).
6. 그러나 downside도 함께 커졌다. P(r<=-2%)가 H2 1.59배, H3 2.40배, H5 2.73배이고, 모든 H에서 baseline보다 높다.
7. 상위 1% 이익 거래를 제거하면 모든 H의 mean lift가 음수가 된다.
8. 상위 10 ticker를 제거하면 모든 H의 mean lift가 음수가 된다.
9. 가장 유력했던 H2도 break-even이 17.9bp로, 50bp stress를 통과하지 못했다.

INCONCLUSIVE 조건은 어느 것도 해당하지 않았다. sample을 충족한 H가 4개였고, UNDERPOWERED인 H가 없었으며, resolved 세션은 104개였고, 무결성 실패도 없었다.

## F. Interpretation

**사실:** H2~H4는 After-hours에서 큰 상승 excursion이 나올 확률을 높였다. 같은 H들은 큰 하락 확률과 MAE 폭도 높였다. 평균 이익은 소수의 극단 거래와 소수 종목에 의존했다.

**해석:** 현재 결과는 방향성 long alpha보다는 after-hours 변동성·이벤트 강도를 골라내는 선택과 더 잘 맞는다.

이 해석을 새로운 Strategy F 규칙으로 사용하지 않는다.

## G. Event Finding

SEC 8-K item 2.02 진단(baseline 기준)에서 P(MFE>=3%)는 다음과 같았다.

- EVENT: 34.0% (1,216행)
- NON_EVENT: 3.0% (13,218행)
- UNKNOWN_EVENT_STATUS: 1,643행. NON_EVENT로 합치지 않았다.

다만 다음 제약이 있다.

- event 진단은 사전등록된 판정 gate가 아니었다.
- SEC coverage가 부분적이다(C-E0 store 기준, E 적격 티커의 약 73%).
- 뉴스와 earnings calendar가 없다.

따라서 인과적 결론은 쓰지 않는다.

## H. Limitations

- SPY Common Raw 21세션 공백(2026-08-06 ~ 09-03): `relative_strength_vs_SPY` 20.3% NaN, 이 기간에는 H4 성립 불가
- 16:05 정각 봉 체결 조건에 따른 선택: 적격 행의 91%가 NO_TRADE
- bid/ask·spread 없음: 비용은 전부 stress assumption
- 104세션 단일 국면 표본
- SEC coverage 부분적
- 초기 RVOL history 한계: 처음 5세션은 NaN, 세션 6~20은 분모가 20세션 미만
- ticker reuse 미감지, list_date 없음

**이 중 어느 것도 동결된 F0 FAIL을 사후에 무효화하지 않는다.** Cost와 Extreme gate는 H1~H5 전부에서 FAIL했고, 두 gate 모두 SPY 공백과 무관한 H1/H2/H3/H5에서도 FAIL했다.

## I. No Rescue Rule

다음은 금지한다.

- F threshold 재조정
- 16:05 fallback 변경(다음 봉 진입, 마지막 체결가 대체 등)
- H6 생성
- secondary horizon(16:30/18:00/20:00, 16:15 진입)을 primary로 바꾸는 것
- SPY 공백을 채워 같은 F0를 재검정하는 것
- event filter를 넣어 F를 살리는 것
- F-D0 진행

새로운 volatility 가설을 연구하려면 Strategy F가 아닌 별도 Candidate를 만들고, 새 사전등록과 새 checksum을 거쳐야 한다. 이 경우에도 현재 104세션 창에서 얻은 F0 결과를 규칙 설계 근거로 쓰면 안 된다. 그 창은 이미 본 데이터다.

## J. Reopening Conditions

Strategy F 자체는 CLOSED이다. 단순한 parameter 변경으로는 재개할 수 없다. 재개하려면 다음 모두가 필요하다.

- 독립적인 새 데이터: F0가 보지 않은 forward 데이터, 또는 dev/validation/locked OOS가 물리적으로 분리된 장기 데이터
- 본질적으로 다른 새 가설
- 결과를 보기 전의 새 사전등록과 새 rules checksum

## K. Project Status

```text
A = OFFICIAL PAPER V1
E = OFFICIAL PAPER V1

B = RETIRED
C = RETIRED
D = RETIRED
F = CLOSED
G = CLOSED
```

**repository 기록과의 대조 (2026-09-27):**

- `backend/app/strategies/registry.py`는 A와 E를 PAPER, B·C·D를 `research_lifecycle=CLOSED`, `lifecycle=RETIRED`로 둔다. 이 파일에는 F와 G 항목이 없다.
- G 종결은 커밋 `cfb29c1`로 기록되어 있다.
- 레지스트리는 운영 UI가 읽는 파일이라 이 closeout에서 수정하지 않았다. F·G 행 추가 여부는 별도 결정 사항이다.
