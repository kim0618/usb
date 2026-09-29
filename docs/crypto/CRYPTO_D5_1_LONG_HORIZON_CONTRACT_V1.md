# US-B CRYPTO D5.1 Long-Horizon Edge Discovery 사전등록 계약 V1

작성 2026-09-24. **이 문서는 계약이다.** D5.1 연구(feature x forward return)를 돌리기 전에 동결한다.
동결 기록: `data/runtime/crypto/d5_1/contract_freeze_v1.json` (sha256). 연구 스크립트는 실행 때 이 파일의
sha256을 다시 계산해 기록과 다르면 **실행을 거부**한다. 바꾸려면 V2를 새로 만든다.

**D5 결과를 이미 본 상태에서 쓰는 계약이다.** D5 계약(`CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1`)은 재사용하지
않는다. D5의 feature 공식과 bucket 규칙은 **글자 그대로 복사**하고(결과에 맞춰 고치지 않음), 새로 추가한
feature는 D5 결과와 무관하게 horizon 규모에 맞춰 정했다. 동결 전 계산은 fold별 가격 경로·국면 비율
(서술 통계)뿐이며, feature와 1h 이상 forward return을 함께 본 계산은 0건이다.

D5가 2021-03 ~ 2026-09 전 구간의 5~30m 결과를 이미 봤으므로 **이 문서의 어떤 구간도 untouched holdout이
아니다.** 진짜 forward 검증은 앞으로 쌓이는 시장·paper 데이터에서 따로 한다(D5.1 범위 밖).

---

## 0. 범위와 금지

| # | 규칙 |
|---|---|
| Z1 | BTCUSDT Linear Perpetual 1종목, Bybit PUBLIC 데이터만 |
| Z2 | 입력은 D2 authoritative 5종(1m kline·mark·index, OI 5m, funding)뿐. D5와 같은 파일, 같은 checksum |
| Z3 | historical 합성 호가는 방향 edge 근거로 쓰지 않는다. realtime 호가·체결은 장기 결과와 섞지 않는다(비용 시나리오 수치만 D5 측정값 재사용) |
| Z4 | 운영 paper 계좌·서버·soak 서비스 접근 0. 계정·API key·주문 0 |
| Z5 | 최종 전략, Score, 확률, AUTO, 하이퍼파라미터 최적화, **feature 조합(복합 조건)** 금지. 셀은 전부 feature 1개 x bucket 1개 |
| Z6 | commit/push 금지 |

---

## 1. 연구 창

| 항목 | 값 |
|---|---|
| 입력 시작 | 2021-02-01T00:00:00Z (W1) |
| 표본 시작 | 2021-03-04T00:00:00Z (30일 정규화 warm-up, D5와 동일) |
| 끝 | 2026-09-23T00:00:00Z 미포함 |

---

## 2. WALK-FORWARD FOLD (동결)

**확장(expanding) walk-forward.** test는 6개월(마지막만 2026-01-01 ~ 2026-09-23). train은 항상 표본 시작부터
test 시작 직전까지. random split 0.

| fold | train | test | train bar | test bar | test BTC | test 추세 BULL / BEAR / SIDE |
|---|---|---|---|---|---|---|
| F1 | 2021-03-04 ~ 2022-01-01 | 2022-01-01 ~ 2022-07-01 | 436,320 | 260,640 | 46,243 -> 19,926 (-56.9%) | 17.1 / 36.1 / 46.8% |
| F2 | 2021-03-04 ~ 2022-07-01 | 2022-07-01 ~ 2023-01-01 | 696,960 | 264,960 | 19,798 -> 16,550 (-16.4%) | 17.5 / 20.9 / 61.6% |
| F3 | 2021-03-04 ~ 2023-01-01 | 2023-01-01 ~ 2023-07-01 | 961,920 | 260,640 | 16,550 -> 30,466 (+84.1%) | 27.8 / 15.1 / 57.2% |
| F4 | 2021-03-04 ~ 2023-07-01 | 2023-07-01 ~ 2024-01-01 | 1,222,560 | 264,960 | 30,473 -> 42,325 (+38.9%) | 15.1 / 5.9 / 79.0% |
| F5 | 2021-03-04 ~ 2024-01-01 | 2024-01-01 ~ 2024-07-01 | 1,487,520 | 262,080 | 42,347 -> 62,750 (+48.2%) | 29.7 / 18.3 / 52.1% |
| F6 | 2021-03-04 ~ 2024-07-01 | 2024-07-01 ~ 2025-01-01 | 1,749,600 | 264,960 | 62,759 -> 93,530 (+49.0%) | 29.1 / 15.7 / 55.2% |
| F7 | 2021-03-04 ~ 2025-01-01 | 2025-01-01 ~ 2025-07-01 | 2,014,560 | 260,640 | 93,590 -> 107,081 (+14.4%) | 18.3 / 12.1 / 69.6% |
| F8 | 2021-03-04 ~ 2025-07-01 | 2025-07-01 ~ 2026-01-01 | 2,275,200 | 264,960 | 107,062 -> 87,595 (-18.2%) | 11.4 / 19.4 / 69.2% |
| F9 | 2021-03-04 ~ 2026-01-01 | 2026-01-01 ~ 2026-09-23 | 2,540,160 | 381,600 | 87,595 -> 86,168 (-1.6%) | 14.7 / 17.0 / 68.3% |

