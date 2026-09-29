# CRYPTO BTC-P2 RESULTS V1

실행 2026-09-28. 계약 sha256 `4d7ecbe8d565c2644c42aa035072a3331c56479f7b53b24c5cc0f4ca1f317693`.
런타임 4,642초. **주문 0, PnL 0, 배포 0.**

---

## 0. 판정

| 항목 | 값 |
|---|---|
| PRIMARY (first touch × D2-only × gated) | **NO_DIRECTION × 4** |
| **BTC-P3** | **NOT_AUTHORIZED** |

그리고 예상하지 못한 결과 하나:

> **P1이 큰 움직임을 예고할수록 방향은 더 안 맞는다.**

---

## 1. PRIMARY 결과

| 조합 | n | M1 AUC | M2 AUC | ECE | decile gap | fold>0.52 | year>0.52 | 판정 |
|---|---|---|---|---|---|---|---|---|
| 4H ±1% | 4,648 | 0.4914 | **0.4928** | 0.075 | -0.036 | 3/8 | 1/5 | NO_DIRECTION |
| 12H ±1% | 3,786 | 0.4885 | **0.4805** | 0.078 | -0.023 | - | - | NO_DIRECTION |
| 12H ±2% | 2,639 | 0.4880 | **0.4717** | 0.112 | -0.032 | - | - | NO_DIRECTION |
| 24H ±2% | 3,796 | 0.4881 | **0.4673** | 0.117 | -0.086 | - | - | NO_DIRECTION |

**네 조합 모두 AUC가 0.5 아래다.** 표본은 전부 사전등록 최소치 2,000을 넘겼으므로
INCONCLUSIVE가 아니라 실제 판정이다.

4H ±1% 상세: fold별 AUC 0.386~0.528, year별 0.435~0.528, **Brier skill -0.0361**.
`P_UP >= 0.70` 구간 69건에서 실제 UP 비율 **43.5%** - 자신 있게 틀렸다.

---

## 2. 가장 중요한 발견: gate가 방향을 악화시킨다

계약 §23의 핵심 질문은 이것이었다:

> 큰 움직임이 올 때만 보면 방향정보가 더 잘 드러나는가?

**답은 반대다.**

| 조합 (first touch, D2-only) | gated n | gated AUC | 전체 n | 전체 AUC | 차이 |
|---|---|---|---|---|---|
| 4H ±1% | 4,648 | **0.4928** | 15,262 | 0.5348 | **-0.042** |
| 12H ±1% | 3,786 | **0.4805** | 27,784 | 0.5074 | -0.027 |
| 12H ±2% | 2,639 | **0.4717** | 13,734 | 0.5307 | **-0.059** |
| 24H ±2% | 3,796 | **0.4673** | 22,512 | 0.5063 | -0.039 |

**네 조합 전부에서 gate가 AUC를 떨어뜨린다.** 평균 -0.042.

변동성 regime별로 보면 같은 이야기가 더 선명하다 (4H ±1%, 전체 구간):

| regime | direction AUC |
|---|---|
| LOW | **0.5711** |
| MID | 0.5342 |
| HIGH | 0.5200 |

**방향은 조용할 때 가장 잘 맞고 격렬할 때 가장 안 맞는다.**

이것은 거래에 쓸 수 있는 방향과 정확히 반대다. 큰 움직임이 없는 구간의 방향은
비용을 넘길 크기가 없고, 비용을 넘길 크기가 있는 구간에서는 방향이 사라진다.

---

## 3. Permutation 통제 (사전등록 200회)

라벨을 무작위로 섞고 walk-forward 전체를 200번 재실행한 분포.

| 조합 | null 평균 | null 표준편차 | **null p99** | null 최대 | 실제 AUC | 초과? |
|---|---|---|---|---|---|---|
| 4H ±1% | 0.5009 | 0.0080 | **0.5232** | 0.5247 | 0.4928 | **아니오** |
| 12H ±1% | 0.4985 | 0.0100 | **0.5217** | 0.5253 | 0.4805 | **아니오** |
| 12H ±2% | 0.4990 | 0.0110 | **0.5211** | 0.5253 | 0.4717 | **아니오** |
| 24H ±2% | 0.4995 | 0.0096 | **0.5187** | 0.5253 | 0.4673 | **아니오** |

### 이 통제가 실제로 한 일

**정보가 전혀 없는 라벨에서도 이 파이프라인은 AUC 0.52를 만들어낸다.**
겹치는 창, fold 구조, 적합 파라미터 수 때문이다.

제가 동결한 WEAK 임계 0.52가 **잡음 바닥과 같은 자리**였다는 뜻이다.
D6를 STRONG 조건에 넣어두지 않았다면 "AUC 0.53, 0.5보다 높음"을 신호로 읽었을 것이다.

