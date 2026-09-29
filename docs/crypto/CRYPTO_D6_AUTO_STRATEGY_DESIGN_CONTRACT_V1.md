# US-B CRYPTO D6-A AUTO 전략 설계 사전등록 계약 V1

작성 2026-09-28. **이 문서는 계약이다.** 이 문서의 sha256을 `data/runtime/crypto/d6/contract_freeze_v1.json`에
기록한 뒤에는 바꾸지 않는다. 바꾸려면 V2를 새로 만든다. **D6-B 구현체는 실행 시 이 파일의 sha256을 다시
계산해 동결값과 다르면 실행을 거부하도록 설계한다.**

근거 종합은 `CRYPTO_D6_CANDIDATE_RATIONALE_V1.md`, 검증 설계는 `CRYPTO_D6_VALIDATION_PLAN_V1.md`,
기계가 읽는 사본은 `data/research/crypto/d6/strategy_contract_v1.json`(이 문서와 동치이며 불일치 시 **이 문서가
정본**).

---

## 0. 범위와 금지

| # | 규칙 |
|---|---|
| Z1 | 거래 대상은 **Bybit BTCUSDT Linear Perpetual 1종목**. 다른 종목·거래소 주문 없음 |
| Z2 | 입력은 D2 authoritative 5종(1m kline·mark·index, OI 5m, funding)뿐. D5·D5.1과 같은 파일·같은 checksum |
| Z3 | **청산(liquidation) 데이터는 V1 feature로 쓰지 않는다.** 분류 `FORWARD_ONLY / FUTURE_V2` |
| Z4 | **maker / LIMIT 진입을 쓰지 않는다.** 진입·청산 전부 taker(MARKET). 근거 AOA E1 |
| Z5 | **ML·LLM을 판단에 쓰지 않는다.** 결정은 결정론적 규칙이다. 같은 입력이면 같은 결정 |
| Z6 | 이번 단계에서 전략 실행 코드·백테스트·주문 0. 운영 서버·운영 Paper 계좌·liqfwd 수집기 접근 0 |
| Z7 | commit/push 금지 |
| Z8 | D6-C 결과를 본 뒤 이 문서의 threshold·weight·horizon·게이트 숫자를 옮기지 않는다. 옮기려면 V2 사전등록 |

### 동결 전에 계산한 것 (전수 기록)

수익률·PnL·손익 관련 계산은 **0건**이다. 계산한 것은 feature 값과 표본 수뿐이며, 전량
`data/runtime/crypto/d6/prefreeze_qc_v1.json`에 남겼다.

| 계산 | 목적 | 결과가 계약에 미친 영향 |
|---|---|---|
| 조건별 진입 bar 비율·episode 수·fold별 분포 | **표본 적정성** 판단 | 변동성 조건을 HIGH 하드필터에서 "LOW 배제"로 바꿈(4.3절). threshold 50 채택 |
| feature 간 Spearman (E3·A1·OI1h·RV24h) | 중복 확인 | 최대 \|ρ\| 0.116. 네 성분을 독립 category로 유지 |
| 진입 시점 24h 실현변동성 분포 | **손절 폭·수량 산식** 설계 | k=2.5 채택 시 손절 거리 중앙 3.72% |

---

## 1. 후보 목록

| # | 이름 | 방향 | 상태 |
|---|---|---|---|
| **CAND-1** | `FORCED_DELEVERAGING_NORMALIZATION` (FDN) | LONG only | **설계 완료. D6-B 구현 대상** |
| CAND-2 | Relative-Value Dislocation | - | **기각.** 근거 문서 5절(basis 재포장 ρ 0.72~0.83, 2025~2026 부호 반전) |
| CAND-3 | Trend Confirmation | - | **기각.** 근거 문서 5절(fold 방향 4~7/9, 연도별 부호 반전) |

**최종 구현 대상은 CAND-1 하나다.**

---

## 2. CAND-1 명세

### 2.1 Name
`FORCED_DELEVERAGING_NORMALIZATION_V1` (약칭 FDN-V1)

### 2.2 Market hypothesis

> perp 가격이 기준가(index) 아래로 밀리고, 동시에 미결제약정이 줄고, 변동성이 낮지 않은 순간은
> **정보에 의한 가격 이동이 아니라 레버리지 해소(강제 매도)에 의한 일시적 가격 이탈**일 확률이 상대적으로 높다.
> 강제 매도가 끝나면 가격은 기준가 쪽으로 되돌아간다. 그 되돌림을 4시간 보유로 taker 왕복 비용보다 크게
> 수취할 수 있는가를 묻는다.

