# AOA E1 Maker Adverse Selection Results V1

작성 2026-09-27. 사전등록 `AOA_E1_MAKER_ADVERSE_SELECTION_CONTRACT_V1.md` (sha256 `87f7c8787e48f06ff298b03507d117e3148762fe0d8036a2d727ed539bf2765c`,
동결 2026-09-27T04:04:39Z)을 그대로 실행한 결과다. 동결 뒤 계약 변경 0회. 판정은 계약 12절 규칙만으로 냈다.
maker/taker 비교표는 `AOA_E1_MAKER_TAKER_COMPARISON_V1.md`.

코드 `backend/app/crypto/research/expert_execution/market.py`(수집), `e1.py`(분석). 재실행:
`PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.e1` (453초).

---

## 0. 결론

| 항목 | 결과 |
|---|---|
| **HISTORICAL verdict** | **MIXED** (C1 X, C2 X, C3 O, C4 X) |
| **BYBIT applicability** | **BYBIT_CF_NEGATIVE** |
| maker 60초 순edge (가치가중) | **-1.51bp** [95% CI -2.59, -0.61] |
| maker 60초 순edge (주문 동일가중) | **+1.62bp** [+0.59, +2.44] |
| maker 60초 역선택 (mid 이동) | **-5.61bp** [-6.86, -4.58] |
| maker 리베이트 + spread capture | +2.41 + 1.69 = +4.10bp |
| 연도별 maker 60초 순edge | 2018 **+2.34** / 2019 -0.17 / 2020 -2.23 / 2021 **-5.08** |
| XBT 환산 (60초 기준) | maker 리베이트 +271.5, spread +190.6, 역선택 -631.9, **순 -169.8 XBT** |
| Bybit VIP 0 가정 (CF-B) maker 60초 | **-7.61bp** [-8.86, -6.58] |
| US-B에 LIMIT/Maker 구현 | **권고하지 않음** (17절) |

**답을 한 줄로**: 이 계좌의 maker fill은 체결 직후 평균적으로 불리하게 움직였고, 거래가치 기준으로는 역선택(-5.6bp)이
BitMEX 리베이트와 spread capture의 합(+4.1bp)보다 컸다. 다만 **작은 주문에서는 반대**였다(주문 동일가중 +1.6bp, 소형 주문 +6.0bp).
큰 주문이 가치의 81%를 차지하기 때문에 가치가중 결과가 음수로 나왔다. 게다가 이 역선택은 2018년에서 2021년으로 갈수록 커졌다.
리베이트가 없는 Bybit 요율을 넣으면 모든 주요 구간에서 음수다.

---

## 1. Prereg hash

| 항목 | 값 |
|---|---|
| 계약 | `docs/crypto/expert_execution/AOA_E1_MAKER_ADVERSE_SELECTION_CONTRACT_V1.md` |
| sha256 | `87f7c8787e48f06ff298b03507d117e3148762fe0d8036a2d727ed539bf2765c` |
| 동결 기록 | `data/research/expert_execution/e1/contract_freeze_v1.json` (2026-09-27T04:04:39Z) |
| 실행 시 검사 | `e1.verify_contract()`가 hash를 다시 계산, 일치 |
| 동결 전 본 것 | archive 목록, 샘플 2일 스키마·행 수·조인율. **체결 전후 mid는 0건 계산** (계약 0절) |

## 2. BitMEX market data

