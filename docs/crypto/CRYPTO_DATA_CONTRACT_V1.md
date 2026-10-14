# US-B CRYPTO 데이터 계약 V1

작성 2026-09-23. 스키마·시간·중복·결손·재연결·provenance 규약을 고정한다.
모든 규칙은 `CRYPTO_D1_PREFLIGHT_FINDINGS_V1.md`의 실측에 근거한다. 구현은 아직 없다.

---

## 1. 시간

| 규칙 | 내용 |
|---|---|
| T1 | **모든 timestamp는 UTC다.** 로컬 시간대·KST·ET는 저장하지 않는다 |
| T2 | 저장 단위는 **epoch milliseconds (int64)**. Bybit가 문자열로 주므로 경계에서 int 변환한다 |
| T3 | naive datetime은 거부한다. `app.core.timezone.require_aware`를 쓴다 (REUSE 승인 모듈) |
| T4 | KST 표시는 **표시 계층에서만** 한다. 저장·판정·digest에는 절대 들어가지 않는다 |
| T5 | 1m bar의 `ts`는 **bar 시작 시각**이다. Bybit `start`가 그렇고, `end = start + 59999`다 |
| T6 | crypto에는 거래일·세션·휴장일이 없다. **연속 UTC 분 격자**가 유일한 시간 구조다 |
| T7 | funding 정산 시각은 UTC **00:00 / 08:00 / 16:00** 고정. 상장 이후 변한 적이 없다(실측) |

### PIT 규칙

| # | 규칙 | 근거 |
|---|---|---|
| PIT1 | bar는 **자기 시작 시각에 보이지 않는다.** 시각 `t`의 판정은 `t` 이전에 마감된 bar만 본다 | 주식 `TICK_EPSILON` 규칙 계승 |
| PIT2 | tick은 `분 경계 + epsilon`, `0 < epsilon < 1분` | 같음 |
| PIT3 | WS `kline.1`의 **`confirm=false` bar는 확정 데이터가 아니다.** OHLC가 채워져 있어도 미완성이다. `confirm=true`만 저장한다 | 실측: 93개 항목 중 confirm=true 3개 |
| PIT4 | 마감 bar는 분 경계 후 **약 440ms**에 도착한다 (실측 .441/.440/.440). 판정 지연 예산은 이 값을 반영한다 | 실측 |
| PIT5 | `tickers` delta로 병합한 mark/funding 상태에는 **관측 시각을 함께 기록한다.** 병합 누락 시 조용히 낡기 때문이다 | 실측 R6 |

---

## 2. 수치 처리 (decimal handling)

| # | 규칙 |
|---|---|
| N1 | **가격과 수량은 문자열 원본을 보존하고, 계산은 `Decimal`로 한다.** float 경유 금지 |
| N2 | float를 받는 경계에서는 `Decimal(str(value))`를 쓴다 (`app.risk.domain.decimal_from` REUSE) |
| N3 | 가격 격자는 `priceScale`(=2)이 아니라 **`tickSize`(=0.1)** 다. 정규화는 tickSize 기준 |
| N4 | 수량 격자는 `qtyStep = 0.001`, 최소 `minOrderQty = 0.001`, 최소 명목 `minNotionalValue = 5 USDT` |
| N5 | `volume`은 BTC 단위 소수, `turnover`는 USDT 단위. 둘을 혼용하지 않는다 |
| N6 | OI `openInterest`는 **BTC(계약수)** 다. USDT 환산은 `openInterestValue`다. 단위 혼동이 가장 흔한 오류다 |
| N7 | funding rate는 비율이다. `0.00007532`는 0.007532%다. **퍼센트로 저장하지 않는다** |
| N8 | Decimal 산출물 저장 시 `0E-10`과 `-0`은 `0`으로 정규화한다 (`zero_normal` 개념 REUSE) |
| N9 | 회계 대조 허용오차는 주식과 같은 `Decimal("0.000001")`을 출발점으로 한다 |