관측 가능한 그림자 세 가지를 쓴다: **가격 이탈**(perp가 index보다 쌈), **포지션 축소**(OI 감소),
**강제성**(변동성이 낮지 않음). 청산 데이터가 쌓이면 이 세 가지는 청산 흐름의 대리지표였는지 직접 검증된다(V2).

### 2.3 이 후보가 기각된 D5 셀과 다른 점

| 항목 | 기각된 셀 | FDN-V1 |
|---|---|---|
| 구조 | feature 1개 x bucket 1개 | **조건 교집합**. 근거는 부분 최대 9.66bp < 교집합 30.19bp(C1) |
| 변동성 | 셀 정의에 없음(D5·D5.1) 또는 HIGH 하드필터(D5.2 C1) | **점수 성분**. HIGH 25 / MID 12 / LOW 0. LOW는 하드 배제 |
| 표본 | C1은 OOS episode 455, 판정 fold 4/9 | OOS episode 1,498, 판정 fold 9/9 |
| 판정 | C1은 S8 구조적 불통과(정의에 변동성을 넣고 변동성 견고성을 요구) | 변동성이 하드조건이 아니므로 **변동성 견고성 검사를 통과할 수 있다** |
| 방향 선택 | - | LONG 고정. 결과를 보고 뒤집지 않는다 |

### 2.4 Required data

| 입력 | 원천 | 해상도 | PIT 가용 |
|---|---|---|---|
| perp 종가 `C` | D2 Bybit kline 1m | 1m | bar 종가 시각 |
| index 종가 `IDX` | D2 Bybit index 1m | 1m | bar 종가 시각 |
| `OI` | D2 open_interest 5m | 5m | **기록 시각 + 5분** (D2 규칙) |
| funding rate | D2 funding | 8h | 정산 시각 |
| 호가(실시간 경로만) | Bybit `orderbook.50` | 실시간 | 수신 시각 |

**D6-C(historical replay)에는 실제 호가가 없다.** `paper/historical.py`의 합성 호가(spread 0.10 = 1틱,
깊이 5 BTC/측, `book_synthetic: true` 스탬프)를 쓰고, 결과 문서에 반드시 "합성 호가 기반"이라고 적는다.

---

## 3. FEATURE (동결, 4개)

기호: `C[t]` = 1m 종가, `IDX[t]` = index 1m 종가, `OI[t]` = PIT 규칙으로 bar t에 알려진 OI,
`r1[t] = ln(C[t]/C[t-1])`. 모든 창은 bar t를 포함해 과거로 센다. NaN은 0으로 채우지 않는다.

| id | 이름 | 공식 | 출처 | lookback |
|---|---|---|---|---|
| `f_basis` | perp-index 괴리 | `ln(C[t] / IDX[t])` | D5 계약 6절 E3 `fund_premium` **글자 그대로** | 0 |
| `f_oi1h` | OI 1시간 로그변화 | `ln(OI[t] / OI[t-60])` | D5 계약 6절 D1 `oi_chg_60` **글자 그대로** | 60 (+5분 지연) |
| `f_drop1h` | 1시간 수익률 | `ln(C[t] / C[t-60])` | D5 계약 6절 A1 `trend_ret_60` **글자 그대로** | 60 |
| `f_rv24h` | 24시간 실현변동성 | `std(r1[t-1439..t])`, ddof 0 | D5 계약 10절 변동성 regime 산식 **글자 그대로** | 1,440 |

**새로 만든 feature는 0개다.** 네 개 전부 기존 동결 계약에서 공식을 그대로 가져왔다. 이것이 "실패한 feature에
이름만 바꿔 새 전략이라고 부르기"를 막는 장치다. 이름이 바뀌었을 뿐 계산은 동일하며, 대응 관계를 위 표에 적었다.

### 3.1 Bucket (동결)

`f_basis`, `f_oi1h`, `f_drop1h`는 D5 계약 5절 규칙을 그대로 쓴다: UTC 날짜 D의 cutoff는 `[D-30일, D)` 값의
**10 / 30 / 70 / 90 분위**, 유효값 50% 미만이면 NaN, `searchsorted(side="right")`.
B1(0-10) / B2(10-30) / B3(30-70) / B4(70-90) / B5(90-100).

