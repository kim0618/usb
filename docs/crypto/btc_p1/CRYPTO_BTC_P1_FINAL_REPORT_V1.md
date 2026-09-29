# CRYPTO BTC-P1 FINAL REPORT V1

계약 §45의 41항목 순서. 2026-09-28. 미커밋.

---

## 1. Git

커밋 0, push 0. 전부 untracked 신규 경로.

| 경로 | 상태 |
|---|---|
| `backend/app/crypto/research/btc_p1/` | 신규 9파일 |
| `backend/tests/crypto/test_btc_p1.py` | 신규 |
| `docs/crypto/btc_p1/` | 신규 5문서 |
| `data/research/crypto/btc_p1/` | 신규 5산출물 |
| `backend/app/crypto/{paper,terminal,server}` | **무변경** |

## 2. 방법론 변경

| 기존 (D5~D5.5) | BTC-P1 |
|---|---|
| 조건 → 즉시 LONG/SHORT | 조건 → 확률 |
| 15m~4h | 4h / 12h / 24h |
| binary 방향 | UP·DOWN 독립 multi-label |
| accuracy·net PnL | Brier·calibration·lift |
| prediction+execution+cost 혼합 | **prediction만** |

거래를 뺀 이유: 여섯 번의 실패에서 edge가 어디서 사라졌는지 구분되지 않았다.

## 3. Contract hash

`7ee69572cfe04081652ddeab6543371f0bbfa196b302721922e0fddb48857a76`
Phase 2 시작 시 대조, 실행 후 재대조 모두 일치.

## 4. Data window

| 항목 | 값 |
|---|---|
| 그리드 | 2021-02-01 ~ 2026-09-22, 2,966,400분, **결측 0** |
| 결정 시점 | 2021-03-03T00:00Z ~ 2026-09-21T23:00Z, 정시 간격 **48,696행** |
| 검증 | 2022-01-01 ~ 2026-09-22, **41,400행** |
| 출처 | D2 canonical 5종, `research/dataset.py` 무변경 재사용 |
| **holdout 지위** | **untouched 아님.** D5·D5.1·D5.2·D6가 이미 본 구간 |

## 5. Target 정의

PRIMARY = path-touch(horizon 안에 터치), SECONDARY = endpoint(종료 시점).
기준가 `close[t]`, forward window `t+1..t+H`. 불완전 horizon은 **NaN**(0 아님).

4H ±0.50/1.00%, 12H ±1.00/2.00%, 24H ±1.00/2.00/3.00% → PATH 14 + ENDPOINT 14 = **28**.

## 6. Base rate

PATH 22.6%~62.7%, ENDPOINT 10.1%~30.3%. 검증 구간 최소 양성 **3,619건**.
→ §31의 표본 요건(>=500)을 28개 전부 충족, **INCONCLUSIVE 0건**.

## 7. Folds

expanding walk-forward 9개, train 시작 고정 2021-03-03.
**embargo 1,440분**을 train 종료·validation 시작 사이와 train 내부 80/20 경계 양쪽에 적용.

| fold | train (model/calib) | valid |
|---|---|---|
| F1 | 7,272 (5,793 / 1,455) | 4,344 |
| F5 | 24,792 (19,809 / 4,959) | 4,368 |
| F9 | 42,336 (33,844 / 8,468) | 6,336 |

## 8. Feature families

34컬럼 6family. 결정 행 48,696개 전부 **결측 0**.
F1 가격경로 10 / F2 변동성 6 / F3 OI 5 / F4 basis 4 / F5 funding 3 / F6 시간·regime 6.

## 9. M0 baseline

fold별 train base rate 상수. pooled로는 9개 값을 가져 **AUC 0.522~0.556**
(전역 상수가 아니므로 Brier skill 비교가 보수적).

## 10. M1

Ridge logistic, robust z(train fit), L2=1.0, IRLS 30회. **numpy 직접 구현.**

## 11. M2

Histogram GBDT 200트리·depth3·lr0.05·leaf200·bins32·L2 1.0, subsample 없음,
early stopping 없음, **hyperparameter search 없음**. **numpy 직접 구현.**

의존성 결정: sklearn·LightGBM·XGBoost·scipy가 **설치도 선언도 안 됨**.
§13 "새 대규모 dependency 도입 금지"를 지켜 직접 구현.
**사전 검증**: 심어둔 비선형 구조에서 M2 skill +0.214 / AUC 0.768 > M1 +0.057 > M0.
순수 잡음에서 skill -0.007 / AUC 0.492.

