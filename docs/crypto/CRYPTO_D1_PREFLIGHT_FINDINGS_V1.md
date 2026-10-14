# US-B CRYPTO D1 공개 데이터 Preflight 실측 결과 V1

작성 2026-09-23. 이 문서는 **계약이 아니라 증거**다. Bybit Mainnet PUBLIC API만 사용해 직접 측정한 값만
적는다. 추정값은 추정이라고 표시하고, 측정하지 못한 것은 UNKNOWN으로 남긴다.

- 저장소 상태: `main`, HEAD `b6afd95`, working tree dirty (수정 17 / untracked 118). **이번 단계에서 저장소
  코드는 한 줄도 추가·수정하지 않았다.**
- 인증: 사용하지 않음. API key·계정·KYC·private endpoint·주문 endpoint 전부 미사용.
- 원시 응답 아티팩트: `data/runtime/crypto_preflight/` (gitignore 대상이므로 커밋되지 않는다)
  - `instruments_btcusdt.json`, `kline_probe.json`, `rest2_probe.json`,
    `oi_funding_floor.json`, `oi_floor2.json`, `ws_probe.json`,
    `ws_features_session_1.json`, `ws_features_session_2_reconnect.json`, `volume_estimate.json`
  - probe 스크립트 6개도 같은 디렉터리에 보존했다. 저장소 import graph에는 연결되지 않는다.
- 측정 시각: 2026-09-23 00:47 ~ 00:58 UTC. Bybit server `time` 기준.

> **HEAD 이동 기록**: 이 문서 작성 중 다른 세션이 Strategy E-MAX 커밋 1건(`9984260`,
> E-RT2.1 가용성 계약 + E-RT3 키움 RVOL 분모 저장소)을 올려 HEAD가 `b6afd95`에서 `9984260`으로
> 옮겨갔다. 그 커밋이 건드린 파일은 `strategy_e_max_rt/{availability,rvol_bootstrap,rvol_store}.py`,
> `dev/run_e_rt2_dryrun.py`, `dev/run_e_rt3_rvol.py`, `tests/strategy_e_max/test_e_rt2_1_rt3.py`,
> `docs/backtest/strategy_e_max/*` 뿐이고, **이 문서가 인용한 파일은 하나도 포함되지 않는다.**
> 따라서 여기 적힌 판정과 실측값은 전부 그대로 유효하다.


---

## 1. Instrument (GET /v5/market/instruments-info, category=linear, symbol=BTCUSDT)

| 필드 | 실측값 |
|---|---|
| `symbol` | `BTCUSDT` |
| `contractType` | `LinearPerpetual` |
| `status` | `Trading` |
| `baseCoin` / `quoteCoin` / `settleCoin` | `BTC` / `USDT` / `USDT` |
| `launchTime` | `1584230400000` = **2020-03-15T00:00:00Z** |
| `priceFilter.tickSize` | `0.10` |
| `priceFilter.minPrice` / `maxPrice` | `0.10` / `1999999.80` |
| `priceScale` | `2` |
| `lotSizeFilter.qtyStep` | `0.001` |
| `lotSizeFilter.minOrderQty` | `0.001` |
| `lotSizeFilter.maxOrderQty` / `maxMktOrderQty` | `1500.000` / `150.000` |
| `lotSizeFilter.minNotionalValue` | `5` (USDT) |
| `leverageFilter.minLeverage` | `1` |
| `leverageFilter.maxLeverage` | `150.00` |
| `leverageFilter.leverageStep` | `0.01` |
| `fundingInterval` | `480` 분 = **8시간** |
| `upperFundingRate` / `lowerFundingRate` | `0.00333` / `-0.00333` |
| `unifiedMarginTrade` | `true` |
| `isPreListing` | `false` |

`GET /v5/market/tickers`의 `fundingIntervalHour = "8"`, `fundingCap = "0.00333"`이 위 값과 일치한다.
**funding interval은 두 endpoint에서 교차 확인되었다.**

