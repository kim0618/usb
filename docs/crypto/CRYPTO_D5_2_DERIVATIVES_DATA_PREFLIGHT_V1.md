# US-B CRYPTO D5.2 Derivatives Data Preflight V1

작성 2026-09-24. **Edge 연구 아님.** 새 파생시장 데이터를 무료·공개 경로로 얼마나 얻을 수 있는지 실측만 했다.
feature 계산 0, 수익률 분석 0, Score/Probability/AUTO 0, 계정·API key·결제 0, 운영 서버 접속 0, commit/push 0.
소스별 상세는 `CRYPTO_DERIVATIVES_SOURCE_MATRIX_V1.md`. probe 원본은 `data/runtime/crypto/d5_2/probes/`.

---

## 0. 결론 (10개 질문)

| # | 질문 | 답 |
|---|---|---|
| 1 | Liquidation 장기 연구 | **무료로는 불가.** 3사 모두 청산 이력 REST가 없거나(Bybit·Binance) 24시간 롤링(OKX)이다. 무료 장기 청산 파일은 Binance COIN-M 스냅샷 16개월(2023-06 ~ 2024-10, 중단)뿐이다. 장기 청산은 **유료(Tardis)** 외 경로가 없다 |
| 2 | Cross-exchange basis 장기 연구 | **가능(무료).** Bybit·Binance·OKX 모두 perp·mark·index·spot 1m이 2019~2021년부터 공개. Binance는 월 zip 아카이브로 받을 수 있다 |
| 3 | Term structure 장기 연구 | **가능(무료).** Binance 연속 분기물 1m(CURRENT 2021-02, NEXT 2021-03)과 개별 분기물 아카이브(USDT-M 2021-03 계약~, COIN-M 2020-09 계약~). Deribit 만기선물 1m도 무료 |
| 4 | 당장 수집할 realtime | **3사 청산 스트림**(Bybit allLiquidation, Binance forceOrder `/market/`, OKX liquidation-orders + 24h REST 백필), **OKX funding·OI 5m**(무료 이력이 3개월/5일뿐) |
| 5 | 무료로 가능한 범위 | 가격·basis·기간구조·funding(Bybit/Binance)·OI 5m(Bybit/Binance)·포지셔닝 비율(Binance)·원시 체결(Bybit 2020~, OKX 2021-10~)·호가 깊이 밴드(Binance 2023~)·DVOL(2021-03~) |
| 6 | 유료가 정말 필요한 곳 | **청산 장기 이력 하나.** 나머지는 무료로 충분하다. OKX funding·OI 장기와 과거 spread 전체는 무료 대체가 불완전하지만 연구 착수를 막지 않는다 |
| 7 | D5.2 Edge 연구에 바로 쓸 데이터 | cross-exchange perp basis(3사 mark/index/last 1m), spot-perp basis(3사 spot 1m), 기간구조(Binance 연속 분기물), Binance metrics(OI·L/S·taker 비율 5m) |
| 8 | Forward-only로 기다릴 데이터 | 3사 청산, OKX funding·OI 고해상도, Bybit 청산 전량 |
| 9 | 예상 저장공간 | 로컬 연구 아카이브 약 3~4GB(체결 제외). 서버 realtime은 §6 실측 기준 하루 수 MB 이하 |
| 10 | 다음 정확한 연구 범위 | §10 |

**D1 기록 정정**: D1은 "과거 개별 trade history endpoint가 공개 REST에 없다"고 적었다. REST 기준으로는 맞지만
`public.bybit.com/trading/BTCUSDT/`에 **2020-03-25부터 어제까지 일별 원시 체결 파일이 누락 없이** 있다.

---

## 1. 무엇을 probe했나

| 거래소 | REST | 공개 아카이브 | WebSocket |
|---|---|---|---|
| Bybit | kline(spot/linear/inverse), premium-index, instruments, delivery-price, historical-volatility, 청산 REST 존재 여부 | public.bybit.com(trading/spot/premium_index/spot_index) | allLiquidation, 구 liquidation 토픽 |
| Binance | fapi/dapi/api klines·mark·index·premium·continuous, funding, OI·OI hist·basis, allForceOrders, exchangeInfo | data.binance.vision(um/cm daily·monthly 전 디렉터리) | forceOrder(`/ws/`, `/market/ws/`, `/public/ws/`), markPrice 건전성 확인 |
| OKX | candles·mark·index(현물/스왑), funding history, premium history, rubik OI, instruments, 청산 REST, delivery history | static.okx.com traderecords | liquidation-orders |
| 선택 | Deribit DVOL·만기선물 1m, Tardis 무료 표본, CoinGlass·Tardis 가격 페이지 | | |

