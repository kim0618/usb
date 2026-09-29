# US-B CRYPTO D5 Edge Discovery 사전등록 계약 V1

작성 2026-09-24. **이 문서는 계약이다.** D5 전체 연구(feature x forward return 통계)를 돌리기 전에
아래 항목을 동결한다. 동결 뒤에는 결과를 보고 어떤 숫자도 옮기지 않는다. 바꾸려면 V2 문서를 새로 만들고
V1 결과는 그대로 둔다.

동결 증거: 이 파일의 sha256을 `data/runtime/crypto/d5/contract_freeze_v1.json`에 기록하고, 연구 실행
스크립트는 실행 시 이 파일의 sha256을 다시 계산해 동결값과 다르면 실행을 거부한다.

동결 전에 계산한 것은 **결과와 무관한 서술 통계뿐**이다(분할 구간 가격 경로, 실시간 spread/depth 분포).
feature와 forward return을 함께 본 계산은 동결 전에 0건이다.

---

## 0. 범위와 금지

| # | 규칙 |
|---|---|
| Z1 | 대상은 Bybit BTCUSDT Linear Perpetual 1종목 |
| Z2 | 입력은 D2 historical 파일(1m kline·mark·index, OI 5m, funding)과 실시간 top-5 테이프 사본뿐 |
| Z3 | 운영 서버, soak 서비스, 운영 paper 계좌(`paper-server-001`)를 읽거나 쓰지 않는다. 실시간 테이프는 이전 세션이 이미 로컬로 받아 둔 사본을 쓴다(8절) |
| Z4 | 계정·API key·private endpoint·주문 0건 |
| Z5 | 최종 전략, LONG/SHORT Score, 확률, AUTO, 하이퍼파라미터 최적화를 만들지 않는다 |
| Z6 | 복잡한 ML을 쓰지 않는다. feature는 아래 18개 고정 공식뿐 |
| Z7 | commit/push 하지 않는다 |

---

## 1. 연구 창과 입력

| 항목 | 값 |
|---|---|
| 시작 | 2021-02-01T00:00:00Z 포함 (`CRYPTO_RESEARCH_WINDOW_V1` W1) |
| 끝 | 2026-09-23T00:00:00Z 미포함 (D2 overlap window 끝, funding이 결정) |
| 1m bar | 2,966,400개 (결측 0, 중복 0, zero-volume 68개) |
| funding 정산 | 창 안 6,181건 |

D2 원본 checksum (manifest 기준):

| dataset | sha256 |
|---|---|
| kline_1m | `994beac8a84bc770c51b8644567a1807ca1625e9696b1a40874f372f3f1dc628` |
| mark_1m | `c930e017bcf4700e158d34a503557855253a4f229ad12b09b78fce173fcf0104` |
| index_1m | `b9750cd436c2a5d82f2ca3b9da8bada82be712fe4cc9f31b84ff07941ea2734e` |
| open_interest_5m | `1409f63c5a8769e0e4e987af49ec3cd72b0963f36d02fbdf306ef73a8f29d403` |
| funding | `6a6c56f0000ec671dd21e5a3afa8ad5981b14ea84f3e9e9b186112d0f7f3c508` |

zero-volume 68개(`CRYPTO_RESEARCH_WINDOW_V1` RW1)는 **그대로 둔다.** 개별 제외하지 않는다.
전체의 0.0023%라 어떤 bucket 통계도 바꿀 수 없는 크기다.

---

## 2. DATA SPLIT (동결)

시간순 3구간. random split 금지. 가장 최근 구간이 Holdout. 경계는 달력 규칙(대략 60/22/18)으로 정했고
구간 안 수치는 경계를 정한 **뒤** 서술용으로만 계산했다.

