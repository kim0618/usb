# US-B CRYPTO / C1 PRODUCTION REMOVAL DEPLOY V1

배포 2026-10-15 12:21~12:25 KST, 관찰 12:40까지. 서버 trader-j. 증거 `deploy_20261015/`.

**판정: CRYPTO C1 PRODUCTION REMOVAL = PASS**

## 1. 배포 창
12:15·12:21 KST(보호 시간대 16:45~22:35 밖). memory PSI 0, MemAvailable 234~263MB, load 0.7~1.0. 12:21 io PSI some avg10 29였지만 vmstat si 0, wa 0~3%, idle 80%+로 하강 중이었다. 실행 중인 usb oneshot 0. 다음 heavy 작업은 13:35 e-rvol-once, 14:10 h-forward였다.

## 2. 배포 전 스냅샷 (12:20:26, 서명 호출 0)
- PAPER: BTC SHORT 0.064 @83,612.90, lev 10, realized 3113.5088, wallet 10037.6398, used_margin 535.12256, fees 1154.3026, funding -153.2559, unrealized 157.88. 기본 런 원장 299 events, sha `0000528a14ef…`, 12개 런 sha 기록.
- LIVE(mirror 파일): BTCUSDT LONG 0.108 @83,913.1, wallet 467.46012643, mirror seq 17288, ExitGuard enabled=false(10/06 이후 갱신 없음). 직전 무장 이력: LIVE_ARM 10/14 21:12:40(무장 상태).
- 서비스: paper 1997371, context 1582777, q123 1684303, btc-context-ui 2063894, frontend 2074429, 전부 NRestarts 0.
- C1: AUTO enabled=false, runtime state armed=true, bars 47,520, signals 0. `/api/crypto/c1/state`와 `/paper/c1-auto`는 200.
- Q123 RUNNING lag 13.4s, flow 10m/1h/4h/1d COMPLETE.

## 3. 배포 소스
backend: 로컬 작업트리의 C1 제거본. 서버 `api.py`, `server.py`, `hot_routes.py`의 sha가 HEAD와 같다는 것을 배포 직전에 다시 확인했다(fdcce57e/d8fa0545/9a40a5e7). 교체본은 699bc056/65b12dea/a7aacd22.

frontend: 운영 빌드 `Rh0hJ2skzuYNTyehDWSI-`의 기록된 기준(H_V2_AUTONOMOUS_PIPELINE_PRODUCTION_V1.md 3절)을 그대로 재구성했다. `git archive 3e2a8ad` 위에 5093f6c·1c6c900·04428f1·2b1285d의 frontend diff를 얹고, 그 위에 C1 제거 diff 11파일을 얹었다. `crypto-terminal.tsx`는 88ceb58 부재로 문맥이 달라 fuzz 적용했다. 순 변경이 C1 줄 제거뿐임은 diff로 확인했다.
- node v20.20.1 / npm 10.8.2(서버와 같음, nodejs.org SHASUMS 검증), package-lock sha 서버와 동일, `.env.local`·`.env.production.local`은 서버본(NEXT_PUBLIC 키만).
- 소스 tsc 0 오류. vitest 1191 통과 / 3 실패. 실패 3개(fx-display, market-context-panel, strategy-compare)는 C1 패치 없는 운영 기준 트리에서도 같은 3개가 실패한다(기존).
- BUILD_ID `EbLTGbg4JLkd9-CIcf4f7`, tgz sha `342093be3ff665f6…`.
- **H 미포함 검증**: H 미배포 커밋(c61c495, 523ef09)이 추가한 문자열(`data-h-auto-result`, `DETERMINISTIC_NO_AI`, `NOT_DECISION_ELIGIBLE`)은 새 빌드 0 / 운영 0이다. 그 커밋들이 삭제한 문자열 `자동 리서치 승인 전`은 새 빌드 2 / 운영 2로 남아 있다. chunk 해시는 빌드 환경 차이로 전부 달라서 증거로 쓰지 않았다.
- C1 문자열(`c1-signal-strip`, `/api/crypto/c1/`, `c1-auto`)은 새 빌드 0 / 운영 1이다. 유지 기능(`visibilitychange`, `/api/crypto/hot`, `btc-context`, `/api/crypto/binance/`, `emergency-close`, `/api/crypto/order`)은 새 빌드와 운영의 파일 수가 같다.
- `/liquidity-preview`는 운영 기준 그대로 남아 있다(88ceb58은 이번 범위가 아님).