> 주의: `tickSize=0.10`인데 `priceScale=2`다. 가격 문자열은 소수점 2자리로 오지만(`86438.30`)
> 실제 격자는 0.1이다. 가격 정규화는 `priceScale`이 아니라 `tickSize`를 기준으로 해야 한다.

---

## 2. 1m Kline (GET /v5/market/kline, interval=1)

### 2.1 도달 한계

- **earliest reachable timestamp = 2020-03-25T10:36:00Z** (`1585132560000`)
- `launchTime`보다 **10.44일 늦다.** `start=launchTime`으로 호출하면 이 시각부터 반환된다.
- `start`를 launchTime 이전(2019-01-01)으로 주면 `retCode=0`, `list=[]`. **HTTP 오류가 아니라 빈 배열이다.**
  경계 판정 로직은 빈 배열을 "데이터 없음"으로 읽어야 한다.
- 1분 단위 이분탐색 14회로 경계를 확정했다: `2020-03-25T09:35:00Z` empty / `09:36:00Z` 데이터 있음.
  (이분탐색은 1시간 창 기준이라 경계가 10:36으로 확정된 절대 최소 timestamp와 다르게 보인다.
  절대 최소값은 `start=launchTime` 호출의 최소 timestamp인 **10:36:00Z**다.)

### 2.2 스키마와 순서

- row = 7개 원소, **전부 문자열**: `[startTime, open, high, low, close, volume, turnover]`
- `startTime`은 ms epoch 문자열. 전부 60000으로 나누어떨어진다 (`aligned_to_interval = true`).
- **반환 순서는 항상 newest-first (desc).** 12개 창 전부에서 일관.
- `limit` 최대 1000. `limit=1500`을 주면 오류 없이 **1000개만** 반환한다 (조용한 절삭).
- `nextPageCursor`는 kline 응답에 **없다.** pagination은 `start`/`end` 창 이동으로만 한다.
- `start`만 주면 그 시각 **이후 가장 오래된 `limit`개**를 준다. 따라서 전진 pagination은
  `next_start = max(returned_ts) + 60000`으로 성립한다. 3페이지 연속 walk에서 중복 0, 결손 0.
- `end`만 주고 그 창에 데이터가 없으면 빈 배열.
- 시간 기준은 **UTC**. 별도 offset 없음.

### 2.3 품질 감사 (총 22,000 bar 실측)

7개 창 x 1000 bar + launch walk 3000 bar + 연도별 12개 창 x 1000 bar.

| 항목 | 결과 |
|---|---|
| duplicate timestamp | **0** |
| missing 1m interval | **0** (전 구간) |
| OHLC 논리 위반 (`low <= open/close <= high`) | **0** |
| `volume` / `turnover` 파싱 가능 | 100% |
| `volume` 소수 존재 | 있음 (예 `1.362`) |

**Bybit는 무거래 분을 생략하지 않고 채워서 보낸다.** 결손 구간이 없는 대신 `volume=0`이고
OHLC가 전부 같은 값인 bar가 나온다. "bar 없음"과 "bar 있으나 거래 0"은 다른 사건이며, gap policy는
이 둘을 구분해야 한다.

### 2.4 초기 구간 품질 (중요)

| 창 (1000 bar) | zero-volume | flat OHLC |
|---|---|---|
| 2020-03-25 (최초) | 460 (46.0%) | 474 (47.4%) |
| 2020-06-10 | 244 (24.4%) | 384 (38.4%) |
| 2021-01-10 | 0 | 2 |
| 2021-07-10 이후 11개 창 전부 | 0 | 0 |

최초 bar들은 종가가 `6500`에 고정된 상장 초기 비유동 구간이다. **2020년 데이터는 절반 가까이가
합성에 가까운 bar이므로 연구 표본으로 그대로 쓰면 안 된다.** 2021-01 이후로는 완전히 정상이다.

→ 연구용 유동 구간의 실질적 시작은 **2021-01-01 (근사)**. 정확한 전환점은 D2에서 월별 zero-volume
비율을 전수 계산해 확정할 것. 현재는 창 표본 기반 근사이므로 UNKNOWN에 가깝다.