실제 값은 네 조합 모두 null 평균보다도 낮다.

---

## 4. 전체 64셀

| 조합 | target | feature set | subset | n | M1 | M2 | 판정 |
|---|---|---|---|---|---|---|---|
| 4H_100 | FIRST_TOUCH | D2_ONLY | **GATED** | 4,648 | 0.4914 | 0.4928 | NO_DIRECTION |
| 4H_100 | FIRST_TOUCH | D2_ONLY | ALL | 15,262 | 0.5401 | 0.5348 | WEAK |
| 4H_100 | FIRST_TOUCH | WITH_EXTERNAL | GATED | 4,648 | 0.4988 | 0.4913 | NO_DIRECTION |
| 4H_100 | FIRST_TOUCH | WITH_EXTERNAL | ALL | 15,262 | 0.5413 | 0.5356 | WEAK |
| 4H_100 | ENDPOINT | D2_ONLY | GATED | 2,397 | 0.4931 | 0.4930 | NO_DIRECTION |
| 4H_100 | ENDPOINT | D2_ONLY | ALL | 7,115 | 0.5147 | 0.5238 | WEAK |
| 4H_100 | ENDPOINT | WITH_EXTERNAL | GATED | 2,397 | 0.4979 | 0.4950 | NO_DIRECTION |
| 4H_100 | ENDPOINT | WITH_EXTERNAL | ALL | 7,115 | 0.5151 | 0.5217 | WEAK |
| 12H_100 | FIRST_TOUCH | D2_ONLY | **GATED** | 3,786 | 0.4885 | 0.4805 | NO_DIRECTION |
| 12H_100 | FIRST_TOUCH | D2_ONLY | ALL | 27,784 | 0.5060 | 0.5074 | NO_DIRECTION |
| 12H_100 | FIRST_TOUCH | WITH_EXTERNAL | GATED | 3,786 | 0.4924 | 0.4765 | NO_DIRECTION |
| 12H_100 | FIRST_TOUCH | WITH_EXTERNAL | ALL | 27,784 | 0.5082 | 0.5081 | NO_DIRECTION |
| 12H_100 | ENDPOINT | D2_ONLY | GATED | 2,394 | 0.5087 | 0.5390 | WEAK |
| 12H_100 | ENDPOINT | D2_ONLY | ALL | 14,902 | 0.4966 | 0.5086 | NO_DIRECTION |
| 12H_100 | ENDPOINT | WITH_EXTERNAL | GATED | 2,394 | 0.5158 | 0.5360 | WEAK |
| 12H_100 | ENDPOINT | WITH_EXTERNAL | ALL | 14,902 | 0.4958 | 0.5084 | NO_DIRECTION |
| 12H_200 | FIRST_TOUCH | D2_ONLY | **GATED** | 2,639 | 0.4880 | 0.4717 | NO_DIRECTION |
| 12H_200 | FIRST_TOUCH | D2_ONLY | ALL | 13,734 | 0.5344 | 0.5307 | WEAK |
| 12H_200 | FIRST_TOUCH | WITH_EXTERNAL | GATED | 2,639 | 0.4889 | 0.4660 | NO_DIRECTION |
| 12H_200 | FIRST_TOUCH | WITH_EXTERNAL | ALL | 13,734 | 0.5334 | 0.5261 | WEAK |
| 12H_200 | ENDPOINT | D2_ONLY | GATED | 1,312 | 0.4891 | 0.5101 | **INCONCLUSIVE** |
| 12H_200 | ENDPOINT | D2_ONLY | ALL | 6,479 | 0.4874 | 0.4830 | NO_DIRECTION |
| 12H_200 | ENDPOINT | WITH_EXTERNAL | GATED | 1,312 | 0.4747 | 0.5068 | **INCONCLUSIVE** |
| 12H_200 | ENDPOINT | WITH_EXTERNAL | ALL | 6,479 | 0.4895 | 0.4933 | NO_DIRECTION |
| 24H_200 | FIRST_TOUCH | D2_ONLY | **GATED** | 3,796 | 0.4881 | 0.4673 | NO_DIRECTION |
| 24H_200 | FIRST_TOUCH | D2_ONLY | ALL | 22,512 | 0.5061 | 0.5063 | NO_DIRECTION |
| 24H_200 | FIRST_TOUCH | WITH_EXTERNAL | GATED | 3,796 | 0.4926 | 0.4664 | NO_DIRECTION |
| 24H_200 | FIRST_TOUCH | WITH_EXTERNAL | ALL | 22,512 | 0.5053 | 0.5067 | NO_DIRECTION |
| 24H_200 | ENDPOINT | D2_ONLY | GATED | 2,032 | 0.5020 | **0.5437** | WEAK |
| 24H_200 | ENDPOINT | D2_ONLY | ALL | 11,240 | 0.4813 | 0.5000 | NO_DIRECTION |
| 24H_200 | ENDPOINT | WITH_EXTERNAL | GATED | 2,032 | 0.5132 | 0.5357 | WEAK |
| 24H_200 | ENDPOINT | WITH_EXTERNAL | ALL | 11,240 | 0.4864 | 0.5058 | NO_DIRECTION |

