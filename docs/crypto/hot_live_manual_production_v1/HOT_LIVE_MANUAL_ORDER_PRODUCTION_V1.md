# HOT LIVE MANUAL ORDER UNLOCK PRODUCTION V1

결과: **ACTIVE** (2026-10-17 KST, backend 20:41:45, frontend 20:43:37, 관찰 21:00~21:10)

출처: `043cee1`만 반영. 로컬 HEAD 전체는 배포하지 않음. push 안 함.

## 배포 전 상태 (20:30, 20:41)
- 동시 배포 없음, 서비스 전부 NRestarts 0. mem PSI avg300 0, IO avg300 10, avail 275MB, swap 536MB.
- LIVE: 사용자가 17:39에 직접 ARMED(SESSION). 포지션 BTC/ETH/SOL/AGT 전부 flat, wallet 147.78458089 USDT, 레버리지 20x.
- HOT = AGTUSDT. 운영 gate `symbol_orders_enabled=false`, `symbol_orders_flag_name=BINANCE_LIVE_HOT_ORDERS` 확인(차단이 실제로 걸려 있었음). 서버 env에 `BINANCE_LIVE_HOT_ORDERS` 설정 없음.
- PAPER: BTC SHORT 0.064 @83612.90 10x, 원장 16개.

## Backend
- 운영 runtime `/root/usb_runtime/crypto_paper/src/backend/app/crypto/terminal/live_routes.py` = `198172e` = `043cee1^` (sha 62ba10c3) 확인 후 `043cee1` 판(sha 35253d67) 1파일만 교체. 제거되는 이름(`_hot_live_refusal`, `_symbol_gates`, `hot_live_orders_enabled`, `HOT_LIVE_ORDERS_ENV`)을 참조하는 다른 운영 모듈 0.
- 교체 전 임시 사본에서 py_compile + import PASS, C1 모듈 0. 스테이지에 `__pycache__` 없음(지난 hotfix 실수 재발 방지).
- 백업 `/root/usb_runtime/hot_live_manual_pre_20261017-204005/` (MANIFEST 검증 PASS).
- `usb-crypto-paper`만 1회 재시작 PID 2263918→2454106, health 28초, NRestarts 0, traceback 0. 재시작 후 `LIVE_DISARM BOOT`(seq 17430), 자동 ARM 없음.
- 롤백: 백업 파일 되돌리고 paper 재시작.

## Frontend
- 서버 `/root/usb/frontend` 소스는 10/4판이라 기준 불가. 07OdWY 재구성 트리(3e2a8ad + 5093f6c/1c6c900/04428f1/2b1285d/16e7597/447b690/4906e95 + 52f8b93/4e31be4 frontend diff + 기록된 `crypto-terminal.merge.diff`)를 로컬 worktree에 다시 만들고, 기준선 빌드를 서빙본과 대조: 문자열 880종 중 차이는 minifier 모듈 번호뿐, 라우트 목록 동일 → 재구성 = 서빙본.
- 043cee1 대상 4파일이 재구성 트리에서 `043cee1^`와 sha 일치 → diff 적용(rej 0). tsc 0.
- vitest crypto/btc 24파일: 적용 후 실패 3 ⊂ 적용 전 실패 4 (새 실패 0). `crypto-live-flow`는 main HEAD에서도 실행마다 통과/실패가 갈리는 타이밍 flaky, `crypto-terminal-integration`은 지난 배포 기록의 기존 실패.
- 빌드 문자열 delta = HOT 차단 문구 6종 제거 + minifier 변수명뿐(`build_string_delta.diff`). 라우트 동일, baked 주소 3종 동일.
- BUILD_ID 07OdWY-ZZkVp2DU514MYg → **PrBzRXD3I4vTsgKCutS24**, 롤백 `.next-build.pre-hot-live-manual-20261017-204005`. `usb-frontend` 1회 재시작, 다른 서비스 PID 불변. /crypto-paper /trading /adoption /trading-h 200.