---

## 3. Funding History (GET /v5/market/funding/history)

- **earliest reachable timestamp = 2020-03-25T16:00:00Z.** `launchTime` ~ `launchTime+11d` 창에서
  반환된 2건 중 최초값이다. launchTime 이전 창(2019-01-01 ~ launchTime)은 `n=0`.
- 최초 funding은 1m kline 최초 bar(10:36Z)보다 5시간 24분 늦다. **두 시계열의 시작점이 다르다.**
- 주기: 전 구간 **정확히 480분(8h)**. gap 히스토그램이 단일값 `{480: n}`이다.
  - 2020년 첫 30일: `{480: 89}`, UTC 시각 `{0, 8, 16}`
  - 2021년 1월: `{480: 90}`, UTC 시각 `{0, 8, 16}`
  - 2026년 9월: `{480: 38}`, UTC 시각 `{0, 8, 16}`
  - **상장 직후부터 현재까지 8시간 주기가 변한 적이 없다.** instrument metadata의 `fundingInterval=480`과 일치.
- 정산 시각은 UTC 00:00 / 08:00 / 16:00 고정.
- row 스키마: `{symbol, fundingRate, fundingRateTimestamp}`. 순서 desc.
- `nextPageCursor` **없음**. pagination은 `endTime`을 최소 timestamp - 1로 내리는 후진 walk로 한다.
  6페이지 연속 walk 성공, 페이지당 200건, 중복 0.
- 실측 rate 범위: 2020년 첫 30일 `-0.00375 ~ +0.0001`, 2026년 9월 `+0.00000219 ~ +0.0001`.
  - 2020년 최저 `-0.00375`는 현재 `lowerFundingRate` 한도 `-0.00333`을 **넘는다.** funding cap은
    시기에 따라 달랐다. 과거 데이터를 현재 cap으로 검증하면 오탐이 난다.
- `limit` 최대 200.

---

## 4. Open Interest (GET /v5/market/open-interest, intervalTime=5min)

- **earliest reachable timestamp = 2020-08-04T07:50:00Z.**
  - 1일 창 월별 스캔: 2020-03-15 / 04-15 / 05-15 / 06-15 / 07-15 전부 `n=0`.
    2020-08-15부터 `n=200`.
  - 2020-07-01 ~ 07-27 격일 스캔 14개 전부 `n=0`. 2020-08-01, 08-03도 `n=0`. **08-05부터 데이터 존재.**
  - 창 cursor walk가 6페이지에서 소진되며 마지막 페이지 최소값이 `2020-08-04T07:50:00Z`.
  - `intervalTime=1d`로 2020년 전체를 조회하면 최초값이 `2020-08-05T00:00:00Z`(첫 완전일)로 일관.
- **OI는 kline보다 4.4개월, funding보다 4.4개월 늦게 시작한다.** 세 시계열의 공통 시작점은 OI가 결정한다.
- row 스키마: `{openInterest, singleOpenInterest, timestamp}`. `openInterest`는 소수 8자리 문자열
  (`60839.25800000`), 단위는 **BTC(계약수)**. `tickers`의 `openInterestValue`가 USDT 환산값이다.
- 주기: 5분 고정. gap 히스토그램 단일값 `{5: n}`, duplicate 0.
- **`nextPageCursor` 있음.** 형식은 URL-encoded `lastid=<n>&lasttime=<epoch_sec>`.
  cursor pagination 5페이지 연속 성공, 페이지당 200건, 창 안에서 연속 후진.
- `startTime`/`endTime` 창을 주면 **창의 최신쪽에서 `limit`개를 잘라 준다.** 창이 넓고 limit이 작으면
  창 앞부분이 조용히 빠진다. 전 구간 수집은 cursor를 반드시 따라가야 한다.
- `intervalTime` 옵션별 200건 도달 범위(창 미지정 시): `5min` 약 16.6h, `15min` 약 2일,
  `30min` 약 4일, `1h` 약 8일, `4h` 약 33일, `1d` 약 200일.

