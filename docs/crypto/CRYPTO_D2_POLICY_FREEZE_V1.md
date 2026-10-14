# US-B CRYPTO D2 정책 동결 V1

작성 2026-09-23. **이 문서는 계약이다.** 여기 적힌 4개 정책은 D2에서 동결되며, D3 이후 구현은
이 계약을 전제로 한다. 계약을 바꾸려면 새 버전 문서를 만들고 변경 이유와 영향 범위를 적는다.

동결 대상 구현: `backend/app/crypto/` (collector version `d2.1`, schema version `crypto.d2.v1`)

---

## P1. Virtual KRW conversion

**전략 PnL은 USDT 단위로만 계산한다. 환율은 전략 판정에 절대 들어가지 않는다.**

1. FX rate는 **각 run 시작 시점에 1회 고정**한다.
2. run 진행 중 환율이 변해도 **전략 PnL에 반영하지 않는다.** 환율 변동 손익과 전략 손익을
   섞으면 전략의 edge를 측정할 수 없다.
3. KRW 환산은 **LIVE reporting 계층에서만** 별도로 처리할 수 있다. 연구·backtest·paper 결과의
   authority는 언제나 USDT 값이다.
4. **D2는 어떤 환율 숫자도 하드코딩하지 않는다.** 이 저장소의 주식 쪽 `frontend/lib/fx.ts` 고정환율을
   crypto가 재사용하는 것도 금지한다. 시장·정산통화·기준시각이 다르다.
5. paper run 생성 시 계약:

```
PaperRunParameters:
  fx_rate_krw_per_usdt   Decimal   # 필수. 기본값 없음. 생략 시 run 생성 실패
  fx_rate_source         str       # 출처 식별자
  fx_rate_asof_utc       str       # ISO8601 UTC. run 시작 시각과의 차이를 기록
```

   세 필드 중 하나라도 없으면 run을 만들지 않는다. **기본값을 주지 않는 것이 이 계약의 핵심이다.**

---

## P2. publicTrade

**raw trade는 영구 보존한다. 공개 REST에 과거 개별 체결 이력이 없으므로(D1 Q1), 지금 버리면
영원히 복구할 수 없다.**

1. **primary identity는 `execId`** (WS 필드 `i`, UUID)다. 중복 판정·재처리 판정 전부 `execId` 기준.
2. **`seq`는 unique key로 쓰지 않는다.** 순서 힌트로만 쓴다.
3. 수신한 raw trade는 필드를 지우지 않고 그대로 append한다. 여기에 아래 3개만 덧붙인다.
   - `message_timestamp_ms` (provider `ts`)
   - `receive_timestamp_ms` (로컬 수신)
   - `lag_ms` (= receive - message)
4. 중복 `execId`는 **저장하지 않고 telemetry 카운터만 올린다**(`duplicate_trades`).
5. 저장 위치: `realtime/public_trade/`. **immutable/raw 계층이며 재생성 대상이 아니다.**

> 로컬 시계와 provider 시계의 차이는 D1에서 median 616ms로 측정됐고 네트워크 지연과 시계 오차를
> 분해하지 못했다(D1 R4). `lag_ms`는 **분해되지 않은 합계**다. 이 사실을 모르고 쓰면 안 된다.

---

## P3. orderbook

**depth50 raw delta는 장기 보존하지 않는다. 1초 feature snapshot만 영구 보존한다.**

1. local in-memory book을 유지하고 snapshot/delta로 재구성한다.
2. `u` 연속성과 `seq` 역행을 검증한다. 위반 시 **local book을 버리고**(`reset`) 즉시 resync한다.
   부분 복구를 시도하지 않는다.
   resync는 **orderbook topic만 unsubscribe 후 재 subscribe**하는 방식이다. Bybit는 새 subscribe에만
   snapshot을 다시 보내므로, gap 이후 가만히 기다리면 feature 스트림이 조용히 멈춘다.
   재구독 횟수는 telemetry `book_resyncs`로 기록한다.
3. reconnect 또는 새 snapshot 수신 시 **local book을 무조건 reset**한다.
4. 1초에 **1건**의 feature snapshot만 저장한다. 기준은 provider `ts`의 초 단위다.
5. 영구 보존 feature 16종 + 타임스탬프 2종 (`features/orderbook_1s/`, `crypto.orderbook_feature.v1`):
   `best_bid`, `best_ask`, `spread`, `spread_bps`, `mid`,
   `bid_volume_top5`, `ask_volume_top5`, `bid_volume_top20`, `ask_volume_top20`,
   `imbalance_5`, `imbalance_20`, `microprice`, `microprice_minus_mid`,
   `top5_depth`, `top20_depth`, `book_pressure_ratio`,
   그리고 `timestamp_ms` / `receive_timestamp_ms`.
6. **이 결정은 되돌릴 수 없다**(D1 R5). feature 정의를 나중에 바꾸면 과거 구간은 재계산할 수 없다.
   feature를 추가하려면 새 `schema_version`을 쓰고, 추가 시점 이전 구간은 **결측으로 표시**한다.
