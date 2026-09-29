# CRYPTO BTC-P2 DIRECTION CONTRACT V1

Preregistration. 동결 2026-09-28, **direction AUC·Brier·Log Loss를 하나도 계산하기 전.**

Research only. **주문 0, entry/exit 0, leverage 0, sizing 0, PnL 0, Paper 0, AUTO 0.**

---

## 1. P1 해석 (전제)

| 항목 | 값 |
|---|---|
| P1 PATH STRONG | 6 |
| 최강 `4H_DOWN_100` M2 | skill +0.180, AUC 0.727, ECE 0.015, fold 9/9, year 5/5 |
| **directional AUC** | **0.502 ~ 0.533** |
| F2 변동성 importance | 나머지 5 family 합의 3~10배 |
| F1 가격경로 importance | 0.001 |

**P1은 `LARGE_MOVE MODEL`이다.** 방향 모델이 아니다.

---

## 2. P2의 질문

> 큰 움직임이 올 가능성이 높은 시점에서,
> **현재 정보만으로 상승과 하락을 구분할 수 있는가?**

| 단계 | 질문 |
|---|---|
| P1 | Will BTC move big? |
| **P2** | **If a big move is likely, which direction?** |

---

## 3. P1 Gate

### 3.1 출처 (누출 방지의 핵심)

gate는 **`data/research/crypto/btc_p1/predictions_v1.parquet`의 fold별 validation 예측**만 쓴다.
컬럼은 `p_m2_cal`.

| # | 규칙 |
|---|---|
| **G1** | **forward용 fold-9 frozen artifact를 과거 전체에 적용해 gate를 만들지 않는다.** 그 모델은 2025-12-31까지 학습했으므로 2022~2025 행을 gate하면 미래 정보가 들어간다 |
| G2 | 따라서 P2는 P1이 검증한 **41,400개 시각**으로 제한된다. 이것이 누출을 피하는 대가다 |

### 3.2 large-move 상태

`P_LARGE_MOVE = max(P_UP_X, P_DOWN_X)` — P1 forward 계약에 이미 사전등록된 정의.

### 3.3 gate 임계 (분위 기준)

**절대 확률 임계를 쓰지 않는다.** 동결 전 실측:

| 조합 | P_LARGE_MOVE 중앙값 | p>=0.60에서 남는 비율 |
|---|---|---|
| 4H ±1% | 0.246 | 2.3% |
| 12H ±1% | 0.524 | 29.0% |
| 12H ±2% | 0.227 | 0.9% |
| 24H ±2% | 0.378 | 7.0% |

같은 임계가 조합마다 2.3%와 29.0%를 남긴다. 따라서:

| 항목 | 값 |
|---|---|
| **PRIMARY gate** | `P_LARGE_MOVE >= (train fold의 0.75 분위)` = **상위 4분의 1** |
| 분위 산출 | **train fold 행에서만.** validation이 자기 크기를 정하지 못하게 한다 |
| REFERENCE | **전체 행** (gate 없음) |
| 진단용 | 상위 10% (`0.90` 분위) |

---

## 4. 데이터

| 허용 | 내용 |
|---|---|
| D2 canonical 5종 | kline·mark·index 1m, OI 5m, funding |
| D5.2 external archive | Binance USD-M perp 1m, Binance spot 1m (**별도 family F6**) |
| P1 frozen predictions | gate·context 전용 |

| 금지 | 이유 |
|---|---|
| liquidation forward | forward only, history 부족 |
| Manual-R2 user labels | 사용자 확정 거래 완료 1건 |
| forward shadow 결과 | 미래 |
| 신규 대규모 다운로드 | 계약 §4 |

### 4.1 외부 데이터에서 발견한 결함 (동결 전 기록)

**Binance spot은 2025-01부터 타임스탬프를 마이크로초로 바꿨고 USD-M 선물은 밀리초를 유지한다.**
밀리초로 읽으면 2025년 이후 행이 서기 58000년으로 가고, as-of 결합이
**2024-12-31 가격을 630일간 forward-fill**한다. 그럴듯한 계열이 나오지만 전부 가짜다.