| 구간 | start (포함) | end (미포함) | 1m bar | BTC 시작 -> 끝 | 구간 수익 | 최저 / 최고 | 최대낙폭 |
|---|---|---|---|---|---|---|---|
| **DISCOVERY** | 2021-02-01T00:00Z | 2024-07-01T00:00Z | 1,794,240 | 33,065 -> 62,750 | +89.8% | 15,487 / 73,868 | -77.6% |
| **VALIDATION** | 2024-07-01T00:00Z | 2025-10-01T00:00Z | 658,080 | 62,759 -> 114,014 | +81.7% | 49,382 / 124,501 | -31.9% |
| **HOLDOUT** | 2025-10-01T00:00Z | 2026-09-23T00:00Z | 514,080 | 114,061 -> 86,168 | **-24.5%** | 57,847 / 126,056 | -54.1% |

시장 국면 커버리지 (10절 추세 regime 정의 기준, bar 비율):

| 구간 | BULL | BEAR | SIDEWAYS | 1m 수익률 연율 변동성 |
|---|---|---|---|---|
| DISCOVERY | 23.7% | 20.8% | 55.5% | 66.3% |
| VALIDATION | 21.0% | 13.0% | 66.1% | 49.4% |
| HOLDOUT | 14.3% | 20.3% | 65.5% | 47.0% |

DISCOVERY는 2021 강세, 2022 약세(-77.6% 낙폭), 2023~24 회복을 모두 담는다. VALIDATION은 강세 쪽,
HOLDOUT은 약세 쪽으로 기운다. 따라서 LONG 쪽 후보는 HOLDOUT에서, SHORT 쪽 후보는 VALIDATION에서
가장 불리한 조건을 만난다. 이 비대칭은 결과 해석 때 명시한다.

**Warm-up**: feature 최대 lookback 1,440 bar + 정규화 창 30일 때문에 DISCOVERY 표본은
**2021-03-04T00:00Z부터** 센다. 입력은 2021-02-01 이후만 쓴다(W1 준수).

**Embargo**: bar t는 t와 t+1+30(최장 horizon의 청산 bar)이 모두 같은 구간 안에 있을 때만 그 구간의
표본이다. 구간 경계를 넘는 forward window는 버린다. horizon과 무관하게 같은 규칙.

---

## 3. PIT / LOOKAHEAD (동결)

| # | 규칙 |
|---|---|
| P1 | bar t는 `ts[t]`에 열리고 `ts[t]+60s`에 닫힌다. bar t의 feature는 bar t가 **닫힌 뒤** 알 수 있는 값만 쓴다 |
| P2 | kline·mark·index 1m 값은 그 bar 종가 시각에 알려진 것으로 본다 |
| P3 | OI 5m 레코드(stamp T)는 **T + 5분**에 알려진 것으로 본다. stamp가 구간 시작인지 끝인지 확정하지 않았으므로 한 구간 늦게 쓴다(보수적) |
| P4 | funding 레코드(stamp T = 정산 시각)는 T에 알려진 것으로 본다 |
| P5 | 진입은 **bar t+1의 시가**. bar t 안에서의 진입 0건 |
| P6 | horizon h의 청산은 **bar t+1+h의 시가**. 보유 구간은 bar t+1 ~ t+h |
| P7 | MAE/MFE는 bar t+1 ~ t+h의 체결가 high/low 극값으로 잰다. 순서 가정이 필요 없는 극값만 쓴다. D4 replay의 합성 호가와 합성 intrabar 경로(low->high->close)는 **쓰지 않는다**. high/low는 체결가이지 실행 가능한 bid/ask가 아니다 |
| P8 | 정규화 cutoff는 그날 00:00Z **이전** 30일 값으로만 만든다(5절) |
| P9 | regime 라벨은 bar t 종가까지의 값으로만 만든다(10절). 단 변동성 regime cutoff는 DISCOVERY에서 한 번 정해 전 구간에 고정한다 |

---

## 4. TARGET (동결)

진입가 `E = open[t+1]`, 청산가 `X_h = open[t+1+h]`.

| horizon | 역할 |
|---|---|
| 5m, 15m, 30m | **주요**. verdict 대상 |
| 1m | 참고용. verdict를 매기되 D6 후보가 될 수 없다 |

각 horizon, 각 표본에서:

