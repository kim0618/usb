# C1 BINANCE VENUE RECHECK V1 보고서

작성 2026-10-14. 대상: C1 신호를 보고 Binance에서 수동/PAPER로 거래하는 운영자와, C1 계열 연구를 다시 여는 사람.
RECHECK ONLY. C1 규칙·threshold·exit·수수료 방식 변경 0, 다운로드 0, 운영 접근 0, 배포 0.

- 계약: `docs/crypto/c1_binance_recheck_v1/CONTRACT_C1_BINANCE_RECHECK_V1.md` sha256 `5c544c32…0208` (Binance 결과 계산 전 동결, `data/research/crypto/c1_binance_recheck_v1/contract_freeze_v1.json`)
- 코드: `backend/app/crypto/research/c1_binance_recheck/{data,recheck,d6c_venue,mce_venue}.py` (D5.2·E1·E2·D6-C·MCE 모듈은 import만, 무수정)
- 산출: `data/research/crypto/c1_binance_recheck_v1/recheck_v1.json` (sha `a160df73…`, 2회 실행 바이트 동일), `e0_rows_v1.parquet`, `d6c_binance_cut_binance_fee_v1.json`, `mce_*_v1.json`
- 테스트: `backend/tests/crypto/test_c1_binance_recheck.py` 9 passed

## U. 최종 판정 (먼저)

**D. C1_VENUE_SENSITIVE_NOT_SAFE_FOR_BINANCE**

같은 C1 규칙을 Binance 입력으로 돌리면 **다른 신호 집합**이 나온다. ±5분 F1 = **0.24**(계약 기준 < 0.50 = VENUE_DIVERGENT).
원인은 OI 1시간 감소 조건이다. Bybit OI와 Binance OI의 1h 변화는 부호 일치 58~64%, 상관 0.28이다.
신호와 별개로 성과 결론도 바뀌었다(C 조건도 충족). D5.2의 유일한 W1 셀은 Binance에서 **REJECT**가 되고,
E2의 B1·B3은 INCONCLUSIVE에서 UNPROMISING이 된다. Manual Context Exit은 B에서 D(후보 2)가 되지만 p 해상도 한계에 걸린 취약한 변화다.
D6-C는 FAIL 그대로다. 게이트 순서(E>D>C>B>A)상 최종은 D다.

| 연구 | 라벨 |
|---|---|
| C1 original (D5.2) | CONCLUSION_CHANGED (WEAK W1 → REJECT) |
| C1 E1 | NUMERICALLY_CHANGED_ONLY (+10.27 → +5.48bp, 둘 다 INCONCLUSIVE) |
| C1 E2 | CONCLUSION_CHANGED (B1·B3 → UNPROMISING, PROMISING 0 유지) |
| D6 / D6-C | UNCHANGED (FAIL, 같은 실패 게이트, Δnet −0.39bp) |
| Manual Context Exit R0.1 | CONCLUSION_CHANGED (B → D, 단 Holm 해상도 한계, R절) |

---

## A. Authoritative C1

| 항목 | 값 |
|---|---|
| contract | `docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md` 4절, sha256 `c49cd0e577a8b81321c943a8eb6fe20b59635592816191efc30f67454bc8cf81` (동결기록 일치) |
| code | `research/derivatives_flow.py` run() C1 3줄 (sha `4f7aa231…`) = `research/c1_exit/e1.py::c1_state` (sha `1709849a…`). 운영 `app/crypto/c1/` 엔진은 이것과 2,921,760봉 parity PASS (`CRYPTO_C1_SIGNAL_V1.md` 4절) |
| 정의 | S1 bucket B1 AND ln(OI_t/OI_t-60) < 0 AND rv24h > 0.0010924950359531157 |
| S1 | ln(Bybit perp 5m close / Binance spot 5m close), 직전 30 UTC일 분위 하위 10% |
| threshold | S1 = rolling 분위(자기 정규화), OI = 부호(엄격 < 0), vol = F1 train(2021-03-04~2022-01-01) 동결값 |
| horizon | 공식 4H(240분). 참고 30m/1h/2h/8h |
| 방향 | **LONG-only**. SHORT 정의 없음(D5.2 C1 SHORT는 LONG의 음수로 REJECT) |
| entry timing | 결정 봉 t 종가 → 진입 open[t+1], 4H 벤치마크 청산 open[t+1+240] |
| cooldown | 없음. 신호 = FALSE→TRUE 전이(episode), 조건이 한 번 끊겨야 다음 신호 |
| exit contract | 연구: 4H 고정(E0). E2 후보 B1/B3/B4/C1x/C2x/C3x, max hold 8H |
| cost | Bybit VIP0 taker 0.055% x 2 + micro 0.0118bp + 보유 중 funding (E1/E2는 D6-C PaperEngine) |
| price/OI/funding source | Bybit linear 1m / Bybit 5m OI(+5분 가용) / Bybit funding. 현물만 Binance |

