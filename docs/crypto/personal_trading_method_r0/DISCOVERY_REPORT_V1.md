# US-B CRYPTO Personal Trading Method (PTM) R0: Discovery Report V1

작성 2026-10-13 KST. 범위: **사전 조사만**. 전략 구현·백테스트·threshold 탐색·LONG/SHORT 신호·배포·주문 0건.
Q123 / Context Collector / Manual Trade Recorder 변경 0건(서버는 `ls`·`stat`·읽기 전용 python만, 쓰기·재시작·API 호출 0).
새 다운로드·새 외부 API 호출 0건. 이 문서는 "무엇을 어떤 데이터로 어떻게 연구할 수 있는가"까지만 다룬다. 전략의 좋고 나쁨은 판정하지 않는다.

**최종 판정: C. METHOD_RESEARCH_FEASIBLE_WITH_LIMITS** (S절)

---

## A. Existing data inventory

감사 방법: 로컬은 파일 전수 파싱(행 수, 중복, 결측, 단조성, 필드). 서버는 경로를 좁힌 `ls`와 읽기 전용 집계. 시각은 UTC.

### A1. 장기 시장 데이터 (로컬, 재현 가능)

| # | dataset | path (`~/usb/data/...`) | venue | start | end | 기간 | rows | 단위 | fields | 결측 | 중복 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **Binance USD-M perp 1m kline (taker 포함)** | `runtime/crypto/d5_2/archive/binance/futures/um/{monthly,daily}/klines/BTCUSDT/1m/*.zip` (89 zip) | Binance | 2021-02-01 00:00 | 2026-09-22 23:59 | 5.64년 | 2,966,400 | 1m | OHLCV, quote_volume, trades, **taker_buy_base, taker_buy_quote** | 0 | 0 |
| 2 | Binance perp 1m kline REST 사본 | `research/crypto/market_context_r0/raw/binance_kline_1m_1786492800000_1791100800000.jsonl` | Binance | 2026-08-12 00:00 | 2026-10-04 07:59 | 53.3일 | 76,800 | 1m | #1과 동일 | 0 | 0 |
| 3 | Bybit linear 1m kline | `runtime/crypto/BTCUSDT/historical/kline_1m/` | Bybit | 2020-03-25 10:36 | 2026-09-23 01:33 | 6.5년 | 3,416,578 | 1m | OHLC, volume, turnover (**taker 없음**) | 0 | 0 |
| 4 | Bybit mark 1m | `.../historical/mark_1m/` | Bybit | 2020-03-25 08:41 | 2026-09-23 01:33 | 6.5년 | 3,416,693 | 1m | OHLC | 0 | 0 |
| 5 | Bybit index 1m | `.../historical/index_1m/` | Bybit | 2020-03-25 07:57 | 2026-09-23 01:33 | 6.5년 | 3,416,737 | 1m | OHLC | 0 | 0 |
| 6 | Bybit funding | `.../historical/funding/` | Bybit | 2020-03-25 16:00 | 2026-09-23 00:00 | 6.5년 | 7,118 | 8h | funding_rate | 0 | 0 |
| 7 | Bybit OI | `.../historical/open_interest_5m/` | Bybit | 2020-08-04 07:50 | 2026-09-23 01:30 | 6.1년 | 645,329 | 5m | open_interest | 4 (2021-02-08 13:15 한 구멍) | 0 |
| 8 | Binance mark / index 1m | `d5_2/archive/binance/futures/um/{monthly,daily}/{markPriceKlines,indexPriceKlines}` | Binance | 2021-02 | 2026-09-22 | 5.6년 | (월 67 + 일 22 zip) | 1m | OHLC | 행 단위 미감사 | 미감사 |
| 9 | Binance funding | `d5_2/archive/binance/futures/um/monthly/fundingRate` | Binance | 2021-02 | 2026-08 | 5.6년 | 67 zip | 8h | funding | 9월분 없음 | 미감사 |
| 10 | Binance 분기물 1m kline | `.../um/monthly/klines/BTCUSDT_YYMMDD/` | Binance | 2021-02 | 2026-09-22 | 5.6년 | 24계약 | 1m | kline | 미감사 | 미감사 |
| 11 | Binance spot / COIN-M 1m | `.../spot/...`, `.../futures/cm/...` | Binance | 2021-02 | 2026-09-22 | 5.6년 | - | 1m | kline | 미감사 | 미감사 |
| 12 | OKX swap last/mark, index 5m | `d5_2/archive/okx/` | OKX | 2021-02-01 | 2026-09-23 | 5.6년 | 593,280 ×3 | 5m | OHLC | 0 (manifest) | - |
| 13 | R0 regime minutes | `research/crypto/r0_regime/regime_minutes_v1.parquet` | 파생 | 2021-02-01 | 2026-09-23 | 5.6년 | 2,966,400 | 1m | regime, r4h, r24h, trend, vol24h, oi4h, basis, funding | - | - |
| 14 | Deribit 옵션 체인 | `runtime/crypto/btc_vol_a/chains/` | Deribit | 2022~2026 중 12개월 | - | 월 단위 표본 | - | tick | IV, greeks | 연속 아님 | - |