## 4. 백업
`/root/usb_runtime/crypto_c1_remove_pre_20261015-122146/`, MANIFEST.txt 32파일(sha256·크기·mtime, 사본 `deploy_20261015/BACKUP_MANIFEST.txt`): api.py, server.py, hot_routes.py, c1_routes.py, paper/c1_auto.py, app/crypto/c1/ 전체, drop-in `c1-signal.conf`, `unit_cat_before.txt`, `removed_runtime_state/state.json`(C1 runtime 상태 202B).
frontend: `/root/usb/frontend/.next-build.pre-c1-remove-20261015-122146` (BUILD_ID Rh0hJ2sk…).

## 5. 적용 (12:22~12:25)
1. 파일 3개 install, c1_routes.py·paper/c1_auto.py와 그 pyc 삭제, `app/crypto/c1/` 삭제(백업에 있음), `data/runtime/crypto/c1/state.json`을 백업으로 이동.
2. py_compile OK. 운영 src 전체에서 c1 import 0. 별도 프로세스 import 검사: 라우트 40개 중 c1 0개, C1 모듈 로드 0, order·emergency-close·hot·binance·state 라우트 존재, `runtime.c1_auto` 속성 없음.
3. drop-in 삭제 → daemon-reload(unit env의 C1 0) → `systemctl restart usb-crypto-paper`(12:23:16) → /health 200(35초 뒤), PID 2142723, 에러 로그 0.
4. frontend: `.next-build` → `.next-build.pre-c1-remove-…`, 새 빌드 배치 → `systemctl restart usb-frontend`(12:24:57) → /crypto-paper 200(12:25:22), PID 2143134. `/trading-h`·`/strategy-e`·`/daily`·`/dashboard` 200. (헬스 루프가 `set -e`와 curl exit 7로 조기 종료돼 재확인 명령으로 검증했다.)
- 다른 서비스 재시작 0(context, q123, btc-context-ui, timers PID 불변).

## 6. 배포 후 안전 확인 (12:24:07)
- PAPER 12개 런 원장 sha·event 수·last_seq **전부 동일**. 포지션 SHORT 0.064 @83,612.90, lev, realized, wallet, used_margin, fees, funding 동일. equity·unrealized만 시세로 변동.
- state: `paper_source=PAPER_MANUAL`, `c1_auto`/`c1_available` 키 없음. `/api/crypto/c1/state`와 `/api/crypto/paper/c1-auto`는 **404**.
- LIVE: 포지션 LONG 0.108 @83,913.1, wallet, ExitGuard 파일, leverage_capability 파일 동일. 주문 이벤트 0. mirror에 추가된 이벤트는 `LIVE_DISARM reason=BOOT`(17289)와 `LIVE_STREAM_STATE`(17290)뿐이다.
  - **주의: 재시작 전 LIVE는 무장 상태(10/14 21:12 ARM)였고, 부팅 시 자동 해제(fail-closed 설계)로 지금은 해제 상태다. LIVE 주문을 다시 내려면 사용자가 재무장해야 한다. 배포에서는 다시 무장하지 않았다.**
- C1 AUTO 상태 파일은 그대로(enabled=false), C1 runtime state 재생성 없음.

## 7. 운영 UI 스모크 (12:26, 헤드리스, ssh 터널 + GET 전용 대역 프록시)
- 프록시는 GET 외 요청을 전부 거부하고, `/binance/*`는 경로당 1회·상한 25로 제한했다(10/12 429 사고 대응).
- PAPER: SHORT, 0.064, 진입 83,612.9 표시. `data-testid*=c1` 0, "C1 AUTO/C1 신호/C1x" 텍스트 0. btc-context-strip 1, HOT 탭, 소스 스위치, 차트(visible bars 240) 정상.
- BINANCE LIVE(3.5초): LONG 0.108, 진입 83,913.1, 차트·컨텍스트 스트립 정상, C1 0. 부팅 해제 상태라 "거래불가 / 거래 활성화"로 표시된다.
- 30.6초 동안 요청 182건: **C1 요청 0, 404 0, non-GET 0**, Binance 서명 upstream 10건. 스모크 중 mirror +1은 LIVE_RECONCILE(TTL, blockers 없음, 포지션 동일).
- 콘솔 오류는 로컬 origin에서 `https://usb.jptcalc.kr/api/v1/dashboard`로 가는 CORS 4건뿐이다(하네스 아티팩트).