---

## 3. 스키마

### 3.1 kline_1m (immutable raw)

| 컬럼 | 타입 | 출처 | 비고 |
|---|---|---|---|
| `ts_ms` | int64 | `list[i][0]` | bar 시작, PK, 60000 배수 |
| `open` / `high` / `low` / `close` | string | `[1]`~`[4]` | 원본 문자열 보존 |
| `volume` | string | `[5]` | BTC |
| `turnover` | string | `[6]` | USDT |

- 원본 row는 7개 원소 **전부 문자열**이다
- PK = `ts_ms`. 단일 심볼이므로 symbol 컬럼은 파티션 경로에 둔다
- **반환 순서는 newest-first**. 저장 시 ascending으로 정렬한다

### 3.2 trade (immutable raw, 실시간 전용)

| 컬럼 | 타입 | WS 필드 | 비고 |
|---|---|---|---|
| `exec_id` | string | `i` | **유일 키.** UUID |
| `ts_ms` | int64 | `T` | 체결 시각 |
| `price` | string | `p` | |
| `size` | string | `v` | BTC |
| `side` | string | `S` | `Buy` \| `Sell` 만 관측됨 |
| `tick_dir` | string | `L` | PlusTick 등 |
| `is_block` | bool | `BT` | |
| `is_rpi` | bool | `RPI` | |
| `seq` | int64 | `seq` | **고유하지 않다** |

- **`seq`는 PK가 아니다.** 한 aggressor가 여러 maker를 치면 여러 체결이 같은 `seq`를 공유한다(실측)
- PK = `exec_id`
- 과거 재수집 불가. 놓친 구간은 영구 손실

### 3.3 orderbook_feature_1s (derived, raw는 저장하지 않음)

| 컬럼 | 타입 | 정의 |
|---|---|---|
| `ts_ms` | int64 | snapshot 생성 시각 (1초 격자) |
| `best_bid` | string | local book 최고 매수가 |
| `best_ask` | string | local book 최저 매도가 |
| `spread` | string | `best_ask - best_bid` |
| `mid` | string | `(best_ask + best_bid) / 2` |
| `bid_volume_top5` | string | 상위 5 level 매수 수량 합 |
| `ask_volume_top5` | string | 상위 5 level 매도 수량 합 |
| `bid_volume_top20` | string | 상위 20 level 매수 수량 합 |
| `ask_volume_top20` | string | 상위 20 level 매도 수량 합 |
| `imbalance_5` | string | `(bid5 - ask5) / (bid5 + ask5)` |
| `imbalance_20` | string | `(bid20 - ask20) / (bid20 + ask20)` |
| `bid_levels` / `ask_levels` | int | 복원된 level 수. 정상값 50 |
| `u` | int64 | 이 snapshot을 만든 마지막 update id |
| `seq` | int64 | 마지막 seq |
| `book_state` | string | `OK` \| `RESYNCING` \| `STALE` |

- `u`/`seq`를 기록하는 이유: 어떤 book 상태에서 계산된 feature인지 사후 추적 가능해야 한다
- `bid_levels != 50`이면 book이 완전하지 않다는 신호다. 실측에서는 전 구간 50이었다

### 3.4 funding (immutable raw)

| 컬럼 | 타입 | 출처 |
|---|---|---|
| `ts_ms` | int64 | `fundingRateTimestamp`, PK |
| `funding_rate` | string | `fundingRate` |

- 8시간 주기, UTC 00/08/16 고정
- **`funding_cap`을 스키마에 넣지 않는다.** cap이 시기별로 달랐고(2020년 실측 `-0.00375` < 현재 한도
  `-0.00333`) 과거 cap 이력을 알 수 없다. 현재 cap으로 과거를 검증하면 오탐이 난다

### 3.5 open_interest_5min (immutable raw)

