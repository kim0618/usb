# MULTI-ENTRY MARKERS + SYMBOL LEVERAGE CAP PRODUCTION V1

결과: **ACTIVE** (2026-10-18 KST, backend 12:22:19, frontend 12:24:07, 관찰 12:30~12:40)

출처: `fb5e21b` + `ebf6898`만. 로컬 HEAD 전체·H/A/E 작업 미포함. push 안 함.

## 배포 전 상태 (12:11, 12:21)
- 동시 배포 없음, 서비스 전부 NRestarts 0. mem PSI avg300 0, avail 329MB, swap 492MB.
- **디스크 99%, 여유 139MB.** 2분 측정 약 640KB/분 감소(이번 배포 전부터). 원인 미조사(범위 밖).
- LIVE: 전 심볼 flat(보고된 SIREN LONG은 이미 청산), wallet 0.01316920 USDT, 사용자 SESSION ARMED. HOT = BRUSDT(설정 20x, 최대 25x). SIRENUSDT는 HOT이 아니라 운영 조회 불가(SYMBOL_NOT_SUPPORTED).
- PAPER: BTC SHORT 0.064 @83612.90 10x, 원장 17개.

## Backend
- 운영 runtime 4파일(terminal/executions.py, live/brackets.py, live/preview.py, terminal/live_routes.py)이 각 커밋 부모판과 sha 일치 확인 후 `ebf6898` 판으로 교체. 다른 파일 0.
- import 검사는 `app`만 2MB 사본(`__pycache__` 제외)으로: compile+import PASS, C1 0, SIZING_BOOK_DEPTH 20 / TAPE 5 그대로.
- 백업 `/root/usb_runtime/crypto_marker_leverage_pre_20261018-121704/` (MANIFEST 4, 검증 PASS).
- `usb-crypto-paper`만 1회 재시작 PID 2454106→2519676, health 34초, NRestarts 0, traceback 0, `LIVE_DISARM BOOT`(19619), 자동 ARM 없음.
- 롤백: 백업 4파일 되돌리고 paper 재시작.

## Frontend
- 서빙본 PrBzRXD3 재구성(어제 레시피 + 043cee1 frontend) → 기준선 빌드 문자열 874종 중 차이는 minifier 모듈 번호뿐, 라우트 동일 → 재구성 = 서빙본.
- 대상 파일이 부모판과 sha 일치(crypto-terminal.tsx만 운영 병합 조정 EntryAnalysisPreview 차이, 예상대로) → fb5e21b·ebf6898 frontend diff 적용(rej 0). tsc 0, eslint 0.
- vitest crypto/btc 26파일: 실패 3 = crypto-live-flow 타이밍 flaky(반복 시 6/6 통과 섞임) + crypto-terminal-integration 기존. 지정 4파일 multi-entry 15 / leverage-cap 11 / manual-ux-final 17 / half-close 10 전부 PASS.
- 빌드 delta = 레버리지 cap 문구 추가 + minifier 변수명. 라우트·baked 주소 3종 동일.
- BUILD_ID PrBzRXD3I4vTsgKCutS24 → **c4VYa7VRAWu0aFBN3fAvV**, 롤백 `.next-build.pre-marker-leverage-20261018-121704`.
- **디스크 때문에 절차 변경:** 지난 배포들은 Next `cache`(~116MB)를 롤백 디렉터리에서 새 빌드로 *복사*해 왔다(최근 롤백 디렉터리 각 ~121MB). 이번엔 *이동*했다. 롤백 디렉터리는 cache만 없고 나머지 sha 동일. 배포 사용량 ~5MB.
- `usb-frontend` 1회 재시작, 다른 서비스 PID 불변.