## B. 현재 venue lineage

| 연구 | price | OI | funding | mark/index | 실행 가정 | fee |
|---|---|---|---|---|---|---|
| C1 신호 생성 | Bybit perp + Binance spot | Bybit | (비입력) | - | - | - |
| C1 운영 엔진 | Bybit REST kline + Binance spot REST | Bybit REST | Bybit(shadow 비용) | - | 운영자가 Binance에서 거래 | shadow는 Bybit 0.055% |
| C1 E1/E2 | Bybit 1m | Bybit | Bybit | Bybit mark(손절 B1) | D6-C PaperEngine, 합성 호가 | Bybit 0.055% |
| D6 / D6-C (FDN-V1) | Bybit 1m | Bybit | Bybit | Bybit mark/index | 동일 | Bybit 0.055% |
| Manual Context Exit R0.1 | Bybit 1m(K1) + Bybit 체결(K15) | Bybit | Bybit | Bybit index | 고정비용 | 11.25bp(Bybit 기준) |

## C. Binance 데이터 lineage

전부 `data/runtime/crypto/d5_2/archive/binance/` (D5.2가 9월에 받은 data.binance.vision 파일, CHECKSUM 검증분). 신규 다운로드 0.

| 계열 | 파일 | 범위 | 결측 |
|---|---|---|---|
| USD-M perp 1m OHLC | `futures/um/{monthly,daily}/klines/BTCUSDT/1m` | 2021-02-01~2026-09-22 | **0분** |
| mark 1m | `markPriceKlines` | 동일 | 12,962분(10일: 2021-07, 2022-07-31, 2022-10-02, 2023-02-24, 2024-08-12, 2026-06-29 등) |
| index 1m | `indexPriceKlines` | 동일 | 18,727분(15일, D6만 사용) |
| OI | `daily/metrics` sum_open_interest(BTC), create_time+5분 | 2021-02-01~2026-09-22 | 584/593,280 (0.10%), 15분 초과 stale 2,126분 |
| funding | `monthly/fundingRate` | 2021-02~**2026-08** | 6,114건, 전부 8h 간격, calc_time의 +ms를 분 단위 floor |
| spot 5m | Bybit 경로와 **같은 배열**(검증: 동일) | | |
| fee | 계정 실측 maker 0.02% / **taker 0.05%** (`binance_live/CRYPTO_BINANCE_LIVE_READ_ONLY_VALIDATION_V2.md` 68행, `/fapi/v1/commissionRate`) | | |

Binance 경로가 연 파일 2,394개, 그중 Bybit D2 폴더(`BTCUSDT/historical`) 0개. Binance perp 5m 계열은 D5.2 `binance_last`와 비트 동일.
예외 1건: MCE 15s는 `BTCUSDT/historical/aggtrades_daily`를 읽는다. 이 폴더는 경로 이름과 달리 **Binance** aggTrades다(파일명·CHECKSUM 형식).

## D. Overlap coverage

| 구간 | 시작 | 끝(배타) | 용도 |
|---|---|---|---|
| W_SIG | 2021-03-04T00:00Z | 2026-09-23T00:00Z | 신호 parity (D5.2 sample 전체) |
| OOS | 2022-01-01T00:00Z | 2026-09-23T00:00Z | E1과 같은 신호 범위 |
| W_OUT | 진입 ≥ 2022-01-01 | 진입+480분 ≤ 2026-09-01T00:00Z | 성과 비교 (Binance funding 로컬 자료가 8월까지) |