| 항목 | 값 |
|---|---|
| 소스 | `https://s3-eu-west-1.amazonaws.com/public.bitmex.com/data/{quote,trade}/YYYYMMDD.csv.gz` (공식 공개 archive) |
| 기간 | 2018-03-04 ~ 2022-01-01, 1,400일 x 2종 = 2,800 파일, 누락 0, 실패 0 |
| 형식 | gzip CSV, 전 심볼, timestamp `YYYY-MM-DDDhh:mm:ss.ffffff000` (마이크로초 정밀도, UTC) |
| quote 스키마 | timestamp, symbol, bidSize, bidPrice, askPrice, askSize (최우선 호가만. 깊이 없음) |
| trade 스키마 | timestamp, symbol, side, size, price, tickDirection, trdMatchID, grossValue, homeNotional, foreignNotional |
| 처리 | 하루씩 받아 스트리밍 파싱, XBTUSD만 zstd parquet로 저장, 원본 gz는 sha256·ETag 기록 뒤 삭제 |
| 저장 위치 | `data/research/expert_execution/e1/market/` (git 제외), manifest `e1/market_manifest.json` |
| **보관 상태 (2026-09-27 closeout)** | `e1/market/quote`·`trade` 27.6GB **삭제됨**. 일별 manifest·sha256·ETag는 유지, 재구축 절차는 `data/research/expert_execution/AOA_CLEANUP_MANIFEST_V1.json`. E1 결과 parquet/json은 유지 |

## 3. Coverage / rows / size

| 항목 | quote | trade |
|---|---|---|
| 원본 다운로드 | **53.31GB** | **35.66GB** |
| 전 심볼 행 | 5,939,320,926 | 935,295,438 |
| XBTUSD 행 | 2,715,336,105 | 652,133,724 |
| 정리 후 보관 | 2,700,640,334 | 652,133,724 |
| 보관 parquet | **24.49GB** | **3.15GB** |
| 수집 시간 | 8,979초 (5 worker, 평균 약 9.9MB/s) | (같은 실행) |

E1 산출물: `maker_taker_metrics.parquet` 58MB (fill 940,987행), `order_level_metrics.parquet` 4.7MB, `regime_metrics.parquet` 120KB.
분석 453초, peak RSS 4.9GB. 수집 worker는 하루 단위 스트리밍이라 worker당 1GB 안팎(2 worker 시험에서 합계 1.5GB).

## 4. Quote quality

| 검사 | 결과 |
|---|---|
| 시간 역행 | 0 |
| 완전 중복 행 | 1,013 (제거) |
| 같은 timestamp 여러 행 | 14,694,758행을 마지막 행으로 대체 (계약 4절) |
| crossed / locked | 3,323행 (0.0001%, 기준·미래 quote로 걸리면 무효 처리) |
| 60초 넘는 quote 공백 | 59회, 최대 3,931초 (거래소 점검 등. 해당 fill·horizon은 stale 규칙으로 제외) |
| 원장 fill ↔ archive trade 조인 | **940,984 / 941,005** (trdMatchID 기준), 가격 일치 100%, timestamp 일치 |
| 미조인 21 | 청산 fill 19 (분석 제외 대상) + 자기체결 2쌍 4 fill (계좌가 양쪽에 선 체결, 공개 tape에 없음) |
| 기준 quote 유효 | **940,987 / 940,988** fill (1건만 stale) |
| 기준 quote 나이 | 중앙 0.020초, p90 0.18초, p99 0.74초, 최대 7.5초 |
| horizon별 유효 | 1s~60s 940,987 / 300s 940,943 / 900s 940,849 |

fill 가격이 기준 quote의 [bid, ask] 안에 있는 비중 67.8%. 나머지는 여러 호가를 한 번에 쓸어 간 체결이다(6절).

## 5. Maker / Taker sample size

| | fill | order x role 단위 | 거래가치 (XBT) |
|---|---|---|---|
| MAKER | 504,389 | 13,266 | 1,127,229 (69.5%) |
| TAKER | 436,598 | 8,956 | 493,536 |
| UNKNOWN | 0 | | |
| 제외 | 청산 19, 기준 quote 무효 1 | | |

분석 단위는 fill(가치가중)과 order x role(동일가중) 두 가지이고, 모든 CI는 주문 첫 fill 날짜 기준 **일 단위 cluster bootstrap** (B=2,000)이다.
fill 94만 개를 독립 표본으로 쓰지 않았다. cluster 수는 거래가 있었던 날 1,031일.

## 6. Entry / add / reduce / close breakdown

60초 horizon, 가치가중 [95% CI], 괄호 뒤는 주문 동일가중.

