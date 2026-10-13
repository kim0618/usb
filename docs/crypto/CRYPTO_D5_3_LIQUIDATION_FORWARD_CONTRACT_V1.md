# US-B CRYPTO D5.3 Liquidation Forward Data Contract V1

작성 2026-09-27. **DATA FOUNDATION ONLY.** 이 계약은 청산 forward 데이터를 어떻게 모으고 무엇을 정본으로 부르는지만 정한다.
edge 계산·C1 재평가·threshold·Score·Probability·AUTO·주문·Paper 변경은 이 단계에서 0이다.

## 1. 범위

| 항목 | 값 |
|---|---|
| 대상 | **USDT 선형 무기한 BTC 3종**: Bybit `BTCUSDT`(linear), Binance USDⓈ-M `BTCUSDT`, OKX `BTC-USDT-SWAP`(ctVal 0.01 BTC, linear, USDT 정산) |
| 제외 | 인버스·만기물(Bybit inverse, Binance COIN-M, OKX `BTC-USD-SWAP`), 다른 심볼 |
| 원천 | 공개 엔드포인트만. API key·계정·private 엔드포인트·주문 엔드포인트 0 |
| 코드 | `backend/app/crypto/liquidation_forward/` (paper·terminal과 import·상태·파일 공유 0) |
| 서버 | 별도 systemd 유닛 `usb-crypto-liqfwd.service` + 일일 정리 `usb-crypto-liqfwd-daily.timer`, 별도 디렉터리 `/root/usb_runtime/crypto_liqfwd/` |

## 2. 원천별 계약 (공식 문서 + D5.2 실측)

| 거래소 | 원천 | 역할 | 공식 문서가 말하는 것 | 실측 (D5.2, 2026-09-27) |
|---|---|---|---|---|
| Bybit | WS `wss://stream.bybit.com/v5/public/linear` topic `allLiquidation.BTCUSDT` | 정본 | Push frequency 500ms, snapshot. `S` = "Position side ... When you receive a Buy update, this means that a long position has been liquidated". `p` = "Bankruptcy price". `v` = executed size. `T` = timestamp ms | 같은 초 최대 5건. REST 없음 → **전량 여부 미검증** |
| Binance | WS `wss://fstream.binance.com/market/stream?streams=btcusdt@forceOrder/btcusdt@markPrice@1s` | 정본(집계) | 문서 페이지가 JS 렌더라 이번 세션에서 원문 인용 실패. 기존 문서 서술: 심볼당 1000ms 안의 최신 청산 주문 1건만 스냅샷으로 push | 심볼·초당 최대 1건 확인 → **`is_aggregated = true` 고정** |
| OKX | REST `GET /api/v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled&limit=100` (+`after` 커서) | **정본** | **현재 공식 문서에 이 REST가 없다**(문서에서 빠졌지만 동작함, 9/27 확인). 롤링 약 21 ~ 24시간 | 5페이지 469건 / 20.9시간, 중복 키 0, 같은 ms 최대 3건 |
| OKX | WS `wss://ws.okx.com:8443/ws/v5/public` channel `liquidation-orders` instType SWAP | 보조(지연·완전성 감사) | 문서: "**This data doesn't represent the total number of liquidations on OKX.**" `posSide` long/short(헤지) 또는 net, `side` = 주문 방향, `bkPx` = "Liquidation mark price", `sz` = 계약 수 | REST 대비 33%만 수신 |

**OKX REST 위험**: 문서에서 빠진 엔드포인트라 예고 없이 사라질 수 있다. 사라지면 수집기가 HTTP 오류를 기록하고 OKX 정본 구간을 DOWN으로 남긴다. WS를 정본으로 승격하지 않는다(문서가 완전하지 않다고 명시).

## 3. 정규화 스키마 `liq.v1` (행 = 청산 이벤트 1건)