| 컬럼 | 타입 | 출처 |
|---|---|---|
| `ts_ms` | int64 | `timestamp`, PK |
| `open_interest` | string | `openInterest`, **BTC 단위** |
| `single_open_interest` | string | `singleOpenInterest` |

### 3.6 mark_1m / index_1m / premium_index_1m (immutable raw)

| 컬럼 | 타입 |
|---|---|
| `ts_ms` | int64, PK |
| `open` / `high` / `low` / `close` | string |

- **volume/turnover 컬럼이 없다.** trade kline과 별 테이블이다. 합치면 안 된다

### 3.7 ticker_state (derived, 병합 상태)

`tickers` topic은 snapshot 1 + delta 다수이고 **delta는 변경 필드만 담는다.** 로컬 병합 상태를
주기적으로 스냅샷해 저장한다. 최소 필드: `mark_price`, `index_price`, `funding_rate`,
`next_funding_time`, `open_interest`, `bid1_price/size`, `ask1_price/size`, `last_price`,
그리고 **`observed_at_ms`** (PIT5).

---

## 4. 중복 정책 (duplicate policy)

| # | 규칙 |
|---|---|
| D1 | 모든 immutable raw 테이블은 **PK 위 upsert (last-write-wins)** 로 쓴다 |
| D2 | 실측 중복 건수: kline 0, funding 0, OI 0, WS trade 0. **그러나 0을 전제하지 않는다** |
| D3 | trade 중복 판정은 `exec_id`로만 한다. `seq`로 하면 정상 체결을 중복으로 버린다 |
| D4 | 재수집 시 기존 행과 **다른 값**이 오면 덮어쓰기 전에 기록한다. 조용한 변경은 데이터 오염 신호다 |
| D5 | pagination 창이 겹치도록 설계하고, 겹친 구간의 값 일치를 검증한다 |

---

## 5. 결손 정책 (gap policy)

**핵심 구분: "bar 없음"과 "bar 있으나 거래 0"은 다른 사건이다.**

| # | 규칙 |
|---|---|
| G1 | Bybit는 무거래 분을 **생략하지 않고 채워서** 준다. 실측 22,000 bar 전수에서 missing interval 0 |
| G2 | 대신 `volume=0`이고 OHLC가 전부 같은 bar가 나온다. 이것은 **결손이 아니라 무거래**다 |
| G3 | `MISSING_BAR`: 기대 격자에 행이 없음. **오류로 취급하고 재수집한다** |
| G4 | `ZERO_VOLUME_BAR`: 행이 있고 `volume=0`. **정상 데이터로 저장하되 플래그를 세운다** |
| G5 | 연구 투입 시 `ZERO_VOLUME_BAR` 비율을 **반드시 함께 보고한다.** 2020년은 24~46%다 |
| G6 | `start`를 데이터 시작 이전으로 주면 `retCode=0`에 `list=[]`가 온다. **HTTP 오류가 아니다.** 빈 배열을 "데이터 없음"으로 읽는다 |
| G7 | `limit=1500`을 주면 오류 없이 **1000개만** 온다. 수집기는 **반환 행수를 매번 검증한다** |
| G8 | OI는 `startTime`/`endTime` 창에서 **최신쪽 `limit`개만** 주고 앞부분을 조용히 자른다. **cursor를 반드시 따라간다** |
| G9 | 시계열별 시작점이 다르다. 공통 구간 밖의 결손은 결손이 아니라 **범위 밖**이다. 이 둘을 구분해 기록한다 |
| G10 | 메모리 기록 함정: 창 밖 range는 조용히 잘린다. 수집기는 요청 범위와 반환 범위를 대조해 기록한다 |

### 시계열별 범위 밖 경계 (실측)

| 시계열 | 이 시각 이전은 `OUT_OF_RANGE`, 결손 아님 |
|---|---|
| kline 1m | 2020-03-25T10:36:00Z |
| funding | 2020-03-25T16:00:00Z |
| OI 5min | 2020-08-04T07:50:00Z |
| index 1m | 2020-03-25T07:57:00Z |
| mark / premium 1m | 2020-03-25T08:41:00Z |

