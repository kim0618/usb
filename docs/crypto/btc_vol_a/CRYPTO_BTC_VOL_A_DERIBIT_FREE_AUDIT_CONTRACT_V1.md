# CRYPTO BTC-VOL-A DERIBIT FREE AUDIT CONTRACT V1

Preregistration. 동결 2026-09-28, **BTC 옵션의 IV·프리미엄을 하나도 보기 전.**

**주문 0, API key 0, 계좌 0, 유료 데이터 0, 배포 0, commit 0.**

---

## 1. 질문 (하나만)

> **P1 high-confidence 시점의 실제 Deribit ATM IV와 straddle 가격이
> VOL-P0가 쓴 proxy(직전 실현변동성)보다 충분히 낮았는가?**

VOL-P0는 IV를 재지 않고 "IV ≈ 직전 실현변동성"이라는 **매수자에게 유리한 가정**으로
`ECONOMICALLY_UNPROMISING`을 냈다. 이번 단계는 그 가정이 실제로 보수적이었는지만 확인한다.

**새 전략을 만들지 않는다. early-exit을 계산하지 않는다.**

---

## 2. 데이터 출처

| 항목 | 값 |
|---|---|
| URL 형식 | `https://datasets.tardis.dev/v1/deribit/options_chain/{YYYY}/{MM}/{DD}/OPTIONS.csv.gz` |
| 인증 | **없음** |
| 무료 범위 | **각 달의 1일만** |

**무료 범위는 Tardis API 자신이 답한 문구로 확인했다**(2026-09-28, 다른 날짜 요청 시 HTTP 401):

> `"For unauthorized requests, only historical CSV market datasets for the first day of each month are available (dataset for '2023-05-02' has been requested)."`

| 확인 | 내용 |
|---|---|
| 2023-05-01 plain GET | HTTP 200, 1.31 GB |
| 2023-05-02 plain GET | **HTTP 401** + 위 문구 |
| 2022-07-01 plain GET | HTTP 200, 1.13 GB, 56 MB/s |
| HEAD / range 요청 | **Cloudflare 403.** plain GET만 사용한다 |

### 2.1 스키마 (동결 전 확인)

```
exchange, symbol, timestamp, local_timestamp, type, strike_price, expiration,
open_interest, last_price, bid_price, bid_amount, bid_iv, ask_price, ask_amount,
ask_iv, mark_price, mark_iv, underlying_index, underlying_price,
delta, gamma, vega, theta, rho
```

§4가 요구한 필드가 **전부 있다.** `timestamp`는 **마이크로초**. 하루 약 2,780만 행.

**Deribit BTC 옵션은 BTC로 호가된다.** 따라서 `bid_price`·`ask_price`·`mark_price`는
**기초자산 대비 비율 그 자체**이며, 별도 환산이 필요 없다.

---

## 3. 날짜

| 항목 | 값 |
|---|---|
| P1 검증 구간 | 2022-01-01 ~ 2026-09-22 |
| 그 안의 무료 날짜 | **57** (2022-01-01 ~ 2026-09-01, 매달 1일) |

---

## 4. P1 threshold (사후 선택 금지)

BTC-P1이 사전등록한 confidence threshold만 쓴다: 0.60 / 0.65 / 0.70 / 0.75.

| 등급 | 값 | 근거 |
|---|---|---|
| **PRIMARY** | **p >= 0.75** | VOL-P0의 `HIGH_STATE`와 **정확히 동일**. 그래야 비교가 성립한다 |
| SECONDARY | p >= 0.70, p >= 0.60 | 표본 크기 때문에 병기 |

`P_LARGE_MOVE = max(P_UP, P_DOWN)`, 출처는 P1 fold별 validation 예측(`p_m2_cal`). **READ-ONLY.**

---

## 5. 조합 선택 (동결 전, 표본 수 근거)

무료 57일에 떨어지는 P1 high-confidence 신호를 **IV를 보기 전에** 세었다.

