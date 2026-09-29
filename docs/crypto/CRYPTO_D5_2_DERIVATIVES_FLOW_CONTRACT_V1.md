# US-B CRYPTO D5.2 Derivatives Flow Edge Study 사전등록 계약 V1

작성 2026-09-27. 이 문서의 sha256을 `data/runtime/crypto/d5_2/contract_freeze_v1.json`에 기록한 뒤에만 feature와 수익률을 계산한다.
`derivatives_flow.run()`은 실행 시 hash를 다시 계산하고 다르면 거부한다.
데이터 가용성 근거는 `CRYPTO_D5_2_DERIVATIVES_DATA_PREFLIGHT_V1.md`(Phase 1, 이 계약보다 먼저 완료)와 `CRYPTO_DERIVATIVES_SOURCE_MATRIX_V1.md`.

## 0. 범위와 금지

| # | 규칙 |
|---|---|
| Z1 | 거래 대상은 Bybit BTCUSDT Linear Perpetual 1종목. 목표 수익률은 D2 Bybit 1m kline(D5·D5.1과 같은 파일) |
| Z2 | 새 입력은 Phase 1에서 LONG_HISTORY로 확인된 무료 공개 데이터만: Binance USDT-M perp last·mark·index 1m, Binance spot 1m, Binance funding, Binance metrics OI 5m, **Binance COIN-M BTCUSD 분기물 개별 계약 1m + COIN-M BTCUSD index 1m**, OKX BTC-USDT-SWAP last·mark 5m, OKX BTC-USDT index 5m |
| Z3 | **청산 데이터는 FORWARD_ONLY라 이 계약에서 쓰지 않는다.** Q1·Q2(청산)는 `CRYPTO_FORWARD_DATA_PLAN_V1.md`로 넘긴다 |
| Z4 | OKX funding(3개월)·OKX OI(5m 5일)는 장기 이력이 없어 쓰지 않는다. funding·OI dispersion은 Bybit vs Binance 2거래소 |
| Z5 | 운영 paper 계좌·서버·주문·API key 0. Score·확률·AUTO·최종 전략 0. feature 조합은 12절의 사전 정의 1개(C1)뿐 |
| Z6 | commit/push 금지 |

## 1. 연구 창과 fold (D5.1과 동일)

- 입력 시작 2021-02-01, 표본 시작 **2021-03-04**, 끝 **2026-09-23 미포함**.
- Walk-forward F1~F9: D5.1 계약 2절 표를 **그대로** 쓴다(확장 train, 6개월 test, F9는 2026-01-01 ~ 2026-09-23). OOS = 2022-01-01 ~ 2026-09-23.
- Embargo: 결정 bar t부터 청산 bar t+1+h가 fold 경계를 가로지르면 버린다(D5.1 동일).
- **이 기간은 D5·D5.1에서 이미 본 기간이다.** 새 feature에 대해서도 untouched holdout이라고 부르지 않는다. D5.1에서 basis(perp가 index보다 쌀 때 LONG)가 해마다 약해졌다는 사실을 알고 설계했다.

## 2. PIT / 실행 (D5.1 3절 그대로)

- 결정은 Bybit 1m bar t 종가 뒤. 진입 E = open[t+1], 청산 X = open[t+1+h]. funding은 보유 중 정산분 포함.
- **5m 격자**: 모든 외부 feature는 UTC 5분 경계 T에서 계산한다. bar t의 결정은 `floor((ts[t] + 60s) / 5m) * 5m`에 끝난 5m bar까지만 본다.
- 5m 종가: Bybit·Binance는 그 5분의 마지막 1m 종가, OKX는 5m 캔들 종가(OKX 캔들 ts = 시작 시각).
- funding은 정산 시각에 가용. OI(Bybit 5m, Binance metrics 5m)는 **기록 시각 + 5분**에 가용(D2 규칙을 Binance에도 적용).
- 한 거래소라도 값이 없으면 그 feature는 NaN. **0으로 채우지 않는다.** stale 이월은 5m 1개까지만(그 이상 결측이면 NaN).

## 3. HORIZON (동결)

