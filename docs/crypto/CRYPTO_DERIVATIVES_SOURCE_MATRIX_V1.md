# US-B CRYPTO Derivatives Source Matrix V1

작성 2026-09-24 (D5.2 preflight). **모든 행은 이번 세션의 실제 probe 결과다.** 계정·API key·결제 0회.
probe 원본: `data/runtime/crypto/d5_2/probes/` (요청 로그 `probe_log.jsonl`, 응답 본문, WS 캡처).
분류 기준은 probe 전에 `data/runtime/crypto/d5_2/classification_criteria_v1.json`에 기록했다:

| 분류 | 기준 |
|---|---|
| LONG_HISTORY | 무료 도달 이력 >= 3년(시작 <= 2023-09-24), 해상도 <= 1h, 2022 약세와 2023~24 강세 포함 |
| SHORT_HISTORY | 30일 이상 3년 미만, 또는 3년 이상이지만 해상도가 1h보다 거침 |
| FORWARD_ONLY | 무료 이력 없음 또는 30일 미만. 지금부터 쌓아야 함 |

access: `FREE`(공개, 키 없음) / `FREE_WITH_ACCOUNT` / `API_KEY_REQUIRED` / `PAID` / `UNAVAILABLE`.
RAW = 거래소 이벤트 1건당 1행. AGGREGATED = 표본추출·스로틀·합산된 것.
저장량은 BTC 1종목 기준, 원본 압축 형태 그대로(별도 표기 없으면).

---

## 1. Liquidation / Forced Flow

| source | exchange | dataset | access | historical_start | resolution | realtime | API key | rate limit | storage (일/월/년) | classification | recommended usage |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WS `allLiquidation.BTCUSDT` (`stream.bybit.com/v5/public/linear`) | Bybit | 청산 이벤트 | FREE | 없음(스트림만) | 이벤트(묶음 여부는 §5 실측) | 예 | 불필요 | 연결당 구독 제한만 | §5 실측 | **FORWARD_ONLY** | 지금부터 수집 |
| WS `liquidation.BTCUSDT` (구 토픽) | Bybit | - | UNAVAILABLE | - | - | 구독 거부 `handler not found` | - | - | - | - | 사용 불가(폐지) |
| REST 청산 이력 | Bybit | - | UNAVAILABLE | - | - | - | - | - | - | - | v5에 엔드포인트 없음(404) |
| WS `btcusdt@forceOrder`, `!forceOrder@arr` (**`fstream.binance.com/market/ws/`**) | Binance | 청산 주문 | FREE | 없음 | 스냅샷(Binance 문서 서술: 심볼당 1초 안의 최신 1건만. §5에서 간격 실측) | 예 | 불필요 | 연결당 스트림 수 제한 | §5 실측 | **FORWARD_ONLY**, **AGGREGATED(스로틀)** | 수집하되 "전량"으로 해석 금지 |
| WS 같은 스트림, 구 경로 `/ws/` | Binance | - | - | - | - | **연결되지만 메시지 0** | - | - | - | - | 경로 이전됨(§6 함정) |
| REST `/fapi/v1/allForceOrders` | Binance | - | UNAVAILABLE | - | - | - | - | - | - | - | 404(폐지) |
| data.binance.vision `cm/daily/liquidationSnapshot/BTCUSD_PERP` | Binance | 청산 스냅샷(COIN-M만) | FREE | 2023-06-25 ~ **2024-10-14에서 중단** | 이벤트(스로틀 스냅샷) | 아니오 | 불필요 | S3 | 약 2KB zip/일 | SHORT_HISTORY(중단된 16개월) | 참고만. USDT-M 없음. **모든 행이 정확히 2번 중복**(1,904행 중 952 중복) |
| REST `/api/v5/public/liquidation-orders` | OKX | 청산 체결 | FREE | **롤링 약 24시간** (페이징 28쪽 2,714건 = 23.8h) | 이벤트 | 아니오 | 불필요 | 문서 기준(미검증). 이번 28쪽 연속 페이징에서 거부 0 | 약 0.3MB JSON/일 | **FORWARD_ONLY** (24h 백필) | 매일 백필로 WS 누락 보완. `sz`는 **계약 수(0.01 BTC/계약)** |
| WS `liquidation-orders` (`ws.okx.com:8443/ws/v5/public`, instType SWAP) | OKX | 청산 체결 | FREE | 없음 | 이벤트 | 예 | 불필요 | 연결 제한 | §5 실측 | **FORWARD_ONLY** | 수집. RAW 여부는 §5 대조 |
| Tardis.dev `datasets.tardis.dev/v1/<exchange>/liquidations/` | 3사 | 청산(거래소 스트림 기록) | **FREE: 매월 1일만** / 나머지 PAID | Binance 2021-01-01 표본 확인, Bybit·OKX 2024-01-01 표본 확인 | 이벤트 | - | 1일 외 401 | - | 수 KB~50KB/일 | 무료분은 표본용 / 전체는 PAID | 유료 구매 시 유일한 3사 장기 청산 소스. 가격 §4 |
| CoinGlass API | 다거래소 | 집계 청산 | PAID | 미확인(키 없음) | 집계 | - | 필요 | 요금제별 30~1,200/분 | - | 미분류 | 구매 안 함. 가격 §4 |

