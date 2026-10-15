# US-B CRYPTO / PRODUCTION MINIMAL RUNTIME CLEANUP V2 - REPORT

작성 2026-10-15 11:40 KST. 서버 trader-j(961MB RAM, swap 2GB). 이전 감사: `../production_minimalization_v1/`.
삭제 매니페스트: `CRYPTO_RUNTIME_CLEANUP_DELETE_MANIFEST_V2.txt`(같은 폴더).

**갱신 2026-10-15 12:40: C1 운영 제거 배포 PASS → `C1_PRODUCTION_REMOVAL_DEPLOY_V1.md`. 아래는 배포 전 작성본.**

**판정: CRYPTO MINIMAL RUNTIME = PARTIAL.** 재시작 없이 할 수 있는 것(liqfwd 정지, C1 AUTO OFF)은 운영 적용을 마쳤다. C1 코드 제거(신호 루프, API, UI)는 로컬 준비와 테스트까지 끝났고, paper·frontend 재시작이 필요해 승인 대기 중이다.

## 1. V1의 BLOCKED 사유 정정

V1은 "PAPER C1 AUTO가 포지션 보유"를 C1 제거의 차단 사유로 들었지만, 실제 포지션 소유자는 수동이었다.
- 기본 PAPER 계좌 포지션: BTC SHORT 0.064 @83,612.90, 원장 `reason=PAPER_MANUAL`, `open-1791372673984-1135933`(10/07 20:31:13 KST).
- C1 AUTO는 그 5초 뒤(20:31:18)에 켜졌다. 본 신호는 0건(`last_seen_signal_id=null`), `owns_position()=False`.
- V1이 본 `has_position=true`는 소유자와 무관하게 계좌에 포지션이 있다는 뜻이었다.
- C1 AUTO는 별도 계좌가 아니라 기본 PAPER 세션 위의 플래그다. 그래서 C1 코드를 제거해도 포지션은 일반 엔진에 그대로 남고, 수동 청산 경로도 유지된다.

## 2. 인벤토리 (11:14 KST, smaps_rollup)

| unit | PID | PSS | swap | CPU | NRestarts | 소비자 | 판정 |
|---|---|---|---|---|---|---|---|
| usb-crypto-paper | 1997371 | 176,305K | 89,312K | 8.2% | 0 | frontend /crypto-api | KEEP (C1 레이어만 제거) |
| usb-crypto-context | 1582777 | 61,619K | 24,220K | 5.5% | 0 | q123, 8014, structure/flow, recorder | KEEP |
| usb-crypto-q123-forward | 1684303 | 62,215K | 48,512K | 2.9% | 0 | 오프라인 분석 | KEEP |
| usb-btc-context-ui | 2063894 | 7,190K | 29,412K | 0.1% | 0 | frontend 컨텍스트 스트립 | KEEP |
| usb-crypto-liqfwd | 3250885 | 20,357K | 32,960K | 0.0% | 0 | **없음(C1 가설 전용)** | **REMOVE → 정지 완료** |
| structure/flow timer (:05/:10) | oneshot | - | - | - | - | 8014 | KEEP |
| manual-research timer (:20) | oneshot | - | - | - | - | = Manual Recorder 본체 | KEEP |
| feedlog timer (매분) | oneshot | - | - | - | - | 호스트 정체 센서 | KEEP |
| context-retention (04:40) | oneshot | - | - | - | - | journal prune | KEEP |
| liqfwd-daily (10:15) | oneshot | - | - | - | - | liqfwd finalize | **REMOVE → 비활성 완료** |

측정 당시 호스트는 스래싱 중이었다(io PSI some avg10 99.8, wa 84~94%, paper cgroup memory.events max 298,625). 이 시각 crypto UI 요청은 0건이었고, 11:13~11:14에 주식 전략 화면 SSR 요청이 몰려 있었다. 정체는 이번 조치 이전부터 진행 중이었다.

## 3. C1 전수 분류