---

## 5. Mark / Index / Premium Index Kline

전부 `GET /v5/market/{mark|index|premium-index}-price-kline`, `interval=1`.

| endpoint | row 길이 | 스키마 | 최신 200건 gap | duplicate | launchTime 기준 최초 도달 |
|---|---|---|---|---|---|
| `mark-price-kline` | 5 | `[ts, o, h, l, c]` | 1분 단일 | 0 | 2020-03-25T08:41:00Z |
| `index-price-kline` | 5 | `[ts, o, h, l, c]` | 1분 단일 | 0 | 2020-03-25T07:57:00Z |
| `premium-index-price-kline` | 5 | `[ts, o, h, l, c]` | 1분 단일 | 0 | 2020-03-25T08:41:00Z |

- **volume/turnover 컬럼이 없다.** trade kline과 스키마가 다르므로 별도 테이블로 저장해야 한다.
- index kline이 trade kline보다 2시간 39분 **먼저** 시작한다. 시작점이 시계열마다 다르다는 사실을
  다시 확인해 준다.
- premium index 값은 비율이다 (예 `-0.00033816`).

---

## 6. Realtime WebSocket (wss://stream.bybit.com/v5/public/linear)

session 1 = 150초, session 2 = 25초 재연결 테스트. 구독 topic 4개:
`publicTrade.BTCUSDT`, `orderbook.50.BTCUSDT`, `kline.1.BTCUSDT`, `tickers.BTCUSDT`.

### 6.1 연결과 총량

| 항목 | session 1 (150s) | session 2 (25s) |
|---|---|---|
| connect | 287.7 ms | 322.7 ms |
| subscribe ack | 75.1 ms | 75.7 ms |
| first data message | 74.7 ms | 75.2 ms |
| subscribe 응답 | `{"success": true, "op": "subscribe"}` | 동일 |
| 총 메시지 | 5,505 | 1,005 |
| 총 바이트 | 1,690,580 | - |
| 처리량 | 36.69 msg/s, 11.27 KB/s | 40.17 msg/s |
| malformed 메시지 | **0** | **0** |
| 오류 프레임 | **0** | **0** |

- ping 7회 전송, 응답 7회. **20초 주기 `{"op":"ping"}`으로 연결이 유지된다.** 무응답 끊김 없음.
- `ts` 기준 서버-로컬 시각차: median 616.4 ms, p95 1151.8 ms, 범위 41.3 ~ 1710.0 ms.
  이 값은 **네트워크 지연과 WSL 로컬 시계 오차의 합이며 이 probe로는 분해할 수 없다.** UNKNOWN으로 둔다.
  latency SLA 판정에 이 숫자를 그대로 쓰면 안 된다.

### 6.2 publicTrade

- 593 메시지 / **1,943 체결** / 12.95 체결/s (session 1). 별도 90초 측정에서 10.63 체결/s.
- side: `Buy` 1,110 / `Sell` 833. **`Buy`/`Sell` 외의 값 0건.**
- 파싱 실패 row 0. `seq` 역행 0. block trade 0.
- 가격 범위 86,432.4 ~ 86,479.6, 수량 합 43.516 BTC.
- row 스키마: `{T, s, S, v, p, L, i, BT, RPI, seq}`
  - `T` 체결시각 ms, `S` side, `v` 수량, `p` 가격, `i` execId(UUID), `L` tick direction,
    `BT` block trade, `RPI` retail price improvement, `seq`
  - 같은 `seq`를 공유하는 체결이 여러 건 나온다 (한 aggressor가 여러 maker를 친 경우).
    **`seq`는 고유키가 아니다. 고유키는 `i`(execId)다.**

### 6.3 orderbook.50 local book 복원