요청 로그 `probe_log.jsonl`(URL·상태·rate 헤더). HTTP 429 0회.

분류 기준(LONG/SHORT/FORWARD_ONLY)과 RAW/AGGREGATED 정의는 **probe 전에**
`data/runtime/crypto/d5_2/classification_criteria_v1.json`에 기록했다(매트릭스 서두 표와 동일).

---

## 2. Liquidation

| 거래소 | realtime | historical | 판정 |
|---|---|---|---|
| Bybit | WS `allLiquidation.<symbol>` 구독 성공. 구 `liquidation.<symbol>`은 `handler not found`로 폐지 | REST 없음(404). 공개 아카이브에 청산 디렉터리 없음. 체결 파일에 청산 플래그 없음 | FORWARD_ONLY |
| Binance | WS `forceOrder`는 **`/market/ws/` 경로에서만** 데이터가 온다(§7). 문서상 심볼당 1초 안 최신 1건만 보내는 스냅샷 | `allForceOrders` REST 404(폐지). vision은 COIN-M `BTCUSD_PERP`만 2023-06-25 ~ 2024-10-14, USDT-M 없음 | FORWARD_ONLY, AGGREGATED |
| OKX | WS `liquidation-orders`(instType SWAP 전체) | REST `liquidation-orders`가 **약 24시간 롤링**(28쪽, 2,714건, 23.8h). 1일 이상 과거 `after` 지정 시 빈 응답 | FORWARD_ONLY(+24h 백필) |

필드(정규화 시 필요):

| 거래소 | side 의미 | price | qty | timestamp |
|---|---|---|---|---|
| Bybit allLiquidation | `S` (문서 서술: Buy = 롱 포지션 청산. **이번 캡처로 미검증**, §6) | `p` | `v` (BTC) | `T` ms |
| Binance forceOrder | `S` = 청산 주문의 방향(SELL = 롱 청산) | `p` 주문가, `ap` 평균 체결가 | `q`/`z` (BTC) | `T` 체결 ms, `E` 이벤트 ms |
| OKX | `side` 청산 주문 방향 + `posSide` 청산된 포지션 | `bkPx` 파산가 | `sz` **계약 수 x 0.01 BTC** | `ts` ms |

세 거래소의 side 의미가 서로 다르다. 정규화 스키마는 "청산된 포지션 방향(LONG/SHORT)" 하나로 통일해야 한다.

실측 RAW/AGGREGATED 판정은 §6.

---

## 3. Cross-exchange

같은 순간(2026-09-24T05:58:02Z 전후 0.3초) 스냅샷 `probes/cross_exchange_snapshot.json`:

| | last | mark | index | best bid / ask | funding | OI |
|---|---|---|---|---|---|---|
| Bybit BTCUSDT | 83,983.50 | 83,985.12 | 84,032.03 | 83,983.40 / 83,983.50 | 0 (표시값) | 60,666.834 BTC |
| Binance BTCUSDT | - | 83,998.50 | 84,043.88 | 83,990.20 / 83,990.30 | 0.00004935 | 97,500.290 BTC |
| OKX BTC-USDT-SWAP | 83,987.1 | 83,984.6 | 84,030.9 | 83,987.1 / 83,987.2 | 0.0000656(이번 기간), 직전 확정 0.00000495 | 29,642.77 BTC (`oiCcy`) |
| 현물 bid / ask | Bybit 84,028.3 / 84,028.4 | Binance 84,036.64 / 84,036.65 | OKX 84,020.4 / 84,020.5 | | | |

세 perp가 모두 자기 index보다 약 44~49 USDT(5.2~5.8bp) 낮다(Bybit last-index 48.5, Binance mark-index 45.4, OKX last-index 43.8). 같은 순간 perp best bid끼리의 차이는 최대 약 7 USDT(Binance 83,990.2 vs Bybit 83,983.4).
(관측 기록일 뿐 신호 판단 아님.)

**timestamp 의미 (정규화 전에 맞춰야 함)**