| 필드 | 형 | 의미 / 규칙 |
|---|---|---|
| event_ts_ms | int | 거래소 이벤트 시각 (Bybit `T`, Binance `o.T`, OKX `ts`) |
| receive_ts_ms | int | 수집기 수신 시각(서버 시계). OKX REST는 **그 행을 처음 받은 폴링 응답 시각** |
| exchange | str | bybit / binance / okx |
| symbol | str | 공통 `BTCUSDT` |
| raw_symbol | str | 원문 심볼 (`BTCUSDT`, `BTC-USDT-SWAP`) |
| instrument_type | str | `linear_perpetual` |
| liquidated_side | str/null | **강제청산된 포지션 방향** LONG / SHORT. 규칙은 4절. 확정 못 하면 null |
| side_rule | str | 어떤 규칙으로 정했는지 (`bybit_S_position_side`, `binance_order_side`, `okx_posSide`, `okx_net_order_side`, `unknown`) |
| raw_side | str | 원문 side 값(OKX는 `posSide/side`) |
| price | float | Bybit `p`(파산가), Binance `o.ap`(평균 체결가, 0이면 `o.p`), OKX `bkPx` |
| price_kind | str | `bankruptcy` / `avg_fill` / `order_price` / `okx_bkPx` |
| qty | float/null | BTC. Bybit `v`, Binance `o.z`(누적 체결), OKX `sz x ctVal`(ctVal은 기동 시 instruments API로 조회, 실패 시 null) |
| notional_usd | float/null | price x qty (**USDT 명목. USD 환산 없음**, USDT ≈ USD로 해석 시 주의) |
| source | str | `bybit_ws` / `binance_ws` / `okx_rest` / `okx_ws` |
| source_event_id | str/null | 3사 모두 청산 이벤트 id를 주지 않는다 → null |
| dedupe_key | str | 5절 |
| is_aggregated | bool | Binance true, Bybit false(500ms 묶음 전송이지만 행마다 개별 이벤트), OKX false |
| is_partial | bool/null | Binance `o.X == PARTIALLY_FILLED`, 나머지 null |
| bankruptcy_price | float/null | Bybit `p`, 나머지 null (OKX `bkPx`는 문서상 "liquidation mark price"라 파산가로 부르지 않는다) |
| mark_price, index_price | null | V1에서는 채우지 않는다 |
| collector_version, schema_version | str | `liqfwd.1.0` (2026-09-27 10:34 ~ 11:17 UTC) → `liqfwd.1.1` (11:17 UTC ~), `liq.v1`. 1.1은 7절 REST 원문 규칙만 다르고 정규화 행 형식은 같다 |

값이 없으면 null. **0으로 채우지 않는다.**

## 4. liquidated_side 규칙 (추측 금지)

| 거래소 | 규칙 | 근거 |
|---|---|---|
| Bybit | `S=Buy` → LONG, `S=Sell` → SHORT | 공식 문서 문장 인용(2절). D5.2 실측 SHORT 16/16이 직전 가격 상승 뒤 |
| Binance | 청산 **주문** 방향 `o.S=SELL` → LONG, `BUY` → SHORT | 청산 주문은 청산되는 포지션을 닫는 반대 방향 주문. D5.2 실측 SHORT 19/19 직전 상승 |
| OKX | `posSide=long` → LONG, `short` → SHORT | 공식 문서 "Position mode side" |
| OKX net | `posSide=net`이면 주문 방향 `side=sell` → LONG, `buy` → SHORT, `side_rule=okx_net_order_side` | 청산 주문은 포지션을 닫는 방향. 9/27 표본 469건 중 net 0건 |
| 그 외 | null, `side_rule=unknown` | 원문 보존 |

LONG 방향 실측 검증은 아직 부족하다(D5.2 캡처가 상승장). **첫 하락 구간에서 3사 모두 재검증**하고 결과를 collector report에 추가한다.

## 5. 중복 제거

거래소가 이벤트 id를 주지 않으므로 거래소별 복합키:

| 원천 | dedupe_key |
|---|---|
| bybit_ws | `T|S|p|v` |
| binance_ws | `o.T|o.S|o.ap|o.z|o.X` |
| okx_rest, okx_ws | `ts|posSide|side|bkPx|sz` |

규칙: **이전 메시지에서 본 키만 중복으로 버린다.** 한 메시지 안에 같은 키가 여러 번 있으면 모두 보존하고 `intra_message_same_key`로 센다(같은 ms에 같은 가격·수량의 별개 청산일 수 있다).
키 기억은 48시간. 재시작 시 최근 48시간 정규화 파일에서 키를 다시 읽는다(재연결 재전송·REST 겹침 폴링 중복 방지).
OKX REST와 WS는 **source가 다르므로 서로 중복 제거하지 않는다.** 연구 정본은 `okx_rest`이고 `okx_ws`는 감사용이다(일일 manifest에 WS 행의 REST 일치율, WS에만 있는 행 수를 기록).
원문(raw)은 WS 청산 메시지를 전부 보존한다. **OKX REST 응답은 새 행이 하나라도 있는 페이지만 원문으로 보존한다**(v1.1). 60초마다 같은 롤링 창을 다시 받으므로 전부 보존하면 하루 약 2MB가 중복 원문으로 쌓인다(v1.0 실측). 폴링 횟수·중복 수는 카운터에 남는다.

## 6. 저장

```
/root/usb_runtime/crypto_liqfwd/data/
  START_MARKER.json                       forward 정본 시작점 (8절)
  raw/exchange=<x>/date=<YYYY-MM-DD>/<x>-<YYYY-MM-DD>T<HH>.jsonl.gz   원문 (수신 시각 기준 시간 세그먼트)
  norm/exchange=<x>/date=<YYYY-MM-DD>/<x>-<YYYY-MM-DD>T<HH>.jsonl.gz  정규화 (같은 세그먼트)
  normalized/exchange=<x>/date=<YYYY-MM-DD>/part.parquet               일일 정리(이벤트 시각 UTC 날짜 기준)
  manifest/segments.jsonl                 세그먼트마다 rows, bytes, sha256, 첫/끝 시각
  manifest/daily/date=<YYYY-MM-DD>.json   일일 manifest (7절)
  runtime/sessions.jsonl, runtime/heartbeat-<YYYY-MM-DD>.jsonl
```

