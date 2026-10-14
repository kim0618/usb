# C1 BINANCE VENUE RECHECK V1 — 사전등록 계약

작성 2026-10-13. RECHECK ONLY, LOCAL ONLY. 새 전략·새 규칙·운영 변경·다운로드 0.
이 문서는 Binance 기준 결과(신호·수익)를 하나도 계산하기 전에 동결한다. 동결 전 계산은 데이터 QC(행 수·결측·시각 정렬)뿐이다.

## 0. 정본 C1 (변경 금지)

정본 = `docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md` 4절(sha256 `c49cd0e5…bc8cf81`, 동결기록 일치)
+ `backend/app/crypto/research/derivatives_flow.py` run()의 C1 3줄, 그것을 그대로 옮긴 `c1_exit/e1.py::c1_state`.

C1[t] = (S1 bucket == B1) AND (ln(OI[t]/OI[t-60]) < 0) AND (rv24h[t] > 0.0010924950359531157)

- S1 = ln(perp 5m close / Binance spot 5m close), 5m 경계 floor((ts+60s)/5m)*5m, carry 1회. bucket = 직전 30 UTC일 1분 계열 분위 (0.10, …) 기준 B1(하위 10%).
- OI = 5m OI, 기록시각 + 5분에 가용(as-of). rv24h = 1분 로그수익률 1440봉 표준편차(ddof 0).
- 변동성 cutoff는 **동결 숫자 0.0010924950359531157을 그대로** 쓴다. Binance 데이터로 재추정하지 않는다(재추정 = threshold 변경).
- 방향 = LONG-only(SHORT 정의 없음). 신호 = FALSE→TRUE 전이(episode). 진입 open[t+1], 4H 벤치마크 청산 open[t+1+240].

## 1. 이식성 판정 (rule portability)

| 항목 | 형태 | venue 단위 의존 |
|---|---|---|
| S1 | 로그 비율의 rolling 분위 순위 | 없음(자기 정규화) |
| OI | ln(OI_t/OI_t-60)의 부호 | 없음(Bybit OI는 BTC, Binance sum_open_interest도 BTC, 어느 쪽이든 비율) |
| rv24h | 무차원 수익률 표준편차, 고정 cutoff | 단위 의존 없음. 단 cutoff가 Bybit F1 train에서 추정됨 → 그대로 사용, HIGH 비율 차이는 보고만 |

raw absolute OI threshold가 없으므로 VENUE_NONPORTABLE_RULE 아님. 어댑터만 교체한다.

## 2. Binance 어댑터 (데이터만 교체)

| 입력 | Bybit 정본 | Binance 재검증 |
|---|---|---|
| perp 1m OHLC | D2 `historical/kline_1m` (Bybit linear) | `d5_2/archive/binance/futures/um/{monthly,daily}/klines/BTCUSDT/1m` |
| spot 5m | Binance spot (양쪽 동일) | 동일 |
| OI | Bybit `open_interest_5m` +5m | Binance metrics `sum_open_interest` (create_time) +5m, as-of (연구 규칙 그대로, staleness 가드 없음) |
| funding | Bybit funding | Binance `fundingRate` (calc_time을 분 단위 floor로 정규화. 원본은 +1~수십 ms) |
| mark 1m | Bybit mark | Binance markPriceKlines |
| index 1m | Bybit index | Binance indexPriceKlines (D6만 사용) |
| 수수료 | Bybit VIP0 taker 0.055% | **Binance 계정 실측 taker 0.05%**(`binance_live/CRYPTO_BINANCE_LIVE_READ_ONLY_VALIDATION_V2.md` §68, `/fapi/v1/commissionRate`) |

Binance 1m 결측 분은 NaN으로 두며 채우지 않는다(연구 규칙상 24h rv가 그 뒤 1440봉 NaN → NOT_ELIGIBLE). 결측 수 보고.
Binance 경로에서 `data/runtime/crypto/BTCUSDT/historical`(Bybit) 읽기는 금지, 테스트로 강제.

## 3. 구간

- W_SIG (신호 parity): 결정 봉 2021-03-04 00:00Z ~ 2026-09-23 00:00Z(배타). OOS 부분창 2022-01-01~.
- W_OUT (성과 parity): 진입 ≥ 2022-01-01, **진입 + 480분 ≤ 2026-09-01 00:00Z**. Binance funding 로컬 자료가 2026-08까지이므로 9월은 양 venue 모두 제외. Bybit 기준선도 같은 창으로 다시 계산해 비교한다. 정본 전체창 수치는 재현 확인용으로 따로 둔다.
- D6-C: 양 venue 모두 grid를 2026-09-01 00:00Z에서 자른다.

## 4. 기준선 재현 (불일치 시 STOP)

