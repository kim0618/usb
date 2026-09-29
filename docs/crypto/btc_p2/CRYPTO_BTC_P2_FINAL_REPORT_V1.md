# CRYPTO BTC-P2 FINAL REPORT V1

계약 §46의 38항목 순서. 2026-09-28. 미커밋. **주문 0, PnL 0, 배포 0.**

---

## 1. Git

branch `main`, HEAD `75a66d1c08fe65cc3ba37b32a9aaaa8a64a7a81c`. 커밋 0, push 0.

신규(전부 untracked): `backend/app/crypto/research/btc_p2/` 8파일,
`backend/tests/crypto/test_btc_p2.py`, `docs/crypto/btc_p2/` 5문서,
`data/research/crypto/btc_p2/` 5산출물.

병렬 작업 경로(`btc_p1_forward`·`manual_r1`·`manual_r2`·`liquidation_forward`·`btc_p1`)
**수정 0건** (타임스탬프 확인). 운영 `crypto/paper`·`crypto/terminal` **무변경**.

## 2. P1 해석

P1은 `LARGE_MOVE MODEL`이다. PATH STRONG 6개, 최강 `4H_DOWN_100` skill +0.180 / AUC 0.727.
그러나 directional AUC 0.502~0.533, F2 변동성이 importance를 독점, F1 가격경로는 0.001.

## 3. P2 연구 질문

> 큰 움직임이 올 가능성이 높은 시점에서, 현재 정보만으로 상승과 하락을 구분할 수 있는가?

## 4. Contract hash

`4d7ecbe8d565c2644c42aa035072a3331c56479f7b53b24c5cc0f4ca1f317693`.
실행 시작·종료 모두 대조 일치.

## 5. Data window

P1이 검증한 **41,400 시각**(2022-01-01 ~ 2026-09-22). 이것이 누출을 피하는 대가다(§6).
D2 canonical 5종 + D5.2 external(Binance USD-M perp 1m, spot 1m).
liquidation·Manual·forward shadow **사용 0**.

## 6. P1 gate 정의

`P_LARGE_MOVE = max(P_UP_X, P_DOWN_X)`, 출처는 **P1 fold별 validation 예측**(`p_m2_cal`).

**forward용 fold-9 frozen artifact를 쓰지 않았다.** 2025-12-31까지 학습한 모델을
2022~2025 행에 적용하면 자기 평가집합을 스스로 고르게 된다.

gate 임계는 **train fold의 0.75 분위**(상위 25%). 절대 임계는 쓸 수 없었다:
같은 0.60이 4H±1%에서 2.3%, 12H±1%에서 29.0%를 남긴다.

## 7. Direction targets

| target | 지위 |
|---|---|
| **T1 First Touch** | **PRIMARY** |
| T2 Endpoint | SECONDARY |
| T3 Dominant Excursion | **동결 전 기각** (T1과 directional 표본 18,140 vs 18,139로 사실상 동일) |

`AMBIGUOUS`(같은 1분봉이 양쪽 클리어)는 **제외했고 규칙으로 채우지 않았다.**
채우는 규칙이 곧 측정하려는 방향 신호가 되기 때문이다.

## 8. Class counts

| 조합 | UP_FIRST | DOWN_FIRST | NEITHER | AMBIGUOUS | UP 비율 |
|---|---|---|---|---|---|
| 4H ±1% | 8,864 | 9,275 | 23,260 | 1 | 48.9% |
| 12H ±1% | 15,786 | 16,005 | 9,608 | 1 | 49.7% |
| 12H ±2% | 8,176 | 8,313 | 24,911 | 0 | 49.6% |
| 24H ±2% | 13,047 | 13,177 | 15,176 | 0 | 49.8% |

**거의 완벽한 균형.** AUC 0.47~0.49가 클래스 편향 탓이 아니다.

## 9. Feature families

7 family, D2-only 34 + external 6 = 40. 41,400행 전부 **결측 0**.