| 값 | 정의 |
|---|---|
| forward raw return | `X/E - 1` |
| LONG gross | `X/E - 1` |
| SHORT gross | `1 - X/E` |
| cost-adjusted (net) | gross - 수수료 - spread - 호가충격 - funding (7·8절) |
| direction hit | gross > 0 인 비율 (비용 전) |
| MAE (LONG) | `min(low[t+1..t+h]) / E - 1`, SHORT는 `1 - max(high)/E` |
| MFE (LONG) | `max(high[t+1..t+h]) / E - 1`, SHORT는 `1 - min(low)/E` |

**funding 비용**: 정산 시각 T가 `ts[t+1] <= T < ts[t+1+h]`이면(= 정산 순간에 포지션을 들고 있으면)
LONG은 `rate`만큼 지불, SHORT는 수취(비율, 명목 1 기준). ZERO 시나리오에는 넣지 않는다.

모든 표본은 bar마다 하나씩이라 horizon > 1이면 겹친다. 이것은 조건부 기대값 측정이지 거래 PnL 곡선이
아니다. 겹침은 9절의 block bootstrap으로 처리한다.

---

## 5. NORMALIZATION + BROAD BUCKET (동결)

- 각 feature f, 각 UTC 날짜 D에 대해 `[D-30일, D)` 구간의 f 값(NaN 제외)에서 분위수 10/30/70/90을 계산한다.
- 그 창의 유효값이 창 크기의 50% 미만이면 그날 bucket은 NaN(표본 제외).
- 날짜 D의 bar는 그 4개 cutoff로 bucket을 받는다. `searchsorted(cutoffs, x, side="right")`

| bucket | 분위 |
|---|---|
| B1 | 0-10 |
| B2 | 10-30 |
| B3 | 30-70 |
| B4 | 70-90 |
| B5 | 90-100 |

동값(tie)이 많은 feature(funding)는 bucket이 한쪽으로 몰릴 수 있다. 그대로 두고 N으로 드러낸다.
threshold를 결과를 보고 움직이지 않는다. 미세 threshold 탐색 0회.

---

## 6. FEATURE (동결, 18개)

기호: `C,O,H,L` = kline 1m 종가·시가·고가·저가, `TO` = turnover(USDT), `OI` = P3 규칙으로 bar 종가에
알려진 OI, `IDX` = index 1m 종가, `r1[t] = ln(C[t]/C[t-1])`. 모든 창은 bar t를 포함해 과거로 센다.
missing 정책: lookback이 연구 창 시작 이전을 요구하면 NaN(표본 제외). 0으로 나누면 NaN.