**#1과 #2 정합성:** 겹치는 2026-09-01~09-22 31,680분에서 quote volume·taker_buy_quote 불일치 **0건**. #1과 #2를 이으면 **2021-02-01 00:00 ~ 2026-10-04 07:59, 결측 0·중복 0**의 연속 Binance perp 1m 시리즈가 된다.

### A2. 체결(trade) 단위 데이터 (로컬, 단기)

| dataset | path | venue | 기간 | rows | aggressor | 연속성 |
|---|---|---|---|---|---|---|
| Binance aggTrades | `runtime/crypto/BTCUSDT/historical/aggtrades_daily/*.zip` | Binance | 2026-09-09 ~ 09-22 (14일) | 18,052,124 | `is_buyer_maker` | agg id 갭 0, 중복 0 |
| Bybit public trades | `.../historical/bybit_trades_daily/*.csv.gz` | Bybit | 2026-09-09 ~ 09-22 (14일) | 28,129,193 (k15 캐시 기준) | `side` | SHA256SUMS 있음 |
| D2 realtime session | `runtime/crypto/BTCUSDT/{realtime,features}` | Bybit | 2026-09-23 29분 | trade 17,939 / book 1s 1,742 | side | 단일 세션 |
| npz 캐시 | `runtime/crypto/manual_context_exit_r0/cache/` | 둘 다 | 위와 같음 | k15 18.05M / 28.13M | Binance k15에는 side 없음 | 파생 캐시 |

### A3. 서버 운영 데이터 (읽기만, 2026-10-13 확인)

| dataset | path | 시작 | 상태 | 크기 | 비고 |
|---|---|---|---|---|---|
| Context Collector journal `context` | `/root/usb_runtime/crypto_context/journal/context` | 2026-10-10 12:47 (session 31cc8177) | 60 segment, 마지막 `.open` | 66M | 1초 1행, flow는 5/15/60초 rolling만 |
| `wall_v2` | `.../journal/wall_v2` | 같음 | 60 segment | 22M | OPEN/연속성 이벤트, distance_bps, notional, multiple |
| `wall_r0` | `.../journal/wall_r0` | 같음 | 60 segment | 51M | OPENED 등 생애주기 |
| `telemetry`/`session` | 같음 | 같음 | 60 segment | 39M / 8K | |
| Flow horizon worker | `/root/usb_runtime/btc_context_ui/flow/` | 2026-10-12 14:38 | 운영 | 69K | `flow_series.json` **최근 1,440분만 유지**(롤링) |
| Manual Trade Recorder | `/root/usb_runtime/manual_trade_research/store/` | 체결 2026-09-29 ~ | 운영 | events 47, episodes 19, snapshots 3,935 | 아래 F절 |
| Recorder market_cache | `.../store/runtime/market_cache/` | 14일분 | 캐시 | - | Binance klines_1m는 6필드(OHLC+volume), **taker 없음** |
| Q123 store | `/root/usb_runtime/q123_forward/store/` | 2026-10-10 | 존재 확인만 | 1.3M | approaches/events/meta/outcomes/quality. **내용 미열람, 이번 연구에 사용 안 함** |

### A4. 기타

| dataset | 내용 | 이번 연구 관련성 |
|---|---|---|
| crypto-paper tape (`runtime/crypto/paper/*`) | 페이퍼 엔진 런의 주문·체결 | 시장 데이터가 아님, 사용 안 함 |
| Manual R1 (`research/crypto/manual_r1/`) | 2026-09-23~24 터미널 17건 | provenance 감사 결과 사용자 거래 1건뿐(나머지 개발 테스트), 사용 안 함 |
| C1 lifecycle (`research/crypto/c1_exit/c1_lifecycle_snapshots_v1.parquet`) | C1 신호 진입 스냅샷 2022-01-05 ~ 2026-08-21, 8,015행 | Phase A 밖. C1은 별도 신호라 보조 필터 후보로만 |
| `~/usb/BTCUSDT-1m-2021-03.csv` 등 루트 4파일 | Binance 2021-03/07 kline, 2021-03 funding, 2021-03-01 metrics(Binance OI 하루) | #1에 포함되는 단편, 출처 불명 사본 |