**변동성 family가 없고**, 부호 있는 feature는 전부 24시간 실현변동성으로 나눴다.
P1이 변동성을 방향으로 착각한 것이 실패 원인이었기 때문이다.

## 10. M0

gate 적용 train 구간의 `UP_FIRST` 빈도(상수). 네 조합 모두 base rate 0.49~0.50.

## 11. M1

Ridge logistic. PRIMARY 4조합 direction AUC **0.4880 ~ 0.4914**.

## 12. M2

Histogram GBDT(트리 200·depth 3·lr 0.05·leaf 50). PRIMARY **0.4673 ~ 0.4928**.

## 13. Overall direction AUC (전체 구간)

| 조합 | M1 | M2 |
|---|---|---|
| 4H ±1% | 0.5401 | 0.5348 |
| 12H ±1% | 0.5060 | 0.5074 |
| 12H ±2% | 0.5344 | 0.5307 |
| 24H ±2% | 0.5061 | 0.5063 |

## 14. P1 high-move subset direction AUC (PRIMARY)

| 조합 | M1 | M2 | 전체 대비 |
|---|---|---|---|
| 4H ±1% | 0.4914 | **0.4928** | **-0.042** |
| 12H ±1% | 0.4885 | **0.4805** | -0.027 |
| 12H ±2% | 0.4880 | **0.4717** | **-0.059** |
| 24H ±2% | 0.4881 | **0.4673** | -0.039 |

> **큰 움직임이 올 때만 보면 방향이 더 안 맞는다.** 네 조합 전부, 평균 -0.042.
> 이것이 §23이 물은 질문의 답이고, 이번 연구의 핵심 결과다.

## 15. Brier

PRIMARY 4H±1% M2 Brier 0.2593, **Brier skill -0.0361**(base rate보다 나쁘다).

## 16. Log Loss

PRIMARY 전 조합에서 M0보다 높다(나쁘다). 상세는 `results_v1.json`.

## 17. Calibration

PRIMARY 4H±1% ECE **0.0752**(기준 0.06 초과).
신뢰도 곡선: 예측 0.735 구간 61건의 실제 **0.426**. **확신할수록 틀린다.**

## 18. Separation deciles

PRIMARY 4H±1% `P_UP` 10분위 실제 UP 비율:
0.520 / 0.523 / 0.471 / 0.492 / 0.475 / 0.518 / 0.490 / 0.482 / 0.500 / 0.485.

**완전히 평평하고 기울기가 음수다.** 사전등록 D5 조건 +8%p에 대해 실측 **-3.5%p**.

## 19. High-confidence buckets

| 임계 | PRIMARY gated (n, 실제) | 전체 구간 (n, 실제) |
|---|---|---|
| `P_UP >= 0.60` | 461, 0.484 | 1,126, 0.543 |
| `P_UP >= 0.65` | 181, 0.503 | 360, 0.558 |
| `P_UP >= 0.70` | 69, **0.435** | 101, 0.564 |

전체 구간에서는 단조 증가하는데 **gate를 걸면 뒤집힌다.**

## 20. 4H

PRIMARY 0.4928(NO_DIRECTION). 전체 구간 0.5348이 first-touch 계열 최고.

## 21. 12H

±1%: PRIMARY 0.4805, 전체 0.5074. ±2%: PRIMARY 0.4717, 전체 0.5307.
`12H_200 ENDPOINT GATED`는 n=1,312로 최소치 미달 → **INCONCLUSIVE**.

## 22. 24H

PRIMARY 0.4673(최저). `24H_200 ENDPOINT GATED`가 64셀 중 최고 AUC 0.5437이나
ECE 0.150·Brier skill -0.092·fold AUC 0.381~0.719로 **진폭이 평균보다 크다**.

## 23. UP/DOWN balance

UP 비율 48.9~49.8%. 균형 문제 없음. `P(UP_FIRST)` **단일 이진 target**으로 학습해
P1의 "UP·DOWN 동시 상승" 구조를 원천 차단했다.

## 24. Fold stability

PRIMARY 4H±1%: fold별 AUC **0.386 ~ 0.528**, >0.52인 fold **3/8**(기준 6/8).