## 2. Cross-exchange perpetual / spot / index

| source | exchange | dataset | access | historical_start (1m) | resolution | realtime | API key | rate limit | storage | classification | recommended usage |
|---|---|---|---|---|---|---|---|---|---|---|---|
| D2 historical (기존) | Bybit | perp kline·mark·index 1m, OI 5m, funding | FREE | 2020-03-25 / OI 2020-08-04 | 1m | - | 불필요 | 문서상 IP당 600회/5초 (**미검증**, D2 수집 중 429 0회) | 수집 완료 1.2GiB | LONG_HISTORY | 그대로 |
| REST `/v5/market/premium-index-price-kline` | Bybit | premium index 1m | FREE | 2021-02-01 이전부터 도달(2020-12-31 확인) | 1m | - | 불필요 | 동일 | kline급 | LONG_HISTORY | basis 원천값 |
| REST `/v5/market/kline` category=spot | Bybit | 현물 1m | FREE | **2021-07-05** | 1m | - | 불필요 | 동일 | ~70MB/년 JSONL | LONG_HISTORY | Bybit 자체 spot-perp |
| public.bybit.com `trading/BTCUSDT/` | Bybit | **perp 원시 체결(일별)** | FREE | **2020-03-25 ~ 어제, 2,374파일(누락 0)** | 체결 | 아니오 | 불필요 | 정적 파일 | **최근 83.5MB gz/일(240만 건)**, 2021년 9.9MB/일 | LONG_HISTORY | taker 흐름·대형 체결 재구성. 청산 플래그 없음. **D1의 "과거 체결 없음"은 REST 한정이었다** |
| public.bybit.com `spot/BTCUSDT/` | Bybit | 현물 체결 | FREE | 2022-11(월별) ~ | 체결 | 아니오 | 불필요 | 정적 | 6.2MB gz/일 | SHORT_HISTORY(~3.9년이지만 시작 2022-11) | 참고 |
| REST `/fapi/v1/klines`·`markPriceKlines`·`indexPriceKlines`·`premiumIndexKlines` | Binance USDT-M | perp·mark·index·premium 1m | FREE | 2019-09-08 / 2019-12-23 / 2019-12-23 / 2019-12-24 | 1m | - | 불필요 | 2,400 weight/분 (**exchangeInfo 실측**, 헤더 `x-mbx-used-weight-1m`) | ~70MB/년/계열 | LONG_HISTORY | cross-exchange basis 주 소스 |
| data.binance.vision `um/monthly/{klines,markPriceKlines,indexPriceKlines,premiumIndexKlines}/BTCUSDT/1m` | Binance | 위와 같음(월 zip) | FREE | 2020-01 ~ 2026-08 (80개월) | 1m | 아니오 | 불필요 | 정적 | 0.5~1.9MB zip/월/계열 | LONG_HISTORY | **REST 대신 이것으로 대량 수집**(요청 수 절감) |
| REST `/fapi/v1/fundingRate` + vision `fundingRate` | Binance | funding | FREE | 2019-09-10 | 8h | - | 불필요 | 동일 | 수 KB/월 | LONG_HISTORY | cross funding |
| vision `um/daily/metrics/BTCUSDT` | Binance | **OI 5m, top trader L/S, 전체 L/S, taker buy/sell 비율** | FREE | **2020-09-01 ~ 2026-09-22, 2,213일 누락 0** | 5m | 아니오 | 불필요 | 정적 | 약 11KB zip/일 = 4MB/년 | LONG_HISTORY | **OI·포지셔닝 장기 연구의 최우선 무료 소스**. 초기 파일 **행 2중복**, 최근 파일 **시간 정렬 안 됨** → 정제 필수 |
| REST `/futures/data/openInterestHist`, `/futures/data/basis` | Binance | OI·basis 5m | FREE | **최근 30일만**(31일이면 거부) | 5m | - | 불필요 | 동일 | - | FORWARD_ONLY(REST 한정) | vision metrics로 대체 |
| vision `um/daily/bookDepth/BTCUSDT` | Binance | 호가 깊이 ±1~5% 밴드 | FREE | 2023-01-01 ~ (1,358일, 누락 3일) | 30초 x 12밴드 | 아니오 | 불필요 | 정적 | 약 470KB zip/일 = 170MB/년 | SHORT_HISTORY(3년 미만) | **historical spread/depth UNKNOWN(D5·D5.1 U3)을 부분 해소할 유일한 무료 소스** |
| vision `um/daily/bookTicker/BTCUSDT` | Binance | 최우선호가 | FREE | 2023-05-16 ~ 2024-03-30 (중단) | 틱 | 아니오 | 불필요 | 정적 | 54~146MB zip/일 | SHORT_HISTORY(중단) | 과거 spread 표본 검증용 |
| vision `um/monthly/aggTrades/BTCUSDT` | Binance | 집계 체결 | FREE | 2020-01 ~ | 체결 | 아니오 | 불필요 | 정적 | 100~500MB zip/월 | LONG_HISTORY | 필요 시만 |
| REST `/api/v3/klines` + vision spot | Binance spot | 현물 1m | FREE | 2017-08-17 | 1m | - | 불필요 | 6,000 weight/분 (exchangeInfo 실측) | ~70MB/년 | LONG_HISTORY | spot-perp basis의 spot 다리 |
| REST `/dapi/v1/klines` BTCUSD_PERP | Binance COIN-M | 인버스 perp 1m | FREE | 2020-08-11 | 1m | - | 불필요 | 동일 | ~70MB/년 | LONG_HISTORY | 선택 |
| REST `/api/v5/market/history-candles` BTC-USDT-SWAP | OKX | perp 1m | FREE | 2019-12-17 | 1m, **요청당 100봉** | - | 불필요 | 문서 기준(미검증), 이분탐색 중 거부 0 | ~70MB/년 | LONG_HISTORY | 전 구간 약 3.5만 요청(약 1시간) |
| REST `history-mark-price-candles`, `history-index-candles` | OKX | mark·index 1m | FREE | 2020-01-04 | 1m | - | 불필요 | 동일 | 동일 | LONG_HISTORY | cross basis |
| REST `history-candles` BTC-USDT | OKX spot | 현물 1m | FREE | 2018-01-12 | 1m | - | 불필요 | 동일 | 동일 | LONG_HISTORY | spot 다리 |
| REST `/api/v5/public/funding-rate-history` | OKX | funding | FREE | **약 3개월(281건)** | 8h | - | 불필요 | 문서 기준(미검증) | - | SHORT_HISTORY(기준상 30일~3년) | 장기는 계산 불가 → Tardis 또는 forward |
| REST `/api/v5/public/premium-history` | OKX | premium | FREE | 약 6개월(180일 도달, 200일 없음) | 1분 | - | 불필요 | - | - | SHORT_HISTORY | 참고 |
| REST `rubik/stat/contracts/open-interest-history` | OKX | OI | FREE | 5m: 약 5일 / 1H: 약 60일 / 1D: 2023-12-31 | 5m/1H/1D | - | 불필요 | 문서 기준(미검증), 0.25초 간격 45회 거부 0 | - | 5m FORWARD_ONLY, 1D SHORT_HISTORY | OKX OI 장기는 불가 |
| static.okx.com `traderecords/trades/daily/` | OKX | perp·**만기선물** 원시 체결 | FREE | **2021-10-01 ~ 어제**(월 표본 결측 0) | 체결 | 아니오 | 불필요 | 정적 | perp 21.6MB zip/일 | LONG_HISTORY(시작 2021-10) | OKX 체결 기반 1m 재구성·기간구조 |
| static.okx.com `aggtrades` | OKX | 집계 체결 | FREE | 2024-01-01 표본 확인(시작일 미탐색) | 체결 | 아니오 | 불필요 | 정적 | 0.9MB zip/일 | 미확정 | - |
| static.okx.com funding 월 파일(`swaprate`) | OKX | - | UNAVAILABLE(추측 경로 404) | - | - | - | - | - | - | - | 경로 미확인 |