실측: 두 venue 모두 **2026-08-21 이후 C1 신호가 0건**이다(Bybit 마지막 진입 08-21 23:50, Binance 08-20 01:05).
그래서 W_OUT 절단이 C1 성과 표본을 하나도 지우지 않는다. L0는 정본 E1 1,603건과 같다.
D6-C만 9월 거래 1건(Bybit 기준)이 잘린다.

## E. 기준선 재현 = PASS (Binance 단계 진입 허용)

| 항목 | 정본 | 현재 코드 재현 |
|---|---|---|
| D5.2 OOS 240m 마스크 C1 봉 | 18,498 | **18,498** |
| vol cutoff | (0.000759…, 0.0010924950359531157) | 일치(atol 1e-15) |
| W_SIG episode | 2,579 | **2,579** |
| OOS episode = E1 trade | 1,603 | **1,603** |
| signal_bar / entry_ts | e1_trades_v1.parquet | **전부 일치** |
| E1 4H net_bp | e1_trades_v1.parquet | **최대 차이 0.0** |
| 범용 C1 함수(cutoff 주입판) vs e1.c1_state | | **마스크 비트 동일** |
| D5.2 C1 셀 (full) | WEAK, W1, +19.278bp | **WEAK, W1, +19.278bp** |
| D6-C (같은 창) | 1,259건 −5.66bp | 저장 trades 재집계 1,258건 −5.70bp (9월 1건 제외) |

## F. Binance signal count

| | Bybit | Binance |
|---|---|---|
| W_SIG 신호(episode) | 2,579 | **2,751** |
| W_SIG TRUE 봉 | 29,655 | 37,975 |
| OOS 신호 | 1,603 | **1,726** |
| OOS TRUE 봉 | 18,499 | 19,235 |
| LONG / SHORT | 2,579 / 0 | 2,751 / 0 |
| 고변동 봉 비율 (W_SIG) | 11.10% | 11.78% |

## G. Signal parity (W_SIG)

| 허용오차 | 매칭 | Bybit-only | Binance-only | precision | recall | F1 |
|---|---|---|---|---|---|---|
| 정확히 같은 분 | 325 | 2,254 | 2,426 | 0.118 | 0.126 | **0.122** |
| ±1분 | 330 | 2,249 | 2,421 | 0.120 | 0.128 | 0.124 |
| ±5분 | 641 | 1,938 | 2,110 | 0.233 | 0.249 | **0.241** |
| ±60분 (참고) | 1,292 | 1,287 | 1,459 | 0.470 | 0.501 | 0.485 |

봉 단위 TRUE Jaccard **0.182**. 분류 = **VENUE_DIVERGENT**(OOS 창도 F1 0.267로 동일 분류).

**왜 갈리나 (조건별, W_SIG 전 분)**

| 조건 | Bybit TRUE 비율 | Binance TRUE 비율 | 분 단위 일치 | Jaccard |
|---|---|---|---|---|
| 고변동 (rv24h > cutoff) | 11.1% | 11.8% | 98.9% | **0.91** |
| S1 하위 10% | 12.1% | 13.1% | 85.4% | 0.27 |
| OI 1h 감소 | 46.9% | 47.9% | **60.6%** | 0.41 |

- OI 1h 변화 상관 0.28. 시간 이동 상관은 lag 0에서 최대(−5분 0.267, 0 0.284, +5분 0.274)라 **시각 정렬 오류가 아니다**.
  연도별 상관 0.12(2021)~0.55(2023), 부호 일치 58~64%. 두 거래소 OI는 다른 참가자 포지션이다.
- S1 상관 0.81, 중앙 할인 −3.98bp(Bybit) vs −4.01bp(Binance). 수준은 같은데 하위 10% 꼬리에 드는 분이 다르다.
- rv24h 비율 중앙 0.9998. 변동성 조건은 이식된다.
- 한쪽만 TRUE인 봉에서 상대 venue가 거짓인 조건: Bybit-only 19,243봉 중 OI 11,916 / S1 11,542 / vol 534,
  Binance-only 27,563봉 중 OI 15,210 / S1 19,718 / vol 1,412.