## UI 스모크 (SSH 터널 + GET 전용 프록시, 비GET 0, 외부 0)
- A 실제(DISARM): HOT BRUSDT 1~20x 활성, 50/100x 비활성, "BRUSDT 최대 25x". BTC 1~20x 활성, 50/100x 계정 제한 비활성(최대 150x라 문구 없음). "조회만 가능" 0.
- B 시뮬(브라우저 안 GET 응답만 설정 50x로): "현재 심볼(BRUSDT)은 최대 25x입니다. 레버리지를 다시 선택하세요." 패널·티켓 양쪽, LONG/SHORT 비활성, 1~20x 선택 가능.
- B2 시뮬(50x + 가짜 LONG 1000): LONG(추가)만 비활성, SHORT(감소)·CLOSE·HALF/FULL CLOSE 활성, 레버리지 버튼은 기존 "보유 중 변경 불가"로 잠김.
- C 시뮬(armed, 유효): HOT LONG/SHORT 활성. 프리셋은 실제 wallet 0.013 USDT라 잔고 부족으로 비활성(정상).
- 첫 시도는 재시작 직후 HOT 계좌 첫 조회가 9초 안에 안 와 "연결 대기"로 찍힘 → 대기 조건을 버튼 출현으로 바꿔 재실행, 이후 조회 0.01~0.3초.

## 멀티 진입 마커 (운영 실제 체결, 읽기 전용, 배포 코드 그대로 라벨링)
- LIVE BTC 41건: 사이클 17, 각 #1부터. 끝: `#3 SHORT 0.021 @ 85538.4`, `SHORT 청산 0.034 @ 84007.6`, `#1 LONG 0.108 @ 83913.1`.
- LIVE BRUSDT 4건: `#1 SHORT 2,938 @ 0.36652048` … `#4 SHORT 2,230 @ 0.36785504`.
- PAPER BTC 51건: 사이클 24.
- 번호 불일치 0, reload 중복 0, 같은 1분 체결(13/1/17건) 모두 개별 마커.
- 평균진입선 코드(positionOverlays / liveOverlays)는 두 커밋이 건드리지 않음 + fixture 확인.

## 계약 (fixture, 배포본과 sha 동일)
test_live_symbol_leverage_cap / test_multi_entry_markers / test_safe_max_brackets / test_hot_live_manual_order / test_manual_ux_half_close / test_live_execution_price 116건 PASS: SIREN 20x(최대 10x) → LEVERAGE_INVALID_FOR_SYMBOL·주문 0·레버리지 쓰기 0, 기존 포지션+추가 → LEVERAGE_TIER_EXCEEDED, HALF/FULL CLOSE·반대 감소 reduceOnly 통과, BTC/ETH/SOL 동일.

## 안전 비교 (12:21 vs 12:40)
LIVE 전 심볼 flat 유지, wallet 동일, 레버리지 20x 동일, 체결 목록 동일, ExitGuard·leverage_capability sha 동일. mirror 신규 = RECONCILE 17, STREAM 2, `LIVE_DISARM BOOT` 1. 주문 0, 레버리지 변경 0. PAPER 원장 17개 sha 동일, 포지션·잔고 동일(equity만 시세). C1 404, liqfwd disabled.

## 관찰 (10분)
avail 283~314MB, swap 491→380MB, mem PSI avg300 1.6→0.6, IO avg300 14→19(재시작 직후 28에서 하강 중). crypto 지연 11~60ms(첫 샘플 690ms), backend 14~32ms(첫 332ms). Q123 RUNNING lag 12~20s, 시간별 3600/3600, kline_err 없음. Flow COMPLETE 14~18초. OOM 0, traceback 0. flow/structure/feedlog/recorder 실패 1(12:25:28 TimeoutError UNREACHABLE, 재시작 직후; 직전 24시간 같은 유형 38건이라 평소 수준).
- **디스크 여유 135→115MB**(배포 ~6MB + 분당 ~600KB 기존 감소). 오래된 frontend 롤백 디렉터리 5개가 각 115~122MB(합계 ~600MB)이나, 삭제는 5중 증명·사용자 승인 사항이라 손대지 않음.