| id | 그룹 | 공식 | 입력 | lookback | PIT 가용 |
|---|---|---|---|---|---|
| A1 `trend_ret_60` | A Trend | `ln(C[t]/C[t-60])` | kline | 60 | bar t 종가 |
| A2 `trend_ret_240` | A Trend | `ln(C[t]/C[t-240])` | kline | 240 | bar t 종가 |
| A3 `trend_dist_sma1440` | A Trend | `ln(C[t] / mean(C[t-1439..t]))` | kline | 1,440 | bar t 종가 |
| B1 `mom_ret_1` | B Momentum | `ln(C[t]/C[t-1])` | kline | 1 | bar t 종가 |
| B2 `mom_ret_5` | B Momentum | `ln(C[t]/C[t-5])` | kline | 5 | bar t 종가 |
| B3 `mom_ret_15` | B Momentum | `ln(C[t]/C[t-15])` | kline | 15 | bar t 종가 |
| C1 `vol_rvol_15` | C Volume | `sum(TO[t-14..t]) / (sum(TO[t-1439..t]) * 15/1440)` | kline | 1,440 | bar t 종가 |
| C2 `vol_rvol_60` | C Volume | `sum(TO[t-59..t]) / (sum(TO[t-1439..t]) * 60/1440)` | kline | 1,440 | bar t 종가 |
| C3 `vol_signed_flow_15` | C Volume | `sum(sign(C-O)*TO, 15) / sum(TO, 15)`, 분모 0이면 NaN | kline | 15 | bar t 종가 |
| D1 `oi_chg_60` | D OI | `ln(OI[t]/OI[t-60])` | OI 5m | 60 bar (+5분 지연) | bar t 종가 |
| D2 `oi_chg_240` | D OI | `ln(OI[t]/OI[t-240])` | OI 5m | 240 | bar t 종가 |
| D3 `oi_chg_1440` | D OI | `ln(OI[t]/OI[t-1440])` | OI 5m | 1,440 | bar t 종가 |
| E1 `fund_last` | E Funding | 마지막 정산 funding rate | funding | 1 정산 | 정산 시각 |
| E2 `fund_sum_3` | E Funding | 마지막 3회 정산 rate 합(24h) | funding | 3 정산 | 정산 시각 |
| E3 `fund_premium` | E Funding | `ln(C[t]/IDX[t])` (perp 체결가 vs index, funding의 원천) | kline, index | 0 | bar t 종가 |
| F1 `vola_rv_60` | F Volatility | `std(r1[t-59..t])` (ddof 0) | kline | 60 | bar t 종가 |
| F2 `vola_rv_ratio` | F Volatility | `std(r1, 60) / std(r1, 1440)` | kline | 1,440 | bar t 종가 |
| F3 `vola_range_15` | F Volatility | `ln(max(H[t-14..t]) / min(L[t-14..t]))` | kline | 15 | bar t 종가 |

정규화는 18개 전부 5절 규칙 하나뿐이다.

셀 수: 18 feature x 5 bucket x 3 주요 horizon x 2 방향 = **540 주요 셀** (+1m 참고 180셀).

---

## 7. FEE SCENARIO (동결)

출처: `data/runtime/crypto/reference/fee_source_verification_v1.json` (D4에서 공식 페이지 캡처,
html sha256 `b9bb8db5…`). `app.crypto.paper.fees.load_scenarios`로 읽는다. 숫자를 코드에 다시 쓰지 않는다.

| 이름 | taker | 성격 |
|---|---|---|
| VIP_0 | 0.0550% | Bybit 공식 perpetual 요율. 계정이 없으므로 **등급 적용은 가정**(`OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED`) |
| ZERO | 0 | 비용 없는 비교 상한선(`NO_COST_BOUND`). spread·충격·funding도 0 |

진입·청산 모두 taker(시장가)로 본다. 수수료 = `taker * (1 + X/E)` (진입 명목 + 청산 명목).
maker 체결은 가정하지 않는다(대기 주문 체결 여부를 과거 데이터로 알 수 없음).

---

## 8. SPREAD / DEPTH SCENARIO (동결)

**근거 데이터**: 운영 paper 터미널이 기록한 실시간 top-5 호가 테이프의 로컬 사본
`data/runtime/crypto/d5/realtime_source/paper-server-001.input.snapshot.jsonl`
(sha256 `67c023a24aee6d657d0771c172c5e04a763dd83312acae825544412ba29732a4`,
2026-09-23T04:57:28Z ~ 2026-09-24T04:04:53Z, 약 23.1시간, MARKET 스냅샷 75,640건, 교차·잠김 호가 0).
이전 세션이 이미 받아 둔 파일을 복사했다. 이번 D5는 서버에 접속하지 않았다.
측정 코드 `backend/app/crypto/research/microcost.py`, 결과 `data/runtime/crypto/d5/realtime_cost_measurement_v1.json`.

**기준 수량 Q_ref = 0.1 BTC.** 근거: 운영 paper 기본 자본 10,000,000원(약 7,440 USDT)을 레버리지 1배로
쓸 때의 명목(약 0.086 BTC)에 가장 가까운 1자리 수량. 결과를 보고 바꾸지 않는다.

측정값 (분수, mid 대비):