## 12. Target별 Brier

전체 표는 `CRYPTO_BTC_P1_RESULTS_V1.md` §1. 최저 Brier 0.104(24H_DOWN_300_EP),
최고 0.237(24H_UP_100).

## 13. Brier skill

**28개 전부 양수.** 최고 +0.1801(4H_DOWN_100 M2), 최저 +0.0152(24H_DOWN_200_EP M2).
PATH 평균이 ENDPOINT보다 뚜렷이 높다.

## 14. Log loss

STRONG 6개 0.30~0.58 범위, M0 대비 전부 개선.

## 15. ROC AUC

최고 **0.7273**(4H_DOWN_100 M2). PATH 0.598~0.727, ENDPOINT 0.553~0.683.

## 16. PR AUC

base rate 의존이 커 단독 해석 불가. 4H_UP_050 0.658(base 0.484),
4H_DOWN_100 0.444(base 0.241), 24H_DOWN_300_EP 0.104(base 0.087).

## 17. Calibration

isotonic(PAV), train 뒤 20%에서 fit, validation 무접촉.
**STRONG 6개 전부에서 ECE가 raw 대비 절반 이하로 내려감**(0.031→0.015 등).

> **정정**: 중간 점검에서 fold 5만 보고 "보정이 ECE를 악화시킨다"고 기록했으나
> pooled 기준으로는 반대다. 단일 fold를 전체로 일반화한 오류였다.

## 18. Calibration buckets

4H_DOWN_100은 0.0~0.6 구간에서 예측과 실제가 **1~3%p 안에서 일치**.
0.9-1.0 버킷은 19행에서 실제 68%로 과신(표본 부족).
24H_UP_100은 낮은 확률에서 크게 틀림(예측 0.016 → 실제 0.434) → slope 0.433으로 S4 탈락.

## 19. High-confidence bucket counts

| target | p>=0.65 n | p>=0.75 n |
|---|---|---|
| 4H_UP_050 | 6,525 | 2,075 |
| 4H_DOWN_100 | 241 | 73 |
| 12H_DOWN_100 | 5,394 | - |

**lift가 큰 곳일수록 표본이 얇다.**

## 20. Lift

4H_DOWN_100: p>=0.65에서 **2.78**, p>=0.75에서 **3.29**.
4H_UP_050: 1.51 / 1.63 (base가 높아 lift는 작으나 표본 두꺼움).
24H_UP_100: 1.19 / 1.28.

## 21. Probability separation

`P(DOWN)-P(UP)` 최상위 10분위의 실제 결과:

| pair | down-heavy DOWN/UP | up-heavy DOWN/UP |
|---|---|---|
| 4H_050 | 0.520 / 0.469 | 0.488 / 0.501 |
| 12H_100 | 0.517 / 0.449 | 0.414 / 0.442 |
| **24H_100** | **0.612 / 0.618** | 0.558 / 0.560 |

최선 3~7%p, **24H_100은 부호가 반대**.

## 22. 4H 결과

**가장 강하다.** 4개 target 전부 STRONG. skill +0.145~+0.180, AUC 0.685~0.727.

## 23. 12H 결과

±1.00% 2개 STRONG(skill +0.113~+0.135), ±2.00% 2개 WEAK(slope 0.65~0.72로 S4 탈락).

## 24. 24H 결과

**6개 전부 WEAK.** skill은 +0.054~+0.106으로 양수지만 **calibration slope 0.43~0.57**로
S4를 넘지 못했다. 24시간은 이 feature set의 범위 밖이다.

## 25. UP vs DOWN

DOWN이 UP보다 근소하게 높다(4H_DOWN_100 +0.180 vs 4H_UP_100 +0.165).
**단 이는 각 방향을 따로 볼 때의 이야기이고 §31의 방향 판별과는 다른 질문이다.**

## 26. Year stability

STRONG 6개 전부 **5/5 연도 양수**. 2023 최강(0.25~0.31), 2024 최약(0.06~0.12).

## 27. Fold stability

STRONG 6개 중 5개가 **9/9 양수**, 12H_DOWN_100 M1만 8/9(fold 6 -0.009).

## 28. Regime stability