| role x 행동 | 주문 | move_bp (역선택) | net_bp | 주문 동일가중 net |
|---|---|---|---|---|
| MAKER ADD | 6,568 | -5.36 [-6.62, -4.21] | **-1.47** [-2.64, -0.35] | +2.43 |
| MAKER REDUCE | 6,457 | -5.76 [-7.76, -4.20] | **-1.64** [-3.60, -0.17] | +0.78 |
| MAKER OPEN | 311 | -7.87 [-19.63, +1.08] | -1.53 [-12.98, +7.27] | +3.54 |
| MAKER CLOSE | 288 | -22.74 [-60.32, -0.53] | -14.81 [-43.54, +2.98] | -0.58 |
| MAKER REVERSE | 831 | -5.78 [-10.15, -1.73] | +3.14 [-1.26, +7.46] | +1.04 |
| TAKER ADD | 4,281 | +10.46 | -0.68 [-4.08, +1.82] | -0.16 |
| TAKER REDUCE | 4,096 | +10.80 | -0.96 [-3.82, +1.58] | -1.55 |

maker 물량은 ADD(50.1%)와 REDUCE(47.8%)에 고르게 나뉜다. **"maker는 청산에서만 썼다"는 구조가 아니다.**
OPEN·CLOSE·REVERSE는 주문 수백 건이라 CI가 넓어 판단하지 않는다.

또 maker 체결의 97.8%(건수)는 최우선 호가에서 났지만 **가치 기준 15.7%는 최우선 호가보다 깊은 가격**에서 났다.
큰 taker가 여러 단계를 쓸어 가며 이 계좌의 대기 주문까지 치운 경우이고, 이때 spread capture는 크지만 직후 mid가 그 방향으로 크게 밀린다.
taker fill은 66.8%(가치 54.3%)가 최우선 호가 밖에서 체결됐다(스스로 여러 단계를 쓸어 감).

## 7. Maker adverse selection by horizon

`move_bp(h)` = s x (mid_h - mid_0), mid_0 기준 bp. 음수 = 체결 뒤 불리하게 이동.

| horizon | 가치가중 [95% CI] | 주문 동일가중 | 중앙값 | favorable / adverse / 0 |
|---|---|---|---|---|
| 1s | **-2.61** [-2.89, -2.33] | -2.07 | 0.00 | 5.5% / 19.9% / 74.7% |
| 5s | **-4.41** [-4.84, -4.00] | -2.74 | | 14.0% / 34.3% / 51.7% |
| 15s | **-5.52** [-6.28, -4.87] | -3.00 | | 23.5% / 44.7% / 31.8% |
| 30s | **-5.40** [-6.26, -4.64] | -2.74 | | 29.8% / 49.3% / 21.0% |
| 60s | **-5.61** [-6.86, -4.58] | -2.51 | -1.35 (p25 -11.09, p75 +3.99) | 34.6% / 52.8% / 12.6% |
| 5m | **-5.52** [-7.22, -4.04] | -0.92 | -1.94 | 44.7% / 52.7% / 2.6% |
| 15m (참고) | -2.84 [-4.88, -0.93] | +1.18 | | 49.5% / 49.6% / 0.9% |

**Q1 답: 예.** 모든 핵심 horizon에서 CI 전체가 0 아래다. 역선택은 15초 안에 거의 다 생기고(-5.5bp) 5분까지 유지되며, 15분이 되면 절반쯤 되돌아간다.

## 8. Taker signed move by horizon

| horizon | move_bp 가치가중 | 주문 동일가중 | favorable / adverse |
|---|---|---|---|
| 1s | +6.18 [+5.68, +6.71] | +4.02 | 89.6% / 2.9% |
| 5s | +7.89 | +5.36 | 91.6% / 4.8% |
| 60s | +10.47 [+8.54, +12.25] | +8.53 | 82.1% / 16.4% |
| 5m | +11.59 | +9.11 | 68.6% / 30.1% |
| 15m | +11.30 | +8.83 | 61.4% / 37.6% |

