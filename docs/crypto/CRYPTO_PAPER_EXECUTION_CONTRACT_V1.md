# US-B CRYPTO PAPER 체결 계약 V1

작성 2026-09-23. **구현하지 않는다. 계약만 정한다.**
이 문서를 만족하지 않는 PAPER 엔진 구현은 버그다.

`CRYPTO_REUSE_MATRIX_V1.md` 5절이 판정한 대로, 주식 `SimBroker`는 재사용하지 않는다.
long-only, next-bar, 레버리지·funding·청산 없음, `Currency`에 USDT 없음. 7개 요구사항 전부가 빠져 있다.

---

## 1. 계좌

| # | 규칙 |
|---|---|
| A1 | 시작 자본 = **KRW 1,000,000 equivalent** |
| A2 | 내부 회계 통화는 **USDT** (정산통화가 USDT) |
| A3 | KRW 환산 방식은 `CRYPTO_V0_RESEARCH_CONTRACT_V1.md` U1 **미결**. 권고는 시작 시점 1회 고정 |
| A4 | 주식 `Currency` enum(KRW/USD)을 쓰지 않는다. USDT가 없다 |
| A5 | 계좌 상태의 authority는 하나다: `equity`, `available_margin`, `used_margin`, `realized_pnl`, `unrealized_pnl` |
| A6 | 불변식: `equity = starting_capital + realized_pnl + unrealized_pnl - cumulative_cash_charges` |

---

## 2. 방향

| # | 규칙 |
|---|---|
| S1 | **LONG과 SHORT를 모두 지원한다.** 이것이 주식 `SimBroker`와의 가장 큰 차이다 |
| S2 | 포지션은 **부호 있는 수량**으로 표현한다. LONG > 0, SHORT < 0, FLAT = 0 |
| S3 | `unrealized_pnl`은 부호를 반영한다: `(mark - avg_entry) * signed_qty`. 주식 `unrealized_pnl`은 long 전용이라 쓰지 않는다 |
| S4 | LONG과 SHORT를 동시에 보유하지 않는다 (one-way mode). hedge mode는 범위 밖 |

---

## 3. 레버리지와 마진

| # | 규칙 |
|---|---|
| L1 | `leverage`는 **파라미터**다. 코드에 상수로 박지 않는다 |
| L2 | 허용 범위는 instrument metadata를 따른다: `minLeverage=1`, `maxLeverage=150`, `leverageStep=0.01` |
| L3 | **선물은 notional이 아니라 margin이 묶인다.** 주식 `fill_cash_flow`(BUY가 notional 전액 차감)는 쓰지 않는다 |
| L4 | `initial_margin = notional / leverage` |
| L5 | `available_margin`이 요구 마진보다 작으면 주문을 거부한다 |
| L6 | 명목 하한을 지킨다: `minNotionalValue = 5 USDT`. 미달 주문은 거부 |
| L7 | 수량 격자 `qtyStep = 0.001`, 최소 `minOrderQty = 0.001`. 격자 밖 수량은 거부하거나 내림한다(어느 쪽인지 구현 시 명시) |
| L8 | 시장가 수량 상한 `maxMktOrderQty = 150`. 초과분 처리 규칙을 명시한다 |

---

## 4. 주문 타입 추상화

| # | 규칙 |
|---|---|
| O1 | `MARKET`과 `LIMIT`을 추상화한다 |
| O2 | **market LONG(매수)은 ask 쪽에서 체결된다** |
| O3 | **market SHORT(매도)은 bid 쪽에서 체결된다** |
| O4 | **동일 가격 낙관 체결을 금지한다.** mid나 last로 시장가를 체결시키면 안 된다 |
| O5 | 시장가 체결은 호가창 **depth를 소모**한다. 주문 수량이 best level 수량을 넘으면 다음 level로 넘어간다 (walk the book) |
| O6 | 지정가는 **가격이 닿아야** 체결된다. 닿았다는 판정은 반대편 호가가 지정가를 통과했을 때다 |
| O7 | 지정가 체결에 queue position을 가정하지 않는다. 낙관적 maker 체결을 금지한다 |
| O8 | 체결 결과는 항상 `fill_price`, `fill_qty`, `fee`, `fill_ts_ms`를 남긴다 |

### 왜 O4가 중요한가