| 항목 | Bybit | Binance | OKX |
|---|---|---|---|
| kline 시각 | 시작 시각 ms(`start`), 최신순 반환 | 시작 시각 ms(open time), 오래된순 | 시작 시각 ms(문자열), 최신순, 요청당 100개 |
| funding 과거 레코드 | 정산 시각 | `fundingTime` = 정산 시각 | 이력은 정산 시각. **실시간 `fundingTime`은 이번 정산 예정 시각** |
| OI | 5m 이력, 스냅샷 시각(D2에서 stamp+5분 지연 사용) | vision metrics `create_time` 5m(UTC 문자열) | rubik 5m/1H/1D, 문자열 ms |
| 체결 아카이브 | 초 단위 소수(`1790035200.1286`) | ms | ms |

**장기 이력 (1m)**: 매트릭스 §2. 요약:

| 계열 | Bybit | Binance | OKX |
|---|---|---|---|
| perp last | 2020-03-25 (D2) | 2019-09-08 | 2019-12-17 |
| mark | 2020-03-25 (D2) | 2019-12-23 | 2020-01-04 |
| index | 2020-03-25 (D2) | 2019-12-23 | 2020-01-04 |
| premium | 2020-12-31 이전 | 2019-12-24 | 약 6개월만 |
| spot | 2021-07-05 | 2017-08-17 | 2018-01-12 |
| funding | 2020-03-25 (D2) | 2019-09-10 | **약 3개월만** |
| OI | 5m 2020-08-04 (D2) | 5m 2020-09-01 (vision, 누락 0) | 1D 2023-12-31 / 5m 약 5일 |

결측·중복: 이번엔 도달 시작점과 파일 목록만 확인했다. vision metrics(일 파일 누락 0, 초기 행 2중복, 최근 비정렬)와
bookDepth(누락 3일) 외의 **bar 단위 결측·중복 검사는 수집 단계(D5.2 데이터 수집)에서 D2와 같은 방식으로 한다.**
계약 변경: Binance·OKX·Bybit 모두 BTC USDT perp 심볼은 연구 창 안에서 바뀌지 않았다(상장 시각: Bybit 2020-03, Binance
2019-09, OKX `listTime` 2019-11-12).

---

## 4. Term structure

| 소스 | 무엇 | 시작 | 비고 |
|---|---|---|---|
| Binance `continuousKlines` CURRENT_QUARTER / NEXT_QUARTER | USDT-M 연속 분기물 1m | 2021-02-03 / 2021-03-16 | 롤 규칙은 거래소 정의 |
| vision `um/monthly/klines/BTCUSDT_YYMMDD` | 개별 계약 24개(210326 ~ 261225) | 첫 계약 BTCUSDT_210326(파일 시작월 미확인) | 롤 규칙을 직접 정의할 때 |
| vision `cm/monthly/klines/BTCUSD_YYMMDD` | COIN-M 개별 계약 26개(200925 ~ 261225) | 2020-06 | 만기 뒤 달 파일 존재(품질 확인 필요) |
| Deribit `get_tradingview_chart_data` | 만기된 인버스 선물 1m | 2021 계약 확인 | 계약명을 달력 규칙으로 생성해야 함 |
| Bybit | 만기 계약 REST kline **빈 목록**. 체결 아카이브에 인버스 분기물(Z21~)·USDT/USDC 선물 있음 | | 체결에서 1m 재구성 필요 |
| OKX | 만기 계약 REST kline **거부(50047)**. 체결 아카이브(2021-10~)에 만기선물 포함 | | 체결에서 재구성 |

계산 가능한 후보(이번엔 계산 안 함): perp-spot basis, futures-index 연율 basis, near/far calendar spread, 곡선 기울기.
Binance `/futures/data/basis`는 30일만이라 장기 basis는 연속 kline + index로 재계산해야 한다.

options(선택): Deribit DVOL 1h 2021-03-24부터, Bybit option HV 약 2년.

---

## 5. Cost / Access

| 분류 | 해당 |
|---|---|
| FREE | 이번에 LONG_HISTORY로 분류한 전부, 3사 청산 WS, OKX 청산 REST(24h), Tardis 매월 1일분 |
| FREE_WITH_ACCOUNT / API_KEY_REQUIRED | 필요한 데이터 없음 |
| PAID | Tardis.dev 전체 이력(공개 요금표 월 $350 ~ $6,000), CoinGlass API(월 $29 ~ $699). **구매 안 함** |
| UNAVAILABLE | Bybit 청산 REST, Bybit 구 liquidation 토픽, Binance allForceOrders, 만기 계약 REST kline(Bybit·OKX), OKX 장기 funding·OI 5m |

