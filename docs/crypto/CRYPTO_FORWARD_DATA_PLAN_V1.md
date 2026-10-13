# US-B CRYPTO Forward Data Plan V1

작성 2026-09-27. D5.2 Phase 1에서 FORWARD_ONLY로 분류된 데이터를 지금부터 쌓는 계획이다. **계획 문서이며, 수집 개시는 서버 작업이라 사용자 승인 뒤에 한다.**
이번 세션에서 서버·운영 WebSocket·Paper 계좌 접근은 0이다. 근거: `CRYPTO_D5_2_DERIVATIVES_DATA_PREFLIGHT_V1.md` §6, `CRYPTO_D5_2_DERIVATIVES_FLOW_RESULTS_V1.md`.

## 1. 수집 대상

| 데이터 | 경로 | 방식 | 이유 |
|---|---|---|---|
| Bybit 청산 | WS `allLiquidation.BTCUSDT` (`stream.bybit.com/v5/public/linear`) | WS 상시 | REST 없음. 같은 초 다건 관측(RAW 추정) |
| Binance 청산 | WS `btcusdt@forceOrder` (**`fstream.binance.com/market/stream`**) | WS 상시 | 초당 1건 집계(스로틀). 건수 아닌 명목·방향으로만 사용 |
| OKX 청산 | **REST `/api/v5/public/liquidation-orders` 24h 롤링을 시간당 1회 이상 폴링** + WS 보조 | 폴링 주, WS 보조 | WS는 REST 전량의 33%만 전달(45분 실측) |
| OKX funding | REST funding-rate-history | 8h마다 | 무료 이력 3개월뿐 |
| OKX OI 5m | REST rubik open-interest 5m | 5분 또는 1시간 배치 | 무료 이력 5일뿐 |
| 건전성 | 거래소별 mark 1개 스트림을 **1분에 1줄만 기록** | WS | 무수신 경보용. 원빈도 저장 불필요 |

제외: cross-exchange 1초 틱, 원시 체결, 호가. 1m 과거 데이터가 무료라 forward 가치가 낮다.

## 2. 수집 시작일

사용자 승인일. 코드는 준비돼 있다: `backend/app/crypto/derivatives/liq_capture.py`(WS 3사, 줄 단위 flush, 재연결 기록).
상시 운용 전 필요한 추가 작업(코드 변경 필요, 이번 범위 밖):
1. OKX REST 폴러(24h 롤링 페이징, 중복 제거 키 = ts + bkPx + sz)
2. 건전성 스트림 1분 다운샘플 기록
3. 일 단위 파일 회전 + sha256 manifest (D2 방식)
4. N초 무수신 경보(Binance `/ws/` 경로 함정 재발 방지)

**격리**: 별도 프로세스·별도 디렉터리(`data/runtime/crypto/forward_liq/`)·별도 systemd 유닛. Paper terminal 프로세스와 코드 경로를 공유하지 않는다. 서버 재시작 없이 추가만 한다.

## 3. 저장 스키마

원본: 거래소 메시지 원문 JSONL(`recv_ms` + `raw`), 일 파일, manifest(sha256, 줄 수, 첫/끝 시각).
정규화(파생물, 언제든 재생성): `deriv_liquidation`

| 필드 | 설명 |
|---|---|
| venue, symbol | bybit/binance/okx, 거래소 심볼 |
| event_ts_ms | 거래소 이벤트 시각 |
| liquidated_side | **청산된 포지션** LONG/SHORT (거래소별 side 의미 통일, `normalize.py`) |
| price, qty_base, notional_quote | OKX qty = 계약 수 x 0.01 BTC |
| raw_side | 원문 side 값 |
| recv_ms | 수신 시각 |
| is_aggregated | Binance True |

모르는 값은 None. 0으로 채우지 않는다.

## 4. 하루 용량

| 구성 | 하루 |
|---|---|
| 청산 메시지만 | Bybit 0.09MB / OKX 2.0MB(SWAP 전체, REST 폴링분 포함 시 +0.3MB) / Binance BTC만 약 0.1MB (전 심볼 스트림이면 8.1MB) |
| 건전성 1분 다운샘플 | 3사 합계 약 0.5MB |
| **합계** | **약 3MB/일 → 월 약 0.1GB, 연 약 1GB** (Binance 전 심볼을 받으면 연 약 4GB) |

## 5. 최소 관찰 기간

| 조건 | 값 | 근거 |
|---|---|---|
| 기간 | **최소 6개월** | D5.2 C1 이벤트는 OOS 4.7년에 179일(연 약 38일). 6개월이면 약 19일 |
| 하락 이벤트 | **LONG 청산 급증 이벤트 30회 이상** | 방향 비대칭이 강해 LONG·SHORT 따로 센다 |
| 방향 검증 | 첫 하락 구간에서 Bybit `S`의 LONG 의미 재검증(mark 하락 뒤 LONG 청산) | 9/27 캡처는 상승장이라 SHORT만 검증 |

## 6. Forward validation 시작 조건

1. 위 5절 충족
2. 수집 무결성: 3사 모두 일 단위 결측 0, 무수신 경보 0 또는 원인 기록, OKX REST 폴링 누락 0
3. **사전등록 계약을 먼저 동결**(수집 데이터를 보기 전). 첫 가설은 D5.2에서 나온 것 하나로 제한:
   - H1: "Bybit perp가 현물 대비 하위 10% 할인 + OI 1시간 감소 + 고변동성(C1)"인 순간에 3사 LONG 청산 명목이 급증했는가
   - H2: C1 순간 가운데 LONG 청산 급증이 동반된 경우만 4h LONG이 VIP_0 비용을 넘는가
   - 판정 규칙은 D5.1/D5.2 틀을 쓰되, **조건에 포함된 regime 축을 robustness에서 제외**하도록 미리 적는다(D5.2 C1 설계 결함의 교훈)
4. 과거 검증을 원하면 Tardis 구매(사용자 결정). 구매하면 H1·H2를 과거 2021 ~ 2026에 먼저 돌리고 forward로 확인한다.

## 7. 하지 않는 것

forward 수집 데이터를 수집 중간에 수익률과 붙여 보지 않는다. Paper Engine·AUTO·Score에 연결하지 않는다. 운영 feed를 바꾸지 않는다.
