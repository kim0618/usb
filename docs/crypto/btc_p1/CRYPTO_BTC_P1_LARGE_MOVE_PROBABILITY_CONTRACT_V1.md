# CRYPTO BTC-P1 LARGE-MOVE PROBABILITY CONTRACT V1

Preregistration. Frozen 2026-09-28, **before any probability performance metric was computed.**

Research only. No orders, no positions, no cost model, no PnL, no AUTO.

---

## 1. 연구 질문

기존 질문은 "다음 15분/30분/1시간에 오를까 내릴까"였고 여섯 번 연속 실패했다
(D5·D5.1·D5.2·D6 FDN-V1·D5.4·D5.5 전부 SURVIVE 0, D6-V2 NOT AUTHORIZED).

이번 질문:

> 현재 정보만으로 앞으로 4h / 12h / 24h 동안
> **수수료를 충분히 압도할 만큼 큰** BTC 방향성 움직임의 발생 확률을
> 안정적으로 추정할 수 있는가?

측정 대상은 **확률 예측 품질 하나**다. Trading edge와 Prediction edge를 분리한다.

---

## 2. 방법론 변경

| 기존 | 이번 |
|---|---|
| 조건 → 즉시 LONG/SHORT | 조건 → 확률 |
| 짧은 horizon (15m~4h) | 4h / 12h / 24h |
| binary 방향 | **UP·DOWN 독립 multi-label** |
| accuracy·PnL | **Brier·calibration·lift** |
| 비용 후 net edge | **비용 미적용** (P2로 이관) |

**왜 거래를 뺐나**: prediction → execution → cost → holding → overlap이 한 번에 섞이면
edge가 어디서 사라졌는지 구분할 수 없다. 예측 정보가 없으면 그 위의 어떤 규칙도 작동할 수 없다.

---

## 3. 데이터

| 항목 | 값 |
|---|---|
| 출처 | D2 canonical 5종 (`data/runtime/crypto/BTCUSDT/historical/`) |
| 계열 | kline 1m, mark 1m, index 1m, open_interest 5m, funding |
| 그리드 | `app.crypto.research.dataset` 1분 그리드 (D5와 동일, 무변경 재사용) |
| 전체 구간 | 2021-02-01T00:00Z ~ 2026-09-22T23:59Z (2,966,400분, 결측 0) |
| OI PIT | 스탬프 T는 **T + 5분**에 알려진 것으로 처리 (D5 규칙 계승) |
| funding PIT | 스탬프 T(정산시각)에 알려짐 |

**금지된 입력**
- liquidation forward 데이터: **사용 금지** (history 부족, V2 feature)
- manual trading 데이터: **사용 금지** (MANUAL-R1.1/R2 별도 진행)
- D5.2 historical derivatives: 기본 모델에서 제외

### 3.1 이것은 untouched holdout이 아니다

2021~2026 구간은 D5/D5.1/D5.2/D6에서 이미 여러 번 본 데이터다.
이번 결과는 **development / historical validation** 성격이며,
true OOS는 향후 forward shadow에서만 가능하다.

---

## 4. 결정 시점 그리드

| 항목 | 값 |
|---|---|
| 간격 | **60분** (UTC 정시) |
| warmup | 30일 (43,200분). 이전 행은 **제외**하며 대치하지 않는다 |
| 첫 행 | 2021-03-03T00:00:00Z |
| 마지막 행 | 2026-09-21T23:00:00Z (24h forward window가 데이터 안에 들어가는 마지막 정시) |
| 행 수 | **48,696** |

분 단위 그리드를 쓰지 않는 이유: 연속된 분은 feature도 label도 거의 같아서
행 수만 60배가 되고 독립 관측은 늘지 않는다.

---

## 5. Target 정의

기준가 = **결정 봉의 종가** `close[t]`. Forward window = 봉 `t+1 .. t+H`.

### 5.1 Primary = PATH-TOUCH

horizon 안에 threshold를 **터치했는가**.

- `UP_X`: `max(high[t+1..t+H]) >= close[t] * (1 + X)`
- `DOWN_X`: `min(low[t+1..t+H]) <= close[t] * (1 - X)`

### 5.2 Secondary = ENDPOINT

horizon **종료 시점** return.

- `UP_X`: `close[t+H] >= close[t] * (1 + X)`
- `DOWN_X`: `close[t+H] <= close[t] * (1 - X)`

**둘을 섞지 않는다.** BTC-P2 승인 판정은 **PATH 계열만** 대상으로 한다.