**해석 주의 (필수)**: taker의 체결 후 이동에는 **자기 주문의 가격 충격이 섞여 있다.** mid_0는 체결 직전 호가이고, 여러 단계를 쓸어 간
taker 주문은 체결만으로 mid를 자기 방향으로 옮긴다(taker spread 비용 평균 -4.2bp. 반틱은 가격 6,000~69,000달러에서 0.04~0.4bp라 그 10배 이상). 1초 안에 +6.2bp가 생기는 것이
대부분 이 효과다. 1초에서 5분 사이 추가분(+5.4bp)과 15분까지 유지되는 점은 자기 충격만으로는 설명되지 않는 지속 이동이 있다는 뜻이지만,
원장과 최우선 호가만으로 "자기 충격"과 "정보(alpha)"를 분리할 수 없다. 따라서 taker move는 **alpha의 상한**으로만 읽는다.

## 9. Historical fee effect

원장 실제 요율(변경 시점은 원장 최빈값):

| 기간 | maker | taker |
|---|---|---|
| 2018-03 ~ 08 | -0.0225% | +0.0675% |
| 2018-09 ~ 2021-08 | -0.025% | +0.075% |
| 2021-09 | -0.01% | +0.025% |
| 2021-10 | -0.01% | +0.03% |
| 2021-11 ~ 12 | -0.01% | +0.035% |

가치가중 fee_bp: maker **+2.41bp**(리베이트), taker **-7.22bp**. 연도별 maker +2.40 / +2.50 / +2.42 / +2.27.

## 10. Maker net execution edge

`net_bp(h)` = fee_bp + spread_bp + move_bp(h).

| horizon | 가치가중 [95% CI] | 주문 동일가중 [95% CI] | hit (net > 0) |
|---|---|---|---|
| 1s | **+1.49** [+1.20, +1.82] | +2.06 | |
| 5s | -0.31 [-0.71, +0.12] | +1.39 | |
| 15s | -1.42 [-2.05, -0.88] | +1.13 | |
| 30s | -1.30 [-2.01, -0.68] | +1.39 | |
| **60s** | **-1.51 [-2.59, -0.61]** | **+1.62 [+0.59, +2.44]** | 53.5% |
| 5m | -1.43 [-2.92, -0.10] | +3.21 | |
| 15m | +1.26 [-0.63, +3.08] | +5.31 | |

60초 분해 (가치가중): 리베이트 **+2.41** + spread capture **+1.69** + 역선택 **-5.61** = **-1.51bp**.
XBT 합계 (60초 mark 기준, 실현손익 아님): +271.5 + 190.6 - 631.9 = **-169.8 XBT**.

**Q3 답: 거래가치 기준으로는 예, 역선택이 리베이트보다 컸다.** 역선택(-5.61bp)은 리베이트(+2.41bp)의 2.3배이고
spread capture를 더해도(+4.10bp) 넘는다. 1초까지만 순이익이고, 5초부터 음수로 돌아선다.
주문 동일가중으로는 반대(+1.62bp)다. 이유는 11절.

## 11. Order-level robustness

가치가중과 주문 동일가중의 부호가 갈리는 것은 **주문 크기** 때문이다. maker 가치의 **80.6%가 큰 주문**(주문 가치 상위 3분위, 87.4 XBT 초과)에 있다.

| maker 주문 크기 | 60초 net 가치가중 [CI] | 주문 동일가중 | move_bp 가치가중 |
|---|---|---|---|
| SMALL (≤ 20.9 XBT) | **+5.97** [+4.90, +7.04] | +7.64 | +2.01 |
| MEDIUM | +2.06 [-0.57, +3.94] | +2.24 | -2.44 |
| LARGE (> 87.4 XBT) | **-2.47** [-3.66, -1.44] | -2.39 | -6.49 |

주문 크기 x 연도 (주문 동일가중 60초 net):