(end는 미포함. bar 수는 embargo 전.) 하락 fold 3, 상승 5, 보합 1.

**OOS 구간** = F1~F9 test의 합 = 2022-01-01 ~ 2026-09-23.

**Embargo (전 horizon 공통)**: 결정 bar t부터 청산 bar t+1+h까지가 fold 경계(위 표의 모든 날짜 경계,
표본 시작, 끝)를 하나라도 가로지르면 그 표본은 버린다. 그래서 train 표본은 test 시작 전에 청산이 끝나고,
test 표본은 자기 fold 안에서 청산이 끝난다.

**train이 하는 일**: 파라미터 적합이 없으므로(모든 정규화는 과거 30일 rolling이라 이미 PIT) train은 셀마다
**방향 선택**에만 쓴다(9절 WF 선택). 미래 fold 정보는 어떤 fold의 선택에도 들어가지 않는다.

---

## 3. PIT / 실행 계약 (동결)

D5 계약 3절 P1~P9를 그대로 적용한다. 요약:

- bar t 종가 뒤에만 feature 계산. 진입 `E = open[t+1]`, 청산 `X = open[t+1+h]`. 같은 bar 진입·청산 0
- OI 5m 레코드는 stamp + 5분에 가용, funding은 정산 시각에 가용
- **same-bar TP/SL, 초단위 stop, bar 내부 순서를 이용한 체결 전부 없음.** D5.1은 horizon 종점 + MAE/MFE 극값만 본다
- MAE/MFE는 bar t+1 ~ t+h 체결가 high/low 극값(순서 가정 없음). 실행 가능한 bid/ask 아님

**다중 timeframe 집계 규칙**: k분 bar는 UTC 00:00 기준 k분 격자에 정렬한 1m bar의 결정적 집계다. bar t 종가
시각을 `T_c = ts[t] + 60s`라 할 때, **마지막으로 완전히 닫힌 k분 bar**는 `floor(T_c / k) * k`에 끝나는 bar다.
그 bar의 종가 = 그 시각에 끝나는 1m bar의 종가. 아직 닫히지 않은 k분 bar의 값은 쓰지 않는다.

---

## 4. HORIZON (동결)

| horizon | bar | 역할 |
|---|---|---|
| 1h | 60 | **주요** |
| 2h | 120 | **주요** |
| 4h | 240 | **주요** |
| 8h | 480 | **참고**. verdict를 매기되 `REFERENCE_ONLY`, D6 후보 불가 |

8h는 결과를 보기 전에 이 문서에서 참고용으로 넣기로 결정했다.

각 표본·horizon에서: raw forward `X/E-1`, LONG `X/E-1`, SHORT `1-X/E`, 시나리오별 net(6절), win rate(net>0),
direction hit(gross>0), MAE/MFE(= 보유기간 adverse/favorable excursion, 비용 전).

**funding**: 정산 시각 T가 `ts[t+1] <= T < ts[t+1+h]`이면 LONG은 rate 지불, SHORT는 수취(명목 1 기준).
1h 이상 보유에서는 정산을 지나는 표본이 많으므로 ZERO를 뺀 모든 시나리오에 넣는다.

---