### 5.3 Threshold grid (동결)

| horizon | thresholds |
|---|---|
| 4H (240분) | ±0.50%, ±1.00% |
| 12H (720분) | ±1.00%, ±2.00% |
| 24H (1440분) | ±1.00%, ±2.00%, ±3.00% |

PATH 14개 + ENDPOINT 14개 = **28 series**. 이 목록은 동결이며 결과를 보고 추가하지 않는다.

**threshold 선택 근거**: VIP0 taker 왕복 비용 약 11bp 대비 50bp는 4.5배, 300bp는 27배다.
**historical PnL을 보고 고르지 않았다.**

### 5.4 UP / DOWN은 배타적이지 않다

24시간 안에 +2%도 가고 -2%도 갈 수 있다. UP과 DOWN을 **독립 label**로 모델링한다.
"상승 60 / 하락 40" 같은 강제 배타 구조를 쓰지 않는다.

### 5.5 불완전한 forward window

horizon이 데이터 끝을 넘는 행의 label은 **NaN**이며 학습·평가 어디에도 쓰지 않는다.
0으로 채우면 표본 마지막 하루가 항상 조용하다고 가르치게 된다.

### 5.6 동결 전 관측된 base rate (결정 행 48,696 기준)

확률 성능이 아닌 **클래스 빈도**이므로 동결 전 관측이 허용된다(§38).

| target | base | target | base |
|---|---|---|---|
| 4H_UP_050 | 51.27% | 4H_DOWN_050 | 51.82% |
| 4H_UP_100 | 26.12% | 4H_DOWN_100 | 27.24% |
| 12H_UP_100 | 48.72% | 12H_DOWN_100 | 49.33% |
| 12H_UP_200 | 23.46% | 12H_DOWN_200 | 24.05% |
| 24H_UP_100 | 62.65% | 24H_DOWN_100 | 62.72% |
| 24H_UP_200 | 37.64% | 24H_DOWN_200 | 38.07% |
| 24H_UP_300 | 22.56% | 24H_DOWN_300 | 23.11% |

ENDPOINT 최저는 24H_DOWN_300_EP 10.08%. 검증 구간 최소 양성 수는 3,619건으로
모든 target이 §31의 표본 요건을 충족한다.

---

## 6. Feature

**최대 6 family, 총 34 컬럼.** 순서 동결.

| family | 내용 | 컬럼 수 |
|---|---|---|
| F1 | price path / multi-horizon returns (5m·15m·30m·1h·2h·4h, 가속 2, range position 2) | 10 |
| F2 | volatility state (rv 1h·4h·24h, vol ratio 2, range expansion) | 6 |
| F3 | OI / positioning (chg 1h·4h·24h, accel, price×OI joint) | 5 |
| F4 | basis / perp pressure (basis, z-24h, chg 1h, accel) | 4 |
| F5 | funding (current, trend-24h, z-30d) | 3 |
| F6 | time / regime (hour sin·cos, dow sin·cos, trend regime, vol regime) | 6 |

| # | 규칙 |
|---|---|
| FE1 | indicator zoo 금지. 결과를 보고 feature를 추가하지 않는다 |
| FE2 | F3·F4·F5는 **standalone alpha로 취급 금지**. context feature로만 |
| FE3 | 과도한 categorical fragmentation 금지. 시간·regime은 연속값 |
| FE4 | level보다 path. 다중 horizon return 벡터·가속·range position 중심 |

**동결 전 관측된 결측률**: 34개 컬럼 전부 48,696행에서 **결측 0**. 대치(imputation) 불필요.

---

## 7. Fold

random split **금지**. time-order expanding walk-forward.

| fold | train end | validate |
|---|---|---|
| F1 | 2021-12-31 | 2022-01-01 ~ 2022-07-01 |
| F2 | 2022-06-30 | 2022-07-01 ~ 2023-01-01 |
| F3 | 2022-12-31 | 2023-01-01 ~ 2023-07-01 |
| F4 | 2023-06-30 | 2023-07-01 ~ 2024-01-01 |
| F5 | 2023-12-31 | 2024-01-01 ~ 2024-07-01 |
| F6 | 2024-06-30 | 2024-07-01 ~ 2025-01-01 |
| F7 | 2024-12-31 | 2025-01-01 ~ 2025-07-01 |
| F8 | 2025-06-30 | 2025-07-01 ~ 2026-01-01 |
| F9 | 2025-12-31 | 2026-01-01 ~ 2026-09-22 |