15m, 30m, 1h, 2h, 4h = **주요**. 8h = 참고(`REFERENCE_ONLY`, D6 후보 불가). 결과를 본 뒤 바꾸지 않는다.

## 4. FEATURE (동결, 17개)

기호: T = 5m 경계. `P_v`/`MK_v`/`IX_v` = 거래소 v의 perp last / mark / index 5m 종가(v ∈ {bybit, binance, okx}). `S` = Binance spot 5m 종가.
`FR_v` = 가장 최근 확정 funding rate. `OI_v` = 가용한 최신 OI. std는 ddof 0. Δk x = x[T] - x[T - k·5m].

| id | 그룹 | 공식 | 질문 |
|---|---|---|---|
| X1 `xbasis_bybit_vs_peers` | Cross-exchange | ln P_bybit - mean(ln P_binance, ln P_okx) | Q3 Bybit 할인/할증 |
| X2 `xlast_dispersion` | Cross-exchange | std_v(ln P_v) | Q3 |
| X3 `xmark_dispersion` | Cross-exchange | std_v(ln MK_v) | Q3 |
| X4 `xbasis_dispersion` | Cross-exchange | std_v(ln(MK_v / IX_v)) | Q3 perp basis dispersion |
| X5 `xfunding_spread` | Cross-exchange | FR_bybit - FR_binance | Q3 funding dispersion |
| X6 `xoi_share_chg_1h` | Cross-exchange | Δ12 ln(OI_bybit / OI_binance) | Q3 OI dispersion |
| S1 `spot_basis_bybit` | Spot-perp | ln(P_bybit / S) | Q4 |
| S2 `spot_basis_z24h` | Spot-perp | (S1 - mean_288(S1)) / std_288(S1), 창 = 직전 288개 5m(현재 포함), 유효 < 50%면 NaN | Q4 |
| S3 `spot_basis_chg_1h` | Spot-perp | Δ12 S1 | Q4 압축/확대 |
| S4 `spot_basis_composite` | Spot-perp | mean_v ln(P_v / S) | Q4 |
| T1 `ts_near_ann` | Term structure | ln(F_near / IX_cm) x 365일 / (만기 - T) | Q5 |
| T2 `ts_far_ann` | Term structure | ln(F_far / IX_cm) x 365일 / (만기 - T) | Q5 |
| T3 `ts_slope` | Term structure | T2 - T1 | Q5 |
| T4 `ts_inverted` | Term structure (이진) | T3 < 0 이면 1 | Q5 역전 |
| T5 `ts_near_chg_24h` | Term structure | Δ288 T1 | Q5 contango collapse |
| C1 `dislocation_event` | 조합 1개 (이진) | S1 bucket = B1 **그리고** Bybit OI 1h 로그변화 < 0 **그리고** 변동성 regime = HIGH (D5.1 8절 cutoff) | Q4 예시 그대로 |
| (참고) | | Bybit 1h OI 로그변화 = ln(OI[t] / OI[t-60]) (D5 grid, PIT 지연 적용) | C1 입력 |

**기간구조 원천 (동결 전 QC로 결정)**: `F` = Binance **COIN-M** BTCUSD 분기물 1m, `IX_cm` = COIN-M BTCUSD index 1m(둘 다 USD 호가).
USDT-M 분기물은 동결 전 QC에서 원월물 커버리지가 2021년 1% / 2022년 0.1% / 2023년 33%로 확인돼(2023년 하반기 전에는 한 번에 한 계약만 상장)
SHORT_HISTORY로 분류하고 쓰지 않는다. COIN-M은 당월물 약 100%, 원월물 연도별 91~93% (`data/runtime/crypto/d5_2/qc_v1.json`). 이 선택은 수익률을 보기 전에 했다.
**기간구조 롤 규칙**: 분기물 만기 = 계약 코드 YYMMDD의 08:00 UTC. near = 만기 - T > **7일**인 계약 중 만기가 가장 가까운 것, far = 그다음 만기 계약. 계약 1m 데이터가 없는 5분은 NaN.

**feature lookback·창·7일 롤 기준은 결과를 보기 전에 한 번 정했다.** 바꾸지 않는다.