**32셀 × 2모델 = 64회 평가를 전부 보고했다.** 고른 것 없음.

| 판정 | 셀 수 |
|---|---|
| NO_DIRECTION | 20 |
| WEAK_DIRECTION | 10 |
| INCONCLUSIVE | 2 |
| **STRONG_DIRECTION** | **0** |

---

## 5. 가장 높은 셀은 무엇인가

`24H_200 ENDPOINT D2_ONLY GATED`, M2, AUC **0.5437** - 64셀 중 최고.

그런데 열어보면 신호가 아니다:

| 항목 | 값 | 기준 | 결과 |
|---|---|---|---|
| ECE | **0.1500** | <= 0.06 | **FAIL** |
| Brier skill | **-0.0918** | > 0 | **FAIL** |
| fold별 AUC | **0.381 ~ 0.719** | - | 진폭이 AUC 자체보다 크다 |
| n | 2,032 | >= 2,000 | 최소치 턱걸이 |

fold 2에서 0.381, fold 3에서 0.719다. **평균이 0.5437인 것은 이 진폭의 부산물이다.**
그리고 Brier skill이 -0.09라는 것은 **"항상 base rate를 말하는 것"보다 확률이 나쁘다**는 뜻이다.

---

## 6. 외부 데이터(F6)는 아무것도 바꾸지 않았다

| 조합 | D2_ONLY | WITH_EXTERNAL | 차이 |
|---|---|---|---|
| 4H_100 gated | 0.4928 | 0.4913 | -0.0015 |
| 12H_100 gated | 0.4805 | 0.4765 | -0.0040 |
| 12H_200 gated | 0.4717 | 0.4660 | -0.0057 |
| 24H_200 gated | 0.4673 | 0.4664 | -0.0009 |

Binance perp·spot 6컬럼을 넣어도 **전부 소폭 하락**했다.
cross-exchange lead-lag와 spot-perp divergence는 **방향 context로서도 값이 없었다.**

D5.2에서 standalone으로 실패한 축이 조합에서도 되살아나지 않았다.
이는 P1에서 OI·basis·funding이 보인 것과 같은 패턴이다.

---

## 7. AUC > 0.5 인데 Brier skill이 음수인 셀들

| 셀 | AUC | Brier skill |
|---|---|---|
| 4H_100 FIRST_TOUCH ALL | 0.5348 | **-0.0074** |
| 12H_200 FIRST_TOUCH ALL | 0.5307 | **-0.0119** |
| 24H_200 ENDPOINT GATED | 0.5437 | **-0.0918** |

AUC는 **순위**만 본다. Brier는 확률의 **값**까지 본다.
순위가 동전던지기보다 아주 약간 나아도, 그 확률을 그대로 믿으면
**"항상 50%"라고 말하는 것보다 손해**라는 뜻이다.

거래는 순위가 아니라 확률값으로 한다.

---

## 8. 표본이 부족한 셀

`12H_200 ENDPOINT GATED` 2셀이 n=1,312로 사전등록 최소치 2,000 미만 → **INCONCLUSIVE**.
AUC 0.5101/0.5068이 나왔지만 **판정하지 않는다.**

---

## 9. 다중 비교

**4조합 × 2target × 2featureset × 2subset × 2model = 64셀.**

귀무가설 아래서도 64개 중 최고는 개별 추정보다 높아 보인다.
그래서 permutation 통제와 fold·year 일관성 조건이 있고,
**BTC-P3는 PRIMARY 셀만으로 결정**하도록 사전등록했다.

실제로 최고 셀(0.5437)이 ECE와 Brier skill에서 무너진 것이 이 경계가 작동한 예다.

---

## 10. 판정 요약

| 항목 | 값 |
|---|---|
| STRONG_DIRECTION | **0 / 64** |
| PRIMARY 판정 | **NO_DIRECTION × 4** |
| permutation 초과 | **0 / 4** |
| 외부 데이터 기여 | **음수** |
| **BTC-P3** | **NOT_AUTHORIZED** |
| 거래·배포·운영 접근 | **0** |

**STRONG_DIRECTION이 없으므로 P3로 가지 않는다.** 계약 §18 그대로다.