| 조합 | p>=0.60 | p>=0.70 | **p>=0.75** |
|---|---|---|---|
| 4H ±1% | 7 ep | 4 ep | **1 ep** |
| **12H ±1%** | 67 ep | 24 ep | **9 ep (31시간, 6개 날짜)** |
| 12H ±2% | 4 ep | 2 ep | **0 ep** |
| 24H ±2% | 27 ep | 5 ep | **5 ep (6시간, 3개 날짜)** |

| 등급 | 조합 | 근거 |
|---|---|---|
| **PRIMARY** | **12H ±1%** | p>=0.75에서 유일하게 두 자릿수에 근접(9 episodes, 6개 날짜, 2022~2026 분산) |
| 병기 | 4H ±1%, 24H ±2% | 측정 가능하면 보고 |
| 제외 | 12H ±2% | p>=0.75 신호 **0개** |

**이 선택은 성능이 아니라 표본 수에 근거하며 §29가 동결 전에 허용한 관측이다.**

---

## 6. 옵션 매칭 규칙 (전부 결과 보기 전 고정)

### 6.1 대상

`symbol`이 `BTC-`로 시작하는 행만. ETH 등 제외.

### 6.2 Quote timing (PIT)

| 규칙 | 값 |
|---|---|
| 사용 가능한 quote | `timestamp <= t` 인 것 중 **가장 늦은 것** |
| stale 허용치 | **300초** |
| 초과 시 | 그 event는 `NOT_MEASURABLE` |
| **t 이후 quote** | **사용 금지** |

### 6.3 Expiry 선택

| 규칙 | 값 |
|---|---|
| 조건 | `time_to_expiry >= horizon + buffer` |
| **buffer** | **2시간** (만기 근접 왜곡 회피) |
| 선택 | 조건을 만족하는 것 중 **가장 가까운 만기** |

12H horizon이면 TTE >= 14시간인 가장 가까운 만기.
**Deribit 최단 만기가 일간(08:00 UTC)이므로 TTE가 horizon보다 크게 길 수 있다.**
실제 TTE 분포를 반드시 보고한다.

### 6.4 ATM 선택

| 규칙 | `abs(strike_price - underlying_price)` **최소** |
|---|---|

delta 기준을 쓰지 않는다. 결과를 보고 strike를 바꾸지 않는다.

### 6.5 Call/Put 짝

같은 만기·같은 strike의 call과 put **둘 다** 있어야 한다. 하나라도 없으면 `NOT_MEASURABLE`.

### 6.6 품질 필터 (사전 고정)

| 조건 | 처리 |
|---|---|
| `bid_price <= 0` 또는 `ask_price <= 0` | 해당 leg 무효 → `NOT_MEASURABLE` |
| `ask_price < bid_price` (crossed) | `NOT_MEASURABLE` |
| `mark_iv` 결측 또는 <= 0 | IV 비교에서 제외 |
| `underlying_price <= 0` | `NOT_MEASURABLE` |
| 중복 (같은 symbol·timestamp) | 마지막 행 채택 |

---

## 7. 계산 (사전 고정)

### 7.1 IV 집계

| 항목 | 정의 |
|---|---|
| `atm_mark_iv` | **call `mark_iv`와 put `mark_iv`의 단순 평균** |
| `atm_ask_iv` | call `ask_iv`와 put `ask_iv`의 단순 평균 |

vega 가중을 쓰지 않는다. 두 leg가 같은 ATM strike라 vega가 거의 같다.
Tardis의 IV는 **백분율 표기**(예: 45.99 = 45.99%)로 가정하고, 첫 실행에서 크기를 확인해 기록한다.

### 7.2 Straddle 가격

| 항목 | 정의 |
|---|---|
| `straddle_ask` | `call.ask_price + put.ask_price` (**매수자가 실제로 내는 값**) |
| `straddle_mid` | `(call.bid+call.ask)/2 + (put.bid+put.ask)/2` |
| `straddle_mark` | `call.mark_price + put.mark_price` |
| `spread_pct` | `(straddle_ask - straddle_mid) / straddle_mid` |

Deribit 호가가 BTC 단위이므로 이 값들이 **기초자산 대비 비율 그 자체**다.

### 7.3 Implied move (두 방식 모두)