---

## 6. 재연결 정책 (reconnect policy)

| # | 규칙 | 근거 |
|---|---|---|
| RC1 | WS 연결 유지는 **20초 주기 `{"op":"ping"}`** 으로 한다. 실측 7회 전송 7회 응답, 끊김 0 | 실측 |
| RC2 | **`type=snapshot`을 받으면 local book을 비우고 재구축한다.** 병합하지 않는다 | Bybit 규약 |
| RC3 | **`u == 1`은 서비스 재시작 신호다.** snapshot과 동일하게 book을 리셋한다 | Bybit 규약 |
| RC4 | `u`가 `이전 u + 1`이 아니면 **불연속이다.** book을 `RESYNCING`으로 표시하고 재구독한다 | 실측 불연속 0이지만 전제하지 않는다 |
| RC5 | snapshot 도착 전 delta가 오면 **버린다.** 적용하지 않는다 | 실측 0건 |
| RC6 | 재연결하면 새 snapshot이 즉시 온다. 실측 connect 322ms, first data 75ms | 실측 session 2 |
| RC7 | book이 `RESYNCING`/`STALE`인 동안 생성된 feature는 **그 상태를 컬럼에 기록한다.** 조용히 내보내지 않는다 | 설계 |
| RC8 | 교차 호가(`best_bid >= best_ask`)는 복원 오류 신호다. 발생 시 재동기한다. 실측 0건 | 실측 |
| RC9 | `tickers`는 delta 병합이므로 **주기적으로 REST `/v5/market/tickers`로 재동기**한다 | R6 |
| RC10 | 장시간 운용의 재연결·`u==1` 빈도는 **UNKNOWN** (150초 표본). D2에서 관측 후 기록한다 | R10 |

---

## 7. immutable raw 대 derived feature

| 구분 | 대상 | 규칙 |
|---|---|---|
| **immutable raw** | kline_1m, funding, open_interest_5min, mark/index/premium_1m, trade | 거래소가 준 값을 그대로 보존. **재계산하지 않는다.** 스키마 변경은 새 버전 테이블로 한다 |
| **derived feature** | orderbook_feature_1s, ticker_state, 모든 지표 | 정의가 바뀌면 재계산 가능해야 한다. **단 orderbook feature는 raw를 버리므로 재계산 불가다** |

### orderbook raw를 버리는 결정

- 실측: raw depth50 delta **745 MiB/일 = 266 GiB/년**. 1초 feature **8.2 MiB/일 = 2.9 GiB/년**(상한)
- **raw는 REST로 재현 불가능하므로 버리면 영구 손실이다.** 이 손실을 명시적으로 수용한다
- 완화: top-20 level까지 집계해 둔다. feature 정의가 늘어날 여지를 남긴다
- **되돌릴 수 없는 결정이다.** feature 정의 변경 시 과거 재계산은 불가능하다

### 보존 분류 (실측 기반)

| 분류 | 대상 | 근거 |
|---|---|---|
| raw 영구 보존 | kline_1m (전체 110 MiB), funding (7,119행), OI (645,408행), mark/index/premium | 용량이 사소하고 재다운로드보다 보존이 싸다 |
| feature만 저장 | orderbook | raw 대비 1/90 |
| 실시간 보존 필수 | trade | **과거 endpoint가 없다.** 25 MiB/일, 8.9 GiB/년. 보존 여부는 `CRYPTO_V0_RESEARCH_CONTRACT_V1.md` U3 미결 |
| 재다운로드 가능 | kline, funding, OI, mark/index | REST 창 조회로 언제든 재구성 |
| 재다운로드 불가 | orderbook delta, trade, tickers delta 순서 | 실시간 유일 |

---

## 8. Data provenance

모든 저장 단위는 다음을 함께 기록한다.