7. 1초 격자는 sub-second 미세구조를 버린다. 1초보다 빠른 신호가 필요한지는 **UNKNOWN**(D1 Q7).

---

## P4. REST rate limit

**공식 한계의 안전한 일부만 쓴다. 한계를 시험하지 않는다.**

1. endpoint별 **8 req/s** 토큰 간격 limiter를 적용한다(`RateLimiter`, `d2.1` 기본값).
2. 재시도는 **지수 backoff + jitter**, 상한 30초, 최대 5회.
3. 재시도 대상:
   - HTTP 429
   - `retCode 10006` (rate limit)
   - `retCode 10000`, `10016` (provider transient service error)
   - transport 오류
   그 밖의 `retCode != 0`은 **즉시 실패**시킨다. 조용히 넘기지 않는다.
4. telemetry는 manifest에 **영구 기록**한다: `requests`, `retries`, `rate_limits`.
5. **극한 rate-limit stress test 금지.** 한도를 찾기 위한 의도적 초과 요청을 하지 않는다.
   따라서 실제 한도는 **UNKNOWN**으로 남는다(D1 R9).

---

## P6. (부수) tickers 상태 병합

D1 R6이 경고한 항목이다. `tickers`는 **snapshot 1건 + delta 다수** 구조이고, delta는 바뀐 필드만
싣는다. 병합 없이 메시지를 그대로 저장하면 대부분의 행이 null이 되고 mark/funding 상태가
조용히 낡는다. D2 1차 smoke에서 실제로 1,371행 중 `markPrice`가 있는 행이 134개뿐이었다.

1. in-memory ticker state를 유지한다. `type=snapshot`이면 **교체**, `type=delta`면 **병합**한다.
2. 저장은 **병합된 전체 상태**로 한다. 필드 목록은 `TICKER_FIELDS`로 고정하고 schema를 버전한다
   (`crypto.ticker.v2`).
3. 각 행에 `message_type`과 `changed_fields`를 함께 남겨, 어떤 값이 그 메시지에서 실제로 바뀐
   것이고 어떤 값이 이월된 것인지 사후에 구분할 수 있게 한다.
4. snapshot 없이 도착한 delta는 **저장하지 않고** telemetry만 올린다
   (`ticker_deltas_without_snapshot`). 빈 껍데기 행을 만들지 않는다.
5. reconnect 시 ticker state를 **버린다.** orderbook과 같은 규칙이다. 재연결 후 첫 snapshot 전까지는
   상태가 없다.
6. 주기적 REST 재동기는 **D2 범위 밖**이다. WS 상태만으로 장기 운용할 때의 드리프트는 D3에서 다룬다.

---

## P5. (부수) 저장 계층 구분

P2/P3에서 파생되는 구분을 명시한다.

| 계층 | 경로 | 성질 | 재생성 |
|---|---|---|---|
| immutable raw | `historical/*`, `realtime/public_trade/` | 정규화만 거친 원본 | provider가 주는 한 재수집 가능. trade는 **불가** |
| derived | `features/orderbook_1s/`, `features/finalized_kline/`, `features/ticker_state/` | 계산·선별 결과 | raw가 없으므로 **재생성 불가** |
| manifest | `manifest/` | 출처 증거 | 데이터와 함께 생성 |
| quarantine | `quarantine/` | 결함이 확인된 산출물 | 삭제하지 않고 보존, 사용 금지 |

모든 영구 파일은 manifest를 통해 `provider`, `market`, `symbol`, `dataset`, `schema_version`,
`collector_version`, `requested_start/end`, `actual_start/end`, `generated_at`, `checksum`을
추적할 수 있어야 한다.

manifest는 두 종류다. 성질이 다르므로 한 스키마로 합치지 않는다.

| 종류 | 파일명 | 대상 | 고유 필드 |
|---|---|---|---|
| dataset manifest | `<dataset>_<start>_<end>.json` | historical 수집분 | `requested_start/end_ms`, `requests`, `retries`, `rate_limits` |
| stream manifest | `<dataset>_session_<session_id>.json` | realtime 세션 산출물 4종 | `session_id`, `session_start/end_ms`, `telemetry{reconnects, malformed, book_gaps, book_resyncs, duplicate_trades, ticker_deltas_without_snapshot, messages}` |

한 realtime 세션의 파일 4개는 **같은 `session_id`**를 파일명과 manifest에 공유한다.

---

## 동결 상태

| 정책 | 상태 | 구현 위치 |
|---|---|---|
| P1 Virtual KRW | **동결. 계약만 정의, 구현 0** | 없음(의도적) |
| P2 publicTrade | **동결. 구현 완료** | `crypto/realtime.py::RealtimeProcessor` |
| P3 orderbook | **동결. 구현 완료** | `crypto/orderbook.py`, `crypto/realtime.py` |
| P4 REST rate limit | **동결. 구현 완료** | `crypto/rest.py` |
| P5 저장 계층 | **동결. 구현 완료** | `crypto/storage.py` |
| P6 tickers 병합 | **동결. 구현 완료** | `crypto/realtime.py::RealtimeProcessor` |