| maker | 2018 | 2019 | 2020 | 2021 |
|---|---|---|---|---|
| SMALL | +5.49 | +10.80 | +11.24 | +12.96 |
| MEDIUM | +2.76 | +5.58 | -4.26 | +1.88 |
| LARGE | +0.70 | -0.46 | -1.62 | **-6.73** |

작은 maker 주문은 4개 연도 모두 순이익이고, 큰 maker 주문은 해마다 나빠졌다. "maker가 좋았나"에 대한 답은 크기에 따라 반대다.
작은 주문이 좋았던 것이 크기 때문인지, 이 트레이더가 작게 넣은 상황(선택) 때문인지는 원장으로 구분할 수 없다.

## 12. Year robustness (C2)

| 연도 | maker net 60s [CI] | move | spread | fee | maker 가치 비중 | 연간 실현손익 (wallet) |
|---|---|---|---|---|---|---|
| 2018 | **+2.34** [+1.28, +3.40] | -2.42 | +2.36 | +2.40 | 63.7% | +188 XBT |
| 2019 | -0.17 [-1.12, +0.77] | -4.93 | +2.25 | +2.50 | 69.4% | +516 XBT |
| 2020 | **-2.23** [-4.14, -0.71] | -6.22 | +1.56 | +2.42 | 68.4% | +764 XBT |
| 2021 | **-5.08** [-9.10, -2.39] | -7.97 | +0.62 | +2.27 | 77.7% | +2,069 XBT |

양수 연도 1개 / 4개 → **C2 불충족**. 해마다 역선택은 커지고(-2.4 → -8.0bp) spread capture는 줄었다(+2.4 → +0.6bp, BTC 가격 상승으로 1틱 0.5달러의 bp 가치가 줄어든 영향 포함).

**Q7 답: 아니다.** 2021년 maker 비중은 가장 높았지만 maker 실행 edge는 가장 나빴다(-5.08bp). 같은 해 계좌 손익은 가장 컸다.
이 계좌의 성과는 maker 실행 품질에서 나오지 않았다. 방향·크기 판단에서 나왔을 가능성이 크지만 E1은 그것을 측정하지 않았다(인과 아님).

## 13. Volatility robustness (C4)

| 직전 60분 변동성 | maker net 60s [CI] | move | spread | 주문 동일가중 |
|---|---|---|---|---|
| LOW (≤ 0.083%/분) | -0.86 [-1.34, -0.37] | -4.02 | +0.72 | +1.37 |
| MID | -0.99 [-1.86, -0.22] | -4.48 | +1.09 | +1.84 |
| HIGH (> 0.157%/분) | **-2.42** [-5.13, -0.18] | -7.74 | +2.93 | +1.74 |

3분위 모두 가치가중 음수 → **C4 불충족**. **Q8 답**: maker 우위가 있는 변동성 구간은 가치가중으로는 없다. 고변동성에서 역선택이 가장 크다(-7.7bp).
주문 동일가중은 세 구간 모두 +1.4 ~ +1.8로 비슷해 특정 구간 의존은 아니다.

그 밖의 regime (maker, 60초 net 가치가중):

| 축 | 결과 |
|---|---|
| 추세 대비 체결 방향 | 추세와 같은 방향(WITH) **+1.07**, 중립 -1.45, 추세 역방향(AGAINST) **-2.90**. 떨어지는 장에서 매수 호가로 받아 줄 때 가장 많이 당했다 |
| spread | 1틱 -1.90 (가치 93%), 2틱 이상 +4.00 (spread capture +11.0) |
| 직전 60초 체결 강도 | LOW -2.33 / MID -1.85 / HIGH -0.64 |
| UTC 시간대 | 00-04시 -4.25 (최악), 20-24시 +0.27 |

## 14. Size sensitivity

| 축 | maker 60초 net 가치가중 |
|---|---|
| fill 크기 3분위 | SMALL -1.61 / MEDIUM -1.19 / LARGE -1.52 (fill 단위로는 차이 없음) |
| 주문 크기 3분위 | SMALL **+5.97** / MEDIUM +2.06 / LARGE **-2.47** (11절) |
| fill 수량 / 표시 호가 수량 | < 0.1: -0.08 / 0.1 ~ 1: **-3.02** / ≥ 1: +1.19 (spread capture +8.0) |