---

## B. Long-term usable datasets

Phase A 본체에 쓸 수 있는 것(전 구간 결측 0, venue 단일, 재현 가능):

1. **Binance USD-M BTCUSDT perp 1m kline + taker_buy_quote, 2021-02-01 ~ 2026-10-04 07:59 (약 5.67년, 2,982,720분).** 가격·flow를 같은 venue·같은 분 경계로 제공한다. 운영 UI의 flow와 같은 원천(D절).
2. Binance mark/index 1m (2021-02 ~ 2026-09-22): basis(premium) 계산용.
3. Bybit funding / OI / mark / index (2020-03 ~ 2026-09-23): Phase B 필터 후보. **venue가 다르다**(C·D절 주의).

forward-safe 판단: 1m kline은 분이 닫힌 뒤 값이 확정되고 이후 바뀌지 않는다. 시점 t의 상태를 `open_ms + 60s <= t`인 분만으로 만들면 미래 정보가 섞이지 않는다. 운영 worker도 `last_closed_end_ms`로 닫힌 분만 쓴다.

---

## C. Flow reconstruction feasibility

### 운영 UI flow의 정확한 정의 (`backend/app/crypto/btc_context_ui_v1/flow_horizon.py`)
- 원천: Binance `/fapi/v1/klines` 1m의 quote volume과 taker buy quote volume.
- buy = Σ taker_buy_quote, sell = Σ (quote - taker_buy_quote), 직전 닫힌 분부터 거슬러 N분 합.
- N: 10m=10, 1h=60, 4h=240, **1d=1,440(달력일이 아니라 trailing 24시간)**.
- `lean` = BUY if buy > sell, SELL if sell > buy, EVEN if 같음. **임계값·dead band 없음.**
- 한 분이라도 빠지면 그 horizon은 `None`.

### 판정

| 질문 | 답 | 근거 |
|---|---|---|
| A. aggressive buy/sell 재구성 가능한 과거 데이터가 있는가 | **있음(1분 단위, 5.67년)**. 체결 단위는 14일뿐 | #1+#2의 taker_buy_quote. aggTrades는 2026-09-09~22 |
| B. 10m/1h/4h/1d flow를 과거에 재구성 가능한가 | **가능, 운영 UI와 비트 단위로 같은 정의** | 운영 worker가 읽는 필드와 같은 필드, archive와 REST 사본 불일치 0. 1d는 2021-02-02 00:00부터(1,440분 warm-up) |
| Context Collector journal로 재구성 | 불가(정확 재구성 아님) | rolling 5/15/60s만, raw trade 저장 안 함(`btc_flow_horizon_v1/SOURCE_AUDIT.md`) |
| Bybit 데이터로 재구성 | kline 불가(taker 필드 없음), trade는 14일만 | |

### 주의할 점 (연구 설계에 반영해야 함)
- **dead band가 없으므로 10m lean은 자주 뒤집힐 가능성이 높다.** R1 0단계에서 horizon별 flip 빈도·상태 지속시간을 수익과 무관하게 먼저 세어야 한다(R절). dead band를 넣는 순간 그것은 threshold이고 family 수에 포함된다.
- 1d는 rolling이라 4h·1h와 강하게 겹친다(1d 창의 1/6이 4h 창). "1D와 4H가 같은 방향"은 부분적으로 기계적 상관이다. 독립성 가정 금지.
- flow lean은 **가격 방향이 아니다.** 가격이 오르면서 SELL lean일 수 있다. 연구는 flow를 쓸지 가격 기반 방향(같은 horizon의 수익률 부호)을 쓸지 정해야 하고, 사용자가 보는 화면이 flow이므로 본체는 flow, 가격 부호는 비교 대조군으로 둔다.

---

## D. OI / funding / basis availability

| 항목 | venue | 기간 | 단위 | 상태 |
|---|---|---|---|---|
| Funding | Bybit | 2020-03-25 ~ 2026-09-23 | 8h | 결측 0 |
| Funding | Binance | 2021-02 ~ 2026-08 | 8h | 2026-09 이후 로컬 없음 |
| OI | Bybit | 2020-08-04 ~ 2026-09-23 | 5m | 결측 4슬롯(20분) |
| OI | **Binance** | 2021-03-01 하루뿐(루트 metrics csv) | 5m | **NOT_AVAILABLE** |
| Basis (premium = mark/index - 1) | Bybit | 2020-03 ~ 2026-09-23 | 1m | 계산 가능 |
| Basis (premium) | Binance | 2021-02 ~ 2026-09-22 | 1m | 계산 가능(mark/index 행 감사는 R1에서) |
| Basis (분기물 - 지수) | Binance | 2021-02 ~ 2026-09-22 | 1m | 계산 가능 |
| R0 regime 파생 (oi4h, basis, funding) | 혼합 | 2021-02 ~ 2026-09-23 | 1m | 기존 산출물, 정의는 `CRYPTO_R0_REGIME_CONTRACT_V1.md` |