## 25. Year stability

PRIMARY 4H±1%: year별 **0.435 ~ 0.528**, >0.52인 year **1/5**(기준 4/5).

## 26. Regime stability

전체 구간 4H±1%: **LOW 0.5711 / MID 0.5342 / HIGH 0.5200**.

**방향은 조용할 때 가장 잘 맞고 격렬할 때 가장 안 맞는다.** §14와 같은 이야기다.

## 27. Feature importance

전체 구간에서 **F1(가격 경로)만 일한다**: 4H±1% AUC drop **+0.0830**,
나머지 여섯 family 합계 0.023.

**P1과 정반대다**(P1에서 F1은 0.001). **변동성을 제거하자 가격 경로가 드러났다.**

gate를 걸면 F1 기여가 **음수**(-0.017 ~ -0.067)가 된다. 정보를 파괴했더니 나아진다는 것은
그 구간에서 모델이 잡음에 적합하고 있었다는 뜻이다.

F3 OI · F4 basis · F5 funding · F6 cross-market은 **어디서도 일하지 않았다.**

## 28. Permutation control

사전등록 200회. 라벨을 섞고 walk-forward 전체를 재실행.

| 조합 | null 평균 | null p99 | 실제 | 초과? |
|---|---|---|---|---|
| 4H ±1% | 0.5009 | 0.5232 | 0.4928 | **아니오** |
| 12H ±1% | 0.4985 | 0.5217 | 0.4805 | **아니오** |
| 12H ±2% | 0.4990 | 0.5211 | 0.4717 | **아니오** |
| 24H ±2% | 0.4995 | 0.5187 | 0.4673 | **아니오** |

**정보 없는 라벨에서도 이 파이프라인은 AUC 0.52를 만든다.**
동결한 WEAK 임계 0.52가 잡음 바닥과 같은 자리였고,
D6를 STRONG 조건에 넣어두지 않았다면 0.53을 신호로 읽었을 것이다.

### 28.1 ungated 셀의 null (사후 진단, 게이트 무관)

계약의 permutation 통제는 PRIMARY(gated)만 덮는다. 0.53 부근인 ungated 셀에
읽을 기준이 없어 같은 방식의 null을 따로 계산했다. **어떤 판정에도 들어가지 않는다**
(BTC-P3는 PRIMARY 셀로 이미 결정됐다). `ungated_null_v1.json`.

| 셀 | n | 실제 AUC | null 평균 | null 표준편차 | null p99 | 초과? | **z** |
|---|---|---|---|---|---|---|---|
| 4H_100 FIRST_TOUCH D2_ONLY **ALL** | 15,262 | **0.5348** | 0.5000 | 0.00479 | 0.5114 | **예** | **+7.27** |

**gated 셀과 정반대 결과다.** ungated 4H_100은 null을 크게 초과한다(z = +7.27).
표본이 15,262행으로 gated(4,648행)의 3배라 null 표준편차가 0.0080에서 0.0048로 좁아진 것도 함께 작용한다.

### 28.2 이것이 판정을 바꾸지 않는 이유, 그리고 무엇을 뜻하는지

**BTC-P3는 바뀌지 않는다.** 계약 §18 P3a가 PRIMARY(gated first-touch)에서 STRONG을 요구하고,
PRIMARY 4개는 전부 AUC 0.5 미만이다.

그리고 이 셀 자체도 사전등록 STRONG 조건을 통과하지 못한다:

| 조건 | 실측 | 기준 | 결과 |
|---|---|---|---|
| D1 AUC | 0.5348 | > 0.55 | **FAIL** |
| D3 year 일관성 | 3/5 | >= 4/5 | **FAIL** |
| D5 decile gap | +0.0737 | >= 0.08 | **FAIL** |
| Brier skill | **-0.0074** | > 0 | **FAIL** |

**그럼에도 정직하게 기록해야 할 것이 있다.**

> **전체 시장에는 잡음으로 설명되지 않는 작은 방향 정보가 있다**(z = +7.27).
> **큰 움직임이 예고된 구간에서는 그것이 사라진다**(gated AUC 0.4928, null 미달).