| 요소 | 위치 | 분류 | 상태 |
|---|---|---|---|
| signal generation + 20s loop | `c1/runtime.py`(운영본: tick마다 33일 1분봉 47,520개 **전체 재수집**) via `terminal/c1_routes.py` lifespan | REMOVE | 로컬 제거, 배포 대기 |
| C1 data refresh | 같은 루프 (`refresh_grid`) | REMOVE | 위와 같음 |
| env flag | drop-in `c1-signal.conf`(`CRYPTO_C1_SIGNAL=on`, `C1_FIXTURE=`) | REMOVE | 배포 시 삭제 |
| C1 AUTO 플래그 | `run/paper-server-001/c1_auto_state.json` | REMOVE | **OFF 적용 완료**(11:18:16) |
| C1 AUTO 코드 | `paper/c1_auto.py`, api.py 진입차단·레버리지고정·리셋차단, 1Hz 루프 reconcile | REMOVE | 로컬 제거 |
| order entry hook | `controller.reconcile` → `session.command(ORDER OPEN)` | REMOVE | 로컬 제거 |
| reconcile hook | `_observe_loop`의 reconcile 호출(기본 세션마다 observe 중복 호출; 1Hz 간격 검사로 no-op) | REMOVE | 로컬 제거 |
| API | `/api/crypto/c1/{state,markers,ledger,c1x,attribution,attribute}`, `/api/crypto/paper/c1-auto` GET/POST | REMOVE | 로컬 제거 |
| state 필드 | `/state`의 `c1_auto`, `c1_available` | REMOVE | 로컬 제거, `paper_source`는 `PAPER_MANUAL` 고정 |
| UI | 차트 마커 레이어, C1SignalStrip, C1AutoControl, 주문 차단 조건 | REMOVE | 로컬 제거 |
| polling | frontend `useC1Signals` 15s (state + markers) | REMOVE | 로컬 제거 |
| runtime state | `data/runtime/crypto/c1/state.json` (202B) | REMOVE | 배포 후 보관 |
| C1-only helpers (운영 src) | `app/crypto/c1/` 13파일 288K | REMOVE(운영 src) / RESEARCH_KEEP(repo) | 배포 후 보관 |
| 과거 거래 출처 라벨 | `paper/analytics.py`의 `PAPER_C1_AUTO` 해석 | SHARED_KEEP | 무변경 |
| 과거 AUTO 런 기록 | `run/paper-server-001-c1-auto/` | KEEP(데이터) | 무변경 |
| 연구 코드·문서 | `research/c1_binance_recheck`, `docs/crypto/C1_*`, 테스트 13개 | RESEARCH_KEEP | 무변경 |
| timer/service | C1 전용 유닛 없음(paper 프로세스 내부 루프) | - | - |

UNKNOWN 0.

## 4. liqfwd 판정: REMOVE (C1 전용 증명)

- 계약서 `CRYPTO_D5_3_LIQUIDATION_FORWARD_CONTRACT_V1.md` 10절에 등록된 forward 가설은 "C1 순간에 3사 LONG 청산이 늘어나는가" 하나뿐이다. 9절 게이트(하락 이벤트 30회)도 이 판정의 시작 조건이다.
- repo 전수 grep에서 importer는 0이다. 테스트 5개는 "import 금지" 목록으로만 언급한다.
- Q123, Wall/Flow, Context UI, HOT, Recorder, terminal, frontend, nginx, 다른 systemd 유닛의 소비 0. 각 서비스는 자기 src를 쓰고 liqfwd data를 읽지 않는다.
- 조치: `disable --now`(11:19:43). 호스트 정체로 SIGTERM이 타임아웃되어 11:20:20 SIGKILL로 끝났다. 직후 raw gz 6개 `gzip -t` OK, 런타임 jsonl 마지막 줄 OK, `reset-failed`로 failed 표시를 지웠다.
- 데이터 16.3MB는 그대로 보존했다. 10/15 UTC 부분일은 raw로만 남는다(필요하면 10/16 이후 daily service 1회로 finalize 가능).

## 5. Manual research timer: KEEP (분리 불가)

`usb-crypto-manual-research.timer`의 ExecStart는 `app.crypto.manual_snapshot_v1.recorder`, 즉 Manual Recorder 자체다. 별도의 "연구 타이머"는 서버에 없다. 이 타이머를 끄면 Recorder가 멈춘다.

## 6. HOT: ACTIVE / ON-DEMAND (V1 판정 유지)

백그라운드 스캐너 없음. scan은 `POST /hot/scan`(버튼)일 때만 돈다. GET `/hot`은 UI가 보일 때만 폴링한다(PAPER 서명 0, LIVE 30s 캐시). 이번 작업에서 무변경.

## 7. Dead research runtime / preview / staging

- 운영 프로세스가 import하는 연구 런타임 없음. 연구 프로세스 0.
- paper 런타임 `app/crypto/research` 2,020K는 import 0이지만 디스크만 차지한다(P2). 운영 위험 대비 이득이 없어 보류했다.
- 운영 frontend 번들에 `/liquidity-preview`와 C1 스트립 chunk가 아직 남아 있다(V1의 88ceb58 미배포). 다음 frontend 배포 때 함께 빠진다.