핵심 제약: **flow는 Binance, OI는 Bybit뿐.** I5(flow 반전 + OI 변화)를 과거에서 재면 venue가 섞인다. 두 venue OI가 같은 방향으로 움직인다는 가정은 검증된 적 없다. Phase B에서 I5를 쓰려면 venue 혼합을 명시하거나 Binance OI(data.vision metrics)를 따로 확보해야 한다(Q절).
또 Binance 계열 로컬 데이터는 2026-09-22(kline은 10-04 07:59)에서 끝나 최근 구간은 비어 있다.

---

## E. Wall availability

| 원천 | 기간 | 판정 |
|---|---|---|
| Context Collector `wall_v2`/`wall_r0` (Binance 책) | 2026-10-10 12:47 ~ 현재(약 2.8일, 진행 중) | forward 전용 |
| D2 orderbook_1s (Bybit) | 2026-09-23 29분 | 연구 불가 |
| MS-V0 로컬 trial, Liquidity Map | 로컬 단기 실험 | 연구 불가 |
| 과거 depth snapshot / diff | 없음 | **NOT_AVAILABLE** |

과거 벽 데이터는 없다. 벽을 쓰는 어떤 규칙도 historical 검증이 불가능하고 forward로만 쌓아야 한다. 참고로 Market Context R0는 "10bp 내 벽은 99% 상존이라 사건이 아니다"를 이미 보고했다.

---

## F. Manual trade overlap

Recorder store(서버, 읽기만) 기준 **에피소드 19건 종료 + 1건 보유 중(LONG, 2026-10-07 02:22 진입)**. 종료 19건 중 `MANUAL_TERMINAL` 16건, `USBM_UNVERIFIED` 3건(09-29~30). 실측 수수료는 47 fill 전부 **5.0bp/side**.

| 종료 시각 기준 | 에피소드 | 그중 manual_only | Binance 1m+taker (≤10-04 07:59) | Bybit 장기(≤09-23) | Collector wall (≥10-10) |
|---|---|---|---|---|---|
| 09-29 ~ 10-04 03:44 종료 | 14 | 11 | **전 구간 커버** | 없음 | 없음 |
| 10-04 03:44 ~ 08:44 | 1 | 1 | 진입만, 청산 미커버 | 없음 | 없음 |
| 10-04 08:44 ~ 10-07 02:22 | 4 | 4 | 없음 | 없음 | 없음 |
| 보유 중 (10-07 ~) | 1 | 1 | 없음 | 없음 | 10-10 이후 일부 |

보유 시간 분포(manual_only 16건): 1시간 미만 7건, 2.9~5시간 2건, 11.97~45.3시간 7건. 보유형 성향과 단타가 섞여 있다. 청산과 반대 방향 진입이 같은 분에 일어나는 **연속 반전(stop-and-reverse) 형태**가 10/01 이후 반복된다.

결론: 사용자 실제 거래와 겹치는 시장 데이터는 **Binance flow 기준 11~12건**, 벽 기준 0건(보유 중 1건 부분). 이 표본으로 성향을 "검증"하는 것은 불가능하고, 사용자가 실제로 무엇을 근거로 들고 있었는지 **사례 서술용**으로만 쓸 수 있다. 성향 검증의 주 원천은 historical replay(M절)다.

---

## G. Entry family candidates

모든 family는 운영 UI와 같은 lean 정의(C절)를 쓴다. 지표 조합·threshold 없음. 진입은 **상태 전이가 일어난 닫힌 분의 다음 분 open**(체결 가정은 K절).

표기: D=1d lean, H4=4h, H1=1h, M=10m. 방향 dir = D(= H4) 방향.

| ID | 이름 | 진입 조건 (전이 시점) | 계산 가능 |
|---|---|---|---|
| F1 | TREND_FOLLOW | D=H4=H1=M=dir가 처음 성립한 분 | 가능, 2021-02-02~ |
| F2 | PULLBACK_RETURN | D=H4=dir 유지 중, M이 dir의 반대였다가 dir로 복귀한 분 (H1은 dir) | 가능 |
| F3 | DEEP_PULLBACK_RETURN | D=H4=dir 유지 중, H1과 M이 반대였다가 H1이 dir로 복귀한 분 | 가능 |
| F0 | NO_TRADE | D ≠ H4 (또는 EVEN/None) | 가능. family가 아니라 **진입 금지 상태**로 둔다 |
| (C0) | 대조군 | 같은 방향·같은 빈도로 무작위 시점 진입, 같은 보유 규칙 | 가능. family 수에 넣지 않는 baseline |