## 3. Term structure / options

| source | exchange | dataset | access | historical_start | resolution | realtime | API key | rate limit | storage | classification | recommended usage |
|---|---|---|---|---|---|---|---|---|---|---|---|
| REST `/fapi/v1/continuousKlines` CURRENT_QUARTER / NEXT_QUARTER | Binance USDT-M | 연속 분기물 1m | FREE | 2021-02-03 / 2021-03-16 | 1m | - | 불필요 | 2,400 weight/분 | ~70MB/년/계열 | LONG_HISTORY | **기간구조 1순위**(롤 규칙이 거래소 정의) |
| vision `um/monthly/klines/BTCUSDT_YYMMDD` | Binance USDT-M | 개별 분기물 1m | FREE | BTCUSDT_210326부터 24개 계약 | 1m | 아니오 | 불필요 | 정적 | 월 zip | LONG_HISTORY | 롤 규칙을 직접 정할 때 |
| vision `cm/monthly/klines/BTCUSD_YYMMDD` + `/dapi/v1/continuousKlines` | Binance COIN-M | 인버스 분기물 1m | FREE | BTCUSD_200925(2020-06)부터 26개 계약 / 연속 2020-06-11 | 1m | 아니오 | 불필요 | 정적 | 월 zip | LONG_HISTORY | 교차 검증. 만기 뒤 달의 파일도 존재(품질 확인 필요) |
| REST `/futures/data/basis` | Binance | 분기물 basis·연율 basis | FREE | 최근 30일만 | 5m | - | 불필요 | - | - | FORWARD_ONLY(REST 한정) | 계산 검증용(연속 kline + index로 재계산 가능) |
| REST instruments + `delivery-price` | Bybit | 만기선물 목록·만기가 | FREE | 인버스 분기 2021~ / USDT 주간·월간 2023~ | - | - | 불필요 | - | - | - | **만기된 계약의 과거 kline은 REST가 빈 목록** |
| public.bybit.com `trading/BTCUSD{H,M,U,Z}yy/`, `BTCUSDT-DDMMMYY/`, `BTC-DDMMMYY/` | Bybit | 만기선물 원시 체결 | FREE | 인버스 분기 BTCUSDZ21~, USDT 선물 2025~, USDC 주간 2023~ | 체결 | 아니오 | 불필요 | 정적 | 분기물 약 1.8MB gz/일 | LONG_HISTORY(인버스 분기) | Bybit 기간구조는 체결에서 재구성해야 함 |
| REST `history-candles` 만기 계약 | OKX | - | UNAVAILABLE | - | - | - | - | - | - | - | "already settled" 거부(code 50047). 체결 아카이브로 대체 |
| REST `get_tradingview_chart_data` 만기 계약 (Deribit) | Deribit | 인버스 선물 1m | FREE | BTC-25JUN21 등 2021 계약 1m 확인 | 1m | - | 불필요 | 문서 기준 | ~70MB/년/계약열 | LONG_HISTORY | 선택. 계약명은 달력 규칙으로 생성해야 함(expired 목록이 최근 것만 반환) |
| REST `get_volatility_index_data` (DVOL) | Deribit | BTC 옵션 내재변동성 지수 | FREE | **2021-03-24** | 1h (1m은 최근만 확인) | - | 불필요 | - | 작음 | LONG_HISTORY(1h) | options 축 선택 항목 |
| REST `/v5/market/historical-volatility` | Bybit | 옵션 HV | FREE | 약 2년 전 창 조회 성공(168건) | 1h | - | 불필요 | - | 작음 | SHORT_HISTORY | 참고 |