경계는 D5.1 `FOLD_STARTS`를 그대로 승계한다. train은 항상 2021-03-03에서 시작(expanding).
총 검증 행 41,400.

### 7.1 Embargo (신규 등재)

**train 종료와 validation 시작 사이에 최대 horizon(1,440분) 공백을 둔다.**

이유: 시각 t의 label은 최대 24시간 앞을 본다. embargo가 없으면 train 마지막 하루의 label이
validation 구간의 미래를 담는다. 시간순 분할만으로는 막히지 않는 누출이다.

**같은 embargo를 train 내부 model/calibration 경계에도 적용한다.**

---

## 8. Leakage 금지

| # | 규칙 |
|---|---|
| L1 | 시각 t의 feature는 t까지의 정보만. 결정 봉의 종가는 알려진 것으로 본다 |
| L2 | centered rolling **금지** |
| L3 | future OI·future funding·backward-fill **금지** |
| L4 | 전역 통계로 정규화 **금지**. scaler는 **train fold에서만 fit** |
| L5 | calibration도 train 내부 slice에서만 fit. validation 접촉 금지 |

---

## 9. 모델 (최대 3개)

### 9.1 의존성 결정 (동결 전 기록)

**scikit-learn·LightGBM·XGBoost·scipy가 이 저장소에 설치되어 있지 않고 선언되어 있지도 않다.**
`requirements.txt`는 numpy·pandas·pyarrow까지다.

§13의 "새 대규모 dependency 도입 금지"를 지키기 위해
**M1과 M2를 numpy로 직접 구현한다.** sklearn을 설치하지 않는다.

대가와 완화책:
- 대가: 직접 구현한 GBDT의 결함이 신호를 가릴 수 있다(거짓 NO_SIGNAL 위험)
- 완화: **학습 가능한 구조를 심은 합성 데이터에서 M2가 M0·M1을 이기는지 먼저 검증**한다.
  이 sanity test를 통과하지 못하면 M2 결과를 보고하지 않는다

### 9.2 M0 — Naive baseline

train fold의 **무조건 빈도**를 상수 확률로 출력. 모든 모델은 이것을 이겨야 한다.

### 9.3 M1 — Ridge logistic regression

| 항목 | 값 |
|---|---|
| 정규화 | train fold의 median / MAD (robust z), MAD=0이면 1.0 |
| 페널티 | L2, lambda = 1.0 (절편 제외) |
| 해법 | IRLS 30회, tol 1e-8 |

### 9.4 M2 — Histogram gradient-boosted trees

| 항목 | 값 |
|---|---|
| 트리 수 | 200 |
| max depth | 3 |
| learning rate | 0.05 |
| min samples / leaf | 200 |
| histogram bins | 32 (train fold quantile) |
| leaf L2 | 1.0 |
| subsampling | **없음** |
| early stopping | **없음** |
| hyperparameter search | **없음.** 단일 고정 config |

§27의 "2~3 complexity level" 허용치를 **쓰지 않는다.** 하나만 돌리면 선택 편의가 0이다.

### 9.5 금지

| 항목 |
|---|
| Deep learning (LSTM·Transformer·NN·time-series foundation model) |
| LLM이 확률을 직접 출력 |
| 수십 모델 leaderboard |
| 대규모 grid search |

---

## 10. Calibration

| 항목 | 값 |
|---|---|
| 방법 | **isotonic regression (PAV)**. 사전등록이며 결과를 보고 바꾸지 않는다 |
| fit 대상 | train window의 **마지막 20%** (model은 앞 80%로 학습) |
| embargo | 80/20 경계에도 1,440분 적용 |
| 보고 | **raw와 calibrated 둘 다** |
| **게이트 판정** | **calibrated 확률 기준** |

M0는 상수라 calibration 대상이 아니다.

---

## 11. Metrics

| primary |
|---|
| Brier score |
| **Brier skill score** (vs M0) |
| Log loss |
| ROC AUC |
| PR AUC (average precision) |
| Expected calibration error (10 equal-width bucket, 가중) |
| calibration slope / intercept (logit(p)에 대한 로지스틱 회귀) |
| reliability curve (10 bucket: N·평균예측·실제빈도) |
| base rate, 예측확률 분포 |

**Accuracy는 secondary.** P(+3%)가 5%면 항상 NO라고 해도 accuracy 95%다.

### 11.1 High-confidence bucket