family는 F1~F3 **3개**로 제한한다. 가격 수익률 부호로 같은 구조를 만든 버전은 family가 아니라 비교 대조군(C1 PRICE_PROXY)으로만 보고, 최종 선택 대상에서 제외한다.

설계상 결정해야 할 것(R1 계약에서 고정, 결과 보기 전):
- 포지션은 한 번에 하나. 보유 중 새 진입 신호는 무시(겹침 방지). 사용자처럼 반대 신호에서 반전할지는 별도 변형이 아니라 **EXIT 규칙의 일부**로만 다룬다.
- EVEN과 None(분 결측, 이번 데이터엔 0)은 "방향 없음"으로 취급.

---

## H. Hold questions

모든 질문은 **같은 진입 집합에서 짝지은 비교(paired)**로 잰다. 진입 후 매 분 상태(D/H4/H1/M lean)를 추적하므로 상태 변화 시점이 분 단위로 남는다.

| ID | 질문 | 비교 방법 | 측정 가능 |
|---|---|---|---|
| QH1 | 10M만 반대가 됐을 때 보유가 유리했나 | M만 반대가 된 첫 분에 청산 vs 그대로 보유(이후 I-규칙까지). 차이 = 보유의 추가 net bp | 가능 |
| QH2 | 1H까지 반대지만 4H/1D 유지일 때 보유가 유리했나 | H1 반대 첫 분 청산 vs 보유 | 가능 |
| QH3 | 1H와 4H가 동시에 반대가 된 뒤 보유하면 악화되나 | H1&H4 반대 첫 분 청산 vs 고정 N시간 추가 보유(N은 계약에서 미리 고정) | 가능 |
| QH4 | 손실 중이지만 thesis 유지일 때 고정 손절보다 나았나 | 진입 후 미실현 손실 구간에 있으면서 D=H4=dir인 사례만 골라, 고정 -X% 손절 vs thesis 유지 보유. X는 지금 정하지 않음(9절 대응, L절) | 가능 |
| QH5 | 수익 중 10M 반전만 보고 청산하면 너무 빨리 나가나 | 미실현 이익 상태에서 M 반전 청산 vs 보유, 차이와 놓친 MFE | 가능 |

주의: QH4·QH5는 "손실 중", "이익 중"으로 표본을 자르는데, 이 조건은 사후가 아니라 **그 시점의 미실현 손익**이라 forward-safe다. 다만 표본 선택이 경로 의존이라 사례 수 보고가 필수다.

---

## I. Thesis invalidation candidates

| ID | 정의 | historical 재현 | 비고 |
|---|---|---|---|
| I1 | H1 lean이 dir 반대 | 완전 재현 | |
| I2 | H4 lean이 dir 반대 | 완전 재현 | |
| I3 | H1과 H4가 동시에 반대 | 완전 재현 | |
| I4 | D와 H4 불일치 | 완전 재현 | 진입 조건(F0)의 역 |
| I5 | flow 반전 + OI 변화 | **부분** | OI는 Bybit뿐, 2026-09-23 이후 없음, venue 혼합. "OI 변화"의 방향·창은 계약에서 고정 필요 |
| I6 | wall / Q123 confirmed condition | **불가** | wall은 2.8일, Q123는 이번 연구에서 사용 금지. 기록만 |

손익률은 invalidation에 쓰지 않는다. 손익 기반 규칙은 **catastrophic safety layer**로만 분리(J·L절).

catastrophic layer 설계 메모: Recorder 이벤트 47건의 `leverage`는 20x 33건, 10x 4건, 1x 4건, 기록 없음 6건이다. 종료 에피소드 MAE가 최대 290bp였는데, 20x였다면 290bp는 증거금의 58%다. 그래서 safety layer는 "% 손절"이 아니라 **레버리지별 청산가 거리 대비 비율**로 정의해야 사용자 상황을 반영한다. 값은 이번에 정하지 않는다.

---

## J. Exit study design

Entry family가 R1에서 살아남은 경우에만 진행. 같은 진입 집합에 아래 청산 규칙을 전부 적용해 **짝지은 결과**를 낸다.

| ID | 청산 규칙 | 종류 |
|---|---|---|
| X1~X5 | 고정 1H / 4H / 8H / 12H / 24H | 시간 기준(대조군 역할) |
| X6 | thesis invalidation (I-후보 중 R1에서 미리 지정한 1개) | thesis |
| X7 | 10M 반전 | 단기 반전 |
| X8 | 1H 반전 (= I1) | |
| X9 | 1H+4H 반전 (= I3) | |
| X10 | profit giveback / trailing | 이익 보호. giveback 비율은 계약에서 1~2개 고정값만 |