실측 spread는 전 구간 **0.1 = 정확히 1 tick**이었다. 즉 BTCUSDT는 spread가 매우 좁다.
그래서 "mid로 체결해도 별 차이 없다"는 유혹이 생긴다. 그러나
- spread가 좁은 것은 **관측 시점의 사실**이고 변동성 급등 구간에서는 벌어진다
- 낙관 체결은 거래 빈도가 높을 때 누적 편향이 된다
- 주식 쪽에서 `execution_price`가 spread를 **bps 추정치로 대체**했는데, crypto는 실호가가 있으므로
  추정을 쓸 이유가 없다

---

## 5. 비용

주식 `accounting.py`의 비용 분류 체계를 계승한다. 이 구분이 없으면 비용을 두 번 차감한다
(메모리에 `net_pnl` 이중차감 사고 기록).

| 비용 | 분류 | 처리 |
|---|---|---|
| spread | `PRICE_EMBEDDED` | 체결가가 이미 반영. **다시 차감하지 않는다** |
| slippage | `PRICE_EMBEDDED` | 같음 |
| fee (taker/maker) | `CASH_SEPARATE` | 명목에 더해 현금에서 **정확히 한 번** 차감 |
| **funding** | `CASH_SEPARATE` | **주식에 없는 새 항목.** 아래 6절 |

| # | 규칙 |
|---|---|
| C1 | `PRICE_EMBEDDED`와 `CASH_SEPARATE`를 반드시 구분해 기록한다 |
| C2 | `net_pnl = gross_pnl - cash_charges`. `gross_pnl`은 체결가 기준 |
| C3 | 분석용 비용 표시(spread_cost, slippage_cost 등)는 `ANALYTICS_ONLY`다. **PnL에서 차감하지 않는다.** 가격에 이미 들어갔다 |
| C4 | 수수료는 taker/maker를 구분한다. 시장가는 taker |
| C5 | **수수료율은 파라미터다.** 계정이 없으므로 실제 수수료 등급을 알 수 없다. 가정값임을 명시 기록한다 |
| C6 | 사이징과 체결이 **같은 수수료 원본**을 읽는다. bps 산술을 두 곳에서 다시 쓰지 않는다 |
| C7 | 회계 authority는 한 곳이다. 한 비용을 두 곳에서 차감하지 않는다 |

---

## 6. Funding

주식에 대응 개념이 전혀 없는 항목이다.

| # | 규칙 |
|---|---|
| FN1 | 정산 주기 **8시간**, UTC **00:00 / 08:00 / 16:00**. 상장 이후 불변(실측) |
| FN2 | 정산 시각에 포지션을 보유하고 있으면 funding을 주고받는다. **보유하지 않으면 발생하지 않는다** |
| FN3 | 부호 규칙: `funding_rate > 0`이면 **LONG이 지불하고 SHORT가 받는다.** 반대면 역방향 |
| FN4 | 금액 = `funding_rate * position_notional` (정산 시각의 명목) |
| FN5 | funding은 `CASH_SEPARATE`다. 현금에서 차감/가산되며 체결가에 반영하지 않는다 |
| FN6 | funding 이벤트는 **개별 원장 항목으로 기록한다.** PnL에 뭉쳐 넣으면 성과 분해가 불가능하다 |
| FN7 | 백테스트는 실제 `funding` history를 쓴다. 추정하지 않는다 |
| FN8 | **funding cap을 현재값으로 과거에 적용하지 않는다.** 2020년 실측 `-0.00375`가 현재 한도 `-0.00333`을 넘는다 |
| FN9 | 실시간 경로에서 다음 정산 시각은 `tickers.nextFundingTime`으로 안다 |

---

## 7. PnL

| # | 규칙 |
|---|---|
| P1 | `realized_pnl`: 청산된 수량의 체결가 기준 손익 |
| P2 | `unrealized_pnl`: 보유 수량의 **mark price** 기준 평가손익. `(mark - avg_entry) * signed_qty` |
| P3 | **평가에는 last price가 아니라 mark price를 쓴다.** 청산 판정이 mark 기준이므로 일관해야 한다 |
| P4 | mark price는 `tickers.markPrice`(실시간) 또는 `mark-price-kline`(과거)에서 얻는다 |
| P5 | `equity = available + used_margin + unrealized_pnl` 형태의 불변식을 세우고 매 시점 대조한다 |
| P6 | 모든 PnL은 `Decimal`. 대조 허용오차는 `Decimal("0.000001")`에서 출발 |
| P7 | 원장(ledger)이 authority다. 집계값은 원장에서 파생되며, 원장과 불일치하면 집계가 틀린 것이다 |

