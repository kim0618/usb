# US-B CRYPTO Forward Data Plan V2

작성 2026-09-27. V1(`CRYPTO_FORWARD_DATA_PLAN_V1.md`, 계획)을 대체한다. V2는 **실제로 가동한 수집기** 기준이다.
계약 `CRYPTO_D5_3_LIQUIDATION_FORWARD_CONTRACT_V1.md`, 보고 `CRYPTO_D5_3_LIQUIDATION_COLLECTOR_REPORT_V1.md`, 사전 `CRYPTO_LIQUIDATION_DATA_DICTIONARY_V1.md`.

## 1. V1 대비 바뀐 것

| 항목 | V1 (계획) | V2 (가동) |
|---|---|---|
| 상태 | 사용자 승인 대기 | **서버 가동 중** (`usb-crypto-liqfwd.service`, 2026-09-27) |
| 시작점 | 미정 | **LIQUIDATION_FORWARD_V1_START = 2026-09-27T10:35:04.118Z (19:35:04 KST)** |
| OKX 원천 | REST 폴링 주 | REST 60초 폴링(10분 겹침, 최대 30페이지). **REST 엔드포인트가 공식 문서에서 빠진 것을 확인**(동작 중) |
| OKX WS | 보조 | 감사용. 문서가 "전체 청산을 나타내지 않는다"고 명시. 구독 직후 과거 스냅샷을 보냄 |
| 건전성 | 1분 다운샘플 기록 | 저장하지 않음. heartbeat(60초)에 거래소별 UP 판정만 기록 |
| 파일 | 일 파일 | 시간 세그먼트(.partial → gzip → 원자적 rename) + 일일 parquet(01:15 UTC 타이머) |
| 디스크 | 여유 충분 가정 | **서버 루트 91% 사용, 여유 약 890MB** → 300MB 미만이면 쓰기 중단 가드 |

## 2. 수집 대상 (변경 없음)

Bybit `BTCUSDT` WS, Binance `BTCUSDT` WS(집계), OKX `BTC-USDT-SWAP` REST(정본) + WS(감사). USDT 선형 무기한만.
OKX funding·OI 5m forward는 이번 V2 범위에서 빠졌다(청산 수집기에 넣지 않았다). 필요하면 별도 소형 폴러로 추가한다.

## 3. 관찰 게이트

edge 판정 시작 = **의미 있는 하락·LONG 청산 이벤트 30회**.
(개정 2026-10-02: "연속 6개월(2027-03-27)" 조건 삭제. 사유는 D5.3 계약 9절 개정 기록.)

이벤트 정의·임계는 **데이터를 보기 전에 새 연구 계약에서** 동결한다. 수집 중에 청산 데이터를 수익률과 붙여 보지 않는다.
수집 공백 처리: 일일 manifest의 UP 구간 기준. 이벤트 창이 어느 거래소의 DOWN 구간과 겹치면 그 이벤트는 30회 카운트에서 제외하고 원인을 기록한다(개정 2026-10-02, 기존 "월 DOWN 2% 초과 달 제외" 규칙을 이벤트 단위로 바꿈).

## 4. 등록된 forward 가설 (판정 금지)

C1(D5.2): Bybit perp가 현물 대비 30일 기준 하위 10% 할인 + OI 1시간 감소 + 고변동성, 4h LONG.
질문 하나: **C1 순간에 3사 LONG 청산이 실제로 늘어나는가.** 게이트 전 결과 보기 금지. 거래 금지.
과거 검증(Tardis)은 보류 상태이며 구매 결정은 사용자 몫이다.

## 5. 운영 점검 (주기)

| 주기 | 무엇 |
|---|---|
| 매일 01:15 UTC (자동) | 일일 parquet + manifest (`manifest/daily/date=*.json`) |
| 주 1회 (수동) | 일일 manifest의 DOWN 구간, 재연결, HTTP 오류, OKX WS/REST 일치율, 디스크 여유 |
| 첫 하락 구간 | LONG side 의미 3사 재검증(계약 4절) |
| 디스크 여유 400MB 미만 | 사용자에게 알림 필요(정리·증설 결정). 수집기는 300MB에서 스스로 멈춘다 |

## 6. 저장량 (실측으로 갱신)

`CRYPTO_D5_3_LIQUIDATION_COLLECTOR_REPORT_V1.md` 20~23절의 실측값이 정본이다.

## 7. 하지 않는 것

Score·Probability·AUTO·주문·Paper 변경·threshold 튜닝·C1 재평가. D6 = NOT AUTHORIZED.