공통: 모든 규칙에 **동일한 catastrophic stop**과 **최대 보유 상한**(예: 데이터 끝, 또는 계약에서 고정한 상한)을 씌운다. 상한에 걸린 건수는 따로 보고한다.

비교는 규칙끼리가 아니라 **X6 대 나머지 짝 차이**를 주 지표로 둔다(사용자 성향 = thesis exit).

---

## 9절 대응: 사용자 성향 검증 구조

같은 진입 집합에서 세 정책을 비교한다.

| 정책 | 정의 |
|---|---|
| A. 고정 -X% stop | 손익만 보고 청산. X는 계약에 **3개 이하 값을 사전 등록**, 전부 보고, 사후 선택 금지 |
| B. thesis 유지 중 HOLD | invalidation이 없으면 보유. 청산은 최대 보유 상한에서만 |
| C. thesis invalidation 시 EXIT | X6 |

A·B·C 모두 동일한 catastrophic stop을 공유한다. 그래서 "catastrophic stop의 효과"와 "thesis stop의 효과"가 분리된다. catastrophic stop이 몇 번 발동했는지는 정책별로 따로 센다.

---

## 10절 대응: 보유 결과 측정 항목

| 항목 | 1m kline으로 측정 | 비고 |
|---|---|---|
| entry time / price | 가능 | 다음 분 open, 또는 mark 기준 대안 |
| thesis state (매 분) | 가능 | D/H4/H1/M lean 4개 |
| thesis invalidation time | 가능 | 분 단위 |
| exit price | 가능 | |
| gross / net return | 가능 | net은 K절 비용 |
| MFE / MAE | 가능, **분 내부 순서는 모름** | 같은 분에 high와 low가 다 찍히면 순서 불명. Recorder의 `extreme_in_fill_minute`와 같은 문제. 보수적으로 불리한 쪽 먼저 가정 + 건수 보고 |
| time to MFE / MAE | 가능(분 해상도) | |
| duration | 가능 | |
| max profit before invalidation | 가능 | |
| giveback before exit | 가능 | = (invalidation 전 최대 미실현 이익) - 실현 |

체결 단위 해상도(초)는 2026-09-09~22 14일만 가능하므로, 분 내부 순서 문제의 크기를 이 14일로 **진단만** 할 수 있다.

---

## K. Cost model availability

| 구성 | 기존 계약 (D5.4/D6) | 이번 연구 적합성 |
|---|---|---|
| 수수료 | 왕복 11.0bp (VIP0 taker 5.5bp/side, `app.crypto.paper.fees`) | **보수적.** 사용자 실측은 Binance 5.0bp/side = 왕복 10.0bp (Recorder fill 47/47) |
| spread + funding | 0.25bp | **보유형에는 맞지 않음.** 0.25bp는 짧은 보유 가정. 12~45시간 보유는 funding 정산 1~5회를 지나므로 funding은 실제 시계열로 계산해야 함 |
| slippage | spread에 포함 | 시장가 1회 진입 기준이면 유지 가능 |

권고(새 비용 생성이 아니라 재사용 방식): 수수료는 기존 11.0bp 계약을 **주 시나리오로 그대로** 쓰고 실측 10.0bp를 민감도로 병기. funding은 고정값 대신 보유 중 지나간 정산 시점의 **Binance funding 실측**을 부호에 맞춰 더한다(Binance funding은 2026-08까지 로컬, 이후는 Bybit로 대체하거나 비움). 이것은 새 비용 숫자가 아니라 기존 원천의 시계열 적용이다.

---

## L. Overfitting controls

1. **정의 고정 먼저, 결과는 나중.** lean 정의는 운영 UI 코드 그대로. dead band·창 길이·family 정의는 R1 계약에서 결과를 보기 전에 고정하고 sha를 남긴다.
2. **family 상한**: entry 3개(F1~F3), invalidation 4개(I1~I4, I5는 Phase B), exit 10개, 고정 stop X 3개 이하. 총 비교 칸 수를 계약에 적고 다중검정 보정 또는 permutation null을 쓴다.
3. **0단계 census는 수익을 보지 않는다.** 상태 빈도·지속시간·flip 수만 세서 표본이 충분한지 확인하고, 여기서 정의를 바꿀 수 있는 마지막 기회로 둔다.
4. **대조군 필수**: (a) 같은 빈도·같은 방향 무작위 진입, (b) circular shift로 flow 상태와 가격을 어긋나게 한 placebo, (c) 가격 부호 proxy. BTC는 2021~2026에 큰 drift가 있어 LONG 편향이 그 자체로 이긴다. 결과는 **방향별 drift를 뺀 값**으로 본다.
5. **사후 최고 조합 선택 금지 계약**: holdout에 들어가는 조합은 validation 끝에 **1개(최대 2개)**만 사전 지정. holdout 결과를 보고 다른 조합으로 바꾸면 그 연구는 FAIL로 기록.
6. **결과 보고 후 threshold 변경 금지**: 변경이 필요하면 새 버전(R2) 계약으로 새 holdout이 필요하다. 이미 본 holdout은 재사용 불가.
7. **겹침 처리**: 한 번에 한 포지션. 표본 수는 독립 에피소드 수로 보고(분 수 아님).

