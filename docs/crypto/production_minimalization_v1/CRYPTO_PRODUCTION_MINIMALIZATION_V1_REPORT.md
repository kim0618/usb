# US-B CRYPTO / PRODUCTION MINIMALIZATION & CLEANUP AUDIT V1 - REPORT

작성 2026-10-14 10:05 KST. 서버 trader-j(961MB RAM, swap 2GB). 로컬 ~/usb HEAD 8b86bcb(origin 대비 2커밋 앞, 작업트리 dirty 196파일은 타 세션 몫).
방법: 기존 unit·runtime 경로만 조회(광역 scan 0), 서명 조회를 유발하는 /binance/fills·/account 호출 0, 서비스 재시작 0, 운영 LIVE/PAPER 주문·AUTO·leverage 변경 0.

**최종 판정: B. CRYPTO_CLEANUP_SMALL** (이번 실행분 디스크 3.4MB, RAM 0. 의미 있는 RAM 회수는 C1 운영 제거(P0)인데 PAPER C1 AUTO가 포지션을 들고 있어 BLOCKED, 사용자 결정 필요)

## A. Current crypto architecture (실측)

```
브라우저(basic auth) ─ nginx usb.jptcalc.kr
  /                  → 127.0.0.1:3000  usb-frontend (next-server, BUILD_ID w3LLcmPdetY4iarG7TXCX, 1c6c900 polling V1)
  /crypto-api/       → 127.0.0.1:8100  usb-crypto-paper  (PAPER 엔진 + Binance LIVE 수동주문 + C1 signal 20s tick + C1 AUTO + HOT 슬롯)
  /btc-context-api/  → 127.0.0.1:8014  usb-btc-context-ui (GET /health /snapshot 만; 저널+structure_snapshot+flow_horizons 읽기전용)
  /api/, /health     → 127.0.0.1:8000  usb-backend (주식 A/E/H, 범위 밖)

usb-crypto-context (collector, Binance BTCUSDT 공개 depth/trade, 1s 저널) → /root/usb_runtime/crypto_context/journal (343MB, 7일 retention v1.1)
   ├─ usb-crypto-q123-forward (저널 폴링 + 공개 kline → store 2.2MB)
   ├─ usb-btc-context-structure.timer(매분 :05, R1 엔진 전진 → state/structure_snapshot.json)
   ├─ usb-btc-context-flow.timer(매분 :10, 공개 1m kline taker 흐름 → flow/flow_horizons.json)
   └─ usb-crypto-manual-research.timer(매분 :20, S1 fill 캐시 + S2 mirror → store/snapshots.jsonl 9.8MB, trigger null, market_requests 0)
usb-crypto-liqfwd (D5.3 청산 forward, Bybit/Binance/OKX 공개) + liqfwd-daily.timer(10:15 KST)
usb-crypto-feedlog.timer(매분, 8100 /state GET → logs/feed.log 146KB, 소비자 0)
```

운영 paper 런타임 src는 로컬 HEAD와 7파일 차이(c1/runtime.py, c1/sources.py, hot_coins/market.py, paper/segments.py, terminal/chart_history.py = 미배포 커밋 8551d5b·7043004; quality.py·realtime.py = 양쪽 다 untracked rsync 잔재). 배포는 항상 파일 단위로 해야 함.

## B. Active services / timers (crypto)