수정 후 실측: spot 최대 staleness **284분**(실제 거래소 중단), 5분 초과 행 1,038 / 2,966,400 = 0.035%.
`MAX_STALENESS_MINUTES = 360` 초과 시 빌드를 **중단**하도록 걸었다.

---

## 5. Direction Target

### 5.1 T1 — First Touch (**PRIMARY**)

horizon 안에서 `+X`와 `-X` 중 **어느 쪽을 먼저 터치했는가**.

| 라벨 | 조건 |
|---|---|
| `UP_FIRST` | +X 터치가 -X 터치보다 앞선 봉 |
| `DOWN_FIRST` | 반대 |
| `NEITHER` | 둘 다 터치 안 함 |
| **`AMBIGUOUS`** | **같은 1분봉이 양쪽을 모두 클리어** |

| # | 규칙 |
|---|---|
| **T1a** | **`AMBIGUOUS`는 제외한다. 규칙으로 채우지 않는다.** 1분 OHLC로는 분 내 순서를 알 수 없고, 채우면 그 규칙이 곧 이 연구가 측정하려는 방향 신호가 된다 |
| T1b | `NEITHER`도 방향 평가에서 제외한다 |

동결 전 실측: `AMBIGUOUS`는 조합당 **0~1행**이다.

### 5.2 T2 — Endpoint (SECONDARY)

horizon 종료 시점 return이 `+X` 이상이면 UP, `-X` 이하면 DOWN, 사이면 NEITHER.

### 5.3 T3 — Dominant Excursion (**동결 전 기각**)

동결 전 class count 실측 결과 T1과 **사실상 동일**했다:

| 조합 | T1 directional | T3 directional | T1 UP | T3 UP |
|---|---|---|---|---|
| 4H ±1% | 18,139 | 18,140 | 8,864 | 8,875 |
| 12H ±1% | 31,791 | 31,791 | 15,786 | 15,783 |
| 24H ±2% | 26,224 | 26,224 | 13,047 | 12,953 |

**독립 target이 아니므로 사용하지 않는다.** 이것은 성능이 아니라 class count에 근거한 결정이며
동결 전에 허용된 관측이다(§41).

---

## 6. Horizon × Threshold 조합 (동결)

| 조합 | P1 대응 target |
|---|---|
| **4H ±1.00%** | `4H_UP_100` / `4H_DOWN_100` |
| **12H ±1.00%** | `12H_UP_100` / `12H_DOWN_100` |
| **12H ±2.00%** | `12H_UP_200` / `12H_DOWN_200` |
| **24H ±2.00%** | `24H_UP_200` / `24H_DOWN_200` |

4개. horizon 3종(4H·12H·24H). **결과를 보고 추가·변경하지 않는다.**

모든 threshold가 왕복 비용 11bp의 9배 이상이다.

### 6.1 동결 전 관측된 class count (T1 First Touch, 전체 41,400행)

| 조합 | UP_FIRST | DOWN_FIRST | NEITHER | AMBIGUOUS | directional | UP 비율 |
|---|---|---|---|---|---|---|
| 4H ±1% | 8,864 | 9,275 | 23,260 | 1 | 18,139 (43.8%) | 48.9% |
| 12H ±1% | 15,786 | 16,005 | 9,608 | 1 | 31,791 (76.8%) | 49.7% |
| 12H ±2% | 8,176 | 8,313 | 24,911 | 0 | 16,489 (39.8%) | 49.6% |
| 24H ±2% | 13,047 | 13,177 | 15,176 | 0 | 26,224 (63.3%) | 49.8% |

**클래스가 거의 균형이다(UP 48.9~49.8%).** 불균형 보정이 필요 없다.

---

## 7. Feature

**7 family, D2-only 34컬럼 + external 6컬럼 = 40.**

| family | 내용 | 컬럼 |
|---|---|---|
| F1 | 가격 경로·형태 | 11 |
| F2 | 캔들 구조 | 6 |
| F3 | OI × 가격 결합 | 6 |
| F4 | basis divergence | 5 |
| F5 | funding context | 3 |
| F6 | cross-market (Binance perp·spot) | 6 |
| F7 | 시간·추세 context | 3 |

### 7.1 변동성 제거 (핵심 설계)

