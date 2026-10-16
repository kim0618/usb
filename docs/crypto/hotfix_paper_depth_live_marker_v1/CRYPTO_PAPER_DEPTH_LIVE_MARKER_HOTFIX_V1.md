# CRYPTO PAPER DEPTH + LIVE MARKER HOTFIX V1

결과: **ACTIVE** (2026-10-16 KST, 적용·재시작 13:14:35, 관찰 13:18~13:27)

출처: `42a1113` + `198172e` backend만. frontend 변경·재시작 0.

## 적용
- runtime `/root/usb_runtime/crypto_paper/src/backend`. 7파일(paper/book, paper/engine, paper/trade_preview, terminal/feed, terminal/session, terminal/executions, terminal/live_routes)은 운영본이 `42a1113^`와 sha 일치 → `198172e` 판으로 교체.
- `hot_coins/market.py`는 운영본이 저장소와 다름(캐시 만료 정리 4줄 미배포) → 통째 교체하지 않고 42a1113 diff만 운영본에 patch(`market_42a.patch`).
- 교체 전 임시 사본 import PASS, C1 모듈 0, SIZING_BOOK_DEPTH 20 / TAPE_BOOK_DEPTH 5.
- 백업 `/root/usb_runtime/crypto_hotfix_pre_20261016-131326/`, `usb-crypto-paper`만 1회 재시작 PID 2252381→2263918, health 82초, NRestarts 0, traceback 0.
- 실수: 로컬 py_compile이 만든 `__pycache__` 8개가 스테이지에 섞여 함께 배포됨(백업엔 기존 pyc). 운영 Python이 기동 시 소스 mtime 불일치로 8개 전부 재컴파일(13:14:47~48), 보낸 pyc 잔존 0 → 실행 코드 영향 없음.

## 계약 확인
- 42a1113: 1Hz 테이프 5단계 유지(재시작 후 MARKET 기록 462건 전부 5×5). 주문 명령 직전 force 기록만 20단계(api.py order, session.command, 폴링 경로 없음). 새 구독·폴링 0, LIVE·Q123·Wall/Flow 변경 0. Safe MAX 정의 `ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT` 유지.
- 198172e: 가격 출처 avgPrice → fills → cumQuote/qty → 체결 캐시(orderId·수량 정확 일치). 시세·mark 대체 없음, 서명 호출 없음.
- HALF CLOSE / LIVE sizing / 주문 경로(live/*, paper/sizing, api, server) 변경 0.

## Sizing (읽기 전용 GET)
- HOT SIRENUSDT MAX 70,030 → **115,461** (5단계 청산 깊이에 묶였던 것이 20단계로 풀림). 25/HALF/75/MAX = 28,865 / 57,730 / 86,595 / 115,461 = 25/50/75/100%.
- BTC SHORT 추가 MAX 1.164 → 1.164. 1.164는 증거금 상한(가용 9,603 USDT × 10x)이라 깊이와 무관. 재시작 직후 몇 초간은 시장 호가가 얇아(20단계 합계 0.26~0.72 BTC) 0.19~0.72로 깊이에 묶였다가 1.164로 복귀. BTC LONG은 기존대로 REVERSE_NOT_ALLOWED(SHORT 보유).

## 안전 비교 (13:13 vs 13:16)
PAPER 원장 15개 sha 동일, BTC SHORT 0.064 @83612.90 10x 동일. LIVE flat·wallet 0.00587989 동일, ExitGuard·레버리지 파일 sha 동일, 새 mirror 이벤트는 `LIVE_DISARM BOOT`(17383)와 stream/reconcile뿐. 주문 0. C1 404, liqfwd disabled. 다른 서비스 PID 불변.

## LIVE 마커
`/api/crypto/binance/executions?symbol=BTCUSDT` = 41건(INITIAL 17, ADD 8, FULL_CLOSE 16), 출처 USER_TRADES_CACHE 41, 가격 누락 0, 중복 0, 마지막 0.108 @83913.1. PAPER 마커 51건 변화 없음.

## 관찰 (10분)
paper RSS 86→70MB, swap 7→0MB, memory.events 0. 호스트 avail 303→361MB, swap used 418→412MB. mem PSI avg300 0→0.1, IO avg300 12→8(적용 직후 import 검사·재시작 순간치 avg10 77 포함). crypto 지연 32~199ms(1회 1083ms), backend 14~31ms. Q123 RUNNING, samples 증가, kline_err 없음. Flow COMPLETE(나이 50~54초 = 측정 시점과 분 단위 갱신의 위상차). flow/structure/feedlog/recorder 실패 0, OOM 0. 테이프 디렉터리 증가는 평소 1Hz 속도.

push 안 함.