| unit | PID | 상태 | enabled | NRestarts | MemoryMax | 목적 | 입력 → 출력 | consumer | 최근 성공 | 7일 실패 |
|---|---|---|---|---|---|---|---|---|---|---|
| usb-crypto-paper | 1997371 | active (10/13 15:13~) | enabled | 0 | 200M (현재 202M, 상한 밀착) | 수동 PAPER/LIVE 터미널 | Bybit WS, Binance REST/WS → run/*, data/runtime/crypto | frontend /crypto-api | 지속 | 0 |
| usb-crypto-context | 1582777 | active (10/10 21:47~) | enabled | 0 | 200M | Wall/Flow 수집 | Binance 공개 → journal | q123, btc-context-ui, structure/flow, recorder | storage_stats 09:54 | 0 |
| usb-crypto-q123-forward | 1684303 | active (10/11 15:57~) | enabled | 0 | 180M | Q123 forward 기록 | journal + kline → store | 분석 스크립트(오프라인) | status.json 09:54, lag 11.8s | 0 |
| usb-btc-context-ui | 1826565 | active (10/12 14:38~) | enabled | 0 | 96M | Wall/Flow UI API | journal, state, flow → /snapshot | frontend strip (1,247회/일) | /snapshot live=true | 0 |
| usb-crypto-liqfwd | 3250885 | active (9/27~) | enabled | 0 | 100M | D5.3 청산 forward | 3거래소 공개 → data 16.7MB | daily finalize | 지속 | 0 |
| usb-btc-context-structure.timer | oneshot | 매분 :05 | enabled | - | 112M, Timeout 120s | R1 구조 전진 | kline → structure_snapshot | 8014 | 09:45 STEPPED | 타임아웃 kill 9회(10/13 호스트 정체 구간) |
| usb-btc-context-flow.timer | oneshot | 매분 :10 | enabled | - | 64M, Timeout 60s | 흐름 지평 | 1m kline → flow_horizons | 8014 | 09:45 UPDATED | 타임아웃 kill 약 15회(동일 구간) |
| usb-crypto-manual-research.timer | oneshot | 매분 :20 | enabled | - | 96M, Timeout 120s | Manual Recorder | S1/S2 → store | 분석(오프라인), 8014 entry(미설정) | 09:45 OK events 47 ep 20 | 타임아웃 kill 9회(동일 구간) |
| usb-crypto-feedlog.timer | oneshot | 매분 | enabled | - | 80M | 피드 전이 로그 | 8100 /state → feed.log | 없음 | 09:44 | 0 (journal의 'TimeoutError' 127건은 로그 본문) |
| usb-crypto-context-retention.timer | oneshot | 매일 04:40 | enabled | - | 100M | 7일 prune | journal | - | 10/14 04:40 v1.1 delete 0 | 0 |
| usb-crypto-liqfwd-daily.timer | oneshot | 매일 10:15 KST | enabled | - | 250M | parquet finalize | data → daily | - | 10/13 (3분52초, 정체 피해) | 0 |

disabled 유닛 usb-e-rt2/usb-e-rvol, failed usb-e-rvol-once는 E 전략(범위 밖, 변경 0).

## C. Memory / swap by component (smaps_rollup PSS, 09:46 KST)

| component | PSS | swap | CPU% | polling / IO | 서명 요청 |
|---|---|---|---|---|---|
| usb-crypto-paper | 161MB | 42MB | 8.7 | UI 열림 시 state 1s·sizing 5s·hot 5s·c1 15s; C1 tick 20s(33일 1m 재수집); tape 1Hz | mirror 8일 평균 RECONCILE 346회/일(0.24/분), UI 열림 시 약 2/분 |
| usb-crypto-q123-forward | 76MB | 33MB | 2.9 | 저널 폴링, 공개 kline | 0 |
| usb-crypto-context | 46MB | 31MB | 5.5 | 공개 WS, 봉인 gz 약 50MB/일 | 0 |
| usb-crypto-liqfwd | 21MB | 29MB | 0.0 | 공개 WS + OKX REST 60s | 0 |
| usb-btc-context-ui | 10MB | 26MB | 0.1 | /snapshot 1,247회/일 | 0 |
| structure/flow/manual/feedlog oneshot | 실행 중 피크 각 33~52MB | 0 | 0.13~0.44s/회 | 분당 4회 python 기동 | manual은 0(trigger null) |
| 합계(상주) | 314MB | 161MB | | | |

호스트: MemAvailable 256MB, swap 607MB 사용, PSI io full avg300 7.25%. 디스크 / 80% (avail 2.0GB).

## D. CORE_KEEP
A 수동 PAPER/LIVE 터미널, B 주문/포지션/account(live_routes, paper engine, wallet_recovery), L ExitGuard(OFF 상태지만 LIVE 안전장치), C Manual Recorder(timer :20 + store), F Wall UI + G Flow horizon UI(8014 + structure/flow timer + frontend strip), I HOT 슬롯(아래 G절), 시스템 health(/health).

## E. FORWARD_RESEARCH_KEEP
D Q123(ACTIVE, 중복 worker 0, 단일 recorder, store/runtime 분리 정상, 디버그 출력 0), E Wall/Flow collector(단일 writer lock, retention v1.1 정상), liqfwd(사용자 게이트 "하락 이벤트 30회" 유지, 10/02 결정). Q123/Wall/Flow에 old preview·replay 프로세스 0, 중복 snapshot 0(8014 src.pre-flow는 롤백본 1세대).

## F. C1 cleanup candidates (전수)

| 요소 | 위치 | 분류 |
|---|---|---|
| drop-in `CRYPTO_C1_SIGNAL=on` | /etc/systemd/system/usb-crypto-paper.service.d/c1-signal.conf | REMOVE (운영 OFF 시 1순위, paper 재시작 필요) |
| C1 runtime 20s tick(33일 1m 재수집, bars 47,520) | runtime src app/crypto/c1/*.py 13파일(로컬 c1/incremental.py는 미배포) | REMOVE |
| API 6개 `/api/crypto/c1/{state,markers,ledger,c1x,attribution,attribute}` | terminal/c1_routes.py | REMOVE (frontend는 state·markers만 15s 폴링, 어제 768회/일) |
| C1 AUTO `/api/crypto/paper/c1-auto` GET/POST + reconcile 훅 | terminal/api.py 22곳, paper/c1_auto.py, server.py 2곳 | REMOVE **BLOCKED**: enabled=true, has_position=true(PAPER BTC, 10/02 진입) |
| C1 AUTO run dir | run/paper-server-001-c1-auto (ledger.jsonl, input.jsonl 699KB) | KEEP (PAPER 거래 기록, 데이터) |
| C1 state | data/runtime/crypto/c1/state.json | REMOVE (OFF 후) |
| frontend | lib/crypto-c1.ts, crypto-c1-auto-control.tsx, crypto-terminal-layout.tsx(useC1Signals, C1SignalStrip, 마커), crypto-candle-chart.tsx 마커 1곳, 테스트 2 | REMOVE (frontend 재빌드·재시작 필요) |
| 백엔드 테스트 16 | tests/crypto/test_c1_*.py, test_paper_c1_auto.py | ARCHIVE (코드 제거와 함께) |
| 연구 | research/c1_binance_recheck, docs/crypto/C1_*, c1_exit/, data/research/crypto/c1_* | RESEARCH_KEEP (삭제 금지) |
| 공유 의존 | c1/sources.py → historical.py·storage.py·rest.py(Bybit) | C1과 함께 제거 가능(다른 importer 0). collector/q123/recorder는 C1 import 0(docstring 명시) |

C1 제거 순서(승인 후): ① 사용자가 PAPER C1 AUTO 해제·포지션 처리 결정 → ② drop-in 제거 + c1_routes import 제거 + api.py AUTO 훅 제거(로컬 PREP·테스트) → ③ frontend C1 제거·빌드 → ④ paper 재시작 + frontend 재시작(호스트 스래싱 2~3분 창 선택). 예상 효과: paper RSS 감소(C1 33일 그리드·캐시, 수십 MB 추정이나 분리 측정 전이라 수치 보류), 20s마다의 Bybit 공개 REST 제거, frontend 15s 폴링 2개 제거.

## G. HOT verdict: **OPTIONAL_KEEP → KEEP (실사용 중)**
- 백그라운드 스캐너 없음(scan은 POST /hot/scan 때만, 어제 8회). GET /hot은 UI 열림 시 5s 폴링(어제 2,451회), PAPER source는 서명 조회 0, LIVE source는 30s 캐시(polling V1).
- 사용 증거: hot_slot.json 10/13 20:57 ARCUSDT 선정, PAPER 런 디렉터리 1000RATS/ARC/MAGIC/OGN/QNT/STRK(지갑 멤버 = 거래 기록). HOT LIVE 스위치(BINANCE_LIVE_HOT_ORDERS)는 env·unit 모두 부재(OFF).
- 제거 시 효과: 메모리 미미(스캔은 on-demand), 서명 0. 결론 KEEP.

## H. Liquidity verdict
- frontend `/liquidity-preview`: **DEAD** (nginx 11일 로그 hit 0, 기본 API 127.0.0.1:8011 리스너 0, env 미설정, nav 링크 0). 운영 번들에 52KB chunk로 포함돼 있음 → 로컬 제거 완료(아래 P절), 다음 frontend 배포 때 사라짐.
- backend `app/crypto/liquidity_map`: **SHARED_KEEP** (context_collector_v1가 `liquidity_map.view`·`market_structure_v0`를 import, q123도 import). paper 런타임에는 없음.
- Market Context V1 preview(8012): 서버에 unit·프로세스·디렉터리 없음(배포된 적 없음). frontend 프리뷰 라우트 2개는 로컬에만 있었고 제거 완료. backend `market_context_v1`는 btc_context_ui_v1이 계약 상수로 10곳 import → SHARED_KEEP.

## I. Dead API / routes (8100 + 8014)

| route | 분류 | 근거 |
|---|---|---|
| c1/ledger, c1/c1x, c1/attribution, POST c1/attribute | RESEARCH_ONLY | frontend ref 0, 연구·검증용 |
| c1/state, c1/markers, paper/c1-auto | USED (C1 OFF 전까지) | frontend 15s 폴링 |
| POST fault/drop-feed | INTERNAL_USED(테스트 전용) | frontend 0, tests 참조. C1과 무관, 유지(제거는 P2) |
| POST reset, POST emergency-close, candles-15s, chart, chart-history, order-preview, state, sizing, live, mode, leverage, order, trades, ledger, performance, pnl-breakdown, hot, hot-coins, hot/scan, binance/* 19개 | USED | frontend ref ≥1 |
| 8014 GET /entry-analysis | DEAD | env BTC_CONTEXT_ENTRY_ANALYSIS_RESULTS 미설정 → 404, frontend 호출부 제거 완료. 백엔드 제거는 8014 재시작 필요(P2) |
| 8014 /health /snapshot | USED | |

## J. Dead frontend components → 로컬 제거 완료 (운영 무변경)
app/liquidity-preview, app/crypto-terminal-preview, app/market-context-preview, components/{liquidity-map-preview, crypto-terminal-preview, market-context-preview, market-context-panel, entry-analysis-preview}(+테스트 4), lib/{liquidity-map, market-context}, __fixtures__/market-context-{btc,eth,sol}.json, crypto-terminal-integration.test.tsx(프리뷰 전용 통합 테스트), crypto-terminal.tsx에서 EntryAnalysisPreview import·JSX 제거(-326자). importer 전수 grep 0 확인.

## K. Dead workers / timers
- 없음(모든 crypto 타이머가 소비자 있음). 후보: usb-crypto-feedlog(소비자 0, 단 10/13 정체를 기록한 유일한 센서) → DISABLE_CANDIDATE P2, 사용자 결정.
- MERGE_CANDIDATE(이번엔 미구현): structure/flow/manual 3개 oneshot이 매분 python 3회 기동(각 0.3~0.4s CPU, 피크 33~52MB). 한 프로세스로 합치면 기동 비용 감소하나 새 worker 작성 = 금지 항목이라 기록만.

## L. Runtime / staging cleanup (실행분)
삭제 7건, 디스크 7,667,488K → 7,664,036K(+3.4MB), 전 서비스 PID·NRestarts 불변:
1~3 crypto_paper/releases/staging-{hot-v1, subdollar, tfrange}-2026101* (3.3MB) 4 crypto_context/staging-retention-3744627 (68K) 5~6 btc_context_ui 고아 .tmp 2개(10/13 타임아웃 kill 잔재) 7 /tmp/usb-crypto-paper-c1.conf(drop-in과 바이트 동일한 사본).
보류: staging-polling-v1-20261013(현 build 유일 tgz 사본, 다음 배포 후), .next-build.pre-* 3세대(14.4MB, A UI 배포 롤백본 포함 → A 담당 결정), before-seed-fix/segment-compression(132K).

## M. Data KEEP list (삭제 금지, 이번 작업 무접촉)
crypto_paper/run/* 전부(PAPER 원장·tape·HOT 런·c1-auto 런), data/runtime/crypto/live/*(mirror 9.6MB, manual_performance, exit_guard, leverage_capability), crypto_context/journal 343MB, q123_forward/store·runtime, manual_trade_research/store 14.6MB, crypto_liqfwd/data 16.7MB, btc_context_ui/state·flow, 모든 before-* 롤백본, repo docs/data/research.

## N. Archive list (승인 시)
paper 런타임 app/crypto/research(2MB, import 0), realtime.py·quality.py(rsync 잔재), before-seed-fix·before-segment-compression, .next-build.pre-flow-20261012. C1 테스트 16개·frontend C1 테스트 2개는 C1 제거와 함께.

## O. Safe delete list → L절 7건 실행 완료.

## P. Restart-required cleanup (LOCAL PREP 완료, 적용 STOP)
- frontend dead preview 제거(J절): 로컬 작업트리 변경 18파일(삭제 17, 수정 1). 적용 = 사용자 커밋 → node20 clean build → .next-build 교체 → usb-frontend 재시작(과거 실측 스래싱 2분40초, paper memory.events 급증) → 별도 승인.
- C1 운영 제거(F절): 승인·AUTO 결정 후 LOCAL PREP 착수.
- 8014 /entry-analysis + entry_context.py + research/wall_lifecycle_r1 import 제거: 8014 재시작(10MB 프로세스) 필요, P2.

## Q. Expected RAM / IO reduction
- 이번 실행: RAM 0, IO 0, 디스크 3.4MB.
- frontend 프리뷰 제거 배포 시: 번들 52KB chunk 제거, 런타임 RAM 변화 사실상 0(라우트는 로드된 적 없음).
- C1 제거 시(추정, 미측정): paper 프로세스에서 33일 1m 그리드·C1 캐시만큼 감소(수십 MB 추정), 20s Bybit 공개 REST 제거, frontend 요청 2종/15s 제거. 수치는 OFF 후 RSS 전후 실측으로 확정.
- feedlog 중단 시: 분당 python 기동 1회 감소, RAM 상주 0.
- liqfwd 중단 시: PSS 21MB + swap 29MB.

## R. Delete manifest
`CRYPTO_CLEANUP_DELETE_MANIFEST_BEFORE.txt`(같은 폴더). 1절 7건 실행, 2절 승인 대기, 3절 로컬 PREP 완료.

## S. Rollback plan
- 서버 삭제분: 1~3은 git fa67437/c5fb88b/37c1c49에서 재생성 가능, 4는 repo 3744627, 5~7 복구 불필요.
- 로컬 PREP: `cd ~/usb && git checkout -- frontend/components/crypto-terminal.tsx && git checkout -- <삭제 17경로>` (커밋 전), 커밋 후엔 revert.
- 운영 코드는 변경 0이므로 운영 롤백 없음.

## T. Tests
- frontend: `tsc --noEmit`(소스 전용, 생성물 .next*/types 제외) exit 0. 프로젝트 tsconfig 그대로는 삭제한 라우트의 **생성된 stub**(.next/.next-dev/.next-build/types) 15건만 오류 → 다음 `next build`가 재생성하면 해소(소스 오류 0).
- frontend vitest 전체: 66파일 1,054 테스트 통과(삭제 전 70파일 1,047+; 프리뷰 전용 4파일 제거). 실행 env `NEXT_PUBLIC_CRYPTO_API_BASE=http://127.0.0.1:9`(미스텁 fetch 실전송 방지, 로컬 8100 리스너 없음 확인).
- backend: 코드 변경 0 → 회귀 불필요(import·API contract 불변). Manual Recorder·Q123·Wall/Flow 운영 상태 전후 동일(B절).