---

## M. Historical validation design

데이터: Binance perp 1m + taker, 2021-02-02 ~ 2026-10-04 07:59.

| 구간 | 기간 | 성격 | 용도 |
|---|---|---|---|
| Train | 2021-02-02 ~ 2023-06-30 | 2021 강세, 2022 약세, 2023 횡보 | census, 정의 확인, 가설 형성 |
| Validation | 2023-07-01 ~ 2025-03-31 | 2024 강세 포함 | holdout 진입 후보 1~2개 지정 |
| Holdout (봉인) | 2025-04-01 ~ 2026-10-04 | 최근 18개월 | 1회만 열람 |

대안: chronological walk-forward(예: 12개월 학습, 3개월 평가, 3개월씩 전진). 어느 쪽이든 R1 계약에서 하나만 고른다. 구간 경계는 결과를 보기 전에 고정.

판정 지표 후보(값은 계약에서): 독립 에피소드 수 하한, net bp 평균의 CI, fold별 부호 일관성, 대조군 대비 차이, catastrophic stop 발동률.

---

## N. Forward validation design

```
Historical discovery (R1~)
  -> rule freeze (sha, 계약 문서)
  -> forward 20 에피소드 (기록만)
  -> forward 50 에피소드 (판정)
  -> 실제 적용 검토 (사용자 결정)
```

Manual Trade Recorder 활용 계획:
- Recorder는 이미 사용자의 실제 진입·청산·MFE/MAE/giveback을 에피소드로 쌓는다. **Recorder를 바꾸지 않는다.**
- flow 상태는 Binance 1m kline에서 사후에 정확히 재계산되므로(C절), 각 에피소드의 진입·보유·청산 시점 thesis 상태를 **나중에 붙일 수 있다.** 기록 시점에 저장할 필요가 없다.
- forward에서 비교할 것: (1) 고정 규칙이 낸 가상 에피소드(rule-only shadow), (2) 사용자 실제 에피소드, (3) 둘의 차이(규칙이 청산하라 했는데 사용자가 들고 있던 구간의 결과). (3)이 사용자 성향의 가치를 직접 잰다.
- 사용자가 thesis를 말로 남기는 경로(MANUAL-R2 human decision schema, `research/crypto/manual_r2/human_decision_schema_v1.json`)가 이미 설계돼 있다. 배포는 별도 승인 사항.
- forward 20/50은 rule-only shadow 에피소드 기준. 사용자 에피소드는 빈도가 낮아 별도 표본으로 센다.

---

## O. UI integration potential

LONG/SHORT 추천 기능은 만들지 않는다. 향후 가능한 역할만 적는다.

| 화면 요소 | Trading Method에서의 역할 후보 | 조건 |
|---|---|---|
| 1d / 4h flow | thesis 상태 표시("큰 방향 유지 / 불일치") | R1에서 4h·1d 상태가 보유 결과와 관련 있을 때만 |
| 1h flow | invalidation 경고 단계 표시 | I1/I3가 살아남을 때 |
| 10m flow | 타이밍 참고. QH1/QH5 결과에 따라 "무시해도 되는 소음"으로 표시할 수도 있음 | |
| 상단벽 / 하단벽 | 현재는 과거 검증 불가. forward 기록이 쌓이기 전에는 참고 표시 이상 금지 | Phase C |
| Recorder 에피소드 | 내 보유 중 thesis 상태 이력, giveback 표시 | 운영 UI 원칙(오늘 얼마만)과 충돌하지 않게 대화·리포트 우선 |

운영 화면은 지금 그대로 둔다. 연구 결과는 대화와 문서로 먼저 전달한다.

---

## P. Missing data