| 필드 | 내용 |
|---|---|
| `source` | `bybit` |
| `source_category` | `linear` |
| `source_endpoint` | 실제 호출한 경로 (`/v5/market/kline` 등) |
| `source_params` | 실제 보낸 파라미터 (정규화된 key=value) |
| `transport` | `REST` \| `WS` |
| `requested_range_ms` | 요청한 `[start, end)` |
| `returned_range_ms` | 실제 반환된 `[min_ts, max_ts]` |
| `row_count` | 행 수 |
| `request_count` | 이 단위를 만드는 데 든 호출 수 |
| `collected_at_ms` | 수집 시각 (UTC ms) |
| `server_time_ms` | 응답의 `time` 필드 |
| `checksum` | 파일 바이트의 sha256 |
| `collector_version` | 수집기 버전 문자열 |
| `completeness` | `COMPLETE` \| `PARTIAL` \| `OUT_OF_RANGE` \| `PROVIDER_FAILURE` |

- `requested_range_ms`와 `returned_range_ms`를 **둘 다** 기록하는 이유: 창 밖 절삭(G8, G10)을
  사후에 탐지할 수 있어야 한다
- **인증 정보·API key는 기록 대상이 아니다.** 애초에 쓰지 않는다

---

## 9. Version / run identity

| # | 규칙 |
|---|---|
| V1 | digest 레시피는 `sha256("usb.crypto.v1:<namespace>\n" + payload)`. 주식 네임스페이스(`usb.research.v1`)를 쓰지 않는다 |
| V2 | **산출 바이트를 바꿀 수 있는 모든 것이 digest에 들어간다**: config fingerprint, 수집기/엔진 버전, dataset identity, 범위, `code_digest` |
| V3 | **바꿀 수 없는 것은 들어가지 않는다**: wall clock, 머신, git 커밋 여부 |
| V4 | git 상태는 digest가 아니라 `source_provenance`로 옆에 기록한다. clean + remote 반영일 때만 실제 해시, 아니면 `UNCOMMITTED` |
| V5 | `code_digest = sha256((root 기준 상대경로, 파일 sha256))` 정렬. 버전을 올리지 않은 수정도 다른 run이 된다 |
| V6 | **digest 대상 파일 목록을 artifact에 명시 기록한다.** 메모리 기록 함정: `code_digest`가 패키지 전체이면 부모 run id를 재현할 수 없다 |
| V7 | run id = `<prefix>-<digest[:20]>` |
| V8 | 집계 코드를 바꾸면 집계 버전을 올린다 (주식 `BASELINE_VERSION` 규칙 계승) |
| V9 | dataset identity는 **시계열별로** 둔다. kline/funding/OI 시작점이 다르므로 하나로 묶으면 범위를 표현할 수 없다 |

---

## 10. 쓰기 규약

| # | 규칙 |
|---|---|
| W1 | `app.backtest.workspace.safe_write.safe_write()`를 그대로 쓴다 (REUSE 승인). `.partial` -> fsync -> sha256 -> `os.replace` |
| W2 | `.gitignore`에 `*.partial`이 이미 있다. 진행 중 파일은 커밋되지 않는다 |
| W3 | 파일 형식은 **Parquet + zstd**. 실측 zstd 33.65 B/row 대 snappy 49.79 B/row로 32% 작다 |
| W4 | manifest는 **주식 manifest를 쓰지 않고 crypto 자체 테이블**을 둔다. 다만 불변식 5개를 복제한다: COMPLETE 행은 path/checksum/row_count NOT NULL, path에 `.partial` 금지, checksum은 디스크 바이트 재독으로 계산, **WAL 금지**, 읽기 전용 열기에서 마이그레이션 금지 |
| W5 | **주식 workspace의 writer lock을 잡지 않는다.** crypto 전용 lock 파일을 쓴다 |
| W6 | crypto 런타임 산출물은 `data/runtime/` 하위에 둔다 (gitignore 대상) |