규칙은 raw absolute threshold가 없어 VENUE_NONPORTABLE_RULE은 아니다. 이식은 가능하지만 **이식한 결과가 다른 신호다**.

## H. Timing parity (±60분 1:1 매칭 1,292쌍, 진단용)

| | 값 |
|---|---|
| 오프셋 중앙 / 평균 | 0분 / +1.2분 (+ = Binance가 늦음) |
| \|오프셋\| 중앙 / p90 / 최대 | 10분 / 45분 / 60분 |
| 같은 분 비율 | 25.2% |
| Binance가 늦음 / 빠름 | 40.0% / 34.8% |
| direction flip | 0 (양쪽 LONG-only) |

±60분 매칭은 "같은 사건"을 넓게 잡은 진단값이다. 새 timing tolerance 규칙으로 쓰지 않는다.

## I. Direction disagreement

0건. 구조적 0이다(두 venue 모두 LONG-only 계약). "방향이 같다"는 뜻이 아니라 **방향을 다르게 낼 수 없는 규칙**이라는 뜻이다.

## J. Entry price difference (매칭 1,292쌍)

| 분해 | 중앙 | \|중앙\| | \|p90\| | \|최대\| |
|---|---|---|---|---|
| 운영자가 실제 내는 차이 (Binance 진입 vs Bybit 진입) | +2.0bp | 18.0bp | 94.4bp | 674bp |
| 같은 봉, venue만 다름 | +2.4bp | 2.6bp | 7.2bp | 229bp |
| 같은 venue(Binance), 시각만 다름 | 0.0bp | 17.5bp | 93.8bp | 560bp |

진입가 차이는 거의 **시각 차이**에서 온다. 같은 분의 venue 가격차는 중앙 2.6bp다.

**가격차, Bybit 신호 주변 (Binance/Bybit, bp)**

| 창 | close \|중앙\| | close \|p99\| | high \|p99\| | low \|p99\| | 신호당 최대 close 괴리 중앙 / p90 / 최대 |
|---|---|---|---|---|---|
| ±5분 | 2.13 | 14.8 | 17.5 | 14.0 | 4.9 / 11.6 / 309 |
| ±15분 | 2.05 | 14.7 | 17.4 | 13.7 | 6.0 / 12.9 / 806 |
| ±60분 | 2.03 | 14.8 | 17.2 | 13.4 | 7.7 / 16.1 / 806 |
| 전 구간 기준 | 1.17 | 11.0 | | | 최대 806 |

C1 주변 괴리는 평소의 약 1.8배다(중앙 2.0 vs 1.2bp). Binance high가 위로 더 튀는 비대칭(high 중앙 +2.4 vs low +0.5bp).

## K. 4H outcome (W_OUT, E1 회계)

**사다리 (fee 효과와 venue 효과 분리)**

| 단계 | 신호 | 가격·funding | fee | 거래 | gross | net 평균 | net 중앙 | 승률 | PF | 7일 블록 CI | 양수 fold | 판정 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L0 | Bybit | Bybit | 0.055% | 1,603 | 21.31 | **+10.27** | +1.25 | 50.4% | 1.15 | [−9.3, +30.0] | 7/9 | INCONCLUSIVE |
| L1 | Bybit | **Binance** | 0.055% | 1,603 | 19.54 | +8.45 | +0.25 | 50.2% | 1.12 | [−11.1, +28.0] | 7/9 | INCONCLUSIVE |
| L2 | **Binance** | Binance | 0.055% | 1,726 | 15.56 | +4.48 | −3.68 | 49.0% | 1.07 | [−10.7, +22.1] | 4/9 | INCONCLUSIVE |
| L3 | Binance | Binance | **0.05%** | 1,726 | 15.56 | **+5.48** | **−2.68** | 49.1% | 1.08 | [−9.7, +23.1] | 4/9 | INCONCLUSIVE |

분해: L0 +10.27 → 가격/funding venue **−1.82** → 신호 **−3.98** → fee **+1.00** → L3 +5.48.
L3 경고: W3(상위 1% 제거 시 부호 반전), W4(양수 fold 4/9), W5(중앙 ≤ 0). L0는 경고 0개였다.
상위 5% 제거 시 L0 −17.4, L3 −20.6.