| 항목 | 상태 |
|---|---|
| Binance perp 1m + taker, 2026-10-04 08:00 ~ 현재 | 로컬 없음. 서버 flow worker는 최근 1,440분만, Recorder cache는 taker 없음 |
| Binance OI 과거 | NOT_AVAILABLE (하루분 제외) |
| Binance funding 2026-09 이후 | 로컬 없음 |
| Bybit 전 계열 2026-09-23 이후 | 로컬 없음 |
| 과거 orderbook / wall | NOT_AVAILABLE |
| 체결 단위 aggressor, 14일 외 | NOT_AVAILABLE |
| Collector journal 이전 구간의 wall/flow 1초 맥락 | NOT_AVAILABLE (2026-10-10부터) |
| 사용자 거래 중 thesis 진술 | 없음 (Recorder는 체결만) |

---

## Q. Additional data actually required

Phase A 본체(R1)에는 **추가 데이터가 필요 없다.** 2021-02 ~ 2026-10-04로 train/validation/holdout을 모두 채운다.

필요해지는 시점별:
1. **forward 단계와 최근 manual 에피소드 매칭**: Binance perp 1m kline(taker 포함) 2026-10-04 08:00 이후. data.vision daily 또는 공개 REST, 서명 없음. 운영 IP와 같은 IP에서 받지 말 것(429 이력).
2. **Phase B I5**: Binance OI 과거(data.vision `metrics` 5m)가 있으면 venue 혼합을 없앨 수 있다. 없으면 Bybit OI로 하고 혼합을 명시.
3. Phase C/D: 새로 받을 수 있는 과거 데이터가 없다. Collector forward 누적만이 원천.

이번 단계에서는 아무것도 받지 않았다.

---

## R. Recommended first real study

**PTM-R1: Flow-Horizon Thesis Replay, Phase A (price/flow only)**

0단계 (수익 미열람, train만)
- D/H4/H1/M lean 시계열 생성(운영 코드와 같은 함수로, 같은 입력이면 같은 출력임을 테스트로 고정).
- horizon별 flip 빈도, 상태 지속시간 분포, D=H4 일치 비율, F1~F3 진입 전이 건수, I1~I4 발생 간격.
- 판정: F1~F3 각각 train 독립 에피소드 수가 계약 하한을 넘는가. 못 넘는 family는 여기서 탈락(수익 안 보고).

1단계 (train, 수익 열람)
- F1~F3 × {X6, 고정 4H, 고정 24H} 와 QH1~QH5 짝 비교, 대조군 3종.
- 출력은 표뿐, 규칙 선택 없음.

2단계 (validation)
- holdout 후보 1~2개 지정, 정책 A/B/C 비교.

3단계 (holdout, 1회)

사전 확인이 필요한 연관성: 과거 crypto 방향 연구(BTC-P2 `CLOSED_PENDING_NEW_INFORMATION`, D5.x)는 가격·파생 특성으로 방향을 잰 것이고, multi-horizon taker flow(1h/4h/1d)를 5년 구간에서 쓴 연구는 없었다(Market Context R0는 2026-08~10 단기 창과 짧은 horizon만). PTM-R1 계약에 이 차이를 명시해야 종결 연구 재개가 아니라는 근거가 된다. 다만 진입 family는 결국 방향 베팅이므로, 과거 결과("방향 edge 없음")가 R1의 사전 기대치를 낮춘다는 점도 계약에 적는다.

R1 실행 전 사용자 결정 필요: 구간 분할 방식(3구간 vs walk-forward), 고정 stop X 후보값 3개, catastrophic layer 정의 방식, 최대 보유 상한.

---

## S. Final verdict

**C. METHOD_RESEARCH_FEASIBLE_WITH_LIMITS**

- 가능: 운영 UI와 같은 정의의 10m/1h/4h/1d flow를 **2021-02-02 ~ 2026-10-04(약 5.67년), 결측 0**으로 재구성할 수 있다. ENTRY/HOLD/THESIS_VALID/THESIS_INVALID/EXIT 5단계와 QH1~QH5, I1~I4, 정책 A/B/C 비교, MFE/MAE/giveback/invalidation timing 측정을 모두 이 데이터로 설계할 수 있다.
- 제한:
  1. OI는 Bybit뿐(venue 혼합), funding·basis는 2026-09 하순에서 끊김.
  2. 벽은 2026-10-10 이후 forward 전용, Q123는 이번 연구에서 제외.
  3. 체결 단위 aggressor는 14일뿐이라 분 내부 MFE/MAE 순서는 진단만 가능.
  4. 사용자 실제 거래와 겹치는 구간은 11~12건으로 사례 서술용이다.
  5. 기존 0.25bp spread+funding 가정은 보유형에 맞지 않아 funding 시계열 적용이 필요하다.

STOP. 다음 단계(PTM-R1 계약 작성)는 사용자 승인 후.