## 8. 자원 효과 (실측)

| | 배포 직전 12:22:41 | 12:35 | 12:40 |
|---|---|---|---|
| paper RSS | 185,624K | 77,232K | 77,616K |
| paper PSS | 175,536K | 66,306K | 66,612K |
| paper swap | 94,964K | 0 | 0 |
| paper cgroup memory.current | 208,339K | 94,176K | 85,580K |
| paper CPU | 8.3%(10/13~10/15 평균) | 5.1%(60s) | 5.2%(60s) |
| paper memory.events max | 11:14→12:22에 +13,796(약 200/분) | 0 | 0 |
| paper threads | - | 16 | 16 |
| host MemAvailable | 256,892K | 318,884K | 321,448K |
| host swap used | 658,448K | 359,952K | 363,112K |
| memory PSI some avg10/avg60 | 0.09 / 0.27 | 0.00 / 0.00 | 0.00 / 0.96 |
| io PSI some avg10/avg60 | 34.99 / 33.33 | 1.77 / 5.78 | 6.41 / 8.44 |

해석 주의:
- 막 재시작한 프로세스는 원래 가볍다. 이전 프로세스는 2일 가동 중이었다. C1 grid 단독 몫은 로컬 실측으로 +47.5~58.5MB였다.
- 호스트 swap -298MB에는 frontend 재시작(next-server swap 해제) 몫이 섞여 있다. 이전 frontend의 next-server 메모리는 측정하지 않았다. 새 next-server는 PSS 140MB / swap 0이다.
- 이 숫자들은 배포 직후 17분의 것이다. 장기값은 며칠 뒤 같은 지표로 다시 봐야 한다.
- C1 background 요청 0의 근거: 운영 src에 C1 코드 없음, import 검사에서 C1 모듈 0, C1 state 디렉터리 재생성 없음, 저널 c1 0건, UI 요청 C1 0.

## 9. Q123 / Wall / Flow / Recorder
- Q123: PID·NRestarts 불변, lag 11.8~13.4s. samples 12:20:26 397,958 → 12:40 399,163(1,191초에 1,205개, 누락 없음). store 증가(approaches 692→696, outcomes 1120→1124).
- collector telemetry(10분 단위 resync): 12:0x 17, 12:1x 14, 12:2x 12, 12:3x 11. 배포 구간에 stale·gap·disconnect 0.
- flow 10m/1h/4h/1d COMPLETE, age 7~57s. structure/flow/manual 타이머 실패 0. Recorder 12:39 OK.
- 참고(배포 전 사건): 11:23~11:29 stale/resync 폭주는 오전 호스트 정체 회복 구간이고, Q123 11시대 quality(SYNCING 448, UNSYNCED 151, samples 3600)에 반영됐다. 재발하지 않았다.

## 10. 롤백 (미사용)
backend: 백업 폴더 파일을 원위치로 `cp -a`하고 `app/crypto/c1/`, drop-in, state.json을 복원한 뒤 daemon-reload, paper 재시작.
frontend: `.next-build`를 지우고 `.next-build.pre-c1-remove-20261015-122146`를 `.next-build`로 되돌린 뒤 frontend 재시작.
C1 AUTO는 롤백 시에도 OFF로 유지한다(사용자 승인 없이는 켜지 않음).

## 11. 남은 정리
- 서버 스테이징 `/root/usb_runtime/crypto_c1_remove_stage/`(tgz 18MB 포함)는 롤백 창이 지나면 삭제할 후보.
- frontend `.next-build.pre-*` 5세대(약 117MB씩 가능성), paper 런타임 `app/crypto/research` 2MB = 디스크 P2.
- HEAD frontend에는 88ceb58(프리뷰 제거)과 H 커밋이 아직 미배포로 남아 있다(별도 결정).