역선택은 fill이 아니라 **주문 크기**와 관련된다. 큰 주문은 오래 걸려 채워지는 동안 반대편의 정보 있는 흐름에 계속 노출된다는 해석과 맞지만,
queue·대기시간이 없어 검증하지 못한다(16절).

## 15. Current Bybit counterfactual (CF, 실제 성과 아님)

VIP 0 perp maker 0.0200%, taker 0.0550% (`fee_source_verification_v1.json`, D4 OFFICIAL). BitMEX에서 측정한 역선택이 그대로 일어난다고 가정.

| 시나리오 | maker 60초 [CI] | 주문 동일가중 | 소형 주문 |
|---|---|---|---|
| CF-A (fee만 교체, BitMEX spread 유지) | -5.92 [-6.99, -5.00] | -2.78 | |
| **CF-B (fee 교체, spread capture 0)** | **-7.61 [-8.86, -6.58]** | -4.51 | **+0.01** |

모든 horizon에서 CF-B maker는 음수다(1초 -4.61 ~ 15분 -4.84). **BYBIT_CF_NEGATIVE.**
taker CF-B는 +4.97bp로 나오지만 8절의 자기 충격이 포함된 값이라 "Bybit에서 taker가 유리하다"는 뜻이 아니다.

**Q9 답: 아니다.** 리베이트(+2.4bp)가 수수료(-2.0bp)로 바뀌면 4.4bp가 사라지고, Bybit BTCUSDT spread는 거의 항상 1틱(약 0.012bp)이라
spread capture도 없다. 남는 것은 역선택뿐이다. 가장 유리한 소형 주문 구간도 0bp(+0.01)다.
단 이 계산은 **BitMEX 2018~2021의 역선택 크기가 2026 Bybit에서도 같다는 가정** 위에 있다. 실제 Bybit 역선택은 측정하지 않았다.

## 16. Queue / fill UNKNOWNs

| 항목 | 상태 | 이유 |
|---|---|---|
| queue 위치 | UNKNOWN | 공개 quote는 최우선 호가 합계 수량뿐, 주문별 순서 없음 |
| 주문 제출·취소·정정 시각 | UNKNOWN | 원장에는 체결만 있다. 첫 fill 이전 대기시간을 모름 |
| 체결되지 않은 주문의 기회비용 | UNKNOWN | 미체결 주문 기록 없음. **maker의 가장 큰 비용일 수 있는 부분이 빠져 있다** |
| decision-time implementation shortfall | UNKNOWN | 결정 시각 없음. mid_0는 "체결 직전"이지 "결정 시점"이 아니다 |
| taker move의 자기 충격 vs 정보 분리 | UNKNOWN | 깊이 데이터 없음 (8절) |
| 호가 깊이 (2단계 이하) | UNKNOWN | quote archive는 top of book만 |

위 값은 추정해서 채워 넣지 않았다. 특히 미체결 기회비용이 빠져 있으므로, 이 결과는 maker에게 **유리한 쪽으로 편향**됐을 수 있다(체결된 주문만 봄).

## 17. US-B에 LIMIT/Maker를 구현할 가치가 있나 (Q10)

**현재 근거로는 권고하지 않는다.**

1. 목적이 비용 절감이라면: Bybit VIP 0에서 maker와 taker의 수수료 차이는 3.5bp다. 이 계좌의 maker fill은 60초 안에 평균 5.6bp(가치가중)를
   역선택으로 잃었다. 절약하는 수수료보다 역선택이 크다.
2. 소형 주문의 긍정적 결과(+6.0bp historical)는 US-B 주문 규모와 비슷한 구간이라 눈에 띄지만, Bybit 요율로는 0bp이고,
   이 트레이더의 선택 효과를 배제할 수 없고, 미체결 기회비용이 빠져 있다.