변동성 regime별 skill이 **LOW > MID > HIGH**로 일관(4H_DOWN_100: 0.323 / 0.153 / 0.110).
→ **skill의 큰 몫이 "조용할 것이다"를 맞힌 것**이며, 쉬운 쪽 절반이다.

## 29. Feature importance

F2(변동성)가 나머지 다섯 family 합의 **3~10배**.
F1 경로 0.001 수준, F3 OI·F4 basis·F5 funding은 **0이고 음수 칸도 있다**.
D5 계열에서 단독 실패한 축들이 **context로서도 값이 없었다.**

## 30. Failure targets

NO_SIGNAL 0건. 실질적 실패는 **24H 계열 6개 전부**로, 원인은 skill 부족이 아니라
**calibration slope(0.43~0.57)** 이다.

## 31. Strongest target

**4H_DOWN_100 (M2)**: skill +0.1801, AUC 0.7273, ECE 0.0149, slope 0.824,
fold 9/9, year 5/5, p>=0.75 lift 3.29.

## 32. Strongest model

**M2가 28개 중 25개에서 M1을 이겼다**(M1 우세는 24H_DOWN_100, 24H_UP_300_EP, 24H_DOWN_300_EP).
다만 격차는 작고(전형 +0.01~+0.02), **M2 우위의 대부분도 변동성 축에서 나온다.**

## 33. Selection-bias warning

모델 2개 × PATH 14개 = **28회 비교**, target 판정은 더 좋은 모델 채택.
귀무가설 아래서도 28개 중 최선은 개별 추정보다 좋아 보인다.
S6·S7이 이를 어렵게 하지만 제거하지 못한다. **제거하는 것은 forward shadow뿐이다.**
다만 6개가 fold 9/9·year 5/5로 통과한 것을 단일 우연으로 설명하기는 어렵다.

라벨 중첩(결정 1시간 간격 vs horizon 4~24시간)으로 유효 독립 표본은 41,400보다 훨씬 적다.
**p-value를 계산하지 않았다.**

## 34. 판정 집계

| 판정 | PATH | ENDPOINT | 전체 |
|---|---|---|---|
| STRONG_SIGNAL | **6** | 0 | 6 |
| WEAK_SIGNAL | 8 | 14 | 22 |
| NO_SIGNAL | 0 | 0 | 0 |
| INCONCLUSIVE | 0 | 0 | 0 |

## 35. BTC-P2 authorization

### 계약 규칙대로: **AUTHORIZED**

§14 "PATH에서 STRONG 최소 하나"를 6개가 충족. **게이트를 사후 수정하지 않았다**(§17 R4).

### 그러나 게이트가 연구 질문을 측정하지 못했다

§1의 질문은 "**어느 방향으로**"였다. 게이트 S1~S7은 방향별 target을 **각각** 평가하므로
UP·DOWN을 똑같이 밀어올리는 변동성 모델이 양쪽을 통과시킨다.

**사후 진단(`direction_diagnostic_v1.json`, 게이트 무관):**

| pair | 변동성 AUC | **방향 AUC** |
|---|---|---|
| 4H_050 | 0.826 | **0.518** |
| 4H_100 | 0.784 | **0.533** |
| 12H_100 | 0.786 | **0.519** |
| 24H_100 | 0.786 | **0.502** |
| 24H_300 | 0.693 | **0.515** |

**큰 움직임이 온다는 것은 예측된다. 어느 쪽인지는 예측되지 않는다.**

§33이 그린 P2(`P(DOWN)=62% / P(UP)=13% → SHORT`)에 필요한 분리가 실측에 없다.
방향 AUC 0.51에서 왕복 11bp를 빼면 D5.5와 같은 자리다.

**P2 착수 여부는 사용자 결정이다.** 계약은 승인했고 데이터는 방향이 없다고 말한다.
둘을 임의로 합치지 않는다.

## 36. Manual-R2 future integration

이번 모델에 사용자 거래 라벨 **미사용**(§34 준수).
MANUAL-R1.1에서 사용자 확정 거래가 완료 1건뿐으로 확인되어 비교 표본이 없다.
향후 R2가 `reason_code`·`conviction`을 쌓으면
"사용자 HIGH SHORT 시점의 model DOWN probability" 비교가 가능해진다.

## 37. Liquidation future integration

liqfwd 데이터 **미사용, 접근 0**(§35 준수).
수집 시작 2026-09-27T10:35:04Z, 게이트 6개월+30이벤트.
축적 후 P1 base vs P1+liquidation을 **forward-only**로 비교한다.