**새 bucket 경계 0개. threshold sweep 0회.**

### 3.2 변동성 regime (동결)

`f_rv24h`의 cutoff는 **D5.1 계약 8절이 이미 동결한 값을 그대로 쓴다**(F1 train = 2021-03-04 ~ 2022-01-01의
30/70 분위). 새로 계산하지 않는다.

| 라벨 | 조건 |
|---|---|
| LOW | `f_rv24h` < **7.592730942805516e-4** |
| MID | 그 사이 |
| HIGH | `f_rv24h` > **1.0924950359531157e-3** |

---

## 4. HARD FILTER (조건 미충족 시 거래 금지)

점수와 **분리한다.** 아래는 점수에 넣지 않고, 하나라도 걸리면 그 시각의 결정은 무조건 `HOLD`다.

| id | 조건 | 값 | 근거 |
|---|---|---|---|
| H1 `DATA_STALE` | 마지막 1m bar 종료 후 경과 시간 | > **90초** 이면 차단 | 1m bar 주기의 1.5배 |
| H2 `OI_STALE` | 마지막 OI 레코드 기록 시각 후 경과 | > **15분** 이면 차단 | OI 5m 주기의 3배 |
| H3 `INSUFFICIENT_HISTORY` | bucket cutoff 유효 표본 | 30일 창의 50% 미만이면 차단 | D5 계약 5절과 동일 |
| H4 `FEATURE_NAN` | 네 feature 중 하나라도 NaN | 차단 | 0으로 채우지 않는다 |
| H5 `VOL_LOW` | 변동성 라벨 = LOW | 차단 | 근거 문서 2절 P3 |
| H6 `SPREAD_WIDE` | (실시간 경로) `(ask-bid)/mid` | > **5bp** 이면 차단 | D5 8절 실측 p50 0.012bp의 400배. 비정상 호가 차단용 |
| H7 `DEPTH_SHORT` | (실시간 경로) 주문 수량이 SAFE MAX 초과 | 차단 | D4.1 `sizing.max_entry()` 결과를 상한으로 |
| H8 `COOLDOWN` | 직전 청산 후 경과 | < **60분** 이면 차단 | 표본 독립성. episode 정의와 동일 |
| H9 `POSITION_OPEN` | AUTO 포지션 보유 중 | 신규 진입 차단 | 동시 1포지션 |
| H10 `DAILY_LOSS_GUARD` | 당일(UTC) AUTO 실현손익 | ≤ **-2.0% (자본 대비)** 이면 당일 차단 | 7절 |
| H11 `CONSECUTIVE_LOSS` | 연속 손실 거래 | **4회** 이상이면 24시간 차단 | 7절 |
| H12 `FUNDING_WINDOW` | 다음 funding 정산까지 | < **5분** 이면 진입 차단 | 진입 직후 정산 지불을 피함. 결과가 아니라 구조로 정한 값 |
| H13 `LEDGER_DIVERGENCE` | 원장 복구 거부·테이프 불일치 | 즉시 EMERGENCY_STOP | `persistence.RecoveryRefused` |

H6·H7은 실시간 경로 전용이다. D6-C historical replay에는 실제 호가가 없으므로 **평가하지 않고, 평가하지
않았다고 결과에 기록한다.**

---

## 5. SCORE 구조 (동결)

### 5.1 원칙

- **가중치를 과거 PnL로 고르지 않는다.** 4개 category **동일 가중(각 25점)**이다.
- 각 성분은 연속값이 아니라 **순서형 3단계(0 / 12 / 25)**다. 미세 조정 여지를 없애기 위함이다.
- 합계 100점.

### 5.2 LONG_SCORE

| category | 배점 | 입력 | 방향 | 정규화 | 25점 | 12점 | 0점 | 경제적 근거 |
|---|---|---|---|---|---|---|---|---|
| **Market Structure** | 0-25 | `f_basis` | 낮을수록 LONG | 30일 rolling 분위 | B1 (하위 10%) | B2 (10-30%) | 그 외 | perp가 index보다 쌈 = 매도 압력이 기준가를 이탈시킴 |
| **Positioning** | 0-25 | `f_oi1h` | 낮을수록 LONG | 30일 rolling 분위 + 부호 | B1 (하위 10%) | `f_oi1h` < 0 | 그 외 | OI 감소 = 포지션 청산 진행 중 |
| **Volatility / Exhaustion** | 0-25 | `f_rv24h` | 높을수록 LONG | D5.1 동결 cutoff | HIGH | MID | LOW | 강제성의 대리지표. **LOW는 H5로 이미 차단** |
| **Price Exhaustion** | 0-25 | `f_drop1h` | 낮을수록 LONG | 30일 rolling 분위 | B1 (하위 10%) | B2 (10-30%) | 그 외 | 직전 1시간에 실제로 밀렸는가 |