이는 §14·§26의 발견과 같은 이야기를 통계적으로 뒷받침한다.
방향 정보는 존재하되 **거래에 필요한 곳에 없다.**

Brier skill이 음수라는 점이 실용적 결론을 못박는다.
순위는 우연보다 낫지만 **확률값을 믿으면 "항상 50%"라고 말하는 것보다 손해**다.

**12H_200 ungated의 null은 계산 중이며, 결론에 영향을 주지 않는다**(PRIMARY 셀로 이미 결정됨).

## 29. Strongest directional target

`24H_200 ENDPOINT D2_ONLY GATED` M2, AUC 0.5437 (64셀 중 최고).
ECE 0.150, Brier skill -0.092, fold 0.381~0.719, n=2,032(최소치 턱걸이).
**평균은 신호가 아니라 진폭의 부산물이다.**

first-touch 계열 최고는 `4H_100 D2_ONLY ALL` 0.5348이나
year 일관성 3/5, decile gap +0.074(기준 0.08), **Brier skill -0.0074**로 STRONG 미달.

## 30. 판정 집계

| 판정 | 셀 수 |
|---|---|
| **STRONG_DIRECTION** | **0 / 64** |
| WEAK_DIRECTION | 10 |
| NO_DIRECTION | 20 |
| INCONCLUSIVE | 2 |

PRIMARY 4셀 = **NO_DIRECTION × 4**.

## 31. BTC-P3 authorization

# NOT_AUTHORIZED

§18 P3c 그대로: STRONG_DIRECTION이 없으므로 P3로 가지 않는다.

**그리고 게이트를 통과했더라도 갈 수 없었다.** §14·§26이 보여주듯,
거래에 필요한 두 조건이 겹치는 구간이 없다:

| | 비용을 넘길 크기 | 방향 |
|---|---|---|
| 조용한 구간 | 없음 | 약간 있음 |
| 큰 움직임 구간 | 있음 | **없음** |

P1 large move + P2 direction을 곱해 P3를 만들려던 구조가 여기서 무너진다.

## 32. Forward implications

| # | 내용 |
|---|---|
| 1 | P1 forward shadow는 **계속 실행한다.** 이번 결과로 수정하지 않았다(§19 FS1, FS2 준수) |
| 2 | P1 shadow의 지위는 그대로다: large-move 예측력의 true OOS 확인 |
| 3 | **P2 forward shadow는 만들지 않는다.** 확인할 신호가 없다 |
| 4 | P1 shadow가 CONFIRMED를 받아도 **방향이 없으므로 LONG/SHORT AUTO는 여전히 불가**다. 이번 결과가 그 판단을 강화한다 |

## 33. Liquidation future role

이번 단계 **사용 0**(테스트로 강제). forward only이고 history가 짧다.
2026-09-27 시작, 게이트 6개월+30이벤트.

축적 후 **P2-V2 feature 후보**다. 청산 연쇄는 이번에 쓴 어느 축과도 다른 정보원이며,
특히 "큰 움직임 구간에서 방향이 사라진다"는 이번 결과에 대한 유력한 반례 후보다.

## 34. Manual future role

이번 단계 **사용 0**. MANUAL-R1.1 기준 사용자 확정 거래가 완료 1건·미결 1건이라
model feature로 쓸 수 없다.

MANUAL-R2가 `reason_code`를 쌓으면 **human-policy comparison**이 가능해진다:
사람이 방향을 고른 시점에 모델의 `P_UP`이 무엇이었는지.

## 35. Tests

`backend/tests/crypto/test_btc_p2.py` **46개**.