같은 신호, 가격만 Binance(L0 vs L1, 1,603쌍): gross 차 평균 −1.77bp, |p90| 5.7bp, |최대| 247bp, 승패 부호 바뀐 거래 0.75%.

**matched vs venue 전용 (±5분 매칭 445쌍)**

| 집합 | 거래 | gross | net | net 중앙 | 승률 |
|---|---|---|---|---|---|
| matched, Bybit 쪽 (L0) | 445 | 10.13 | −0.85 | −2.27 | 49.4% |
| matched, Binance 쪽 (L3) | 445 | 6.75 | −3.31 | −1.76 | 49.0% |
| Bybit-only (L0) | 1,158 | 25.61 | **+14.55** | +2.35 | 50.8% |
| Binance-only (L3) | 1,281 | 18.62 | +8.53 | −3.79 | 49.1% |

두 venue가 동의한 신호는 둘 다 음수다. 평균의 양수는 venue 고유 신호에서 나온다. 사후 관찰이며 필터로 쓰지 않는다(19절).

**참고 horizon (net 평균, bp)**

| | 30m | 1h | 2h | 4h | 8h |
|---|---|---|---|---|---|
| L0 | −4.24 | +1.10 | +2.81 | +10.27 | +14.45 |
| L3 | −4.04 | −2.43 | +1.71 | +5.48 | +5.85 |

## L. MFE / MAE (4H, bp)

| | MFE 평균 / 중앙 | MAE 평균 / 중앙 | MFE·MAE 도달 중앙 시각 |
|---|---|---|---|
| L0 | 174.0 / 126.9 | −158.0 / −103.5 | 100분 / 87분 |
| L1 | 172.0 / 124.1 | −158.4 / −103.9 | 100 / 86 |
| L3 | 162.4 / 113.8 | −147.8 / −98.6 | 102 / 97 |

같은 신호에서 venue만 바꾸면 MFE −2.0bp, MAE −0.5bp(평균). 경로 폭은 venue보다 신호 집합이 바꾼다.

## M. Stop / exit parity (E2 정의 그대로, ±5분 매칭 445쌍, L0 vs L3)

| 규칙 | 의미 | Bybit 발동 | Binance 발동 | 발동 불일치 | 보유시간 차 \|중앙\| / \|p90\| (둘 다 발동) | 청산가 차 \|p90\| | net 차 평균 / \|p90\| |
|---|---|---|---|---|---|---|---|
| B1 | 2.5σ 손절(mark low, catastrophic) | 39 | 38 | 3 (0.7%) | 4 / 21분 | 33bp | +0.5 / 55bp |
| B3 | trailing(이익 50% 반납) | 122 | 122 | 26 (5.8%) | 4 / 66분 | 49bp | +0.6 / 68bp |
| B4 | breakeven(추정 net ≤ 0) | 49 | 52 | 19 (4.3%) | 1 / 18분 | 43bp | −0.9 / 63bp |
| C1x | 프리미엄 정상화(point) | 383 | 383 | 26 (5.8%) | 35 / 170분 | 184bp | +1.1 / 208bp |
| C2x | OI 반등(point) | 445 | 445 | 0 | 30 / 140분 | 116bp | **−11.0** / 127bp |
| C3x | 변동성 정상화(point) | 85 | 85 | 6 (1.3%) | 5 / 34분 | 37bp | +1.1 / 63bp |

TP 규칙은 C1 exit 연구에 없다(N/A). 매칭 쌍은 진입 시각부터 다르므로 이 차이는 "같은 거래의 venue 차"가 아니라 "같은 사건의 venue 차"다.
B4의 내부 비용 추정 상수(0.055%)는 규칙의 일부로 보고 바꾸지 않았다.

## N. Funding difference

| | funding 평균 (bp/거래, 4H) |
|---|---|
| L0 Bybit | −0.01 |
| L1 같은 신호, Binance funding | +0.04 |
| L3 | +0.03 |

같은 신호 쌍 차이: 평균 +0.05bp, |p90| 1.1bp, |최대| 5.1bp. 정산 시각 기준, 보유 중 지나간 것만. **net 결론에 영향 없음.**