**변동성 family가 없다.** 그리고 부호 있는 feature는 전부 **24시간 실현변동성으로 나눈다.**

이유: P1의 예측력이 전부 변동성에서 나왔고 변동성에는 방향이 없다.
원시 수익률을 넣으면 같은 혼동을 물려받는다. 남는 것은 크기가 아니라 **형태**여야 한다.

수준 feature는 최근 range 내 위치로 표현한다.

### 7.2 Feature set

| set | 컬럼 | 지위 |
|---|---|---|
| **`D2_ONLY`** | 34 | **PRIMARY** |
| `WITH_EXTERNAL` | 40 | SECONDARY |

D2-only를 primary로 두는 이유: 외부 아카이브 의존 없이 forward에서 그대로 돌릴 수 있는 집합이다.

| # | 규칙 |
|---|---|
| FE1 | 결과를 보고 더 좋은 쪽을 최종 모델로 선택하지 않는다. **둘 다 보고한다** |
| FE2 | 수백 feature + feature selection 금지 |
| FE3 | importance를 보고 feature를 바꾼 뒤 같은 validation을 최종 성과로 제시 금지 |

동결 전 실측: 40컬럼 전부 41,400행에서 **결측 0**.

---

## 8. Fold

P1 fold에서 파생한다.

| P2 fold | train (P1 folds) | validate (P1 fold) |
|---|---|---|
| 1 | 1 | 2 |
| 2 | 1-2 | 3 |
| ... | ... | ... |
| 8 | 1-8 | 9 |

**8개 fold.** P1 fold 1은 앞에 아무것도 없으므로 학습 전용이다.

| # | 규칙 |
|---|---|
| **F1** | **embargo 1,440분**을 train 종료와 validation 시작 사이에 둔다. 방향 라벨이 최대 24시간 앞을 보기 때문이다 |
| F2 | scaler·gate 분위는 **train fold에서만** fit |
| F3 | random split 금지 |

---

## 9. PIT

| # | 규칙 |
|---|---|
| P1 | 모든 feature는 decision t까지 |
| P2 | future return·high/low·OI 금지 |
| P3 | 전 구간 정규화 금지 |
| P4 | 외부 데이터는 **직전 관측 forward fill**만. 보간 금지(보간은 미래 가격을 쓴다) |

---

## 10. Model (최대 3)

| 모델 | 정의 |
|---|---|
| **M0** | gate 적용된 train 구간의 `UP_FIRST` 빈도 (상수) |
| **M1** | Ridge logistic, robust z(train fit), L2 = 1.0, IRLS 30회 |
| **M2** | Histogram GBDT: 트리 200, depth 3, lr 0.05, **min samples/leaf 50**, bins 32, leaf L2 1.0 |

M2의 leaf 최소치를 P1의 200에서 **50으로 낮춘다**: gate 후 표본이 훨씬 작다. 결과 전에 정한다.

| # | 규칙 |
|---|---|
| M1 | **단일 고정 config.** grid search 금지 |
| M2 | subsampling·early stopping 없음. 난수 없음 |
| M3 | **P1 volatility 신호를 복사하지 않는다.** direction target으로 새로 학습한다 |

---

## 11. 방향 대칭성

**`P(UP_FIRST)` 하나의 이진 target**으로 학습한다. `P(DOWN_FIRST) = 1 - P(UP_FIRST)`.

UP과 DOWN을 독립 이진 모델로 만들면 **P1에서 발생한 "둘 다 동시에 오른다" 문제**가 재현된다.
하나의 방향 target은 구조적으로 그 실패를 막는다.

---

## 12. Metric

| primary | |
|---|---|
| **direction AUC** | `UP_FIRST` vs `DOWN_FIRST` 직접 판별 |
| balanced accuracy | |
| Brier / Log Loss | |
| ECE / calibration slope | |
| reliability buckets | 10 equal-width |

| # | 규칙 |
|---|---|
| **MT1** | **UP target AUC와 DOWN target AUC가 각각 높다는 이유로 통과시키지 않는다.** P1의 실수를 반복하지 않는다 |
| MT2 | direction AUC는 **0.5와 직접 비교**한다 |

---