## 4. 유료 소스 (구매하지 않음, 가격만)

| source | 확인한 공개 가격 | 역사범위 | 무료 대체 |
|---|---|---|---|
| Tardis.dev | 요금표 월 **$350 ~ $6,000**(구독 범위·결제 주기별, 일부는 분기·연 결제만). 2026-09-24 tardis.dev 첫 페이지 기준. 청산 전용 최소 요금제는 확정 못 함 | 청산 2021-01부터 표본 확인 | 매월 1일 무료분(표본 검증용), 자체 forward 수집 |
| CoinGlass API | 월 **$29 / $79 / $299 / $699** (HOBBYIST/STARTUP/STANDARD/PROFESSIONAL, 연 결제 할인). 2026-09-24 coinglass.com/pricing 기준 | 키 없이 확인 불가 | 3사 WS forward 수집 |

## 5. Realtime 청산 스트림 실측

**미완료 (세션 종료로 중단, 2026-09-24).** 25분·20분 캡처를 끝나기 전에 중단했다. 중단 시점 부분 수치: Binance `/market/ws/` forceOrder BTC 11줄·전체시장 257줄, OKX liquidation-orders(SWAP 전체) 254줄, Bybit allLiquidation BTC 이벤트 미확인(파일 버퍼링으로 0줄 표시). RAW/AGGREGATED 실측 대조(OKX REST 전량 vs WS)와 스트림별 일일 저장량은 **하지 못했다.** 다음 세션에서 캡처를 다시 돌려 채울 것.