`LONG_SCORE = MarketStructure + Positioning + Volatility + PriceExhaustion` (0-100)

### 5.3 SHORT_SCORE

**`SHORT_DISABLED_FOR_V1`.** 대칭 정의(`f_basis` B5, `f_oi1h` B5, `f_drop1h` B5)는 문서에 남기되
**V1에서 계산하지 않고 진입하지 않는다.**

근거: 세 연구 전부 SHORT 쪽 증거가 약하다. D5 WEAK LONG 25 vs SHORT 4, D5.1 25 vs 3, D5.2 73 vs 24.
D5.2에서 SHORT 최선인 S1-B5-240m-SHORT는 gross +4.43bp에 CI가 0을 걸쳐 REJECT다. 그리고 C1 형태의
조합 이벤트는 SHORT 쪽으로 정의된 적이 없다. **억지로 양방향으로 만들지 않는다.**

### 5.4 필수 조건 (mandatory minimum)

점수가 높아도 아래를 못 채우면 진입하지 않는다. 총점만 보면 "핵심 아닌 성분으로 점수를 채운" 진입이 생긴다.

| # | 조건 |
|---|---|
| M1 | Market Structure ≥ 12 (`f_basis`가 최소한 하위 30%) |
| M2 | Volatility ≥ 12 (LOW가 아님. H5와 중복이지만 명시) |

### 5.5 진입 threshold (동결)

| 항목 | 값 | 어떻게 정했는가 |
|---|---|---|
| `LONG_ENTRY_MIN_SCORE` | **50** | 100점 척도의 중간값. M1+M2 최소치(24)를 넘으려면 나머지 두 category에서 최소 1개가 25점이어야 한다는 구조적 의미가 있다. **성과로 고르지 않았다.** 37 / 62 / 75에서도 fold별 표본이 충분함을 동결 전에 확인했고(9/9), 그래서 표본이 선택 기준이 되지도 않았다 |
| `SHORT_ENTRY_MIN_SCORE` | 해당 없음 (V1 비활성) | - |
| `SCORE_SEPARATION_MIN` | **10** | LONG_SCORE - SHORT_SCORE ≥ 10 이어야 진입. V1은 SHORT 비활성이라 항상 성립하지만, V2에서 "LONG 51 / SHORT 46"을 LONG으로 만들지 않도록 지금 등록한다 |

### 5.6 HOLD

`HOLD`는 예외가 아니라 **기본 상태**다.

```
if any(hard_filter): return HOLD
if LONG_SCORE < 50:  return HOLD
if M1 or M2 미충족:   return HOLD
if (LONG_SCORE - SHORT_SCORE) < 10: return HOLD
return LONG
```

동결 전 측정: 이 규칙에서 진입 신호가 켜진 bar는 유효 bar의 **4.05%**다. 나머지 95.95%는 HOLD다.
**항상 포지션을 잡는 전략이 아니다.**

---

## 6. ENTRY / EXIT (동결)

### 6.1 Entry

| 항목 | 규칙 |
|---|---|
| 신호 관측 시각 | 1m bar `t`의 **종가 확정 후** (`ts[t] + 60s`) |
| 결정 timestamp | 같은 시각. bar `t` 이전에 마감된 정보만 사용 |
| 최초 합법 진입 | **`open[t+1]`** = 다음 bar 시가. 같은 bar 진입 금지 |
| 주문 타입 | **MARKET (taker)**. LIMIT 금지(Z4) |
| 체결 가격 | LONG은 ask 쪽, 호가 소모(walk the book). 중간가 체결 금지(D3 계약 O2·O4·O5) |
| 수량 | 8절 |
| 레버리지 | 9절 |
| 쿨다운 | 청산 후 60분(H8) |

### 6.2 Exit (후보당 3개로 제한)