---

## 8. 청산 시뮬레이션 (필수 요구사항)

| # | 규칙 |
|---|---|
| Q1 | **청산 시뮬레이션은 필수다.** 없으면 레버리지 성과가 전부 거짓이 된다 |
| Q2 | 청산 판정은 **mark price** 기준이다. last price가 아니다 |
| Q3 | 유지마진율(maintenance margin rate)이 필요하다. **공개 API로 정확한 티어 구조를 아직 확인하지 않았다. UNKNOWN** |
| Q4 | Q3이 미해결이므로 **구현 전에 `/v5/market/risk-limit`을 확인해야 한다.** D2 선결 과제 |
| Q5 | 청산 발생 시 포지션은 강제 종료되고 마진이 소실된다. 이 사건은 원장에 **별도 유형**으로 기록한다 |
| Q6 | 청산을 "큰 손실"로 근사하지 않는다. 실제 청산 가격 모델이 필요하다 |
| Q7 | 청산이 한 번이라도 발생한 run은 **성과 지표와 별도로 청산 횟수를 반드시 보고한다.** 청산을 숨긴 수익률은 무의미하다 |
| Q8 | risk-of-ruin 계산의 입력이 청산이다 (`CRYPTO_METRICS_AND_GATE_V1.md`) |

---

## 9. PAPER / LIVE adapter 경계

| # | 규칙 |
|---|---|
| B1 | 전략·신호·리스크·회계는 PAPER와 LIVE에서 **동일 코드**를 쓴다. 분리하지 않는다 (AGENTS.md 3절 절대규칙 1~3 계승) |
| B2 | **Execution Adapter만 분리한다** (AGENTS.md 절대규칙 3) |
| B3 | 전략은 broker 구현을 직접 참조하지 않는다. interface 뒤에 둔다 (절대규칙 4~5) |
| B4 | **LIVE adapter는 구현하지 않는다.** `CRYPTO_V0_RESEARCH_CONTRACT_V1.md` F7 |
| B5 | adapter 경계에는 주문 제출·취소·조회만 둔다. 회계는 경계 안쪽(공통)이다 |
| B6 | PAPER adapter는 **주문 endpoint를 절대 호출하지 않는다.** 네트워크로 나가는 주문 경로가 존재하지 않아야 한다 |

---

## 10. 검증 요구사항

구현 시 반드시 만족해야 한다.

| # | 요구 |
|---|---|
| T1 | 결정성: 같은 입력 같은 config면 같은 산출 바이트 |
| T2 | 회계 대조: 모든 시점에서 A6, P5 불변식 성립 |
| T3 | 비용 이중차감 없음: `PRICE_EMBEDDED` 항목이 PnL에서 다시 차감되지 않음을 테스트로 고정 |
| T4 | LONG/SHORT 대칭: 같은 가격 경로에서 LONG과 SHORT의 부호가 정확히 반대 |
| T5 | funding 부호: `rate > 0`에서 LONG 지불, SHORT 수취 |
| T6 | 낙관 체결 금지: market LONG 체결가가 **best_bid 이하로 나오지 않음** |
| T7 | 청산 경로: 청산이 발생하는 시나리오 테스트가 존재 |
| T8 | differential: 같은 입력을 두 경로로 돌린 산출이 바이트 일치 (주식 parity 방법론 계승) |

---

## 11. 미해결 (구현 전 필수)

| # | 항목 | 상태 |
|---|---|---|
| X1 | 유지마진율 티어 구조 | **UNKNOWN.** `/v5/market/risk-limit` 확인 필요 |
| X2 | taker/maker 수수료율 가정값 | **미정.** 계정 없어 실제 등급 불가. 가정 명시 필요 |
| X3 | KRW 환산 방식 | **UNRESOLVED** (U1) |
| X4 | `qtyStep` 미달 수량: 거부 대 내림 | **미정** |
| X5 | `maxMktOrderQty` 초과 처리 | **미정** |
| X6 | 지정가 체결 판정의 정확한 조건 (touch 대 trade-through) | **미정** |

X1은 청산 시뮬레이션의 입력이고 청산은 필수 요구사항이므로, **X1 해결 없이 PAPER 엔진을 구현하면
안 된다.**