예측확률 >= 0.60 / 0.65 / 0.70 / 0.75 각 구간에서
sample count, actual frequency, lift(= actual / base rate)를 계산한다.

**진단용이다. 결과를 보고 threshold를 trading rule로 승격 금지.**

### 11.2 Probability separation

`P(DOWN_X) - P(UP_X)`가 큰 구간의 실제 결과를 진단용으로 보고한다. 거래하지 않는다.

---

## 12. Stability

| 축 | 값 |
|---|---|
| fold | 9개 |
| year | 2022·2023·2024·2025·2026 (검증 구간만) |
| vol regime | `vol_regime_ln` 3분위 (train fold 경계) |
| trend regime | `trend_regime_24h_bp` 3분위 (train fold 경계) |

특정 한 해에만 좋은 모델은 신뢰하지 않는다.

---

## 13. PASS 게이트 (동결)

**target마다 독립 평가.** 하나가 좋다고 전체 PASS가 아니다.
모델(M1·M2)별로 각각 판정하고, target의 판정은 두 모델 중 **더 좋은 쪽**을 쓴다.

판정 순서: INCONCLUSIVE → STRONG → WEAK → NO_SIGNAL.

### INCONCLUSIVE
- 검증 양성 수 < 500, **또는** 검증 행 수 < 5,000

### STRONG_SIGNAL (전부 충족)

| # | 조건 |
|---|---|
| S1 | Brier skill score > **0.010** |
| S2 | ROC AUC > **0.550** |
| S3 | ECE <= **0.050** |
| S4 | calibration slope ∈ **[0.70, 1.30]** |
| S5 | 예측확률 >= 0.65 구간의 N >= **200** **그리고** lift >= **1.30** |
| S6 | Brier skill > 0 인 fold가 **9개 중 7개 이상** |
| S7 | Brier skill > 0 인 year가 **5개 중 4개 이상** |

### WEAK_SIGNAL
STRONG 미달이면서 Brier skill > **0** 이고 ROC AUC > **0.520**

### NO_SIGNAL
그 외

---

## 14. Trading gate

**PATH 계열에서 STRONG_SIGNAL이 최소 하나 나와야만 BTC-P2 = AUTHORIZED.**

P2에서 처음으로 Probability → Expected Value → cost → LONG/SHORT/HOLD를 연결한다.
**P1에서 거래 금지, 규칙 생성 금지, sizing 금지, leverage 금지.**

STRONG_SIGNAL이 없으면 P2로 가지 않는다.

---

## 15. 복리 목표와 분리

"100만원 → 수십억" 같은 compounding 목표를 모델 PASS 기준으로 쓰지 않는다.
순서는 prediction edge → trading edge → risk → leverage → compounding이다.
**레버리지로 예측력 부족을 보정하지 않는다.**

---

## 16. Determinism

| 항목 | 값 |
|---|---|
| seed | 20260928 |
| 무작위성 | 없음 (subsampling·shuffle·난수 초기화 전부 미사용) |
| data order | 시간 오름차순 고정 |
| 재현 조건 | 동일 contract + 동일 데이터 → 동일 결과 |

---

## 17. 보고 규칙

| # | 규칙 |
|---|---|
| R1 | **사전등록한 28개 target 전부 보고.** 좋은 것만 고르기 금지 |
| R2 | feature importance를 보고 feature를 바꾼 뒤 같은 validation을 최종 성과로 제시 금지 |
| R3 | 좋은 target 하나를 찾으려고 target을 추가 생성 금지 |
| R4 | 결과를 보고 threshold 수정 금지 |
| R5 | best config를 nested validation 없이 최종 OOS라고 부르지 않는다 |
| R6 | 모델 2개 × primary 14 target = 28회 비교이므로 **selection-bias 경고를 보고서에 명시** |

---

## 18. 금지 (운영)

| 항목 |
|---|
| commit / push |
| production server |
| systemd |
| frontend / UI 구현 |
| Paper engine |
| AUTO |
| liquidation forward collector |

이번 작업 전부 로컬.

---

## 19. Phase

| phase | 내용 |
|---|---|
| **Phase 1** | 이 계약 동결 + sha256 기록. 동결 전 허용: class frequency, feature availability, missing rate, target sample counts. **금지: AUC·Brier·Log Loss·확률 성능·PnL** |
| **Phase 2** | dataset build → folds → M0 → M1 → M2 → calibration → metrics |

Phase 2 실행 시 contract sha256을 대조하고 불일치하면 `CONTRACT_HASH_MISMATCH`로 중단한다.