| 영역 | 내용 |
|---|---|
| first touch | UP/DOWN 선후, NEITHER, **AMBIGUOUS 미추정**, **결정 봉 자체는 트리거 불가**, horizon 밖 무효, 정확 도달 포함, **chunk 크기 무관** |
| endpoint / dominant | 종료 봉만 읽음, 임계 요구 |
| gate | **fold별 예측 사용 강제·frozen artifact 미사용 강제**, 정렬, `max` 정의, train 분위 |
| folds | 8개, P1 fold1은 학습 전용, **embargo**, 무중첩, 시간순 |
| features | **변동성 family 부재 강제**, 34/6/40, **미래 행 추가해도 과거 불변**, **vol 정규화 작동**, external 없으면 F6 부재 |
| external | **마이크로초 정규화**, **긴 forward fill 거부**, staleness 한도 |
| verdict | 표본 검사 우선, **permutation 실패 시 STRONG 차단**, decile, best-of |
| contract | 문자열 결속, 해시, 편집 거부, 조합 동결 |
| safety | **주문·병렬작업 경로 참조 0**, **PnL·leverage 참조 0** (주석·문자열 제거 후 코드만 검사) |

## 36. Crypto regression

**1,179 passed, 0 failed** (저장소 루트 기준).

## 37. Limitations

| # | 한계 |
|---|---|
| 1 | P1 validation 41,400행으로 제한된다. gate 누출을 피한 대가 |
| 2 | gated 표본이 조합당 2,600~4,600행. fold당 300~700행이라 fold별 AUC가 매우 불안정 |
| 3 | 라벨 중첩(1시간 간격 vs 4~24h horizon)으로 유효 독립 표본이 훨씬 적다. **p-value 없음** |
| 4 | **64셀 다중 비교.** permutation 통제와 PRIMARY 한정으로 막았으나 제거하지는 못했다 |
| 5 | feature importance는 마지막 fold만, gated는 558~707행뿐이라 그 자체가 잡음이 크다 |
| 6 | 2021~2026은 D5·D6·P1이 이미 본 구간이다. untouched holdout이 아니다 |
| 7 | OKX 5m 아카이브가 있었으나 **사용하지 않았다**(1m과 granularity 혼합 회피). 미검증 축이 남아 있다 |
| 8 | **계약 문서에 em dash 5개.** 전 프로젝트 스타일 규칙 위반이나 sha256 동결 후라 수정하면 사전등록이 무효가 되어 그대로 둔다. **BTC-P1에서 같은 실수를 하고 메모리에 "동결 전 대시 검사 필수"를 적어두고도 반복했다** |
| 9 | 비용 미적용. 거래 가능성은 검증하지 않았다 |

## 38. Next exact step

**사용자 결정 대기. 자동 진행하지 않는다.**

| 선택지 | 내용 |
|---|---|
| **A** | **방향 탐색 종결** - 일곱 번의 AUTO 연구 + P1 + P2에서 방향은 나오지 않았다. P1 forward shadow만 유지하고 방향 라인을 닫는다 |
| **B** | **liqfwd 축적 후 P2-V2** - 청산 데이터는 이번에 쓴 어느 축과도 다르다. 게이트는 6개월+30이벤트이므로 2027년 3월 이후 |
| **C** | **변동성 응용으로 전환** - P1이 확인한 것은 보정된 변동성 예측기다. 방향을 요구하지 않는 구조에 쓸 수 있는지는 별도 연구 |
| **D** | **P2-V1.1: 조용한 구간 전용 재설계** - 방향이 LOW vol에서 가장 잘 맞았다(0.5711). 다만 그 구간은 비용을 넘길 크기가 없어 **성공해도 거래로 이어지지 않을 가능성이 높다** |

**권고: A + C.**

방향은 여덟 번째 시도에서도 나오지 않았고, 이번에는 **왜 안 나오는지**까지 보였다 -
크기와 방향이 같은 구간에 존재하지 않는다. B는 새 정보원이 쌓일 때까지 기다리는 것이 맞고,
D는 구조적으로 막다른 길로 보인다.

**C가 유일하게 새로운 길이다.** P1이 실제로 찾은 것(보정된 변동성 예측기,
skill +0.180 / AUC 0.727 / fold 9/9 / year 5/5)을 방향 없이 쓰는 방법이 있는지가
아직 한 번도 검토되지 않았다.