## UI 스모크 (SSH 터널 + 로컬 GET 전용 프록시, 비GET 0, 외부 요청 0)
- 20:47:53 사용자가 직접 코인탐색(POST hot/scan, nginx tjd618)을 눌러 HOT이 AGTUSDT → **SIRENUSDT**로 바뀜. 배포와 무관(probe는 POST 차단). 스모크는 SIRENUSDT로 진행.
- A 실제 DISARM: HOT·BTC 모두 "거래불가 / 거래 비활성"(NOT_ARMED)만. "조회만 가능", "HOT 심볼 LIVE", HOT_LIVE_ORDERS_DISABLED, BINANCE_LIVE_HOT_ORDERS 0. LONG/SHORT 비활성(arm), 25%/HALF/75%/MAX·레버리지 1~20x 활성.
- B1 armed 시뮬레이션(브라우저 안에서 GET 응답만 armed로 변경, 서버 무변경): HOT LONG/SHORT·프리셋 4종·레버리지 1~20x 활성.
- B2 armed + 가짜 HOT LONG 1000 포지션: HALF CLOSE 500 / FULL CLOSE / CLOSE / 추가 LONG 활성, 레버리지는 "포지션 보유 중 변경 불가"(기존 계약)로만 비활성.
- C BTC armed 시뮬레이션: 기존과 동일(50x/100x 계정 제한 비활성 그대로). ETH/SOL은 Dynamic HOT V1 화면에 탭이 없어 UI 확인 대상 아님(백엔드 fixture로 확인).

## 계약 (fixture, 배포본과 sha 동일한 파일)
`test_hot_live_manual_order.py` 22건 PASS: armed HOT LONG/SHORT 전송, disarmed = LIVE_TRADING_DISABLED·전송 0, preflight_open 호출, Safe MAX·MARKET_LOT_SIZE·MIN_NOTIONAL·stepSize 거부 시 전송 0, 보유 HOT 추가 진입, 반대 방향 reduceOnly 감소·초과 flip 거부, HALF/FULL CLOSE reduceOnly=true, HOT 레버리지 쓰기 HOT만, gate에 HOT 키 없음, AUTO exit guard HOT 거부, HOT 읽기 경로 주문 0, HOT 모듈 주문 경로 0, BTC/ETH/SOL 동일. multisymbol/hot_symbol 스위트 새 실패 0.

## AUTO
운영: HOT exit-guard = AUTO_SINGLE_SYMBOL(guard_symbol BTCUSDT, OFF), HOT 모듈 주문 호출 0, C1 py 0·라우트 404, liqfwd disabled/inactive.

## 안전 비교 (20:41 vs 21:10)
PAPER 원장 16개 sha 동일, 포지션·잔고 동일(equity만 시세로 변동). LIVE 전 심볼 flat 유지, wallet 147.78458089 동일, 레버리지 20x 동일, ExitGuard·leverage_capability sha 동일. mirror 신규 이벤트 = RECONCILE 22, STREAM 2, `LIVE_DISARM BOOT` 1. 주문 0, 레버리지 변경 0.

## 관찰 (10분)
avail 293→287MB(배포 전 275~300), swap used 544→383MB, mem PSI avg300 1.8→0.8(21:02 avg10 20 순간 1회), IO avg300 46→19 — 20:5x에 운영자(본 세션)의 `find /` 실수로 IO avg10 99까지 올랐다가 회복. crypto 지연 31~55ms, backend 13~30ms(첫 샘플 222ms). paper RSS 94→92MB, memory.events 0. Q123 RUNNING lag 11.5~19.3s, samples 증가, kline_err 없음, 시간별 품질 3600/3600(누락 0). Flow COMPLETE 나이 28~31초. flow/structure/feedlog/recorder 실패 0, OOM 0, traceback 0.

## 실수 기록
- 업로드 명령의 stdin 덮어쓰기로 첫 스테이지가 비어 check가 STOP(운영 무변경), 재업로드.
- check 스크립트의 py_compile 출력을 /dev/null로 지정해 check FAIL(운영 무변경), 수정.
- HOT 슬롯 파일 위치를 찾으려 서버에서 `find /`를 실행해 IO PSI 급등, 즉시 중단. 이후 회복.
