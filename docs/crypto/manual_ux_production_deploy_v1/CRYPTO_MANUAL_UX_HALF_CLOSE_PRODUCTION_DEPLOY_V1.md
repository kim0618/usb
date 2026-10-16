# CRYPTO MANUAL UX + HALF CLOSE PRODUCTION DEPLOY V1

결과: **ACTIVE** (2026-10-16 KST, backend 11:40:31, frontend 11:48:57, 관찰 11:52~12:01)

출처 커밋: `52f8b93` + `4e31be4`만. 로컬 HEAD 전체는 배포하지 않음.

## Backend
- 운영 runtime = `/root/usb_runtime/crypto_paper/src/backend` (/root/usb 아님).
- 대상 8파일이 운영에서 `52f8b93^`과 sha 일치를 확인한 뒤 `4e31be4` 판으로 교체, `terminal/executions.py` 신규. 다른 파일 0.
- 교체 전 임시 사본에서 py_compile + import PASS, C1 모듈 0. BOOK_DEPTH=5(feed.py, hot_coins/market.py)는 대상 밖이라 변경 0.
- 백업 `/root/usb_runtime/crypto_manual_ux_pre_20261016-113758/` (MANIFEST 8, 검증 PASS).
- `usb-crypto-paper`만 1회 재시작 PID 2142723→2252381, health 40초, NRestarts 0, traceback 0.
- 롤백: 백업 8파일 되돌리고 executions.py 삭제, paper 재시작.

## Frontend
- 운영 서빙본 lhfMQRNC의 재구성 트리(3e2a8ad + 5093f6c/1c6c900/04428f1/2b1285d/16e7597 + 447b690/4906e95 frontend diff)와 동일함을 diff로 확인 후, 두 커밋의 frontend diff 적용(rej 0).
- 병합 조정 1건: 운영에 남아 있는 EntryAnalysisPreview(88ceb58 미배포)가 쓰는 `contextBase` 구조분해를 유지(`crypto-terminal.merge.diff`). 운영 env 미설정이라 렌더 null.
- tsc 0. vitest crypto/btc 601 통과, 1 실패 = `crypto-terminal-integration.test.tsx`(HEAD에선 88ceb58로 삭제된 파일)가 이번에 의도적으로 제거한 분석 패널 존재를 단언.
- 빌드 문자열 delta = crypto 수동매매 UI만, 라우트 변경 0, baked 주소 3종 동일.
- BUILD_ID lhfMQRNCjhcSjNt_5G58v → **07OdWY-ZZkVp2DU514MYg**, 롤백 `.next-build.pre-crypto-manual-ux-20261016-113758`.
- 첫 시도는 스모크 경로를 `/crypto`(원래 없는 라우트, 404)로 잘못 써서 자동 롤백됨 → `/crypto-paper`로 고쳐 재배포. usb-frontend는 총 3회 재시작(배포, 롤백, 재배포), 다른 서비스 0. 실패본 `.next-build.failed-crypto-manual-ux-20261016-113758`는 새 빌드 사본이라 삭제 가능.

## 안전 비교 (배포 전 11:35 vs 최종 12:02)
- PAPER 원장 15개 sha 전부 동일. BTC SHORT 0.064 @83612.90 10x, wallet/realized 동일. 다른 심볼 열린 포지션 0.
- LIVE: 배포 전부터 flat(wallet 0.00587989 USDT), 배포 후 동일. ExitGuard·leverage_capability 파일 sha 동일. 새 mirror 이벤트는 stream/reconcile + `LIVE_DISARM BOOT`(17377)뿐, 주문 0.
- C1 라우트 404, liqfwd service/timer disabled 유지.

## UI 스모크 (터널 + GET 전용 프록시, 비GET 0, 외부 요청 차단)
PAPER·LIVE 모두 BTCUSDT. HOT 버튼 1개, 목록 UI 없음. 25/HALF/75/MAX 활성(보유 중에도), LONG·SHORT 활성. 선택 수량 / 내가 거는 금액 / 이번 주문 규모만 표시, 상세 비용 패널 0. PAPER 레버리지 1/5/10/20/50/100. LIVE 레버리지 1~20x 활성, 50x/100x 비활성(계정 제한, 2026-10-29 해제 안내). HALF CLOSE 0.032 + FULL CLOSE 나란히, 주문 영역엔 HALF CLOSE 없음. 성과/거래내역/원장/런 설정 패널 0, 벽선 0, C1 0, LIVE에 PAPER 잔고 노출 0. LIVE 15분 제한 문구 0. 프록시 91요청 전부 200, binance 서명 경로는 경로당 1~3회.

## 확인된 결함 (미수정)
- **LIVE 과거 체결 마커가 실데이터에서 0건.** 실제 mirror의 Binance MARKET 주문 응답에 `avgPrice`가 없어 `live_executions`가 41건 모두 가격 없음으로 버린다(테스트 픽스처엔 avgPrice가 있음). 주문·포지션 영향 없음. 수정은 별도 작업(예: 스트림 체결가나 plan reference_price 사용).
- 로컬 pytest에서 `test_binance_live_api` 2건이 묶음 실행 때만 실패(단독 통과) = 기존 hot_slot.json 격리 문제.

## 관찰 (10분)
paper RSS 119→72MB, swap 51→0MB, memory.events high/max/oom 0. 호스트 avail 227→276~308MB, swap used 481→342MB. PSI 끝 무렵 mem avg300 0.7, IO avg300 11(배포 전 avg300 mem 22 / IO 47은 직전 A 배포 꼬리). crypto 지연 32~393ms, backend 14~35ms(첫 샘플 3.3s 1회). Q123 RUNNING, samples 증가, kline_err 없음. Flow COMPLETE, 나이 12~16초. flow/structure/feedlog/recorder 실패 0, OOM 0.

push 안 함.