Tardis 무료분(매월 1일)의 행 수: Binance 2021-01-01 3,392건 / 2024-01-01 1,206건, Bybit 2024-01-01 331건,
OKX 2024-01-01 373건(OKX `amount`는 계약 수). 매월 1일만으로는 연속 이벤트 연구가 안 되지만, 자체 forward 수집분과
형식·건수를 대조하는 검증용으로는 쓸 수 있다.

---

## 6. Realtime 청산 스트림 실측 (2026-09-27 완료)

9/24 중단분을 다시 수행했다. 연구 전용 프로세스 `backend/app/crypto/derivatives/liq_capture.py`, 저장 `data/runtime/crypto/d5_2/capture_v2/`
(운영 paper feed와 별도 프로세스·디렉터리). 2026-09-27 09:15 ~ 10:00 UTC, **45분**, 3사 동시, 재연결 0회.
분석 `capture_report.py` → `capture_v2/capture_report.json`.

| 항목 | Bybit `allLiquidation.BTCUSDT` | Binance `/market/` forceOrder | OKX `liquidation-orders` (SWAP 전체) |
|---|---|---|---|
| BTC 청산 이벤트 | 16 (시간당 21.3) | 21 (시간당 28.0) | 15 (시간당 20.0) |
| BTC 명목 | 94,873 USD | 123,268 USD | 50,824 USD |
| 방향 | SHORT 16 | SHORT 19 / LONG 2 | SHORT 15 |
| 같은 초 최대 이벤트 수 | **5** | **1** | 1 |
| 전체 심볼 이벤트 (참고) | BTC 토픽만 구독 | 718 (`!forceOrder@arr`) | 174 |
| RAW / AGGREGATED | RAW 추정 (REST 없어 전량 대조 불가) | **AGGREGATED 확인** (심볼·초당 1건 상한) | **부분 전송 확인**: WS 수신 4분 창에서 REST 46건 중 WS 15건만(33%). REST 전량이 WS의 상위집합(WS에만 있는 건 0) |
| 하루 저장량: 청산 메시지만 | **0.09MB** | **8.1MB** (전 심볼 스트림 포함, BTC만이면 훨씬 작음) | **2.0MB** (SWAP 전체) |
| 하루 저장량: 건전성 스트림 포함 | 134MB (tickers 100ms급) | 34MB (markPrice 1s) | 95MB (mark-price 전 푸시) |

**side 의미 검증** (각 거래소 자기 mark의 이벤트 전 60초 변화):
SHORT 청산 16/19/15건 모두 직전 60초에 가격이 **올랐다**(중앙 +7.1 / +9.3 / +11.9bp, 하락 비율 0%). 정규화 매핑(Bybit `S`=Sell → SHORT,
Binance 주문 BUY → SHORT, OKX `posSide`=short → SHORT)과 일치한다. **캡처 45분 동안 상승장이라 LONG 쪽 검증은 사실상 없다**(Binance 2건뿐, 방향 혼재).
Bybit `S`의 LONG 의미는 문서 서술(Buy = 롱 청산) 그대로 두고, forward 수집 첫 하락 구간에서 재검증한다.

결론:
- 3사 모두 청산은 **FORWARD_ONLY**. 과거 edge 검증(Q1·Q2)은 무료로 불가.
- OKX는 **WS만으로는 1/3만 잡힌다** → forward 수집은 REST 24h 폴링(시간당 1회 이상)이 주, WS는 지연 보조.
- Binance forceOrder는 스로틀 스냅샷이라 "건수"를 청산 강도로 쓰면 안 된다(초당 1건 상한). 명목·방향 신호로만.
- 저장량은 건전성 스트림 설계가 좌우한다. 청산 메시지 + 저빈도 건전성(1분 1회 mark)이면 3사 합계 하루 수 MB.

## 7. 함정 (다음 세션이 반드시 알아야 할 것)