## 8. 우선순위

| P | 항목 | 효과 근거 | 상태 |
|---|---|---|---|
| P0 | C1 background loop + grid | 로컬 실측(증분 OFF = 운영 방식): grid 1회 생성 **RSS +47.5MB**(2회 후 +58.5MB), 갱신 1회 30~34초. 운영은 갱신 후 20초 쉬고 다시 시작하므로 거의 상시 다운로드 상태다. 200M 상한에 붙은 paper cgroup 안에서 돈다 | 배포 대기 |
| P0 | liqfwd | PSS 20.4MB + swap 33.0MB | **완료** |
| P1 | C1 AUTO 진입 경로 | 신규 진입 가능성 0으로 | **완료(플래그 OFF)** |
| P1 | frontend C1 15s 폴링 2종 | 요청 감소 | 배포 대기 |
| P2 | 운영 src c1/ 288K, research 2MB, state.json | 디스크 | 배포 후 / 보류 |

## 9. 자원 전후 (Phase 1)

| | before 11:14 | after 11:37 |
|---|---|---|
| MemAvailable | 213,976K | 253,972K |
| swap used | 709,648K | 653,772K (-55.9MB) |
| memory PSI some avg10/avg300 | 98.91 / 62.80 | 1.55 / 12.05 |
| memory PSI full avg10 | 88.07 | 0.98 |
| io PSI some avg10/avg300 | 99.79 / 92.38 | 15.08 / 37.25 |
| io PSI full avg10 | 88.10 | 11.04 |

| service | PSS+swap before | after |
|---|---|---|
| paper | 176,305 + 89,312K | 170,869 + 92,296K |
| context | 61,619 + 24,220K | 86,366 + 4,864K |
| q123 | 62,215 + 48,512K | 65,195 + 46,948K |
| btc-context-ui | 7,190 + 29,412K | 3,940 + 32,908K |
| liqfwd | 20,357 + 32,960K | 0 |
| 합계 | 552,102K | 503,386K (-48.7MB) |

주의: PSI 개선의 대부분은 진행 중이던 정체가 끝난 데 따른 것이다. 이번 조치로 귀속할 수 있는 수치는 liqfwd 몫(상주 약 53MB)뿐이다.

## 10. Q123 / Wall / Flow 보호

- Q123 RUNNING, lag 12.5s → 11.9s, out_of_order 0, store_conflicts 0, kline_error null.
- collector 저널 reconnect/resync/gap/timeout: 11:14 이후 0건.
- structure/flow/manual/feedlog 타이머 kill 0건. 로그의 "TimeoutError" 문자열은 feedlog 본문이다.
- feedlog의 paper UNREACHABLE: 정리 전 10:19~11:08 사이 10회, 정리 후 11:20~11:37 사이 1회(11:33). 기존 현상이며 증가하지 않았다.
- threshold·계약 변경 0. Q123 11시대 품질 레코드(12:00 기록)는 이 보고서 이후에 확인해야 한다.

## 11. 포지션 / 원장 (before 11:17:32, after 11:36:51)

- PAPER 12개 런 원장: **전부 sha256·이벤트 수·last_seq 동일**. 기본 런 `paper-server-001` sha `0000528a14ef2f35d0f4090f5d2cfef24bc59e6956ec56d387ef4301e19b4d4e`, 299 events.
- PAPER 포지션: BTC SHORT 0.064 @83,612.90 (PAPER_MANUAL), 나머지 11개 런 무포지션. 불변.
- PAPER 계좌: realized_pnl·wallet_balance·used_margin 535.12256 동일.
- LIVE: BTCUSDT LONG 0.108 @83,913.1, wallet 467.46012643, mirror seq 17288. 불변. 서명 호출 0, 파일에서만 읽었다.
- 바뀐 것: C1 AUTO `enabled` true→false, `paper_source` 라벨 PAPER_C1_AUTO→PAPER_MANUAL.
- 주문 0, 레버리지 변경 0, 재시작 0(paper/context/q123/ui PID·NRestarts 불변).

## 12. 로컬 C1 코드 제거 (미커밋)

