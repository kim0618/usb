# US-B CRYPTO 성과 지표와 판정 게이트 V1

작성 2026-09-23. **지표의 정의를 고정한다. PASS 임계값은 고정하지 않는다.**

---

## 1. 이 문서가 숫자를 정하지 않는 이유

**PASS threshold를 지금 발명하지 않는다.** D1 결과 이후 별도 사전등록(preregistration) 단계에서 동결한다.

근거:
- 아직 crypto 연구 결과가 **0건**이다. 분포를 모르는 상태에서 임계값을 쓰면 그 숫자는 근거가 없다
- 지표를 먼저 계산하고 임계값을 나중에 정하면 **결과를 보고 기준을 맞추는 것**이 된다.
  따라서 임계값은 **첫 연구 실행 전에** 별도 문서로 동결하고 checksum을 남긴다
- 메모리에 기록된 주식 쪽 절차와 같다: 사전등록 -> 동결 -> 실행 -> 판정

이 문서가 하는 일은 **무엇을 계산할지와 어떻게 계산할지**를 고정하는 것이다.

---

## 2. 계산 authority 규칙

주식 `app/backtest/baseline/analytics.py`의 docstring이 정한 authority를 계승한다
(코드는 재사용하지 않는다. import 148개 모듈).

| # | 규칙 |
|---|---|
| AU1 | **원장(ledger)이 authority다.** 모든 집계는 원장에서 파생된다 |
| AU2 | Net PnL과 Total Return은 `ending_equity` 대 시작 자본에서 계산한다 |
| AU3 | 청산 손익은 원장의 포지션 종료 이벤트에서 읽는다 |
| AU4 | **equity curve와 drawdown은 체결 시퀀스가 아니라 equity 시계열에서 계산한다** |
| AU5 | 사유(reason)는 코드가 쓴 문자열 그대로 보존한다. **본 적 없는 사유는 자기 이름으로 세고, 이웃에 접어 넣지 않으며, 없으면 `NONE`으로 센다** |
| AU6 | 집계 코드를 바꾸면 집계 버전을 올린다 |
| AU7 | 값을 알 수 없으면 `UNKNOWN`이다. 0으로 채우지 않는다 |
| AU8 | 모든 money 지표는 `Decimal`. 대조 허용오차 `Decimal("0.000001")`에서 출발 |

### 24/7 시장에서 equity curve의 격자

주식은 `SESSION_CLOSE` equity를 썼다. **crypto에는 세션이 없다.**

- equity 샘플 격자를 **명시적으로 정해야 한다.** 후보: UTC 일 마감(00:00), funding 정산 시각(00/08/16), 1시간
- **미정.** 사전등록 단계에서 확정한다
- 격자 선택이 MDD와 Sharpe를 바꾸므로 **아무 값이나 고르면 안 된다.** 선택 근거를 기록한다

---

## 3. 향후 평가 항목

### 3.1 거래 기반

| 지표 | 정의 |
|---|---|
| `trades` | 종료된 포지션 수 |
| `win_rate` | `net_pnl > 0`인 거래 / 전체 거래 |
| `avg_win` | 승리 거래의 `net_pnl` 평균 |
| `avg_loss` | 패배 거래의 `net_pnl` 평균 (부호 유지) |
| `expectancy` | `win_rate * avg_win + (1 - win_rate) * avg_loss` |
| `profit_factor` | 총이익 / 총손실 절대값. 총손실 0이면 `UNKNOWN`(무한 아님) |
| `longest_losing_streak` | 연속 패배 최대 길이 |
| `longest_winning_streak` | 연속 승리 최대 길이 |

### 3.2 손익 분해

| 지표 | 정의 |
|---|---|
| `gross_pnl` | 체결가 기준 손익. `PRICE_EMBEDDED` 비용이 이미 반영됨 |
| `net_pnl` | `gross_pnl - cash_charges` |
| `fees` | `CASH_SEPARATE` 수수료 합 |
| `slippage` | **`ANALYTICS_ONLY` 표시값.** PnL에서 차감하지 않는다. 가격에 이미 들어갔다 |
| `spread_cost` | 같음. `ANALYTICS_ONLY` |
| `funding` | funding 순수지 합. **별도 항목으로 반드시 분리한다** |
| `total_return` | `net_pnl / starting_capital` |