| 항목 | session 1 | session 2 |
|---|---|---|
| snapshot | 1 | 1 |
| delta | 3,866 | 695 |
| **`u` 불연속** | **0** | **0** |
| `u == 1` 재시작 이벤트 | 0 | 0 |
| snapshot 전 delta 도착 | 0 | 0 |
| **교차 호가(bid >= ask)** | **0** | **0** |
| level 삭제(`size=0`) | 7,600 | 1,167 |
| 최종 level 수 | bid 50 / ask 50 | bid 50 / ask 50 |

- 규칙대로 동작했다: `type=snapshot`이면 local book을 비우고 재구축, `type=delta`면 병합,
  `size="0"`이면 해당 가격 삭제.
- **`u`가 3,866회 연속 증가하며 한 번도 끊기지 않았다.** local book 복원은 정상이다.
- 재연결 시 **새 snapshot이 즉시 도착**하고 book이 다시 채워진다. session 2가 이를 확인했다.
- 1초 feature snapshot 144건(session 1) / 24건(session 2) 생성. **전 구간 양쪽 50 level 유지.**
- spread는 전 구간 **0.1 = 정확히 1 tick** (min=median=max=0.1). 최우량 유동성 구간의 특성이다.
- 메시지 스키마: `{topic, type, ts, cts, data:{s, b, a, u, seq}}`.
  `ts`는 전송시각, `cts`는 매칭엔진 시각.

### 6.4 kline.1

- 90 메시지, 데이터 항목 93개 중 **`confirm=true` 3건** (150초 = 2.5분이므로 마감 bar 3개와 일치).
- OHLC 논리 위반 0.
- 마감 bar가 **분 경계 후 약 440ms에 도착**한다. 3건의 도착 시각이 `.441`, `.440`, `.440`으로 거의 동일.
- 스키마: `{start, end, interval, open, close, high, low, volume, turnover, confirm, timestamp}`
  - `end`는 `start + 59999`. `timestamp`는 메시지 생성시각.
- **`confirm=false` bar는 진행 중인 미완성 bar다.** 확정 데이터로 저장하면 안 된다.

### 6.5 tickers

- 947 메시지 = snapshot 1 + **delta 946**.
- **delta는 변경된 필드만 담는다.** 로컬 상태에 병합해야 완전한 스냅샷이 된다.
- 관측된 필드 27개. 다음이 유용하다:
  `markPrice`, `indexPrice`, `fundingRate`, `nextFundingTime`, `fundingIntervalHour`, `fundingCap`,
  `openInterest`, `openInterestValue`, `singleOpenInterest`, `bid1Price/Size`, `ask1Price/Size`,
  `lastPrice`, `volume24h`, `turnover24h`, `tickDirection`.
- **mark price, index price, funding rate, next funding time, open interest를 단일 topic에서 받을 수 있다.**
  실시간 경로에서 mark/index/funding/OI 전용 구독은 필요하지 않다.

---

## 7. 저장용량 실측과 추정

행 크기는 **실제 데이터로 파일을 써서 측정**했다 (parquet zstd / parquet snappy / CSV / JSONL).

| 데이터 | 측정 표본 | parquet zstd | CSV | JSONL |
|---|---|---|---|---|
| 1m kline | **실제 10,000 bar** | 33.65 B/row | 68.2 B/row | 151.2 B/row |
| trade (정규화) | 실제 957 체결 | 28.59 B/row | 107.54 B/row | 207.48 B/row |
| orderbook 1s feature | 실제 87 row | 99.85 B/row | 133.17 B/row | 302.72 B/row |

> orderbook feature의 99.85 B/row는 표본이 87행뿐이어서 **parquet footer 고정 오버헤드가 크게 섞인
> 상한값**이다. 하루 86,400행 규모에서는 실제로 더 낮아진다. 아래 추정은 이 상한을 그대로 써서
> 보수적으로 잡았다. 정확한 값은 D2에서 1일치를 실제로 써 보고 확정할 것.

### 7.1 1m Kline (BTCUSDT 1종목)