| # | 종류 | 규칙 | 근거 |
|---|---|---|---|
| **X1 PRIMARY** | 시간 청산 | 진입 후 **240분(4h)** 경과 시 `open` 가격에 MARKET 청산 | 아래 6.3 |
| **X2 RISK** | 변동성 조정 손절 | mark 가격이 `entry x (1 - 2.5 x sigma_4h)` 이하가 되면 즉시 MARKET 청산 | 10절 |
| **X3 MAX HOLD** | 최대 보유 | 240분. X1과 같다(별도 규칙이 아니라 상한이 primary와 일치함을 명시) | - |

**Exit 규칙은 이 3개뿐이다.** score reversal exit, trailing, 부분 청산, 익절 목표는 **넣지 않는다**.
이유: D5·D5.1·D5.2의 추정량은 전부 "horizon 종점 청산"이고, 여기에 규칙을 더하면 연구 결과와 비교
불가능해진다. 또 AOA E2에서 partial close는 HOLD / NOT AUTHORIZED다.

### 6.3 왜 4h인가 (cherry-picking 아님을 기록)

| 근거 | 내용 |
|---|---|
| 1 | 4h는 **D5.1·D5.2 두 계약 모두에서 "주요(primary)" 등급의 가장 긴 horizon**이다. 8h는 두 계약 모두 `REFERENCE_ONLY`이고 D6 후보 불가로 이미 동결돼 있다. 결과를 보고 고른 것이 아니라 **기존 계약의 등급 경계를 그대로 물려받았다** |
| 2 | 가설의 시간 규모와 맞는다. 입력이 1시간 OI 변화와 24시간 변동성이므로 분 단위 청산은 신호보다 빠르고, 8h는 funding 정산을 반드시 지난다 |
| 3 | C1 결과가 4h였다는 사실은 **근거로 쓰지 않는다**(선택 편향). 위 1·2만으로 정한다 |

2h와 8h는 **보고 전용 부가 arm**으로 D6-C에서 같이 돌리되, **판정에 쓰지 않는다**(검증 계획 5절).

---

## 7. ACCOUNT RISK (동결)

숫자는 전부 **결과 최적화가 아니라 구조적 근거**로 정했다. 민감도는 D6-C에서만 본다.

| id | 항목 | 값 | 근거 |
|---|---|---|---|
| R1 | 거래당 위험예산 | 자본의 **0.5%** | 연속 4회 손실이 자본의 2%(= R4 일일 한도)와 맞아떨어지게 잡음 |
| R2 | 포지션당 최대 명목 | 자본의 **1.0배** 이하 | AOA 2019~2021 최고 노출/자본 중앙 0.77~1.02배와 같은 영역(D4.1 비교 3절) |
| R3 | 동시 포지션 | **1개** | one-way mode. H9 |
| R4 | 일일 손실 가드 | 자본 대비 **-2.0%** 도달 시 당일(UTC) 진입 중단 | R1 x 4 |
| R5 | 연속 손실 가드 | **4회** 연속 손실 시 24시간 중단 | R1 x 4 |
| R6 | 쿨다운 | 청산 후 **60분** | H8 |
| R7 | 긴급 정지 | 원장 불일치(H13), 피드 5분 이상 두절, SAFE MAX 조회 실패 시 `EMERGENCY_STOP` (신규 진입 중단, 보유분은 X1/X2 규칙대로 청산) | - |
| R8 | 최대 레버리지 | **1x** | 9절 |

---

## 8. POSITION SIZE (레버리지와 분리)

```
sigma_4h   = f_rv24h x sqrt(240)                 # 1m 변동성을 4시간으로 환산
stop_dist  = clip(2.5 x sigma_4h, 0.01, 0.10)    # 손절 거리 (비율). 하한 1%, 상한 10%
notional   = (equity x 0.005) / stop_dist        # 손절이 맞으면 자본의 0.5% 손실
notional   = min(notional, equity x 1.0)         # R2 상한
qty        = floor_to_step(notional / entry_price, 0.001)
qty        = min(qty, SAFE_MAX_qty)              # D4.1 sizing.max_entry() 결과
if qty < 0.001 or notional < 5 USDT: HOLD        # D3 계약 L6·L7
```