| 항목 | p50 | p95 | p99 | 관측 최대 |
|---|---|---|---|---|
| spread (ask1-bid1)/mid | 1.184e-6 (0.0118bp) | 1.191e-6 | 1.193e-6 | **1.627e-4 (1.627bp)** |
| 0.1 BTC 매수 walk 충격 | 0 | ~0 | ~0 | **4.98e-5 (0.498bp)** |
| 0.1 BTC 매도 walk 충격 | 0 | ~0 | ~0 | **4.12e-5 (0.412bp)** |
| top-5 매수측 깊이 부족 비율 | 2.85% | | | |
| top-5 매도측 깊이 부족 비율 | 3.55% | | | |

**시나리오 (왕복, 분수)**:

| 이름 | 공식 | 값 |
|---|---|---|
| BASE | p50 spread + p50 매수충격 + p50 매도충격 | **1.184e-6 (0.0118bp)** |
| STRESS | **관측 최대** spread + 관측 최대 매수충격 + 관측 최대 매도충격 | **2.537e-4 (2.537bp)** |

STRESS를 p95/p99가 아니라 관측 최대로 잡은 이유: p99까지 사실상 1틱이라 분위 기반 STRESS는 BASE와
구별되지 않는다. 숫자를 지어내지 않으면서 가장 보수적인 값이 관측 최대다.

**이 시나리오가 못 잡는 것 (명시)**:
1. 테이프는 조용한 23시간 하루치다. 2021~2026 급락 구간의 spread·깊이는 이보다 훨씬 나빴을 수 있다.
   historical 호가가 없으므로 그 크기를 알 수 없다. **과거 급변 구간 비용은 UNKNOWN.**
2. top-5 깊이가 0.1 BTC에 못 미치는 스냅샷 약 3%는 이 엔진(부분체결 없음)에서 체결 자체가 거부된다.
   비용으로 환산할 수 없어 시나리오에 넣지 않고 비율만 보고한다.
3. 진입가 proxy는 bar t+1 시가(체결가)다. mid와의 차이는 반 spread 이내라 BASE/STRESS 안에 들어간다.
   판단 후 주문까지의 지연(latency)은 모델링하지 않는다.

**결과 시나리오 조합**: `ZERO`(gross), `VIP_0 + BASE`(**주 시나리오**), `VIP_0 + STRESS`.

---

## 9. STATISTICS (동결)

셀 = feature x bucket x horizon x 방향. 구간(DISCOVERY / VALIDATION / HOLDOUT)마다 따로 계산한다.

| 통계 | 정의 |
|---|---|
| N | 표본 bar 수 |
| days | 표본이 1개 이상인 UTC 날짜 수 |
| mean | 시나리오별 net 평균 (ZERO는 gross) |
| median | ZERO와 VIP_0+BASE만 |
| win rate | net > 0 비율 (시나리오별) |
| direction hit | gross > 0 비율 |
| net expectancy | = VIP_0+BASE mean |
| SE, CI | **moving block bootstrap**, 블록 = 연속 7 UTC일, 반복 1,000회, seed 20260924. 일별 (합, 개수)를 블록으로 재표집해 비율 추정량(합/개수)을 다시 계산. SE = 재표집 표준편차, CI = 재표집 분위(95%: 2.5/97.5, 90%: 5/95) |
| MAE, MFE | 평균, 비용 전 |

naive iid bootstrap을 쓰지 않는 이유: 한 bucket 표본은 시간적으로 뭉쳐 있고(regime), horizon>1이면
표본끼리 forward window가 겹친다. 7일 블록은 그 둘을 다 덮는다.

**다중검정 규모 (사전 기록)**: 주요 540셀. 귀무가설에서 DISCOVERY 95% CI 하한 > 0은 셀당 약 2.5%라
약 13셀이 우연히 통과한다. VALIDATION(단측 5%)과 HOLDOUT(부호 50%)을 연속 통과할 우연 기대값은
약 0.3셀이다. feature끼리 상관이 커서 셀은 독립이 아니다. 이 숫자는 SURVIVE 해석 때 함께 적는다.

---

## 10. ROBUSTNESS 분해 (동결)

세 구간을 합친 표본에서, 주 시나리오(VIP_0+BASE) net mean을 아래 라벨별로 계산한다.