1. **Binance USDT-M 선물 WS 경로 분리.** `wss://fstream.binance.com/ws/btcusdt@markPrice@1s`는 연결되고 구독 응답도 오지만
   **메시지 0**이다. markPrice·forceOrder는 `/market/ws/`, bookTicker는 `/public/ws/`에서 온다. 처음 3분 probe에서
   Binance 청산 0건을 "청산이 없었다"로 읽을 뻔했다. 수집기는 **건전성 스트림(markPrice@1s)을 항상 같이** 구독하고,
   N초 무수신이면 경로 문제로 경보해야 한다.
2. OKX는 Python 기본 User-Agent를 403으로 거부한다.
3. 3사 청산 side 의미가 다르다(§2).
4. OKX 수량은 계약 단위(BTC-USDT-SWAP 0.01 BTC).
5. OKX funding의 실시간 `fundingTime`은 예정 시각이다. 과거 레코드와 섞으면 8시간 어긋난다.
6. Binance vision 품질: 중복 행, 비정렬, 만기 뒤 파일.
7. Python 파일 쓰기 버퍼 때문에 장시간 WS 캡처 중간 점검에서 줄 수가 0으로 보인다(`buffering=1` 필요). 실제 수집기는 줄 단위 flush.

---

## 8. Architecture (설계만, 코드 0)

기존 `backend/app/crypto/`(D2 수집·paper·research)는 건드리지 않는다. 새 패키지를 옆에 둔다.

```
backend/app/crypto/derivatives/
  adapters/bybit.py      raw 수신/다운로드 -> RawRecord(원문 보존)
  adapters/binance.py
  adapters/okx.py
  schema.py              정규화 dataclass
  normalize.py           거래소별 raw -> 정규화 (단위·side·timestamp 통일)
  archive.py             공개 아카이브(zip/gz) 다운로드 + checksum + manifest
  realtime.py            청산 WS 수집기(건전성 스트림 동반, 줄 단위 fsync)
```

정규화 스키마(초안):

| 테이블 | 키 | 필드 |
|---|---|---|
| `deriv_bar_1m` | venue, instrument, price_type(LAST/MARK/INDEX/PREMIUM/SPOT), open_ts_ms | o, h, l, c, volume_base, turnover_quote, source, source_ts_semantics |
| `deriv_funding` | venue, instrument, settle_ts_ms | rate, is_settled, source |
| `deriv_oi` | venue, instrument, ts_ms, resolution | oi_base, oi_quote, known_at_ms(PIT 지연 규칙) |
| `deriv_liquidation` | venue, instrument, event_ts_ms, seq | liquidated_position_side(LONG/SHORT), price, bankrupt_price, qty_base, notional_quote, raw_side, is_aggregated, recv_ts_ms |
| `deriv_contract` | venue, instrument | kind(PERP/FUTURE/SPOT), expiry_ts_ms, contract_size, settle_ccy |

원칙: raw는 D2처럼 원문을 JSONL로 보존하고 manifest(sha256)를 붙인다. 정규화는 파생물이라 언제든 재생성한다.
`known_at_ms`를 스키마에 넣어 PIT 지연 규칙(D5 P3 같은 것)을 데이터 쪽에서 강제한다.

**의존성**: 새 의존성 0. 기존 httpx·websockets·stdlib zipfile/gzip로 충분하다.
**CCXT**: 도입하지 않는다. 1m 과거 kline·funding 조회는 CCXT로도 되지만, 이번 데이터의 핵심(공개 zip 아카이브,
Binance WS 경로 분리, 청산 스트림의 거래소별 의미 차이)은 CCXT가 감춰 버리는 부분이다. 청산 스트림 지원 여부는
이번에 설치하지 않아 확인하지 않았다.

---

## 9. 저장량

로컬(WSL, 여유 874GB). 연구용 무료 아카이브, BTC 1종목:

| 데이터 | 기간 | 압축 크기(추정 근거) |
|---|---|---|
| Binance perp·mark·index·premium 1m (vision 월 zip) | 2020-01 ~ | 계열당 0.5~1.9MB/월 → 약 400MB |
| Binance spot 1m | 2017-08 ~ | 약 2MB/월 → 약 220MB |
| Binance 연속 분기물 CURRENT/NEXT 1m (REST → gzip JSONL) | 2021-02 ~ | 약 150MB |
| Binance metrics 5m | 2020-09 ~ | 약 11KB/일 → 약 25MB |
| Binance bookDepth | 2023-01 ~ | 약 470KB/일 → 약 640MB |
| OKX perp·mark·index·spot 1m (REST 페이징 → gzip JSONL) | 2018~2020 ~ | 약 1GB |
| Bybit spot·premium 1m | 2021 ~ | 약 150MB |
| Deribit DVOL 1h | 2021-03 ~ | 수 MB |
| **합계(추정)** | | **약 3GB** |
| (선택) Bybit perp 원시 체결 | 2020-03 ~ | **최근 83.5MB/일 → 연 약 30GB.** 전량 다운로드 권장 안 함, 표적 기간만 |
| (선택) OKX perp 원시 체결 | 2021-10 ~ | 21.6MB/일 → 연 약 8GB |