backend:
- 삭제: `app/crypto/terminal/c1_routes.py`, `app/crypto/paper/c1_auto.py`, `tests/crypto/test_c1_api.py`, `tests/crypto/test_paper_c1_auto.py`
- 수정: `app/crypto/terminal/api.py`, `app/crypto/terminal/server.py`, `app/crypto/terminal/hot_routes.py`(독스트링), `tests/crypto/{test_c1_isolation,test_crypto_multisymbol,test_hot_symbol_v1,test_paper_terminal_api}.py`
- 신규: `tests/crypto/test_c1_operational_removal.py` (5개). 다루는 범위: C1 라우트 0, terminal/paper/live/hot이 C1 패키지·AUTO import 0, state에 C1 필드 없음·옛 엔드포인트 404, AUTO 출처 포지션 + enabled 상태파일로 재시작 → 수량·진입가·레버리지·실현손익·지갑 동일, 원장 바이트 동일, 1Hz 루프 112틱 동안 원장 무변화, 수동 청산 가능, 기존 원장은 접두사로 보존, enabled 상태파일이 있어도 수동 진입 허용.

frontend: 삭제 `lib/crypto-c1.ts`, `components/crypto-c1-auto-control.tsx`, 테스트 2개. 수정 `crypto-candle-chart.tsx`(C1 전용 마커 레이어 제거, contextLines 경로 유지), `crypto-terminal-layout.tsx`, `crypto-terminal.tsx`, `crypto-paper-terminal.tsx`, `lib/crypto-paper.ts`, `__fixtures__/crypto-terminal-paper.json`, `vitest.setup.ts`(주석).

테스트:
- backend crypto 전체: before 3,290 passed / 26 failed / 14 errors / 33 skipped → after 3,267 / 26 / 14 / 33. **실패 ID 집합 동일(신규 0)**. 기존 40건은 btc_p1/p2, btc_vol_a, d6_golden, c1(x)_parity, c1_binance_recheck, multisymbol 5, leverage_capability, feed_seed, expert_e1(데이터 의존).
- frontend vitest: 66 files / 1,054 → 64 / 1,006, 실패 0. 소스 tsc 오류 0(생성 stub `.next*/types` 15건은 V1과 같은 잔재).

## 13. 운영 배포 계획 (승인 전 STOP)

필요 재시작: **usb-crypto-paper**(필수), **usb-frontend**(재빌드).

backend (파일 단위, 서버 `api.py`/`server.py`/`hot_routes.py` sha == 로컬 HEAD 확인됨):
1. `releases/before-c1-removal-<ts>/`에 백업: api.py, server.py, hot_routes.py, c1_routes.py, paper/c1_auto.py, `c1/` 디렉터리, drop-in `c1-signal.conf`, `data/runtime/crypto/c1/state.json`.
2. 로컬본 3파일 복사. c1_routes.py·c1_auto.py 삭제. `c1/`과 state.json을 백업 폴더로 mv.
3. drop-in 삭제 → `daemon-reload` → `systemctl restart usb-crypto-paper`. e-paper 창(16:45~22:35 KST)은 피한다.
4. 검증: /health 200, `/state.paper_source=PAPER_MANUAL`, `/api/crypto/c1/state` 404, 12개 원장 sha·포지션 재비교, PSS/swap/memory.events 전후 비교.
- 서버 src에서 HEAD와 다른 5파일(c1/runtime.py, c1/sources.py, hot_coins/market.py, paper/segments.py, terminal/chart_history.py)은 건드리지 않는다. c1 두 개는 2단계에서 보관된다.
- 롤백: 백업 폴더 원복 + drop-in 복원 + daemon-reload + 재시작. C1 AUTO 플래그는 이미 OFF라 롤백해도 신규 진입은 없다.

frontend:
- 운영 빌드 `Rh0hJ2skzuYNTyehDWSI-`(10/14 13:20)는 88ceb58(프리뷰 제거) 이전 기준이다. HEAD에는 그 뒤로 H 화면 커밋 2개(c61c495, 523ef09: trading-h page, lib/strategies.ts)가 있다. **HEAD로 빌드하면 미배포 H 화면 변경이 같이 나간다 → 사용자 결정 필요.**
- 백엔드만 먼저 배포하면 구 화면이 `/api/crypto/c1/*`에 15s마다 404를 받고, C1 AUTO 박스가 빈 상태로 그려진다. 주문 기능에는 영향이 없다. 두 개를 같은 창에서 배포하는 것을 권장한다.
- 절차: node20 clean build → `.next-build.pre-c1-removal-<ts>` 백업 → 교체 → `usb-frontend` 재시작(과거 실측 스래싱 2~3분).

예상 절감(근거 있는 것만):
- paper: C1 grid 로컬 실측 +47.5~58.5MB 상당. 매 50초 주기의 33일 전체 재수집(CPU·네트워크) 제거. 실제 PSS 변화는 재시작 후 실측으로 확정.
- frontend: 15s 폴링 2종 제거.