## O. Fee difference

0.055% → 0.05%(계정 실측) = 거래당 **+1.00bp**(왕복 fee 11.01 → 10.01bp).
fee만으로 바뀐 판정은 없다. L2와 L3은 같은 판정이다(E1 INCONCLUSIVE, E2 status 동일, D5.2 REJECT).
판정을 바꾼 것은 **신호 집합(−3.98bp)과 가격(−1.82bp)**이다.

## P. C1 E1 / E2 impact

| | Bybit (L0) | Binance (L3) | 라벨 |
|---|---|---|---|
| **C1 original (D5.2 C1-EVENT-240m-LONG)** | **WEAK (W1)**, bar 가중 net +19.28bp, CI [+4.3, +35.5], N_eff 77 | **REJECT**, net +13.34bp, CI **[−3.5, +29.1]**, N_eff 80 (Bybit fee로도 REJECT, CI [−4.5, +28.1]) | **CONCLUSION_CHANGED** |
| E1 (4H, episode) | E1_INCONCLUSIVE, +10.27 | E1_INCONCLUSIVE, +5.48 | NUMERICALLY_CHANGED_ONLY |
| E2 | B1·B3·B4·C1x·C3x INCONCLUSIVE, C2x UNPROMISING, PROMISING 0 | **B1·B3 UNPROMISING**, B4·C1x·C3x INCONCLUSIVE, C2x UNPROMISING, PROMISING 0 | **CONCLUSION_CHANGED** (더 나쁜 쪽) |

E2 후보 상세 (net 평균 / E0 대비 delta / delta CI):

| | L0 | L3 |
|---|---|---|
| E0 | +10.27 | +5.48 |
| B1 | +12.37 / +2.10 / [−22.7, +26.9] | +4.49 / −0.99 / [−23.1, +22.1] |
| B3 | +11.33 / +1.06 / [−24.1, +25.3] | +5.47 / −0.00 / [−21.5, +23.3] |
| B4 | +12.45 / +2.18 / [−25.3, +27.8] | +6.93 / +1.45 / [−22.9, +26.3] |
| C1x | +15.35 / +5.08 / [−16.0, +27.6] | +6.15 / +0.67 / [−15.3, +15.6] |
| C2x | −3.23 / −13.50 / [−25.9, −1.1] | −7.78 / −13.25 / [−28.7, −0.6] |
| C3x | +14.54 / +4.27 / [−23.8, +31.6] | +5.83 / +0.35 / [−24.5, +25.9] |

**C1x(운영에 진단 필드로 기록 중)의 E0 대비 우위 +5.08bp가 Binance에서 +0.67bp로 거의 사라진다.** winner 보존 48.8% → 49.3%(여전히 G2 < 50%).

## Q. D6 / D6-C impact

| | Bybit 정본 (같은 창) | Binance 데이터 + Binance fee |
|---|---|---|
| 판정 | FAIL, D6-D NOT AUTHORIZED | **FAIL, D6-D NOT AUTHORIZED** |
| 실패 게이트 | G1 G2 G3 G5 G7 G8 G9 G10 (G6 평가불가) | **같은 집합** |
| 거래 | 1,258 | 1,476 |
| net 평균 / 중앙 | −5.70 / −0.88bp | −6.09 / +0.64bp |
| gross / edge-cost ratio | +5.55 / 0.50 | +4.20 / 0.41 |
| CI (7일 블록) | [−15.4, +3.7] | [−14.0, +2.1] |
| PF / MDD / 수익률 | 0.91 / 11.5% / −7.5% | 0.88 / 13.4% / −12.7% |
| 민감도 6종 | 전부 음수 | 전부 음수(−3.6 ~ −9.3) |
| 엔진 대조 | 불일치 0 | 136,425봉 불일치 0 |

라벨 **UNCHANGED** (판정 동일, |Δnet| 0.39bp ≤ 1bp). 거래 수는 17% 다르다(신호 집합 차이). Bybit-fee Binance arm은 시간 제약으로 돌리지 않았다. fee 효과는 K절과 같은 +1bp 수준이라 FAIL을 바꿀 수 없다.