| # | 규칙 |
|---|---|
| N1 | **SAFE MAX를 position sizing으로 쓰지 않는다.** SAFE MAX는 "이 스냅샷에서 진입과 즉시 청산이 성립하는 최대 수량"이라는 **실행 안전**이고, 포트폴리오 위험 안전이 아니다(D4.1 비교 9절). 여기서는 **상한 constraint로만** 쓴다 |
| N2 | 위험예산은 `stop_dist`로 나눈다. 변동성이 크면 수량이 작아진다 |
| N3 | 동결 전 측정(변동성 분포만 사용): 진입 후보 시점의 `notional / equity`는 p05 0.067 / p50 0.134 / p95 0.169. **1.0배 상한에 걸리는 경우 0%**. 즉 이 전략은 자본의 약 1/7 규모로 거래한다 |
| N4 | 수수료·funding 부담은 명목에 비례한다. N3 기준으로 왕복 11bp x 0.134 = 자본 대비 약 **1.5bp / 거래** |

---

## 9. LEVERAGE 정책 (AUTO 전용, MANUAL과 독립)

| # | 규칙 |
|---|---|
| L1 | **AUTO V1 레버리지 = 1x 고정.** 선택지·파라미터가 아니다 |
| L2 | 근거: US-B Paper 엔진은 격리형이라 `is_liquidatable`이 `used_margin + unrealized ≤ maintenance_margin`이고, **청산 거리는 수량과 무관하게 레버리지 설정이 정한다**. 1x에서 청산가는 0이다(D4.1 비교 4절 엔진 계산값: 1x 없음 / 3x -33.1% / 5x -19.7% / 10x -9.70% / 20x -4.69% / 50x -1.65%) |
| L3 | 1x에서는 **강제청산이 구조적으로 불가능**하므로, 위험 통제가 전적으로 X2 손절과 7절 가드로 넘어온다. 이것이 의도다 |
| L4 | 레버리지는 edge가 아니라 risk multiplier다. 같은 신호의 이익·손실·수수료를 같은 배수로 키운다(AOA closeout 3절). **gross가 비용을 넘는지는 레버리지와 무관**하므로 D6-C에서 레버리지 민감도를 돌리지 않는다 |
| L5 | **20x / 50x를 AUTO에 쓰지 않는다.** AOA 2018 p90(16.5배)을 넘는 영역이다 |
| L6 | **MANUAL 레버리지 선택지(1/3/5/10/20/50)·SAFE MAX·UI 경고는 변경하지 않는다.** AOA closeout 4절에서 전부 NOT AUTHORIZED다 |

---

## 10. STOP / RISK EXIT (동결)

| 항목 | 값 |
|---|---|
| 기준 가격 | **mark price** (엔진 청산 판정과 같은 기준) |
| 공식 | `stop_price = entry_price x (1 - clip(2.5 x sigma_4h, 0.01, 0.10))` |
| 판정 주기 | 1m bar 단위(D6-C) / 피드 수신마다(실시간) |
| 체결 | MARKET taker |
| 동결 전 측정 | 손절 거리 p05 2.95% / p50 3.72% / p95 7.42% (진입 후보 시점의 변동성 분포만 사용) |

| # | 근거 |
|---|---|
| S1 | **AOA의 무손절을 복제하지 않는다.** AOA E2에서 무손절은 -2% 손절 대비 +1.13%p(CI 0 걸침)이고 깊은 역행의 48%만 회복했다. 생존편향에 가장 취약한 항목으로 CLOSED됐다 |
| S2 | 고정 -X%가 아니라 변동성 조정이다. 같은 -3%가 조용한 장과 급변장에서 다른 사건이기 때문 |
| S3 | k=2.5는 "4시간 보유의 통상 변동 범위를 벗어난 움직임"의 경계로 정했다. 2.0 / 3.0은 D6-C 민감도 arm |
| S4 | **청산 거리 가드**: `stop_dist ≤ 청산 거리 / 3`을 요구한다. 1x에서는 청산 거리가 무한이므로 항상 성립한다. 이 가드는 V2에서 레버리지를 올릴 경우를 위해 지금 등록한다 |
| S5 | 손절은 연구 추정량(horizon 종점 청산)과 다른 결과를 만든다. 그래서 D6-C는 **NO_STOP arm(연구 대조용)과 STOP arm(운영용)을 둘 다** 돌린다. 판정은 STOP arm으로 한다 |

---

## 11. COST 가정 (동결)