| 범위 | bar 수 | parquet zstd | CSV | JSONL |
|---|---|---|---|---|
| 1일 | 1,440 | 0.046 MiB | 0.094 MiB | 0.208 MiB |
| 1개월 | 43,200 | 1.39 MiB | 2.81 MiB | 6.23 MiB |
| 1년 | 525,600 | 16.9 MiB | 34.2 MiB | 75.8 MiB |
| **전체 history** (2020-03-25 ~ 2026-09-23, 2,373일 = 3,417,120 bar) | 3.42M | **110 MiB** | 222 MiB | 493 MiB |

**전체 1m history가 parquet 110 MiB다. 용량은 제약이 아니다.**

### 7.2 Trades

- 실측 10.63 ~ 12.95 체결/s → **약 92만 ~ 112만 체결/일**
- raw WS JSON 그대로: **173.65 MiB/일** (publicTrade topic 바이트 실측 2,107 B/s)
- 정규화 parquet zstd: **25.04 MiB/일**, **8.93 GiB/년**
- CSV: 94.19 MiB/일

### 7.3 Orderbook

- raw depth50 delta 그대로 저장: **744.98 MiB/일**, **265.54 GiB/년** (실측 9,041 B/s)
- 1초 feature snapshot만 저장: **8.23 MiB/일 (상한)**, **2.93 GiB/년 (상한)**
- **raw 대비 1/90 이하.** raw 장기 보존은 실익 대비 비용이 성립하지 않는다.

### 7.4 Funding / OI / Tickers

| 데이터 | 행 수 | 용량 |
|---|---|---|
| funding | 3행/일, 1,095행/년, 전체 history 7,119행 | 1 MiB 미만 |
| OI 5min | 288행/일, 105,120행/년, 2020-08-04 이후 645,408행 | 약 20 MiB (parquet 추정) |
| tickers raw WS | - | 118.89 MiB/일 (실측) |

### 7.5 결론: 무엇을 어떻게 보존하는가

**raw로 영구 보존 (immutable)**
- 1m kline (전체 history 110 MiB)
- funding history (전체 7,119행)
- OI 5min (전체 645,408행)
- mark / index / premium index 1m kline
- 근거: 전부 REST로 재현 가능하지만 용량이 사소하고, 재다운로드 비용보다 보존이 싸다.

**feature만 저장 (derived)**
- orderbook: 1초 feature snapshot만. raw depth50 delta는 **저장하지 않는다.**
- 근거: raw 266 GiB/년 대 feature 2.9 GiB/년. 그리고 **raw orderbook은 REST로 재현 불가능하므로
  버리면 영구 손실이다.** 이 손실을 감수하는 것이 명시적 결정이다. 필요 feature가 늘어날 가능성에
  대비해 top-20 level까지의 집계를 함께 남긴다.

**재다운로드 가능 (재현 가능하므로 유실 허용)**
- 1m kline, funding, OI, mark/index kline: 전부 REST 창 조회로 언제든 재구성된다.
- 검증은 저장본과 재조회본의 byte/row 대조로 한다.

**재다운로드 불가능 (한 번 놓치면 영구 손실)**
- orderbook delta 스트림, 개별 trade의 실시간 도착 순서, tickers delta 시퀀스
- **`GET /v5/market/recent-trade`는 최근 체결만 준다. 과거 개별 체결 history endpoint는 공개 REST에
  없다.** 따라서 trade 원장은 실시간으로 받아 두지 않으면 복구할 수 없다.
  - 정규화 parquet 25 MiB/일 = 8.9 GiB/년은 보존 가치가 있다고 판단한다.

**전 topic raw를 다 저장하면** 174 + 745 + 119 + 17 = **약 1.03 GiB/일 = 376 GiB/년**이다. 채택하지 않는다.
**권고 구성**(kline raw + trade 정규화 + OB 1초 feature + funding + OI)은 **약 33.3 MiB/일 = 11.9 GiB/년**이다.

---

## 8. 답해야 했던 질문에 대한 실측 답