## U. Production deployment plan (별도 승인)
1. 사용자 커밋·push(`cd ~/usb && git push origin main`).
2. frontend: usb-frontend 정지 없이 로컬 node20 clean build → scp → `.next-build.pre-cleanup-<ts>` 백업 → mv 교체 → `systemctl restart usb-frontend` (호스트 스래싱 창 2~3분, e-paper 창 16:45~22:35 KST 피할 것).
3. 검증: BUILD_ID 변경, app-path-routes-manifest에 /liquidity-preview 부재, /crypto-paper 200, /btc-context-api/snapshot 폴링 지속, paper PID 불변.
4. C1 OFF는 위 F절 순서, 별도 승인.

## V. Final verdict: **B. CRYPTO_CLEANUP_SMALL**
운영 crypto 구성은 이미 "터미널 + Wall/Flow 수집·UI + Q123 + Recorder + liqfwd"로 좁혀져 있고, 죽은 것은 프리뷰 라우트·스테이징 잔재뿐이었다. 메모리에 의미 있는 유일한 후보는 C1 운영 레이어인데 PAPER C1 AUTO가 포지션을 보유 중이라 이번 작업 금지 항목(AUTO 변경·재시작)에 걸려 BLOCKED. 사용자 결정 2개: ① C1 AUTO 해제·PAPER 포지션 처리 후 C1 제거 진행 여부, ② feedlog 타이머·liqfwd 지속 여부.