## 5. FEATURE (동결, 24개) 와 BUCKET

### 5.1 D5에서 그대로 가져온 18개

D5 계약 6절 표의 A1~F3, 공식·입력·lookback·결측 정책 **글자 그대로**. 구현도 D5 코드
(`app.crypto.research.features.compute_features`)를 수정 없이 호출한다.
D5에서 WEAK였던 단기 평균회귀(A1·B1·B2·B3·C3)와 perp-index 괴리(E3)가 여기 포함된다.

### 5.2 새 feature 6개

기호는 D5와 같다. `MK` = mark 1m 종가, `IDX` = index 1m 종가. `Ck[j]` = 3절 규칙으로 bar t 시점에 마지막으로
닫힌 k분 bar에서 j개 이전 k분 bar의 종가(j=0이 마지막으로 닫힌 bar).

| id | 그룹 | 공식 | 입력 | lookback | 근거 |
|---|---|---|---|---|---|
| G1 `basis_mark_index` | 7 Mark-vs-Index basis | `ln(MK[t] / IDX[t])` | mark, index | 0 | 요구된 mark-index 축. E3(체결가-index)와 다른 가격원 |
| G2 `basis_mark_index_mean60` | 7 Mark-vs-Index basis | `mean(ln(MK/IDX), t-59..t)` | mark, index | 60 | 순간 괴리 대신 1시간 평균 괴리 |
| M1 `mtf5_trend_1h` | F MTF 5m trend | `ln(C5[0] / C5[12])` | kline | 12 x 5m | 5m 격자 1시간 추세 |
| M2 `mtf15_trend_4h` | F MTF 15m trend | `ln(C15[0] / C15[16])` | kline | 16 x 15m | 15m 격자 4시간 추세 |
| M3 `mtf30_trend_24h` | F MTF 30m trend | `ln(C30[0] / C30[48])` | kline | 48 x 30m | 30m 격자 24시간 추세 |
| M4 `mtf1h_vol_24h` | F MTF 1h vol | `std(ln(C60[j]/C60[j+1]), j=0..23)` (ddof 0) | kline | 25 x 1h | 1h 격자 24시간 변동성 |

lookback은 주요 horizon(1~4h)과 같은 규모에서 한 번 정했다. 결과를 보고 바꾸지 않는다.

### 5.3 정규화와 bucket

D5 계약 5절과 **동일**: UTC 날짜 D의 cutoff는 `[D-30일, D)` 값의 10/30/70/90 분위, 유효값 50% 미만이면 NaN,
`searchsorted(side="right")`. bucket B1(0-10) / B2(10-30) / B3(30-70) / B4(70-90) / B5(90-100).
새 bucket 경계 0개. 미세 threshold 탐색 0회.

셀 수: 24 x 5 x 3 주요 horizon x 2 방향 = **720 주요 셀** (+8h 참고 240셀).

---

## 6. 비용 시나리오 (동결)

수수료 출처: `data/runtime/crypto/reference/fee_source_verification_v1.json` 을
`app.crypto.paper.fees.load_scenarios`로 읽는다(VIP_0 taker 0.00055, maker 0.0002). 코드에 숫자를 다시 쓰지 않는다.
spread/depth 출처: D5 측정 `data/runtime/crypto/d5/realtime_cost_measurement_v1.json`
(23.1시간 top-5 실시간 테이프, Q_ref 0.1 BTC). **BASE = p50 합 1.184e-6, STRESS = 관측 최대 합 2.537e-4** (D5 계약 8절 공식 그대로).

| 시나리오 | 수수료 | spread/depth | funding | 성격 |
|---|---|---|---|---|
| ZERO | 0 | 0 | 0 | 비용 없는 상한선 |
| VIP0_FEE | taker x (1 + X/E) | 0 | 포함 | fee-adjusted |
| **VIP0_BASE** | taker x (1 + X/E) | BASE | 포함 | **주 시나리오** (fee + spread-adjusted) |
| VIP0_STRESS | taker x (1 + X/E) | STRESS | 포함 | 스트레스 |
| HYPOTHETICAL_MAKER_COST | maker x (1 + X/E) | 0 | 포함 | **참고용. 실제 체결 가능으로 해석 금지.** 대기 주문 체결 여부·역선택을 모델링하지 않음 |