| 항목 | 값 | 출처 |
|---|---|---|
| taker | **0.055%** (0.00055) | `data/runtime/crypto/reference/fee_source_verification_v1.json`, Bybit VIP 0. **코드에 숫자를 다시 쓰지 않고 `app.crypto.paper.fees.load_scenarios`로 읽는다** |
| maker | 0.020% | 같은 파일. **쓰지 않는다**(Z4) |
| VIP 등급 | **가정**(`OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED`). 계정 없음 | 같은 파일 `residual_assumption` |
| 왕복 비용 | 진입 + 청산 각각 taker. 명목 기준 약 **11bp** | D5 7절 |
| spread/depth BASE | 1.1841571614073798e-06 | `data/runtime/crypto/d5/realtime_cost_measurement_v1.json` (23.1시간 top-5 테이프) |
| spread/depth STRESS | 2.537159730588128e-04 | 같은 파일 관측 최대 |
| funding | 보유 중 정산분 실제값. LONG은 rate>0이면 지불 | D2 funding. 4h 보유는 약 50% 확률로 정산을 지남(D5.1 3절) |
| 환율 | 표시 전용. 판정은 USDT 기준 | D4 `run_config.json` |

**비용은 사실상 전부 수수료다**(D5 7절, spread BASE 0.01bp). 이 계약은 그 사실을 바꾸지 않는다.

---

## 12. 예상 실패 모드 (사전 기록)

| # | 실패 모드 | 어떻게 드러나는가 |
|---|---|---|
| F1 | **희석 실패**: edge가 HIGH 변동성에만 있었고 MID를 섞으면 사라진다 | OOS net이 0 근처이고 변동성 분해에서 MID가 음수 |
| F2 | **소멸**: 구성 신호가 이미 사라져서 최근 fold에서만 음수 | F7~F9 net 음수, 연도별 단조 감소 |
| F3 | **1개 연도 의존**: 2023 같은 한 해가 전체 net을 만든다 | G5(leave-one-year-out) 실패 |
| F4 | **비용 미달**: gross는 양수인데 11bp를 못 넘는다. D5·D5.1·D5.2에서 반복된 형태 | ratio < 1.2 |
| F5 | **손절 역효과**: 손절이 되돌림 전에 털린다(평균회귀 전략의 전형) | STOP arm이 NO_STOP arm보다 크게 나쁨 |
| F6 | **표본 집중**: 진입이 소수 일자에 몰려 CI가 넓다 | 판정 fold < 6 또는 단일 거래가 net의 10% 초과 |
| F7 | **선택 편향 실현**: C1이 720셀 중 우연히 뽑힌 1셀이었다 | 위 전부가 동시에 나타남 |

---

## 13. VERDICT GATE (D6-C, 결과 보기 전 동결)

**전부 통과해야 `AUTO_CANDIDATE_PASS`다.** 하나라도 실패하면 `FAIL`이고, 이 계약 아래에서 재시도하지 않는다.

| id | 게이트 | 기준 |
|---|---|---|
| G1 | OOS VIP0_BASE 순손익 | 평균 > 0 **이고** 95% CI 하한 > 0 |
| G2 | edge / cost ratio | **≥ 1.2** (D5.1 S5와 동일. 비용 모델 오차 20% 여유) |
| G3 | fold 일관성 | 판정 가능 fold 중 gross > 0 비율 **≥ 2/3**, net > 0 비율 **≥ 1/2** |
| G4 | 표본 | OOS 거래 **≥ 200건**, 판정 가능 fold **≥ 6개** (fold당 거래 ≥ 20 이고 days ≥ 20) |
| G5 | 연도 집중 | leave-one-year-out 후에도 net > 0. **단일 연도가 총 net 합의 40%를 넘지 않음** |
| G6 | 단일 거래 집중 | **단일 거래가 총 net 합의 10%를 넘지 않음** |
| G7 | 비용 스트레스 | VIP0_STRESS net > 0 **이고** taker +20% 가정에서도 net > 0 |
| G8 | 파라미터 민감도 | 아래 6개 변형 전부에서 net > 0: score threshold 37 / 62, 손절 k 2.0 / 3.0, OI 창 30분 / 120분 |
| G9 | regime 견고성 | 추세 3라벨 중 2개 이상 net > 0, **변동성 라벨(HIGH·MID) 둘 다 net > 0**, UTC 세션 4개 중 3개 이상 net > 0 |
| G10 | 자본 곡선 | 8절 sizing·1x 기준 **PF > 1.3**, **MDD < 15%** |
| G11 | 연구 정합성 | NO_STOP arm의 거래당 평균이 D5.2 C1 계열 추정과 같은 부호. **불일치 시 구현 결함을 먼저 의심한다** (PASS 조건이 아니라 sanity check) |

