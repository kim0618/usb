# US-B CRYPTO Liquidation Data Dictionary V1 (`liq.v1`)

작성 2026-09-27. D5.3 forward 수집 데이터의 필드 의미와 원천 대응표. 계약: `CRYPTO_D5_3_LIQUIDATION_FORWARD_CONTRACT_V1.md`.
구현: `backend/app/crypto/liquidation_forward/parsers.py`.

## 1. 원천 필드 → 정규화 필드

| 정규화 | Bybit `allLiquidation.BTCUSDT` | Binance `btcusdt@forceOrder` (`data.o`) | OKX REST / WS `liquidation-orders` (`details[]`) |
|---|---|---|---|
| event_ts_ms | `T` "Updated timestamp in milliseconds" | `T` (주문 거래 시각) | `ts` |
| raw_symbol | `s` | `s` | 상위 `instId` = `BTC-USDT-SWAP` |
| raw_side | `S` (Buy/Sell) | `S` (BUY/SELL) | `posSide/side` |
| liquidated_side | `S`: Buy → LONG, Sell → SHORT ("When you receive a Buy update, this means that a long position has been liquidated") | 주문 방향 SELL → LONG, BUY → SHORT | `posSide` long → LONG, short → SHORT. net이면 `side` sell → LONG, buy → SHORT |
| price | `p` "Bankruptcy price" | `ap` 평균 체결가(0이면 `p` 주문가) | `bkPx` "Liquidation mark price. The price of the transaction with the system's liquidation account" |
| price_kind | `bankruptcy` | `avg_fill` / `order_price` | `okx_bkPx` |
| qty (BTC) | `v` "Executed size" | `z` 누적 체결 수량 | `sz` 계약 수 x ctVal(0.01 BTC, 기동 시 instruments API로 확인) |
| is_aggregated | false (500ms 묶음 push, 행은 개별 이벤트) | **true** (심볼당 1초 1건 스냅샷) | false |
| is_partial | null | `X == PARTIALLY_FILLED` | null |
| bankruptcy_price | `p` | null | null (문서가 파산가가 아니라 "liquidation mark price"라 부름) |
| source_event_id | 없음 → null | 없음 → null | 없음 → null |

기타 필드: `receive_ts_ms`(서버 수신 시각), `exchange`, `symbol`(`BTCUSDT` 통일), `instrument_type`(`linear_perpetual`), `notional_usd`(price x qty, **USDT 명목**),
`source`(`bybit_ws`/`binance_ws`/`okx_rest`/`okx_ws`), `dedupe_key`, `side_rule`, `mark_price`·`index_price`(V1은 null), `collector_version`, `schema_version`.

## 2. 사용 시 주의

| 항목 | 주의 |
|---|---|
| Binance | 집계 스트림이다. **건수를 청산 강도로 쓰지 않는다.** 방향과 명목만 |
| Bybit | 완전성 미검증(REST 없음). 500ms 주기 push |
| OKX | 연구 정본은 `source == okx_rest`. `okx_ws`는 감사용(문서: 전체 청산을 나타내지 않음). 두 source는 서로 중복 제거하지 않았으므로 합치지 말 것 |
| OKX REST | **현재 공식 문서에 없는 엔드포인트**(2026-09-27 확인, 동작함). 사라질 수 있음 |
| 지연 | `receive_ts_ms - event_ts_ms`는 네트워크 지연이 아니다. 서버·거래소 시계 차이가 섞이고, OKX REST는 폴링 주기(60초)와 백필이 섞인다. 일일 manifest의 `delay_ms_live`만 실시간 수집분이다 |
| 백필 | OKX REST 첫 폴링과 OKX WS 구독 직후 스냅샷은 수집기 기동 전 이벤트를 준다(`pre_session_rows`). **forward 정본은 `START_MARKER.json` 시각 이후 이벤트만** |
| 공백 | 일일 manifest의 `down_intervals` 구간은 "청산 없음"이 아니라 "관측 안 됨"이다 |
| 가격 종류 | 3사 가격이 서로 다른 것(파산가 / 평균 체결가 / 청산 mark가)이라 거래소 간 가격 비교에 쓰지 않는다 |