VIP_0 등급 적용은 계정이 없어 가정(`OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED`).

**EDGE_TO_COST_RATIO** = `gross 평균 / 비용 평균`, 비용 평균 = `gross 평균 - net 평균`(시나리오별).
비용 평균 <= 0(funding 수취가 수수료보다 큰 경우)이면 ratio는 `INF_COST_NONPOSITIVE`로 표기. 주 ratio는
VIP0_BASE 기준, OOS 합산 표본에서 계산한다. **ratio <= 1이면 net <= 0이므로 실거래 후보가 아니다.**

depth는 SAFE MAX 실행 계약(D4.1)과 무관하다. 여기서 depth는 Q_ref 0.1 BTC 비용 추정에만 쓰고 방향 통계에
섞지 않는다.

---

## 7. 통계 (동결)

셀 = feature x bucket x side x horizon. 계산 단위:

1. **fold별 test**(F1~F9 각각): N, days, mean(시나리오 5종), median(ZERO·VIP0_BASE), win rate, MAE, MFE,
   SE·95% CI(아래 bootstrap)
2. **OOS 합산**(F1~F9 test 전체): 위 전부 + edge_to_cost_ratio
3. **WF 선택 OOS**(9절): train으로 방향이 선택된 fold의 test 표본만 합친 것
4. train 요약(fold별 선택 판단용): VIP0_BASE net mean

**block bootstrap**: 일별 (합, 개수)를 연속 7 UTC일 블록으로 재표집(moving block), 1,000회, seed 20260925.
비율 추정량 합/개수. SE = 재표집 표준편차. 95% CI = 2.5/97.5 분위. 8h 보유 표본의 겹침(최대 480 bar)은
7일 블록 안에 들어간다.

**p-value로 후보를 고르지 않는다.** 모든 판정은 CI와 함께 **비용 대비 크기**(ratio)를 본다.

**다중검정 규모 (사전 기록)**: 주요 720셀. 귀무가설에서 OOS 95% CI 하한 > 0은 셀당 약 2.5%라 약 18셀이
우연히 통과할 수 있다. 그래서 SURVIVE는 CI 하나가 아니라 fold 일관성·WF 선택·ratio·robustness를 모두 요구한다.
feature끼리 상관이 커서 셀은 독립이 아니다.

**표본 크기 근거 (MDE)**: 4h 보유 1m 표본은 겹침 때문에 비겹침 환산 N_eff = N / h_bar. OOS 합산 셀 N_eff >= 200
을 요구한다. D5에서 관측한 30m gross 표본 SE 규모를 쓰지 않고, 일반 논리로 정한다: 4h BTC 수익률 표준편차가
대략 1% 규모이므로 N_eff 200이면 SE ~ 7bp로 11bp 비용과 같은 자릿수의 효과를 가릴 수 있는 최소선이다.
fold별로는 N >= 500 이고 days >= 20인 fold만 판정 분모에 넣는다.

---

## 8. Regime / Robustness (동결)

라벨 정의는 D5 계약 10절과 같다. 단 변동성 cutoff는 **F1 train(2021-03-04 ~ 2022-01-01)** 의 24시간 1m 변동성
30/70 분위로 한 번 정해 고정한다(OOS 이전 데이터만).

| 축 | 라벨 |
|---|---|
| 추세 | 직전 7일 로그수익 > +5% BULL, < -5% BEAR, 그 외 SIDEWAYS |
| 변동성 | HIGH / MID / LOW (위 cutoff) |
| UTC 세션 | 진입 bar 시각 00-06 / 06-12 / 12-18 / 18-24 (+24시간 표는 보고만) |
| 연도 | 진입 bar UTC 연도 2022~2026 |
| fold | F1~F9 |

전부 **OOS 합산 표본의 VIP0_BASE net** 으로 판정한다. 라벨 N < 200이면 판정에서 제외.

**Robustness PASS (전부)**:
- R1 추세 3라벨 중 net > 0 이 2개 이상
- R2 변동성 3라벨 중 2개 이상
- R3 UTC 세션 4개 중 3개 이상
- R4 연도 중 net > 0 비율 >= 2/3
- R5 **leave-one-year-out**: net 합이 가장 큰 연도를 빼도 나머지 OOS net mean > 0
- R6 **leave-one-trend-out**: net 합이 가장 큰 추세 라벨을 빼도 나머지 net mean > 0