## R. Manual Context Exit impact

원 연구 코드는 수정하지 않았다. K1(1m)·K15(15s)·파생(funding/index/OI) 로더와 COST_BP만 Binance로 교체했다(11.25 → 10.25bp).
Binance 1m 로컬 자료는 2021-02부터라 1h/4h/1d의 "K1 전체" 창이 달라진다. 그래서 Bybit도 2021-02부터로 잘라 같은 창으로 다시 돌렸다.
C1J/backfill 부분은 원래부터 Binance 계열 저널이라 재실행하지 않았다.

| arm | 창 | 판정 | 후보 |
|---|---|---|---|
| 정본 R0.1 (Bybit, 2020-03~) | | B. MANUAL_CONTEXT_R0_PASS | 0 |
| Bybit 2021-02~ | 같은 창 | **B. MANUAL_CONTEXT_R0_PASS** | 0 |
| Binance 2021-02~ | 같은 창 | **D. HOLD_EXIT_CANDIDATES_FOUND** | hold 1, entry 1 |

창을 바꿔도 Bybit은 B이므로 판정 변화는 venue 효과다. 라벨: **CONCLUSION_CHANGED** (계약 규칙상).

후보 2칸의 venue 비교:

| 칸 | Bybit 2021 (n / 효과 / CI / p / Holm) | Binance 2021 |
|---|---|---|
| hold `1h\|BREAK\|f_basis_favours\|fwd5_bp` | 193 / +12.52bp / [4.65, 20.34] / 0.002 / 0.244 | 221 / +12.82bp / [4.76, 21.04] / **0.0** / **0.0** |
| entry `15s\|BREAK\|rr_ge_1` | 67 / +0.97 / [0.20, 1.80] / 0.014 / 0.448 | 110 / +2.00 / [0.74, 3.65] / **0.0** / **0.0** |

**해석은 조심해야 한다.**
- hold 칸의 효과 크기는 두 venue가 같다(+12.5 vs +12.8bp). 판정을 가른 것은 효과가 아니라 p값이다.
- 부트스트랩은 2,000회라 p = 0.0은 실제로 "p < 0.0005"라는 뜻일 뿐이다. 이 상한에 패밀리 크기 144를 곱한 Holm 상한은 0.072로 0.05를 넘는다. **이 해상도로는 Binance에서의 유의성이 확정되지 않는다.**
- entry 칸(15s)은 Bybit 체결(K15)과 Binance aggTrades로 만든 봉이 다르고 n도 다르다(67 vs 110).
- Binance 2026-09 funding이 없어 9월 보유분의 funding은 빠졌다. 15s는 전부 9월이지만 보유가 짧아 정산을 거의 지나지 않는다.
- 이 두 칸은 R0.1 계약상 "후보"일 뿐 규칙이 아니다. 이번 recheck는 후보를 채택하지 않는다(19절). 다룬다면 새 lineage로만 다룬다.

실행: Binance 107초(최대 RSS 4.2GB), Bybit 2021 83초. Binance arm은 2회 실행 결과가 runtime 필드를 빼고 동일하다.
산출: `mce_binance_2021_v1.json`, `mce_bybit_2021_v1.json`.

## S. 현재 UI / runtime source (repo 기준, 운영 무접근)

| 항목 | 내용 |
|---|---|
| 신호 엔진 | `backend/app/crypto/c1/` (`runtime.py` tick 20초, 결정 1분 격자) |
| 입력 | **Bybit** `/v5/market/kline` 1m, **Bybit** `/v5/market/open-interest` 5m, Bybit funding(shadow 비용), Binance spot `/api/v3/klines` (`c1/sources.py`) |
| UI 마커 | `frontend/lib/crypto-c1.ts` → `/api/crypto/c1/state`, `/api/crypto/c1/markers` (보라 ▲ C1 LONG, ◇ C1x) |
| LONG/SHORT | LONG만. SHORT 마커 없음 |
| 운영 상태 | `CRYPTO_C1_SIGNAL=on`일 때만 동작. 10/11 배포 문서 mutation proof에 `c1-auto enabled + has_position` 기록 → **운영에서 켜져 있고 PAPER C1 AUTO(`paper/c1_auto.py`, 4H 자동 청산)가 이 신호로 거래 중** |
| 실행 venue | PAPER 엔진/수동 LIVE는 Binance |

