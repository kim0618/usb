# US-B CRYPTO D6 실행 의미론 해석 기록 I1 V1

작성 2026-09-28. **이 문서는 D6-A 계약을 수정하지 않는다.** 동결 계약
`CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md` (sha256 `1a6bcaa5…a622cc0d`)은 그대로이고,
이 문서는 그 계약의 두 조항이 만나는 지점에서 생기는 모호성을 **별도 기록으로** 해소한다.

D6-C 실행 시 **D6-A 계약 해시와 이 문서의 해시를 둘 다 검증한다.**

---

## 1. 모호성

계약의 두 조항이 함께 읽히면 서로 다른 구현을 낳는다.

| 조항 | 내용 |
|---|---|
| §6.1 Entry | 최초 합법 진입 = **`open[t+1]`** (다음 bar 시가) |
| §8 Position size | `qty = floor_to_step(notional / **entry_price**, 0.001)` |
| §3 PIT / 검증계획 §4 P1 | **bar t의 결정은 t 이전에 마감된 bar만 본다** |

`entry_price`를 `open[t+1]`로 읽으면 §6.1과는 맞지만 §3과 충돌한다. 수량 산출이 `open[t+1]`에
의존하고, 계약 §8의 마지막 줄이 `if qty < 0.001 or notional < 5 USDT: HOLD`이므로,
**미래 bar 하나가 LONG을 HOLD로 뒤집을 수 있다.** 크기가 아니라 **신호**가 미래를 본다.

`entry_price`를 `close[t]`로 읽으면 §3은 지켜지지만 §6.1이 말한 진입가와 다른 가격으로 수량을 정한다.

D6-B는 임시로 후자(`close[t]`)를 택하고 이 항목을 **I1으로 올려 확인을 요청했다.**

---

## 2. 채택 해석: Decision / Execution 분리

**둘 중 하나를 고르는 문제가 아니다.** 계약이 한 시점에 뭉쳐 놓은 두 가지 일을 분리한다.

### 2.1 Decision time `t` (bar t 종가 직후)

사용 가능한 정보: **`t`까지의 데이터뿐.**

| 산출 | 내용 |
|---|---|
| hard filters | 13개 판정 |
| score | LONG_SCORE와 4 category 내역 |
| 방향 | **LONG / HOLD** |
| `target_notional` | `equity x 0.005 / stop_distance`, `equity x 1.0` 상한. **가격이 필요 없다** |
| `stop_distance` | `clip(2.5 x f_rv24h x sqrt(240), 0.01, 0.10)`. `f_rv24h`는 t 데이터 |
| strategy intent | `ENTRY_INTENT_LONG` 또는 없음 |

**`open[t+1]` 사용 0.** 최종 `qty`는 decision 산출물이 아니다.

`target_notional`이 가격 없이 계산된다는 점이 이 해석의 핵심이다. 계약 §8의 분자는 `equity x 0.005`이고
분모는 `stop_distance`이며, 가격은 그 다음 줄에서 **명목을 수량으로 바꿀 때만** 등장한다.

### 2.2 Execution time `t+1`

LONG intent가 이미 확정된 뒤에만 일어난다.

| 단계 | 내용 |
|---|---|
| 기준가 | `reference_price = open[t+1]` (계약 §6.1의 최초 합법 진입가) |
| 수량 | `final_qty = floor_to_step(target_notional / reference_price, 0.001)` |
| 주문 | MARKET taker. 엔진이 호가를 소모해 체결 |
| 실제 체결가 | `actual_entry_price` = 엔진이 돌려준 평균 체결가 (LONG은 ask 쪽) |
| 손절가 | `stop_price = actual_entry_price x (1 - stop_distance)` |

`t+1` 가격은 **score·LONG/HOLD·과거 hard filter 판정을 바꾸지 않는다.**

### 2.3 EXECUTION_REJECT

실행 시점 안전 제약이 걸리면 **신호를 HOLD로 다시 쓰지 않는다.** 별도 결과로 기록한다.

| 코드 | 조건 |
|---|---|
| `EXECUTION_REJECT_QTY_BELOW_MIN` | `final_qty < 0.001` |
| `EXECUTION_REJECT_NOTIONAL_BELOW_MIN` | `final_qty x reference_price < 5 USDT` |
| `EXECUTION_REJECT_SAFE_MAX` | `final_qty > SAFE MAX` (과거 호가 없음 → D6-C에서 평가 불가) |
| `EXECUTION_REJECT_ENGINE` | 엔진이 주문을 거부(증거금·위험한도·유동성) |

거부된 실행은 **거래가 아니다.** 성과에 들어가지 않고, 건수를 따로 보고한다.
쿨다운(H8)은 거부에서 시작하지 않는다(청산이 없었으므로).

---

## 3. 왜 이 해석인가

### 3.1 PIT 근거

PIT는 D6 전체에서 가장 강한 조항이다. 계약 §3, 검증계획 §4 P1~P7, D6-B의 PIT 테스트 10개가 모두 그 위에
서 있다. **신호가 미래를 보면 D6-C의 모든 숫자가 무의미해진다.** 반면 §6.1의 `open[t+1]`은 *체결*에 관한
규정이고, 체결은 정의상 결정 이후에 일어난다. 두 조항이 충돌할 때 PIT가 이긴다.

Decision/Execution 분리는 충돌을 **해소한다**: 결정은 `t`만 보고, 체결은 §6.1대로 `open[t+1]`에서 난다.
어느 조항도 희생되지 않는다.

### 3.2 실거래 근거

실제 운용 순서와 같다.