| 축 | 라벨 정의 |
|---|---|
| 추세 | bar t 종가 기준 직전 7일(10,080 bar) 로그수익 `> +5%` BULL, `< -5%` BEAR, 그 외 SIDEWAYS |
| 변동성 | `std(r1, 1440)`이 DISCOVERY 분포의 70분위 초과 HIGH, 30분위 미만 LOW, 그 외 MID. cutoff는 DISCOVERY에서 한 번 계산해 고정 |
| 시간대 | 진입 bar(t+1)의 UTC 시각. 00-06 / 06-12 / 12-18 / 18-24 네 세션 (+24시간별 표는 후보에만 보고) |
| 연도 | 진입 bar의 UTC 연도 2021~2026 |

라벨 안 N < 200이면 그 라벨은 판정에서 뺀다(분모에서도 뺀다).

**Robustness PASS 조건 (전부)**:
- R1 추세 3라벨 중 net > 0 인 것이 2개 이상
- R2 변동성 3라벨 중 net > 0 인 것이 2개 이상
- R3 UTC 세션 4개 중 net > 0 인 것이 3개 이상
- R4 연도 중 net > 0 인 것이 판정 가능 연도의 2/3 이상

---

## 11. VERDICT (동결)

주 시나리오 = VIP_0 + BASE. "same direction"은 ZERO(gross) mean의 부호가 그 셀 방향과 같다(> 0)는 뜻.

**SURVIVE** (주요 horizon 5/15/30만, 아래 전부):
- S1 DISCOVERY: net mean > 0 이고 95% CI 하한 > 0
- S2 VALIDATION: net mean > 0 이고 90% CI 하한 > 0
- S3 HOLDOUT: net mean > 0
- S4 세 구간 모두 gross mean > 0 (같은 방향)
- S5 세 구간 모두 N >= 1,000 이고 days >= 30
- S6 VALIDATION+HOLDOUT 합산 VIP_0+STRESS net mean > 0
- S7 Robustness PASS (10절)

**WEAK** (SURVIVE가 아니면서 아래 중 하나):
- W1 S1·S2·S3·S4·S5는 통과했으나 S6 또는 S7 실패
- W2 비용에 죽은 gross edge: 세 구간 모두 gross mean > 0, DISCOVERY gross 95% CI 하한 > 0, VALIDATION gross 90% CI 하한 > 0, S5 통과

**REJECT**: 그 밖 전부.

1m 참고 셀은 같은 규칙으로 라벨을 붙이되 SURVIVE 조건을 채워도 `REFERENCE_ONLY`로 표시하고 D6 후보에서 뺀다.

SURVIVE는 **최종 전략 PASS가 아니다.** "비용 후에도 시간적으로 재현된 조건부 기대값이 있다"는 뜻뿐이다.

---

## 12. MICROSTRUCTURE (분리)

실시간 테이프(8절, 약 23시간)는 장기 historical 연구와 **섞지 않는다.** 표본이 하루치라 장기 edge 근거가
될 수 없다. 탐색 결과(top-5 호가 불균형 vs 이후 mid 변화)를 결과 문서 별도 절에 `EXPLORATORY`로만 적고,
어떤 후보의 verdict에도 쓰지 않는다. 사전 정의: 불균형 `(bidQty5-askQty5)/(bidQty5+askQty5)`, 테이프 내부
5분위 bucket, forward mid 변화 10초·60초·300초.

---

## 13. 산출물

| 파일 | 내용 |
|---|---|
| `docs/crypto/CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1.md` | 이 문서 (동결) |
| `docs/crypto/CRYPTO_D5_EDGE_RESULTS_V1.md` | 결과 |
| `docs/crypto/CRYPTO_D5_CANDIDATE_REGISTRY_V1.md` | 후보 대장 |
| `backend/app/crypto/research/` | dataset·features·study·microcost 코드 |
| `data/runtime/crypto/d5/` | 동결 기록, 셀 전체 결과 JSON (gitignore) |