| 방식 | 식 |
|---|---|
| (a) straddle 기반 | `straddle_ask` (이미 비율) |
| (b) IV 기반 | `0.7978845608 × atm_ask_iv × sqrt(TTE_years)` |

두 값이 크게 다르면 그 사실을 보고한다.

---

## 8. 비교 (사전 고정)

### 8.1 Check A - tenor 정규화

같은 timestamp의 VOL-P0 proxy(직전 24h 실현변동성을 연율화)를 불러와 비교.

| 값 | 12H ±1% p>=0.75 기준 |
|---|---|
| VOL-P0 proxy IV | **103.5%** |
| VOL-P0 향후 실현 | 86.4% |
| **VOL-P0 손익분기 IV** | **86.5%** |

핵심: **`atm_ask_iv` vs 86.5%.**

### 8.2 Check B - 만기 일치 (**결정적**)

TTE가 horizon과 다르므로 프리미엄을 직접 비교하면 불공정하다.
**실제 TTE로 맞춘 비교를 primary로 한다.**

| 값 | 정의 |
|---|---|
| 비용 | `straddle_ask` (실제 TTE, 실제 ask) |
| 수익 | `abs(close[t + TTE] / close[t] - 1)` - **같은 TTE 구간의 endpoint 이동** |
| **edge** | **수익 - 비용** |

`close`는 D2 연구 그리드에서 **READ-ONLY**로 읽는다.

참고로 함께 보고: 같은 TTE 구간의 **max absolute excursion**.

---

## 9. 수수료

Deribit 공식 옵션 수수료를 확인해 기록한다. **임의 가정하지 않는다.**
확인 실패 시 `UNKNOWN`으로 두고 **수수료 전 결과만** 판정 근거로 쓴다.

수수료 전 결과가 이미 음수이면 그 사실을 강조한다.

---

## 10. 판정 게이트 (동결)

**PRIMARY 조합(12H ±1%) × PRIMARY threshold(p>=0.75) × Check B 기준.**

판정 순서: INCONCLUSIVE → CONFIRMS_UNPROMISING → EARLY_EXIT_WORTH_STUDYING.

### INCONCLUSIVE
- 측정 가능한 event가 **5개 미만**, 또는
- event의 **50% 초과**가 quote 부재/품질 실패

### CONFIRMS_UNPROMISING
- Check B의 **median edge <= 0** (ask로 산 straddle이 같은 TTE endpoint 이동을 못 넘김)

### EARLY_EXIT_WORTH_STUDYING
**둘 다** 충족:
- Check B의 **median edge > 0**
- **median(max absolute excursion over TTE) > median(straddle_ask)**

**이것은 early-exit 연구를 "검토할 가치"가 있다는 뜻이며 전략 승인이 아니다.**

---

## 11. 이번 단계 금지

| 항목 |
|---|
| 새 ML 모델, P1 재학습, threshold 변경 |
| **early-exit 계산** (5분/15분/30분/최적 exit 전부) |
| strike·expiry 사후 선택, PnL mining |
| 실주문, Paper, production, server deploy, commit/push |
| `btc_p1`·`btc_p1_forward`·`btc_p2`·`btc_vol_p0`·`manual_*`·`liquidation_forward` 수정 |
| 유료 데이터 구매 |

---

## 12. 표본 한계 (미리 명시)

| # | 한계 |
|---|---|
| 1 | 무료 데이터가 **매달 1일**에만 있다. 시장 전체를 대표하지 않는다 |
| 2 | PRIMARY 표본이 **9 episodes / 6 날짜**다. 통계적 확정이 아니다 |
| 3 | 특정 월초에 몰린 selection bias 가능 |
| 4 | TTE가 horizon보다 길어 term structure 영향이 섞인다 |

**표본이 작으면 평균만 보고 결론 내리지 않는다.** event-level 표를 전부 보고한다.

---

## 13. Freeze

이 문서의 sha256을 기록하고 실행 시 대조한다. 불일치하면 `CONTRACT_HASH_MISMATCH`로 중단.

동결 전 허용된 관측: 파일 존재, 스키마, 날짜 수, signal count.
동결 전 금지: **IV, premium, PnL, realized comparison.** 하나도 보지 않았다.