## 13. Separation 진단

`P_UP`의 10분위마다 실제 `UP_FIRST` 비율을 보고한다.
최상위·최하위 분위의 실제 방향 차이가 명확해야 한다.

---

## 14. 고신뢰 구간

`P_UP >= 0.60 / 0.65 / 0.70` 및 대칭인 `P_UP <= 0.40 / 0.35 / 0.30`에서
N·실제 비율·base rate·lift·보정오차를 보고한다.

**분석 bucket이며 trading rule이 아니다.**

---

## 15. Permutation control

| 항목 | 값 |
|---|---|
| 대상 | **PRIMARY 구성만** (T1 × `D2_ONLY` × M2 × gated) |
| 반복 | **200** |
| 방법 | gate 적용 부분집합의 라벨을 전체 치환한 뒤 walk-forward 전체 재실행 |
| seed | **20260928** |
| 판정 | 실제 AUC가 치환 분포의 **99분위를 초과**해야 한다 |

## 16. Stability

fold(8) · year(2022~2026) · 변동성 regime(LOW/MID/HIGH) · 추세 regime별 direction AUC.
regime 라벨은 **P1 predictions에 이미 기록된 것**을 쓴다(새로 자르지 않는다).

## 17. 판정 게이트 (동결)

조합 × 모델 × feature set마다 독립 평가. **gate 적용 부분집합 기준.**

### INCONCLUSIVE
gate 후 directional 행 < **2,000**

### STRONG_DIRECTION (전부 충족)

| # | 조건 |
|---|---|
| D1 | direction AUC > **0.55** |
| D2 | AUC > **0.52** 인 fold가 **8개 중 6개 이상** |
| D3 | AUC > **0.52** 인 year가 **5개 중 4개 이상** |
| D4 | ECE <= **0.06** |
| D5 | `P_UP` 최상위 10분위와 최하위 10분위의 **실제 UP 비율 차이 >= 8%p** |
| D6 | **permutation 200회 분포의 99분위 초과** (PRIMARY 구성만 산출) |
| D7 | gate 후 directional 행 >= **2,000** |

### WEAK_DIRECTION
AUC > **0.52** 이나 STRONG 미달

### NO_DIRECTION
AUC <= **0.52**

---

## 18. BTC-P3 게이트

| # | 규칙 |
|---|---|
| **P3a** | **PRIMARY target(T1)에서 최소 하나가 `STRONG_DIRECTION`이어야 BTC-P3 = AUTHORIZED** |
| P3b | `WITH_EXTERNAL`에서만 STRONG이면 **그 사실을 명시**하고, forward에서 외부 데이터 의존을 재검증해야 한다 |
| P3c | STRONG_DIRECTION이 없으면 **P3로 가지 않는다** |

P3에서 처음 P1 large move + P2 direction + expected value + cost를 합친다.

---

## 19. Forward shadow와의 관계

| # | 규칙 |
|---|---|
| FS1 | P1 forward shadow는 별도로 계속 실행된다. **이번 작업에서 수정하지 않는다** |
| FS2 | P2 historical 결과를 보고 P1 shadow를 바꾸지 않는다 |
| FS3 | P2가 STRONG_DIRECTION이어도 **P2 forward shadow가 별도로 필요하다** |

## 20. 금지

| 항목 |
|---|
| LONG/SHORT 주문, entry/exit, leverage, sizing, PnL |
| Paper Engine, AUTO |
| liquidation 데이터, Manual 라벨 |
| 결과를 보고 threshold·feature·target 변경 |
| 대규모 grid search |
| commit / push |
| server·production·frontend 배포 |
| `btc_p1_forward`·`manual_r1`·`manual_r2`·`liquidation_forward` 경로 수정 |

## 21. Determinism

seed **20260928**. 난수는 permutation control에만 쓴다.
동일 contract + 동일 데이터 → 동일 결과.

## 22. Freeze

동결 전 허용: class counts, feature availability, missingness, base rates.
동결 전 금지: direction AUC, Brier, Log Loss, model outcome, PnL.

이 문서의 sha256을 기록하고 실행 시 대조한다. 불일치하면 `CONTRACT_HASH_MISMATCH`로 중단.