## 38. Tests

`backend/tests/crypto/test_btc_p1.py` **59개**.

| 영역 | 내용 |
|---|---|
| windows | brute force 대조, forward가 다음 봉부터 시작, 불완전 구간 NaN, shift 역방향 전용 |
| targets | path vs endpoint 구분, 정확히 터치한 경우, 불완전 horizon NaN, UP/DOWN 동시 발생, **결정 봉 자체를 읽지 않음** |
| PIT | **미래 행 추가해도 과거 feature 불변**, scaler train 전용 |
| folds | expanding·무중첩, **embargo = 최대 horizon**, calibration 경계도 embargo |
| models | 결정성, 확률 범위, **심은 구조 복원**, **잡음에서 무신호**, leaf 최소치 |
| calibration | 단조성, **알려진 오보정 복구**, 빈 slice no-op |
| metrics | 수기 계산 AUC 대조, 상수 예측 0.5, 보정선 slope 1 복원, 과신 탐지 |
| gates | 7조건, 표본 검사 우선, fold·year 일관성 탈락 |
| direction | 변동성/방향 분리, 정렬 불일치 거부, **게이트 무관 확인** |
| contract | 문자열 결속, 해시 대조, 수정된 계약 거부 |

**crypto 전체 회귀: 1,084 passed, 0 failed** (저장소 루트에서 실행).
`backend/`에서 돌리면 상대경로 때문에 무관한 8건이 error가 난다.

## 39. Git status

커밋 0. push 0. 운영 코드 무변경.

## 40. Risks / limitations

| # | 한계 |
|---|---|
| 1 | **untouched holdout 아님.** true OOS는 forward shadow에서만 |
| 2 | **방향 예측력 없음**(AUC 0.502~0.533). 발견된 것은 변동성 예측기 |
| 3 | skill의 큰 몫이 LOW vol 구간의 "조용할 것이다" |
| 4 | 라벨 중첩으로 유효 표본이 41,400보다 훨씬 적음. p-value 없음 |
| 5 | 28회 비교의 selection bias |
| 6 | M1·M2가 직접 구현. 합성 검증은 통과했으나 성숙한 라이브러리와 동일하지 않음 |
| 7 | family importance가 마지막 fold(6,336행)만. family 간 상관으로 F1의 0이 "무정보 증명"은 아님 |
| 8 | 24H 계열은 calibration slope 0.43~0.57로 확률로 신뢰 불가 |
| 9 | **비용 미적용.** 거래 가능성은 전혀 검증되지 않음 |
| 10 | **계약 문서에 em dash 3개**(221·225·233행). 전 프로젝트 스타일 규칙 위반이나 **sha256 동결 후라 수정하면 사전등록이 무효**가 되어 그대로 둔다. 동결 전에 잡았어야 할 누락 |

## 41. Next exact step

**사용자 결정 대기. 자동 진행하지 않는다.**

| 선택지 | 내용 |
|---|---|
| **A** | **BTC-P2 착수** - 계약상 승인됨. 단 방향 AUC 0.51이므로 LONG/SHORT 규칙은 성립 불가. P2를 "방향" 대신 "변동성 기반 구조"로 재정의해야 함 |
| **B** | **P1.5: 방향 전용 재설계** - target을 방향 판별(한쪽만 터치)로 바꾸고 feature를 방향 정보 중심으로 다시 사전등록 |
| **C** | **forward shadow 시작** - 현 모델을 그대로 동결해 미래 구간에서만 검증. untouched holdout 문제를 유일하게 푸는 길 |
| **D** | **종결** - 일곱 번째 연구에서도 방향은 없었다는 것으로 마감 |

**내 권고**: C를 A·B와 **병행**. 지금 확보한 것(보정된 변동성 예측기)은
일곱 번 만에 처음으로 게이트를 통과한 결과이지만, 그 구간은 이미 여섯 번 본 데이터다.
forward shadow는 비용이 거의 없고, 없으면 이 결과의 지위가 끝내 확정되지 않는다.

**B는 실패 가능성이 높다고 본다.** F1(가격 경로, 방향 정보를 담을 수 있는 유일한 family)의
importance가 0.001이었고, 이는 방향 AUC 0.51과 독립적으로 같은 말을 한다.
그래도 시도한다면 **새 정보원**(liquidation forward, 오더북)이 쌓인 뒤가 맞다.