## 6. 함정

1. **Binance USDT-M WS 경로 이전**: `fstream.binance.com/ws/<stream>`은 연결·구독 응답은 되지만 markPrice·forceOrder
   메시지가 0이다. `/market/ws/`(markPrice, forceOrder)와 `/public/ws/`(bookTicker)로 나뉘었다. 3분 probe에서 이것을
   "청산 없음"으로 오판할 뻔했다. 건전성 확인용 스트림(markPrice@1s)을 항상 같이 띄울 것.
2. **OKX는 Python 기본 UA를 403으로 거부**한다. 브라우저 UA 필요.
3. OKX 청산·OI의 `sz`/`oi`는 **계약 수**(BTC-USDT-SWAP 1계약 = 0.01 BTC). `oiCcy`가 BTC.
4. OKX funding의 `fundingTime`은 **이번 정산 예정 시각**, `prevFundingTime`/`settFundingRate`가 직전 확정분이다. Bybit/Binance의
   과거 funding 레코드 시각(정산 시각)과 의미가 다르다.
5. Binance vision 파일 품질: metrics 초기 파일 행 2중복, 최근 파일 시간 비정렬, cm 청산 스냅샷 전 행 2중복,
   만기된 COIN-M 분기물 파일이 만기 뒤 달까지 존재.
6. OKX 1m 이력은 요청당 100봉이라 전 구간 수집에 약 3.5만 요청이 든다.

---

## 추가 (2026-09-27): 요청 형식 요약표 + 이번 세션 실측 반영

§1~§4의 상세 행은 그대로 두고, D5.2 Phase 1 완료 시점의 판정을 요청 형식으로 요약한다. 분류 기준은 서두 표(probe 전 동결) 그대로.
2026-09-27에 새로 실측한 항목은 **굵게** 표시.