| # | 질문 | 답 |
|---|---|---|
| Q1 | 가입/API key 없이 필요한 데이터가 모두 수집 가능한가 | **가능하다.** instrument, 1m kline, funding, OI, mark/index/premium kline, orderbook, trades, tickers 전부 PUBLIC으로 획득. 인증 0회, 429 0회, 오류 0회. 단 **과거 개별 trade history는 공개 REST에 없다**(제약). |
| Q2 | 1m kline earliest reachable | **2020-03-25T10:36:00Z** (launchTime + 10.44일). 연구용 유동 구간은 2021-01 이후 권고. |
| Q3 | funding earliest reachable | **2020-03-25T16:00:00Z** |
| Q4 | OI earliest reachable | **2020-08-04T07:50:00Z** (5min). 세 시계열 공통 시작점을 결정하는 제약. |
| Q5 | realtime publicTrade 안정성 | **안정적.** 150초에 1,943 체결, malformed 0, seq 역행 0, side 이상값 0. |
| Q6 | orderbook.50 local book 복원 | **정상.** snapshot 1 + delta 3,866, `u` 불연속 0, 교차호가 0, 재연결 시 snapshot 즉시 재수신. |
| Q7 | 1초 feature snapshot이 장기 운용에 충분한가 | **저장 관점에서는 충분**(2.93 GiB/년). 다만 **1초 격자는 sub-second 체결 미세구조를 버린다.** 진입 판정이 1초보다 빠른 신호를 요구하면 부족하다. 어떤 신호를 쓸지는 D0/D1 범위 밖이므로 **UNKNOWN**으로 남긴다. |
| Q8 | 하루/한 달/1년 용량 | 권고 구성 기준 **33.3 MiB/일**, 약 1.0 GiB/월, **11.9 GiB/년**. 1m kline 전체 history 110 MiB. |
| Q9 | US-B에서 재사용 가능한 것 | `CRYPTO_REUSE_MATRIX_V1.md` 참조. 무조건 재사용 가능한 것은 `safe_write`, `core/timezone`뿐이다. |
| Q10 | D2 구현 착수 가능한가 | **가능. D1 = PASS.** 단 아래 미해결 항목을 D2 범위에 명시적으로 넣는다. |

---

## 9. 위험과 UNKNOWN

| # | 항목 | 상태 |
|---|---|---|
| R1 | 세 시계열의 시작점이 다르다 (kline 2020-03-25, funding 2020-03-25T16:00, OI 2020-08-04) | 확정. 공통 연구 시작점은 OI가 결정 |
| R2 | 2020년 kline의 46%가 zero-volume/flat bar | 확정. 월별 전수 비율은 **UNKNOWN**, D2에서 계산 |
| R3 | funding cap이 과거에 달랐다 (2020년 `-0.00375` < 현재 한도 `-0.00333`) | 확정. cap 변경 이력은 **UNKNOWN** |
| R4 | WS `ts` 시각차 median 616ms가 네트워크 지연인지 로컬 시계 오차인지 분해 불가 | **UNKNOWN.** NTP 동기 후 재측정 필요 |
| R5 | orderbook raw를 버리는 결정은 되돌릴 수 없다 | 명시적 수용. feature 정의 변경 시 과거 재계산 불가 |
| R6 | `tickers` delta 병합 누락 시 mark/funding 상태가 조용히 낡는다 | 설계 위험. D2에서 주기적 REST 재동기 필요 |
| R7 | OI 창 조회가 limit 초과분을 조용히 절삭한다 | 확정. cursor 필수 |
| R8 | `limit=1500`이 오류 없이 1000으로 절삭된다 | 확정. 수집기는 반환 행수를 검증해야 한다 |
| R9 | rate limit 한도를 의도적으로 시험하지 않았다 | **UNKNOWN.** 전 probe에서 429 0회였으나 수집기 병렬도 설계 전 확인 필요 |
| R10 | 장기 연속 운용(수 시간 이상)에서의 재연결 빈도·`u==1` 재시작 빈도 | **UNKNOWN.** 150초 표본으로는 알 수 없다 |
| R11 | 1초 feature가 신호에 충분한지 | **UNKNOWN.** 신호 설계 이후에만 답할 수 있다 |