1. 화면의 신호를 보고 **진입하기로 정한다**. 이때 정하는 것은 "자본의 0.5%를 걸겠다"는 **위험 크기**다.
2. 주문을 낸다.
3. **체결가는 그 뒤에 알게 된다.** 수량은 주문 직전의 호가로 계산하고, 손절은 체결가에서 잡는다.

"체결가를 알아야 수량을 정할 수 있다"는 순환이고, 실거래에는 그런 순환이 없다.
`target_notional`을 먼저 고정하는 것이 그 순환을 끊는 유일한 방법이다.

### 3.3 회계 근거

위험예산의 정의가 보존된다. `target_notional x stop_distance = equity x 0.005`이므로,
손절이 맞으면 자본의 0.5%를 잃는다. 이 등식은 **명목을 먼저 정할 때만** 정확하다.
가격으로 나눈 뒤 수량을 내림하면 등식이 한 스텝만큼 깨지는데, 내림은 항상 크기를 줄이므로
실제 손실은 예산 **이하**다. 방향이 안전한 쪽이다.

---

## 4. 기각한 해석

### 4.1 `qty = target_notional / close[t]` (D6-B의 임시 해석)

**기각.** PIT는 지키지만 계약 §6.1이 명시한 진입가와 다른 가격으로 수량을 정한다. 그 차이만큼
`target_notional x stop_distance = equity x 0.005` 등식이 깨지고, 그 오차가 `close[t]`와 `open[t+1]`의
차이에 비례한다. 갭이 큰 순간(= 이 전략이 진입하는 순간)에 오차가 가장 커진다는 점에서 나쁜 성질이다.

D6-B가 이것을 택한 이유는 "결정이 사이징 가능 판정을 통해 미래를 볼 수 있다"였고,
**이 문서의 2.1절이 그 경로 자체를 없앴으므로**(최종 qty가 decision 산출물이 아님) 더는 필요하지 않다.

### 4.2 `qty = target_notional / open[t+1]`를 decision 안에서 계산

**기각.** §3 위반. 미래 bar가 신호를 뒤집는다.

### 4.3 사이징 불가 시 HOLD로 기록

**기각.** 프롬프트 §1과 이 문서 2.3절대로 `EXECUTION_REJECT`다.
HOLD로 쓰면 "신호가 없었다"와 "신호는 있었지만 못 넣었다"가 한 통계에 섞인다.

---

## 5. 결정에 미치는 영향

| 항목 | 영향 |
|---|---|
| hard filter 13개 판정 | **없음.** 전부 `t` 데이터 |
| LONG_SCORE, category | **없음** |
| LONG / HOLD | **없음.** 사이징이 더는 방향을 바꾸지 않는다 |
| `target_notional` | 가격 없이 결정 시점에 확정 |
| `stop_distance` | 결정 시점에 확정. **진입 후 미래 변동성으로 넓히지 않는다** |

D6-B 엔진의 `decision.decide()`는 `entry_reference_price`가 주어지면 사이징 실현가능성을 HOLD 사유로
쓴다. **D6-C는 그 경로를 쓰지 않는다**: `entry_reference_price=None`으로 호출해 사이징을 실행 단계로
미루고, 신호 판정은 `filter_pass` + score + 필수조건 + separation으로만 읽는다.
그 동치성은 `test_d6c_i1.py`가 검사한다.

---

## 6. 실행에 미치는 영향

| 항목 | 영향 |
|---|---|
| 진입 기준가 | `open[t+1]` (계약 §6.1 그대로) |
| 최종 수량 | `floor_to_step(target_notional / open[t+1], 0.001)` |
| 체결 | MARKET taker, 엔진이 합성 호가를 소모. LONG은 ask |
| 손절가 | **실제 체결가** 기준 |
| 안전 제약 실패 | `EXECUTION_REJECT` (신호는 LONG으로 남음) |
| 성과 | 거부된 실행은 거래에 포함되지 않음. 건수를 별도 보고 |

---

## 7. 이 해석이 D6-A 계약과 충돌하지 않음

| 계약 조항 | 이 해석에서의 상태 |
|---|---|
| §3 PIT | **지켜짐.** 결정은 `t`만 본다 |
| §6.1 진입 = `open[t+1]` | **지켜짐.** 체결이 거기서 난다 |
| §8 `qty = notional / entry_price` | **지켜짐.** `entry_price = open[t+1]`, 다만 decision이 아니라 execution에서 |
| §8 `if qty < 0.001 or notional < 5: HOLD` | **문구만 다름.** HOLD 대신 `EXECUTION_REJECT`로 기록. 거래가 발생하지 않는다는 결과는 같고, 통계에서 구분된다 |
| §10 손절 | **지켜짐.** `stop_distance`는 t 정보로 확정, 진입가에 적용 |
| §7 R1 위험예산 0.5% | **지켜짐.** 명목을 먼저 정하므로 등식이 정확 |

유일하게 문구가 다른 것은 §8의 마지막 줄이고, 그 차이를 7절 표에 명시했다. **조용히 바꾸지 않았다.**

---

## 8. 동결

| 항목 | 값 |
|---|---|
| 이 문서 | `docs/crypto/CRYPTO_D6_EXECUTION_INTERPRETATION_I1_V1.md` |
| 기계본 | `data/research/crypto/d6/execution_interpretation_i1_v1.json` |
| 동결 기록 | `data/runtime/crypto/d6/i1_freeze_v1.json` |
| D6-A 계약 | 수정 0. 해시 불변 |

D6-C 실행기는 **D6-A 계약 해시와 이 문서의 해시를 둘 다** 재계산해 동결값과 다르면 실행을 거부한다.