3. D5.1 CASE B는 "maker 가정 비용 약 4bp"로 구제를 계산했다. E1은 maker 체결에 **추가로** 60초 기준 약 5.6bp의 역선택이 붙을 수 있음을 보여 준다.
   D5.1 결과 자체는 수정하지 않지만, CASE B의 maker 가정은 이 역선택을 넣지 않은 **상한**이라는 D5.1 U4 경고가 실측으로 뒷받침됐다.
4. Paper Engine에 지정가 체결을 넣으려면 queue·체결확률 모델이 필요한데, 역사 데이터로는 만들 수 없다(16절).

구현한다면 조건: Bybit 자체의 forward 호가·체결 기록으로 소형 지정가 주문의 역선택과 체결률을 직접 잰 뒤에만.

## 18. Next step

**STOP 권고.** E1의 질문("이 계좌의 maker는 역선택을 이겼나")에는 답이 나왔고, 결과는 US-B에 maker를 도입할 근거가 되지 못한다.

E2를 한다면 역사 데이터가 아니라 **Bybit forward shadow 측정**이어야 한다: 실제 주문 없이 가상의 소형 지정가 주문을 호가에 올려 두고,
공개 체결 흐름으로 "체결됐다면" 시점과 그 뒤 mid 경로를 기록하는 방식. 이것도 queue 위치를 모르므로 체결률은 상한만 나온다.
선택은 사용자 몫이다.

## 19. Tests

| 파일 | 결과 |
|---|---|
| `backend/tests/crypto/test_expert_execution_e1.py` | **12 passed** (합성 데이터) |
| crypto 전체 | **381 passed** |

E1 테스트: 기준 quote 엄밀 이전(같은 시각 quote 무시), midpoint·부호(매수/매도, spread·move·svf·net), maker/taker 매핑(ordtype 미사용),
기준 quote 없음·stale·crossed, 미래 quote stale은 해당 horizon만 결측, regime feature의 미래 누출 없음(체결 이후 quote·trade를 바꿔도 불변),
원장 요율·Bybit 요율 매핑, 연말 경계(2018-12-31 → 2019-01-01 quote 사용), 중복·같은 timestamp 행 처리, cluster bootstrap 재현성,
주문 단위 동일가중. 격리: `grep -rln expert_execution backend/app`은 패키지 자신뿐, 패키지는 `app.*`를 import하지 않음(E0 테스트).

## 20. Git

- commit / push 0.
- 새 파일 (untracked): `market.py`, `e1.py`, `test_expert_execution_e1.py`, 문서 3편, `data/research/expert_execution/e1/` 산출물.
- 수정한 기존 파일: 없음. E0 코드도 수정하지 않았다(E1은 E0 parquet을 읽기만 함).
- `data/research/expert_execution/e1/.gitignore`로 `market/`(27.6GB)와 `raw_tmp/`를 제외했다.
- 운영 서버·Paper 계좌·D2/D5/D5.1 데이터 접근 0. D4 fee 기록 JSON은 읽기만 했다.

## 21. Verdict

| 판정 | 값 | 근거 |
|---|---|---|
| C1 가치가중 60초 > 0 이고 CI 하한 > 0 | **X** | -1.51 [-2.59, -0.61] |
| C2 4개 연도 중 3개 이상 > 0 | **X** | 1개 (2018) |
| C3 주문 동일가중 > 0 | **O** | +1.62 |
| C4 변동성 3분위 모두 > 0 | **X** | -0.86 / -0.99 / -2.42 |
| **HISTORICAL** | **MIXED** | NOT_SUPPORTED 조건(가치가중 ≤ 0 이고 주문가중 ≤ 0)은 주문가중 +1.62로 불충족 |
| **BYBIT** | **BYBIT_CF_NEGATIVE** | CF-B -7.61bp |

규칙상 MIXED이지만, 내용은 "가치 대부분(큰 주문)에서는 역선택이 이겼고, 작은 주문에서만 maker가 이겼으며, 그마저 Bybit 요율로는 0"이다.