**`funding`을 분리하는 이유**: funding은 방향과 보유시간에 비례하는 구조적 비용/수익이다.
신호의 성과와 섞으면 "전략이 돈을 벌었는가"와 "funding을 받았는가"를 구분할 수 없다.
LONG 편향 전략이 음의 funding 구간에서 수익을 내는 것은 신호의 성과가 아니다.

### 3.3 위험

| 지표 | 정의 |
|---|---|
| `MDD` | equity 시계열의 최대 낙폭. 격자는 2절 참조 |
| `max_drawdown_duration` | 최대 낙폭 회복까지 경과 시간 |
| `sharpe` | 수익률 시계열 기준. **연율화 계수를 명시한다.** crypto는 365일이며 주식의 252일이 아니다 |
| `sortino` | 하방 편차 기준. 같은 연율화 규칙 |
| `liquidation_count` | **청산 발생 횟수. 0이 아니면 반드시 별도 보고한다** |
| `risk_of_ruin` | 자본 소진 확률 추정. 입력은 청산 모델 |

| # | 규칙 |
|---|---|
| RK1 | **Sharpe/Sortino 연율화 계수는 365를 쓴다.** 주식 252를 쓰면 crypto에서 틀린다. 24/7이므로 휴장일이 없다 |
| RK2 | 표본이 작으면 Sharpe/Sortino를 **보고하지 않는다.** "where appropriate"의 의미다. 최소 표본 수를 사전등록에서 정한다 |
| RK3 | `liquidation_count > 0`인 run의 수익률은 **청산 횟수와 함께만** 보고한다. 청산을 숨긴 수익률은 무의미하다 |
| RK4 | MDD는 **격자 의존적이다.** 어떤 격자로 계산했는지 항상 함께 기록한다 |

### 3.4 레버리지별

| # | 규칙 |
|---|---|
| LV1 | `return`, `MDD`, `risk_of_ruin`을 **레버리지별로 분해해 보고한다** |
| LV2 | 레버리지를 올렸을 때 return이 선형으로 늘고 MDD도 선형으로 늘면 그것은 정보가 아니다. **비선형 구간(청산이 개입하는 지점)이 관심 대상이다** |
| LV3 | 메모리 기록 계승: 주식 E-MAX M5에서 **15bp부터 MDD 한도를 초과**했다. 레버리지 스윕도 한도 초과 지점을 찾는 것이 목적이다 |
| LV4 | 레버리지 스윕은 **Edge tuning이 아니다.** 같은 신호의 위험 특성을 측정하는 것이다. 신호 파라미터 탐색과 혼동하지 않는다 |

### 3.5 Score bucket calibration

| # | 규칙 |
|---|---|
| SC1 | Score를 쓰는 전략이 생기면 **bucket별 실현 성과를 보고한다** |
| SC2 | calibration은 "score가 높은 bucket이 실제로 더 좋은가"를 묻는다. 단조성이 깨지면 score가 작동하지 않는 것이다 |
| SC3 | bucket 경계는 **사전에 정한다.** 결과를 보고 경계를 옮기면 안 된다 |
| SC4 | bucket별 거래 수를 함께 보고한다. 메모리 기록 함정: 상위 3건이 성과의 96%를 만드는 집중 사례가 있었다 |
| SC5 | **D0/D1에 Score는 존재하지 않는다.** 이 절은 향후 규약이다 |

### 3.6 MANUAL 대 AUTO 분리

| # | 규칙 |
|---|---|
| MA1 | **MANUAL과 AUTO의 성과를 반드시 분리 집계한다** |
| MA2 | 원장의 각 거래는 어느 모드에서 생성되었는지 기록한다 |
| MA3 | `AUTO_STOPPING`에서 종료된 거래는 **진입 시점 모드(AUTO)로 귀속한다.** 종료 시점 상태가 아니다 |
| MA4 | 두 모드를 합친 지표도 보고하되, **합친 값만 보고하지 않는다** |
| MA5 | 모드가 불명인 거래는 `UNKNOWN`으로 센다 (AU7) |