## 5. Bucket (D5 5절 동일)

연속 feature 14개(X1~X6, S1~S4, T1·T2·T3·T5): 1m 격자로 옮긴 값에 D5 `bucketize` 그대로(UTC 날짜 D의 cutoff = [D-30일, D)의 10/30/70/90 분위, 유효 50% 미만이면 NaN). B1 ~ B5.
이진 feature 2개(T4, C1): 상태 1인 표본만 한 셀.

셀 수: 14 x 5 x 2 방향 x 5 주요 horizon = 700 + 2 x 2 x 5 = 20 → **주요 720셀** (+ 8h 참고 144셀).

## 6. 비용 (D5.1 6절, maker 제외)

| 시나리오 | 내용 |
|---|---|
| ZERO | 0 (상한) |
| VIP0_FEE | taker x (1 + X/E) + funding |
| **VIP0_BASE** | taker x (1 + X/E) + BASE spread/depth + funding (**주 시나리오**) |
| VIP0_STRESS | taker x (1 + X/E) + STRESS + funding |

taker·BASE·STRESS 값은 D5.1과 같은 출처·함수로 읽는다(`fee_source_verification_v1.json`, D5 `realtime_cost_measurement_v1.json`).
**Maker 시나리오는 넣지 않는다**(E1 BYBIT_CF_NEGATIVE).

## 7. 통계 (D5.1 7절 그대로)

fold별 test, OOS 합산, WF 선택 OOS. 7 UTC일 moving block bootstrap 1,000회, seed **20260927**. edge_to_cost_ratio = gross / (gross - net).
N_eff = N / h_bar. fold 판정 분모는 N ≥ 500 이고 days ≥ 20인 fold.

## 8. Regime (D5.1 8절 그대로)

추세(7일 ±5%), 변동성(F1 train 24h 1m 변동성 30/70 분위), UTC 세션 4개, 연도, fold. R1~R6 동일.

## 9. VERDICT (D5.1 9절 그대로, 주요 horizon 집합만 15m~4h)

SURVIVE = S1 ~ S8 전부(fold 방향 ≥ 2/3, fold net ≥ 1/2, OOS VIP0_BASE net CI 하한 > 0, WF 선택 ≥ 3 fold 이고 양수, ratio ≥ 1.2, STRESS net > 0, N_eff ≥ 200 이고 판정 fold ≥ 6, Robustness PASS).
WEAK = W1(S3 통과, 다른 조건 실패) 또는 W2(gross CI 하한 > 0, S1·S7 통과, S3 실패). REJECT = 그 밖. 8h는 REFERENCE_ONLY.

## 10. DECISION GATE (기계적)

| CASE | 조건 | 분기 |
|---|---|---|
| **A** | 주요 horizon SURVIVE ≥ 1 | D6 Score 후보 |
| **C** | SURVIVE 0 | cross-exchange·spot-perp·기간구조 축(이 데이터·이 창) 종료. 청산은 forward 수집으로 |

## 11. 재포장 검사 (사전 등록, 판정 비변경)

SURVIVE·WEAK 셀의 feature마다 OOS 표본(60분 간격 추출)에서 D5.1 G1 `basis_mark_index`, D5 E3 `fund_premium`와의 Spearman 상관을 계산한다.
|ρ| ≥ 0.7이면 대장에 `REPACKAGED_BASIS` 표시. SURVIVE를 취소하지는 않지만 D6 승격 권고 전에 반드시 서술한다.

## 12. 금지된 튜닝

bucket 경계·feature 공식·lookback·롤 기준(7일)·horizon·fold·비용 변경, 결과를 본 뒤 거래소 선택(가장 좋은 거래소만), 연도·regime 선택, C1 외 조합 추가, threshold sweep. 전부 금지.

## 13. 산출물

`docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_RESULTS_V1.md`, `CRYPTO_D5_2_CANDIDATE_REGISTRY_V1.md`(생성물),
코드 `backend/app/crypto/research/derivatives_flow.py`, 데이터 `data/runtime/crypto/d5_2/`(gitignore).