**G9의 변동성 조건이 C1의 설계 결함을 고친 지점이다.** C1은 정의에 HIGH를 넣어 이 검사를 구조적으로 통과할
수 없었다. FDN-V1은 HIGH와 MID를 모두 포함하므로 검사가 의미를 가진다.

---

## 14. MANUAL / AUTO 격리 (강제)

| 항목 | MANUAL | AUTO |
|---|---|---|
| run 디렉터리 | 현행 `CRYPTO_PAPER_ROOT` / `run_id` | **별도 `run_id`, 별도 root** |
| 프로세스 | 현행 터미널 서비스 | **별도 프로세스·별도 포트** |
| 계좌 / 잔고 / 증거금 | 독립 | 독립 |
| 포지션 | 독립 | 독립 |
| 원장(`input.jsonl` / `ledger.jsonl`) | 독립 | 독립 |
| 실현손익 / reset anchor / 거래통계 | 독립 | 독립 |
| 위험 가드 | 사람 | 7절 계약 |
| 전략 상태 | 없음 | AUTO 전용 |
| **공유 가능** | \- | **read-only 시장 데이터뿐** |

| # | 규칙 |
|---|---|
| I1 | AUTO는 MANUAL의 잔고·포지션·원장을 **읽지도 쓰지도 않는다** |
| I2 | AUTO 주문이 MANUAL 포지션을 상계·증감하지 않는다 |
| I3 | 한 프로세스가 두 계좌를 동시에 들지 않는다. `PaperSession`은 `run_dir` 1개에 묶인다 |
| I4 | AUTO 배포는 **운영 MANUAL 계좌를 건드리지 않는 격리 인스턴스**로만 한다 |
| I5 | 위반 가능성이 있는 구현은 D6-B에서 거부한다 |

---

## 15. AUTO UI 계약 (미래. 이번 단계 구현 0)

**chart 없음. 수동 주문 버튼 없음.** 표시 항목만 동결한다.

| 구역 | 항목 |
|---|---|
| 상태 | RUNNING / STOPPED, strategy version, contract sha256, 마지막 결정 시각 |
| 결정 | 현재 결정 **LONG / SHORT / HOLD**, LONG_SCORE와 4개 category 내역, HOLD 사유(어느 하드필터인지) |
| 포지션 | 진입가, 수량, 레버리지(1x), 미실현손익, **지금 청산 시 예상 순손익**, 손절가, 남은 보유시간 |
| 성과 | 실현손익, 수수료, funding, 거래수, 승률, PF, MDD, 누적 순손익 |
| 이력 | 최근 결정 N건, 최근 거래 N건 |
| 위험 | 차단 중인 가드, 오류, EMERGENCY_STOP 상태 |

수동 주문 버튼·차트·레버리지 선택기는 AUTO UI에 **넣지 않는다**.

---

## 16. 승격 순서 (강제)

```
D6-A  설계 동결 (이 문서)
  ↓
D6-B  Score 엔진 구현 + 계약 hash 검증 + 단위 테스트 (주문 0, 백테스트 0)
  ↓
D6-C  Historical replay / 백테스트 → 13절 게이트 판정
  ↓   (FAIL이면 여기서 종료. 재시도 금지)
D6-D  Realtime shadow decision. 결정만 기록, **주문 0**
  ↓
D7    독립 AUTO Paper Trading (MANUAL과 완전 별도 계좌)
```

**D6-C 통과만으로 실제 AUTO를 시작하지 않는다.** 각 단계 전환은 사용자 승인이 필요하다.

---

## 17. 산출물

| 파일 | 상태 |
|---|---|
| `docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md` | 이 문서(동결 대상) |
| `docs/crypto/CRYPTO_D6_CANDIDATE_RATIONALE_V1.md` | 근거 |
| `docs/crypto/CRYPTO_D6_VALIDATION_PLAN_V1.md` | 검증 설계 |
| `data/research/crypto/d6/strategy_contract_v1.json` | 기계가 읽는 사본 |
| `data/runtime/crypto/d6/contract_freeze_v1.json` | sha256 동결(gitignore) |
| `data/runtime/crypto/d6/prefreeze_qc_v1.json` | 동결 전 계산 전수 기록(gitignore) |
| `backend/tests/crypto/test_d6_contract.py` | 계약 검증 테스트 |

전략 실행 코드 0줄. 기존 엔진 수정 0. 운영 서버·Paper 계좌·liqfwd 접근 0. commit/push 0.