| Exchange | Dataset | Access | Historical Start | Resolution | Realtime | API Key | Rate Limit | Raw/Aggregated | Classification |
|---|---|---|---|---|---|---|---|---|---|
| Bybit | 청산 WS `allLiquidation.BTCUSDT` | FREE_PUBLIC | 없음 | 이벤트 | 예 | 불필요 | 연결 단위 | **RAW 추정(같은 초 최대 5건 관측). REST가 없어 전량 대조 불가** | FORWARD_ONLY |
| Bybit | 청산 REST | UNAVAILABLE | - | - | - | - | - | - | - |
| Binance | 청산 WS `forceOrder` (`/market/`) | FREE_PUBLIC | 없음 | 초당 최대 1건 | 예 | 불필요 | 연결 단위 | **AGGREGATED (45분 캡처에서 심볼·초당 최대 1건 확인)** | FORWARD_ONLY |
| Binance | 청산 REST `allForceOrders` | UNAVAILABLE | - | - | - | - | - | - | - |
| OKX | 청산 WS `liquidation-orders` | FREE_PUBLIC | 없음 | 이벤트 | 예 | 불필요 | 연결 단위 | **부분 전송: 같은 4분 창 REST 46건 중 WS 15건(33%)만 수신** | FORWARD_ONLY |
| OKX | 청산 REST `liquidation-orders` | FREE_PUBLIC | 롤링 약 24h | 이벤트 | 폴링 | 불필요 | 문서 기준 | RAW(WS보다 완전) | FORWARD_ONLY (24h 백필 필수) |
| 3사 | 청산 장기 (Tardis) | PAID | 2019~ | 이벤트 | - | 필요 | - | 거래소 스트림 기록 | (구매 시) LONG_HISTORY |
| Bybit | perp last·mark·index 1m, OI 5m, funding (D2) | FREE_PUBLIC | 2020-03-25 (OI 2020-08-04) | 1m / 5m / 8h | 예 | 불필요 | 600/5s(미검증) | AGGREGATED(bar) | LONG_HISTORY |
| Binance | USDT-M perp last·mark·index 1m (vision) | FREE_PUBLIC | 2020-01 | 1m | 예 | 불필요 | S3 | AGGREGATED(bar) | LONG_HISTORY. **창 안 결측: last 0, mark 12,962분, index 18,727분** |
| Binance | spot BTCUSDT 1m (vision) | FREE_PUBLIC | 2017-08 | 1m | 예 | 불필요 | S3 | AGGREGATED(bar) | LONG_HISTORY. **2025년 파일부터 timestamp가 마이크로초(정규화 필요)**, 결측 1,073분 |
| Binance | funding (vision 월) | FREE_PUBLIC | 2020-01 | 8h | 예 | 불필요 | S3 | 정산값 | LONG_HISTORY |
| Binance | metrics OI·L/S·taker 5m (vision 일) | FREE_PUBLIC | 2020-09-01 | 5m | - | 불필요 | S3 | AGGREGATED | LONG_HISTORY (2,060일 결측 0) |
| Binance | **USDT-M 분기물 개별 계약 1m** | FREE_PUBLIC | 2021-02 (210326) | 1m | 예 | 불필요 | S3 | AGGREGATED(bar) | 당월물 LONG_HISTORY / **원월물 SHORT_HISTORY: 2023년 하반기 전에는 한 번에 한 계약만 상장(원월물 유효 2021 1%, 2022 0.1%, 2023 33%)** |
| Binance | **COIN-M BTCUSD 분기물 개별 계약 1m + COIN-M BTCUSD index 1m** | FREE_PUBLIC | 계약 2020-07~ / index 2020-06 | 1m | 예 | 불필요 | S3 | AGGREGATED(bar) | **LONG_HISTORY: 당월물 약 100%, 원월물 연 91~93%**. D5.2 기간구조의 원천 |
| OKX | **BTC-USDT-SWAP last·mark 5m, BTC-USDT index 5m (REST history)** | FREE_PUBLIC | 2021-02-01까지 도달 확인 | 5m | 예 | 불필요 | 20/2s, 10/2s | AGGREGATED(bar) | **LONG_HISTORY: 3계열 593,280행, 공백 0** (5,933페이지씩, 429 0회) |
| OKX | funding | FREE_PUBLIC | 약 3개월 | 8h | 예 | 불필요 | - | 정산값 | SHORT_HISTORY |
| OKX | OI 5m / 1D | FREE_PUBLIC | 5일 / 2023-12-31 | 5m / 1D | 예 | 불필요 | - | AGGREGATED | FORWARD_ONLY(5m) / SHORT_HISTORY(1D) |
| Deribit | 만기 선물 1m, DVOL 1h | FREE_PUBLIC | 2021 | 1m / 1h | 예 | 불필요 | - | AGGREGATED | LONG_HISTORY (D5.2에서 미사용) |

**CCXT**: 도입하지 않았다. 필요한 원천(vision zip + CHECKSUM, OKX history 엔드포인트, 거래소별 청산 side 의미)은 native API로 모두 받았다. 새 의존성 0.