**현재 화면의 C1 마커는 Bybit C1이다.** Binance에서 같은 규칙을 돌렸을 때의 신호와 ±5분 기준 약 24%만 겹친다.

## T. Operational risk

1. 화면의 C1 마커를 보고 Binance에서 진입하면 **Bybit 신호의 Binance 체결**이 된다. 이 조합(L1)은 +8.45bp, CI [−11.1, +28.0]으로 원판정과 같은 INCONCLUSIVE다. 가장 나쁜 조합은 아니다.
2. 그러나 "Binance 기준 C1"(L3)은 +5.48bp, 중앙 −2.68bp, 양수 fold 4/9다. C1 마커가 Binance 시장 상태를 대표한다고 볼 근거가 없다.
3. PAPER C1 AUTO는 Bybit 신호로 진입하고 Binance 가격으로 체결한다. 전방 기록을 평가할 때 비교 기준은 L1(+8.45)이지 E1 정본 +10.27이 아니다.
4. C1x 진단의 연구 근거(E0 대비 +5bp)는 Binance에서 +0.7bp다. 화면 문구는 이미 EXIT가 아니라 진단이라고 하지만, 운영자가 의미 있는 신호로 읽으면 안 된다.
5. 운영 변경은 하지 않았다. 바꿀지(마커 venue 표기, AUTO 중단, Binance 입력 전환)는 사용자 결정이며, Binance 입력 전환은 **새 lineage(새 계약)**다.

## V. Recommended action

1. 운영·코드 변경 없음(이번 범위). 화면에 "C1 = Bybit 입력 신호"라는 사실만 운영자가 인지할 것.
2. C1 계열(E1/E2/C1x/D6)의 수치를 Binance 거래 판단 근거로 인용하지 말 것. 인용한다면 L1/L3 수치를 함께 쓸 것.
3. PAPER C1 AUTO 전방 기록의 비교 기준은 L1(+8.45bp, 1,603건)로 바꿔 읽을 것.
4. "Binance C1"을 원한다면 새 연구 lineage로만 가능하다(OI 정의를 venue 합산 등으로 바꾸는 것은 규칙 변경). 이번 데이터는 그런 재설계를 정당화하는 근거가 아니다. 두 venue가 동의한 445건이 둘 다 음수였기 때문이다.
5. 커밋은 사용자 몫. 대상: 계약·보고서 2개, 코드 4개 + `__init__`, 테스트 1개. `data/research/...` 산출물은 기존 관례대로.

## W. Limitations

- Binance funding 로컬 자료가 2026-08까지라 성과는 2026-09-01 전까지만 본다. C1은 08-21 이후 신호가 없어 표본 손실은 0이다. D6-C는 9월 1건이 빠진다.
- 합성 호가(D6-C SyntheticBook, spread 1틱)는 Bybit 기준 측정값이다. Binance 호가 깊이는 반영하지 않았다.
- Binance mark 결측 10일(12,962분). C1 성과 표본에는 걸린 거래가 0이다. D6-C에서는 결측 분에서 mark 손절이 발동하지 않는다. 걸린 거래 수는 거래 목록을 저장하지 않아 세지 못했다.
- OI 결측 0.10%는 연구 규칙(as-of 무한 이월)대로 처리했다. 운영 엔진의 15분 stale 가드는 연구 규칙이 아니라 적용하지 않았다(15분 초과 stale 2,126분).
- metrics create_time의 의미는 미확인이다. Bybit 연구와 같은 +5분 지연을 줬다. lag 상관은 0에서 최대였다.
- micro 비용(0.0118bp)과 STRESS 추가분은 Bybit 측정값 그대로다(크기상 무관).
- D5.2 변동성 cutoff는 Bybit F1 train 동결값을 그대로 썼다. Binance로 재추정하지 않았다(계약). 고변동 조건 Jaccard가 0.91이라 결론에 영향이 작다.
- 실행 중 WSL이 세 번 재시작돼 D6-C의 Bybit-fee Binance arm은 생략했다.