MA3의 이유: AUTO가 진입 결정을 했으므로 성과는 AUTO의 것이다. 종료 시점으로 귀속하면
AUTO OFF를 누르는 타이밍이 성과 귀속을 바꿔 버린다. 메모리 기록: 주식에서 재진입
lifecycle 오귀속 사고가 있었다.

---

## 4. 집중도 진단 (필수)

메모리에 반복 기록된 실패 패턴이다. 주식에서 여러 번 같은 함정에 빠졌다.

| # | 규칙 |
|---|---|
| CN1 | **상위 N개 거래가 총손익에서 차지하는 비율을 반드시 보고한다** |
| CN2 | 단일 거래가 증분의 과반을 만들면 `OVERLY_DEPENDENT`로 표시한다. 메모리 기록: O1 robustness에서 **1건이 증분의 89%** 였다 |
| CN3 | 상위 3건이 성과의 대부분이면 `HIGHLY_CONCENTRATED`로 표시한다. 메모리 기록: P0가 여전히 그랬고, V2 baseline top3가 37.8%였다 |
| CN4 | 집중도 표시가 붙은 결과는 **PASS로 판정하지 않는다.** 임계값과 무관하게 별도 게이트다 |
| CN5 | 시간 집중도도 본다. 특정 기간에 손익이 몰리면 표시한다 |

---

## 5. 판정 라벨

임계값은 아직 없지만 **라벨 체계는 지금 고정한다.**

| 라벨 | 의미 |
|---|---|
| `PASS` | 사전등록된 임계값을 만족 |
| `PASS_WITH_LIMITATIONS` | 만족하지만 명시된 제약이 있음 |
| `BORDERLINE` | 일부 지표만 미달 |
| `INCONCLUSIVE_STATISTICAL` | 표본 부족 또는 신뢰구간 하한이 0을 포함 |
| `FAIL` | 임계값 미달 |
| `NO_MEANINGFUL_EDGE` | 통계적으로 유의하나 실질 크기가 무의미 |
| `OVERLY_DEPENDENT` | 소수 거래 의존 (CN2) |
| `HIGHLY_CONCENTRATED` | 집중도 과다 (CN3) |
| `BLOCKED` | 선결 조건 미해결로 판정 불가 |
| `UNKNOWN` | 계산 불가 |

`INCONCLUSIVE_STATISTICAL`과 `NO_MEANINGFUL_EDGE`를 분리하는 이유: 주식 쪽에서 이 둘을 구분하지
않아 "유의하지만 쓸모없는" 결과를 반복 검토했다. 메모리 기록 E-D6, E-R3, M2, M4가 그 사례다.

---

## 6. 사전등록 단계에서 동결해야 하는 것

이 문서가 미룬 항목이다. **첫 연구 실행 전에** 별도 문서로 동결하고 checksum을 남긴다.

| # | 항목 |
|---|---|
| Z1 | 모든 지표의 PASS 임계값 |
| Z2 | equity 샘플 격자 (2절) |
| Z3 | Sharpe/Sortino 최소 표본 수 (RK2) |
| Z4 | 집중도 임계값의 구체적 수치 (CN2, CN3) |
| Z5 | 연구 구간 시작점 (권고 2021-01-01, 전수 계산 후 확정) |
| Z6 | 레버리지 스윕 값 목록 |
| Z7 | 신뢰구간 계산 방법과 유의수준 |
| Z8 | in-sample 대 out-of-sample 분할 규칙 |
| Z9 | MDD 한도 (LV3 참조) |

---

## 7. 지금 하지 않는 것

| # | 금지 |
|---|---|
| NG1 | PASS threshold 숫자를 발명하기 |
| NG2 | 지표를 계산해 보고 나서 임계값 정하기 |
| NG3 | Edge 전략·Score·AUTO 전략 구현 |
| NG4 | 백테스트 실행 |
| NG5 | 주식 `analytics.py`를 import 하기 (148개 모듈) |