서버(realtime forward, BTC만): §6 실측 기준. 청산 3사 + OKX funding·OI 5m 폴링은 하루 수 MB 이하로
서버 여유(기존 기록 약 1.3GB, 이번 미확인)에 부담이 작다. **cross-exchange 1초 틱은 서버에 쌓지 않는다**(하루 수십 MB,
그리고 1m 과거 데이터가 무료라 필요성이 낮다).

서버 수집 개시는 이번 범위가 아니다(서버 미접속). 권고만 한다.

---

## 10. 다음 정확한 연구 범위 (권고)

**D5.2-A (바로 가능, 무료, 장기)**: cross-exchange / spot-perp / 기간구조 basis.
- 입력: 3사 perp·mark·index·spot 1m, Binance 연속 분기물, Binance metrics 5m
- 연구 창: 3사 공통 가용 시작(2021-02 ~, Bybit spot 쓸 경우 2021-07 ~)
- D5·D5.1 교훈 적용: 사전등록, walk-forward, taker 11bp 비용, ratio 기준, 연도 robustness. **basis가 해마다 약해졌다는
  D5.1 결과를 사전에 명시**하고, 새 feature가 같은 신호의 재포장인지(상관) 먼저 검사
- 선결: 수집 단계(D2 방식: manifest, 결측·중복 전수, 결정적 재현)

**D5.2-B (forward)**: 3사 청산 스트림 서버 수집 개시 → 최소 수개월 누적 뒤 이벤트 연구. 무료 경로로는 과거 검증이 불가능하므로
결론까지 시간이 걸린다. 과거 검증을 원하면 Tardis 구매가 유일한 경로(구매 결정은 사용자).

**D5.2-C (보조)**: Binance bookDepth(2023~)로 D5·D5.1의 "과거 spread/depth UNKNOWN"을 부분 해소. 고변동성 구간 비용이
STRESS보다 나빴는지 확인할 수 있는 유일한 무료 근거다.

---

## 11. Phase 1 완료 (2026-09-27 추가)

| 항목 | 결과 |
|---|---|
| 청산 실측 | §6 완료. 3사 FORWARD_ONLY 확정, OKX WS 부분 전송, Binance 집계 |
| 수집한 장기 데이터 | Binance vision 2,659 파일(USDT-M perp·mark·index·spot·funding·metrics·USDT-M 분기물) + COIN-M 분기물·index, 전부 공개 CHECKSUM sha256 검증. OKX 5m 3계열 각 593,280행 공백 0. 합계 **852MB** (`data/runtime/crypto/d5_2/archive/`, manifest 포함) |
| 동결 전 QC | `data/runtime/crypto/d5_2/qc_v1.json`. 5m 격자 593,280개 기준 유효 비율: Bybit·Binance last·OKX 3계열·funding·OI 100%, Binance mark 99.56%, index 99.37%, spot 99.97%, COIN-M index 99.81% |
| 발견 1 | **Binance spot 1m 파일은 2025년부터 timestamp가 마이크로초**다. 정규화하지 않으면 2025~2026년 현물이 전부 사라진다(`norm_ms`, 테스트 있음) |
| 발견 2 | **USDT-M 분기물은 2023년 하반기 전까지 한 번에 한 계약만 상장**돼 원월물이 없다(유효 2021 1%, 2022 0.1%, 2023 33%). 기간구조는 COIN-M(당월 약 100%, 원월 91~93%)으로 바꿨다. 계약 동결 전 결정 |
| 발견 3 | OKX 5m history는 2021-02까지 공백 없이 도달한다(preflight의 "REST 페이징 약 1GB"는 1m 기준 추정, 5m로 받아 35MB) |
| Phase 2 계약 | `CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md` sha256 `c49cd0e5…bc8cf81` |