R5·R6이 "특정 1년 / 특정 regime에만 의존"을 막는 규칙이다.

---

## 9. VERDICT (동결)

주 시나리오 VIP0_BASE. "방향 일관"은 ZERO gross mean이 셀 방향으로 > 0.

**WF 선택**: fold k에서 셀의 **train** VIP0_BASE net mean > 0 이면 그 fold에서 "선택됨". 선택된 fold들의 test
표본을 합친 것이 WF 선택 OOS.

**SURVIVE** (주요 horizon, 아래 전부):
- S1 fold 방향 일관: 판정 가능 fold 중 test gross > 0 인 비율 >= 2/3
- S2 fold net: 판정 가능 fold 중 test VIP0_BASE net > 0 인 비율 >= 1/2
- S3 OOS 합산 VIP0_BASE net mean > 0 이고 95% CI 하한 > 0 (= CI 반폭 < net mean, "CI가 지나치게 넓지 않음"의 정의)
- S4 WF 선택: 선택된 fold >= 3개이고 WF 선택 OOS VIP0_BASE net mean > 0
- S5 edge_to_cost_ratio (OOS 합산, VIP0_BASE) >= 1.2 (비용 모델 오차 20% 여유)
- S6 OOS 합산 VIP0_STRESS net mean > 0
- S7 OOS 합산 N_eff >= 200, 판정 가능 fold >= 6
- S8 Robustness PASS (8절)

**WEAK** (SURVIVE 아님):
- W1 S3 통과(OOS net 양수·CI 하한 양수)했으나 다른 조건 실패
- W2 비용에 죽은 gross: OOS gross 95% CI 하한 > 0, S1 통과, S7 통과, 그러나 S3 실패

**REJECT**: 그 밖.

8h 셀은 같은 규칙으로 라벨을 붙이되 SURVIVE/WEAK는 `REFERENCE_ONLY(<라벨>)`로 바꾸고 D6 후보에서 뺀다.

결과를 본 뒤 위 숫자(2/3, 1/2, 3, 1.2, 200, 6)를 옮기지 않는다.

---

## 10. DECISION GATE (동결, 기계적 판정)

| CASE | 조건 | 분기 |
|---|---|---|
| **A** | 주요 horizon SURVIVE >= 1 | D6 Score 후보로 승격 |
| **B** | SURVIVE 0, 그리고 W2 셀 중 OOS gross가 HYPOTHETICAL_MAKER 비용을 1.2배 이상 넘고 (maker ratio >= 1.2) maker 시나리오 OOS net 95% CI 하한 > 0 인 셀이 >= 1 | D5.2-MAKER 연구 후보 |
| **C** | A도 B도 아님 | 기존 price/OI/funding feature 축 종료, New Derivatives Data 연구로 |

**CASE D**(외부 변수 필요성)는 기계적으로 판정하지 않는다. A/B/C 판정 뒤 결과 문서에 근거가 있을 때만
"추가 권고"로 적는다. D는 A/B/C를 대체하지 않는다.

---

## 11. 금지된 튜닝 (명시)

- 결과를 본 뒤 horizon 추가·삭제, feature 추가·삭제·공식 변경, bucket 경계 변경, fold 경계 변경, 비용 수치 변경,
  verdict 숫자 변경
- feature 2개 이상 조합, regime 필터를 붙인 셀 생성, 방향을 결과에 맞춰 뒤집기
- 특정 fold·연도·시간대만 골라 보고하기(전 셀 JSON을 필터 없이 남긴다)

---

## 12. 산출물

| 파일 | 내용 |
|---|---|
| `docs/crypto/CRYPTO_D5_1_LONG_HORIZON_CONTRACT_V1.md` | 이 문서(동결) |
| `docs/crypto/CRYPTO_D5_1_LONG_HORIZON_RESULTS_V1.md` | 결과 |
| `docs/crypto/CRYPTO_D5_1_CANDIDATE_REGISTRY_V1.md` | 후보 대장(생성물) |
| `backend/app/crypto/research/long_horizon.py` 외 | 코드 (D5 모듈은 수정하지 않음) |
| `data/runtime/crypto/d5_1/` | 동결 기록, 전 셀 결과(gitignore) |