현재 코드로 Bybit C1을 재계산해 다음과 일치해야 Binance 단계로 간다.
- D5.2 OOS 240m 마스크 C1 봉 수 18,498, vol cutoff 동결값.
- W_SIG episode 수 2,579 (CRYPTO_C1_SIGNAL_V1 §4).
- E1 trade 1,603건의 signal_bar·entry_ts·net_bp (`e1_trades_v1.parquet`) 전부 일치(net 차 ≤ 1e-9).

## 5. Signal parity 정의

episode 시작 시각(결정 봉) 집합 Y(Bybit), N(Binance). 허용오차 τ∈{0, 1, 5}분에서 |차|가 작은 쌍부터 1:1 탐욕 매칭.
precision = 매칭/|N|, recall = 매칭/|Y|, F1. 봉 단위 TRUE 마스크 Jaccard. timing 분석용 "같은 event" = ±60분 1:1 매칭.
방향: 양쪽 다 LONG-only라 direction disagreement는 구조적으로 0(보고는 함).

**분류 (W_SIG 전체, primary)**
- EXACT_SAME: F1(τ=0) ≥ 0.95
- TIME_SHIFTED_SAME: 아니고 F1(τ=5) ≥ 0.80
- VENUE_SENSITIVE: 0.50 ≤ F1(τ=5) < 0.80
- VENUE_DIVERGENT: F1(τ=5) < 0.50

## 6. 성과 사다리 (fee 효과와 venue 효과 분리)

| 단계 | 신호 | 가격·funding | 수수료 |
|---|---|---|---|
| L0 | Bybit | Bybit | Bybit 0.055% |
| L1 | Bybit (같은 결정 봉) | Binance | Bybit 0.055% |
| L2 | Binance | Binance | Bybit 0.055% |
| L3 | Binance | Binance | Binance 0.05% (재검증 primary) |

L0→L1 = 가격/funding venue 효과, L1→L2 = 신호 효과, L2→L3 = 수수료 효과.
회계는 E1 Executor(D6-C PaperEngine, SyntheticBook)를 그대로 쓰고 수수료만 엔진 설정에서 교체한다.

## 7. 연구별 재판정 (규칙·게이트 무수정, 같은 W_OUT에서 L0 vs L3)

| 연구 | 재판정 대상 라벨 |
|---|---|
| C1 original (D5.2 `C1-EVENT-240m-LONG`) | D5.2 verdict (SURVIVE/WEAK/REJECT) + 사유, bar 가중 VIP0_BASE |
| C1 E1 | `gate()` (E1_POSITIVE_BASELINE / INCONCLUSIVE / NEGATIVE) |
| C1 E2 | 6후보 status (`e2.status`), PROMISING 수. B4 trigger 내부 TAKER 상수(0.00055)는 **규칙 일부로 보고 그대로 둔다** |
| D6 / D6-C | `gates.verdict` (FDN-V1 PASS/FAIL) |
| Manual Context Exit R0.1 | 6TF verdict(B PASS·후보 0). K1→Binance 1m, K15→Binance aggTrades(로컬 14일), 파생→Binance. 2026-09 funding 결측은 0으로 두지 않고 해당 거래 수를 보고 |

라벨 규칙: Bybit 재현 실패 → NOT_REPRODUCIBLE / Binance로 계산 불가 → INVALID_UNDER_BINANCE / 라벨 변경 → CONCLUSION_CHANGED /
라벨 동일·주 지표 |Δ| > 1bp → NUMERICALLY_CHANGED_ONLY / 그 외 UNCHANGED.

## 8. 최종 게이트 (위에서부터 첫 해당)

- E. C1_RECHECK_BLOCKED: 4절 기준선 불일치, 또는 Binance 신호 입력 결측으로 W_SIG 계산 불가.
- D. C1_VENUE_SENSITIVE_NOT_SAFE_FOR_BINANCE: 5절 분류가 VENUE_SENSITIVE 또는 VENUE_DIVERGENT.
- C. C1_BINANCE_RESEARCH_CONCLUSION_CHANGED: 7절 중 하나라도 CONCLUSION_CHANGED 또는 INVALID_UNDER_BINANCE.
- A. C1_BINANCE_PARITY_PASS: 분류 EXACT_SAME/TIME_SHIFTED_SAME 이고 7절 전부 UNCHANGED.
- B. C1_BINANCE_NUMERIC_DIFFERENCE_ONLY: 그 외.

## 9. 사후 수정 금지

threshold·cutoff·dead band·horizon·exit·filter·방향·regime·confidence 부분집합 탐색 금지. 결과를 본 뒤의 아이디어는 새 lineage로만.
결정성: 전 산출물 2회 실행 바이트 동일 확인.