쓰기: `.partial`에 줄 단위로 쓰고, 시간이 바뀌면 fsync → gzip(`.gz.partial`) → fsync → **원자적 rename** → sha256을 manifest에 기록.
비정상 종료로 남은 `.partial`은 다음 기동 때 **마지막 불완전 줄을 잘라내고** 같은 절차로 봉인, `recovered=true`, 잘라낸 바이트 수 기록. 기존 파일 덮어쓰기 0(같은 이름이 있으면 `-r<n>` 접미사).

**디스크 가드**: 서버 여유 공간이 **300MB 미만**이면 쓰기를 멈추고(`disk_guard=true`) 그 구간을 DOWN으로 기록한다. 운영 Paper 서비스가 디스크 부족으로 멈추는 것을 막기 위한 규칙이다.

## 7. 품질·공백

- heartbeat 60초마다: 거래소별 연결 상태, 마지막 수신 나이, 누적 이벤트·중복·오류·재연결·HTTP 오류·rate limit, 디스크 여유, RSS.
- **재시작**: 연속한 heartbeat가 같은 수집기 세션일 때만 UP으로 잇는다. 재시작은 옛 세션의 마지막 heartbeat부터 새 세션의 첫 heartbeat까지 DOWN(보수적, 실제 중단보다 길게 잡힘).
- **수집 구간 판정**: 거래소별로 "그 60초 동안 연결돼 있었고 건전성 신호(Bybit pong, Binance markPrice, OKX pong / REST 성공)를 90초 안에 받았다"면 UP. 아니면 DOWN. heartbeat 자체가 150초 넘게 없으면 수집기 DOWN.
- OKX REST는 롤링 약 21시간이라, 마지막 성공 폴링 뒤 20시간 안에 다시 성공하면 그 사이는 **REST로 복구된 구간**으로 표시한다.
- **DOWN 구간을 "청산 없음"으로 해석하지 않는다.** 연구 입력은 UP 구간으로 제한한다.
- 일일 manifest: exchange, date, files, rows, first/last event, bytes, sha256, duplicates, intra_message_same_key, invalid, null_side, late_events(수신 - 이벤트 > 300초 또는 이벤트 날짜 ≠ 수신 날짜), 지연 p50/p95/max(음수 포함 원값, 시계 차이를 지연이라 단정하지 않음), LONG/SHORT 건수·명목, 세션 시작·종료, 재연결, HTTP 오류, WS 오류, rate limit, UP/DOWN 구간.

## 8. Forward 시작점

`LIQUIDATION_FORWARD_V1_START` = 서버 수집기가 3사 모두 UP으로 처음 판정된 heartbeat 시각. `START_MARKER.json`과 collector report에 UTC·KST·collector/schema version·거래소 목록·소스 sha256으로 동결한다.
그 이전 청산 데이터(D5.2 캡처, Tardis 표본 등)는 forward 정본에 섞지 않는다.

## 9. 관찰 게이트 (edge 판정 시작 조건)

**의미 있는 하락·LONG 청산 이벤트 30회.** 이벤트 정의·임계는 **향후 연구 계약에서 데이터를 보기 전에** 동결한다. 이 단계에서 threshold를 정하지 않는다.

> **개정 2026-10-02 (사용자 결정)**: 원래 조건 "(1) 시작점부터 연속 6개월"을 삭제했다. 필요한 것은 기간이 아니라 독립 이벤트 수이고, D5.2 C1 빈도(5.6년 N_eff 77, 연 약 14개)로 보면 6개월도 수익성 판정에는 모자라 기간 조건이 판정력을 더하지 않는다. 개정 시점까지 청산 데이터를 C1·수익률과 붙여 본 적 없음(결과를 보고 바꾼 것이 아님).

## 10. C1 forward 가설 (등록만)

D5.2 C1(Bybit perp가 현물 대비 30일 기준 하위 10% 할인 + Bybit OI 1시간 감소 + 고변동성, 4h LONG; historical net +19.3bp [+4.3, +35.5], ratio 2.77, N_eff 77, WEAK)에 대해 forward에서 확인할 질문 하나만 등록한다:
**"C1 순간에 3사 LONG 청산(강제 매도)이 실제로 늘어나는가."** 9절 게이트 전 결과 판정 금지. 거래 금지.

## 11. 상태 고정

D6 = NOT AUTHORIZED. AUTO 미구현. 향후 AUTO는 MANUAL과 계좌·원장·포지션·리스크·분석을 모두 분리하고, AUTO UI는 결과·상태 대시보드 중심(차트·수동 주문 없음)으로 한다. 이번 단계에서 AUTO 코드·UI 0.
