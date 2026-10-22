# US-B — Crypto Paper IO OPT V1 + Idle Job Cleanup Audit

2026-10-22 KST. 최초 감사 단계의 운영 조회는 READ ONLY. 운영 부하 테스트, 배포, 재시작, stop/disable,
타이머 변경, 실제 주문, commit, stage, push 모두 수행하지 않았다.
이후 명시적으로 승인된 COMMIT ONLY 재검증과 파일 inventory는 W절에 기록한다.

```text
CRYPTO_PAPER_IO_OPT_V1 = READY_FOR_PRODUCTION_APPROVAL
BTC_CONTEXT_STRIP = SAFE_TO_STOP
MANUAL_RESEARCH = SAFE_TO_STOP
GTC_BR_SESSIONS = STALE_LEAK
DEPLOY = NO
SERVICE STOP = NO
TIMER CHANGE = NO
PUSH = NO
```

READY는 아래 **두 파일의 최소 후보**와 관련 회귀에 대한 판정이다. 전체 crypto 연구 테스트가
모두 통과했다는 뜻이나 Strategy I 실행 승인이라는 뜻은 아니다. idle-job stop은 별도 승인 대상이다.
원본 로컬 증거: `/home/tjd618/usb-io-opt-evidence-20261022/`.
재검토 가능한 축약 증거는 이 디렉터리의 JSON/patch에 포함했다.

## A. Git / runtime-copy 상태

- Branch `main`, HEAD `6682429`, `origin/main...HEAD = 0 / 0`.
- 시작 시 다른 세션의 tracked 수정 21개와 다수 untracked 파일이 있었다.
  기존 파일은 보호했다. Strategy I, E/H/A, LIVE order 코드는 수정하지 않았다.
- 운영 entrypoint: `app.crypto.terminal.server:app`, workers 1, port 8100.
  `PYTHONPATH=/root/usb_runtime/crypto_paper/src/backend`, paper root는 `.../crypto_paper/run`.
- 운영 crypto Python 파일 **143개 / 1,438,663 bytes**를 지정 경로에서 해시 비교했다.
  수정 전 저장소와 다른 파일은 `paper/segments.py`, `hot_coins/market.py`,
  `terminal/chart_history.py` 세 개다. 나머지 140개는 동일하다.
- segments 차이는 기존 OPT V1 전체다. 두 chart 파일 차이는 만료 페이지를 캐시에서 먼저
  제거하는 기존 별도 개선이다. **이번 후보에는 두 chart 파일을 포함하지 않는다.**
- 최종 production-equivalent 트리는 운영 143개 중 **segments.py, api.py 두 개만 다르다**.
  `runtime_source_manifest.json`, `source_hashes.json`, `runtime_candidate.patch` 참조.
- preflight PASS: 여유 1,109,393,408 bytes, NO_RECEIPT 0, 마지막 archive pull 09:41 KST.
  메모리 PSI를 먼저 확인하고 모든 추가 조회는 known path + `ionice -c3 nice -n19`로 제한했다.

## B. Crypto-paper architecture

`Runtime.sessions`는 관측 세션, `Runtime.feeds`는 공개 시세 피드, `Runtime.retired`는
거래 이력이 있는 과거 HOT의 wallet 보존 세션이다. SharedCash는 retired account도 포함한다.
BTC/ETH/SOL은 Bybit, 동적 HOT은 Binance 공개 피드를 사용한다. LIVE client/user stream은
별도 객체이며 이번 코드 변경 대상이 아니다. 15초봉은 `server.trade_feeds`가 따로 소유한다.

observe loop는 0.25초마다 세션 map 사본을 순회하고, 각 세션은 quote timestamp 기준
최대 1Hz로 MARKET을 기록한다. 명령 직전 forced observation은 별도이며 그대로 유지한다.
InputTape는 레코드를 RAM에 누적하지 않는 `retain=False`다.

## C. GTC/BR root cause

**STALE_LEAK**: 현재 슬롯은 APR인데 GTC/BR 테이프가 두 차례 표본에서 계속 증가했다.
둘의 최신 checkpoint는 flat이고 마지막 ledger event는 RUN_START였다. 과거 HOT의 flat 세션을
계속 관측해야 하는 history/chart 계약은 없다. 저장 파일을 보존하는 것과 활성 feed/observe를
계속 유지하는 것은 별개다.

정확한 코드상 누수 지점:

1. `hot_scan()`이 HOT을 교체하고 `await runtime.retire(previous)`를 호출한다.
2. `Runtime.retire()`가 sessions/feeds에서 이전 심볼을 제거한다.
3. `await feed.stop()`에서 event loop가 다른 요청을 처리할 수 있다.
4. 기존 코드는 **await가 끝난 뒤** `hot_scan()`에서 `retire_dynamic(previous)`를 실행했다.
5. 그 사이 이전 심볼의 지연된 PAPER poll이 `session_for()`를 호출하면 허용 검사를 통과한다.
   미거래 세션을 재구성하거나 retired 세션을 부활시키고 새 feed를 만든다.
6. 원래 retirement는 이전 feed만 종료한다. 새 세션/피드는 map에 남아 계속 기록한다.

미거래/거래 이력 보존 두 경우를 fixture로 재현했으며 수정 전 둘 다 실패했다
(`leak_before.txt`). 운영의 특정 HTTP 요청을 이 interleaving에 직접 연결하는 로그는 없다.
따라서 **누수 메커니즘은 재현으로 확정**, GTC/BR에서 어느 요청이 trigger였는지는 추정이다.

HOT의 과거 선정 시각은 단일 `hot_slot.json`이 덮어써져 정확히 복원할 수 없었다.
아래는 선정 시각이 아닌 첫 RUN_START 관측이다.

| 심볼 | 첫 RUN_START KST | 현재 확인 |
|---|---|---|
| BR | 10/17 22:11:13 | flat checkpoint, RUN_START 이후 거래 event 없음, 계속 append |
| GTC | 10/18 15:40:46 | 위와 동일 |
| APR | 슬롯 selected_at 10/18 18:05:33.524 | 현재 HOT |

## D. Session lifecycle

정상 경로는 HOT 교체 → 기존 15초봉 feed 정리 hook → paper retirement → 공개 소켓 종료 →
seed task/seed marker/error/history cache 제거다. tape writer는 매 append마다 파일을 열고
닫으므로 해제해야 할 장기 tape fd는 없다.

미거래 세션은 map과 wallet membership에서 제거한다. 거래한 세션은 기존 회전/checkpoint를
만들고 retired에 보존한다. SharedCash의 실현손익/수수료/펀딩은 잃지 않는다. 재시작 시 과거
flat 동적 run은 retired로 복구되어 observe/WS를 시작하지 않는다. 열린 paper 포지션은
계속 admitted/active 상태로 남아 조회/청산 가능하다.

최소 수정: `Runtime.retire()`의 **flat backstop 이후, 첫 await 이전**에 `retire_dynamic()`을
호출한다. 열린 포지션은 RuntimeError로 거절되어 admission/session/feed/account가 그대로다.
기존 `hot_scan()`의 마지막 discard는 idempotent하게 남겨 두었다. LIVE source와 주문 경로는
변경하지 않았다. 기존 운영 누수는 이번에 제거하지 않았다. 승인된 재시작 시 boot recovery가
과거 flat GTC/BR을 비활성으로 복구하는지 확인해야 한다.

## E. should_rotate root cause

운영 write path:

```text
observe → InputTape.record → append → flush → fsync → apply_tape_record
        → maybe_rotate → should_rotate → active_records → active tape 전체 text count
        → (20,000 records 이상이면) rotate + manifest + checkpoint + gzip
```

매 MARKET append마다 전체 tape를 재읽는다. 명령은 기존처럼 다음 MARKET rotation check에
포함된다. 읽기 낭비와 별도로 매 세션 fsync의 4KB 단위 write amplification도 크다.
OPT V1은 후자의 fsync 의미를 바꾸지 않으므로 **쓰기 약 1.9GB/day를 직접 줄이지 않는다**.

## F. Existing Tape Rotation OPT V1

정본: `7043004159fb1e3c6bf2b6a33915299f2af74ba8` (10/13).
커밋은 `segments.py`와 `test_tape_rotation_count_v1.py` 두 파일이며, main에 이미 포함되어 있다.
`segments.py`의 마지막 변경도 이 커밋이다. 커밋 본문에 **Not deployed**가 명시돼 있고,
실측 runtime 파일도 이전 전체 count 구현과 일치한다. 왜 승인이 보류됐는지에 대한 추가
근거는 없으므로 운영 누락 이유를 임의로 추정하지 않는다.

구현은 초기 한 번 count, 이후 dev/inode/크기/256-byte head·tail을 검증하고 append 영역만
읽는다. truncate/replace/관측 가능한 head·tail rewrite, CR/codec 문제는 기존 count로
fallback한다. rotate 후 cache를 비운다. `active_records`, storage, rotate 의미는 그대로다.

범위: 운영 writer의 append-only 계약에 대한 최적화다. head/tail fingerprint는 파일 중간의
임의 외부 overwrite를 전부 검출하는 무결성 검사가 아니다. 같은 크기의 중간 rewrite까지
보장한다고 주장하지 않는다. 이번에 counter를 새로 재작성하지 않았다.

## G. Proposed implementation

운영 적용 후보는 아래 두 파일뿐이다.

| 파일 | 변경 |
|---|---|
| `backend/app/crypto/paper/segments.py` | 기존 정본 OPT V1 그대로 재사용. 이번 repo 신규 수정 없음 |
| `backend/app/crypto/terminal/api.py` | import + retirement 전에 동적 admission 취소. 실질 코드 1행 |

검증용으로 두 테스트 파일에 엄격 byte parity 2개와 lifecycle 3개를 추가했다.
최소 runtime patch는 운영 원본에 로컬 dry-run/apply 후 candidate SHA 일치를 확인했다.

## H. Tape parity

기존 8개 deterministic replay 조합(서로 다른 seed/경계/압축), restart/torn write,
command/forced MARKET/fill/checkpoint/ledger 검증을 재사용했다. 추가 테스트는 gzip header clock을
고정하고 **모든 파일의 원시 bytes를 정규화 없이 비교**한다. plain/gzip 두 경우 모두 PASS.

검증 항목: line count/content/order/timestamp, seq, cut boundary, unpacked gzip, compressed
bytes, manifest의 bytes/SHA 포함 전체 필드, checkpoint/account/position/ledger/final state.
압축 헤더의 시계 차이를 동일하게 만들었을 뿐 운영 timestamp 생성 로직은 바꾸지 않았다.

HOT 선택, wallet, 기존 BTC 포지션, 열린 SHORT 보존은 별도 lifecycle/수동매매 회귀로 검증했다.
회전 최적화에서 candle/LIVE/HOT-selection 코드는 byte 단위 미변경이다. 운영 거래 원문을
가져오지 않고 저장소 고정 fixture(공개 depth snapshot 포함)를 사용했다.

## I. IO benchmark

로컬 synthetic ASCII row 304 bytes, 크기별 30 append + 30 fsync, primed counter.
50k/100k는 scaling 측정용으로 threshold만 높인 격리 실험이며 운영 한도는 20,000 그대로다.

| 초기 lines | check 평균 ms: old → opt | 논리 read bytes: old → opt | 감소 |
|---:|---:|---:|---:|
| 1k | 0.491 → 0.120 | 9,261,360 → 39,840 | 99.57% |
| 10k | 2.886 → 0.154 | 91,341,360 → 39,840 | 99.956% |
| 50k | 11.183 → 0.181 | 456,141,360 → 39,840 | 99.991% |
| 100k | 27.656 → 0.166 | 912,141,360 → 39,840 | 99.996% |

100k에서 CPU 835.3 → 9.85ms, wall 1,073.5 → 189.4ms. max RSS 50,616 → 50,580KiB:
seed allocation을 포함한 child peak라 상주 RAM 절감 증거로 사용하지 않는다.
쓰기 양쪽 9,120 bytes / harness write 30회 / fsync 30회. steady opt pread 150회,
full scan old 31회 → opt 1회. 초기 cold count는 최적화도 O(N)이다.

`benchmark.py`, `benchmark.json` 참조. baseline 논리 bytes는 ASCII 전체 파일 크기의 합,
optimized는 실제 os.pread 반환 bytes를 집계했다. 후자는 계측 wrapper overhead도 포함한다.
로컬 sandbox에 `/proc/self/io`와 strace가 없어 **전체 kernel read/write syscall·physical bytes는
UNKNOWN(null)**이다. ru_inblock/oublock은 0을 반환했다. 물리 read가 99.99% 줄었다고
해석할 수 없다. 환경상 block counter 0 역시 실제 디스크 IO가 0이라는 증명이 아니다.

## J. BTC Context Strip verdict

**SAFE_TO_STOP (기술적 영향 확인, 실제 중지 안 함).** `/crypto-paper`의 별도 BtcContextStrip
보조 정보: LIQUIDITY/STRUCTURE/FLOW, OI/funding/basis, 기록된 entry 비교다. 브라우저가
`/btc-context-api/` → 8014를 조회한다. API가 없으면 해당 보조 정보가 비거나 dash가 된다.
주문 ticket, 계좌, paper, Q123, context collector는 이 API/worker에 의존하지 않는다.
structure/flow는 downstream producer이며 collector를 중지시키지 않는다.

runtime API/entry_context SHA는 repo와 동일하다. entry store와 entry-analysis 설정은 없다.
앞선 감사의 snapshot 사용량(10/19 0, 10/20 4, 10/21 4, 10/22 0)을 이번 접근 로그 전수
재집계로 갱신하지는 않았다. 이를 신규 실측으로 표기하지 않는다.

현재 UI PSS 2,125KiB / SwapPss 34,168KiB. 기존 일일 관측 structure peak 약 46MB,
flow 14MB. 둘을 끄면 2,880 launches/day를 제거한다. 서로 다른 초에 실행되므로
46+14MB를 상주 RAM 절감으로 합산하지 않는다.

## K. Manual Research verdict

**STALE_AND_SAFE_TO_STOP / 최종 코드 SAFE_TO_STOP**, 현재 consumer 구성 기준.
- 48 derived events, 20 episodes, S1 fills 59, open episode 없음.
- recorder status/market_status OK, pending classification 없음, anomalies 없음,
  deferred snapshots 0, market requests 0, 신규 written 전부 0.
- 마지막 events 신규 row: 10/15 23:58:20.558 KST.
- 마지막 snapshot/episode row: **10/17 22:48:20.542 KST**.
- S1 계산 마지막 갱신: 10/18 20:07:32.981 KST.
- trigger URL은 의도적으로 비어 있다. UI가 performance cache를 갱신할 때만 S1이 전진한다.
  이미 처리한 closed episodes와 followups를 매분 dedupe하므로 새 row가 없는 것이 정상이다.

종료된 데이터 계약이나 recorder 고장으로 판정하지 않는다. BTC 새 수동거래와 S1 갱신이
생기면 다시 데이터가 필요해질 수 있는 살아 있는 계약이다. `entry_context.py`라는 실제
consumer 구현은 존재하지만 운영 `BTC_CONTEXT_MANUAL_STORE` 미설정으로 비활성이다.
따라서 단순 consumer grep 0을 consumer 코드 부재로 해석하면 틀리다.

활성 후속 연구가 이 store를 기다린다는 증거는 찾지 못했지만 사람의 미래 연구 의도까지
부정하지 않는다. 중지 시 향후 진입 시점의 context를 잃을 수 있으므로 그 손실을 알고
승인해야 한다. LIVE 수동주문·계좌·performance cache 자체는 recorder와 무관하다.
매분 중지 후보 1,440 launches/day, 기존 peak 약 33MB. 데이터 파일은 보존한다.

## L. Feedlog

**KEEP**. unit은 유지됐고 파일/설정/타이머는 미변경이다. 상태 변화만 기록하는 장애 센서다.
기존 peak 11MB / 약 270KB 기록이라는 판정을 뒤집을 추가 비용 증거는 없다.

## M. Q123/context untouched

최종 readback: paper PID 2519676, Q123 1684303, context 1582777, BTC UI 2063894.
시작 표본과 같고 각 NRestarts=0. 대상 세 서비스는 active다.
Q123/context의 code/config/retention/watermark/timer/서비스는 변경하지 않았다.
I PID=0, timer disabled 그대로다. Q123 n30 분석/종료는 별도 작업이다.

## N. SAFE daemon findings

| daemon | PSS KiB | SwapPss KiB | 확인 / 미확정 |
|---|---:|---:|---|
| multipathd | 23,003 | 0 | vda 단일 disk, multipath -ll 출력 없음, RequiredBy 없음, 약 23MB locked |
| rpcbind | 215 | 412 | 0.0.0.0:111 TCP/UDP, rpcinfo에는 portmapper만 등록, NFS mount/fstab entry 0 |
| networkd-dispatcher | 402 | 10,232 | RequiredBy 없음. hook 디렉터리는 존재하며 사용자 hook 내용 전체 의존 증명은 미완료 |

현재 NFS 사용 증거는 없다. 향후 외부 automount/운영 관례까지 부정하지 않는다.
rpcbind는 socket activation도 함께 검토해야 한다. 셋 모두 별도 유지보수 후보이며 중지하지
않았다. 합계 PSS 23,620KiB(23.07MiB), swap 10,644KiB(10.39MiB). swap을 RAM에 더하지 않는다.

## O. A snapshot cost

A consumer timer disabled / service inactive, HOLD_A 존재. 그러나 producer는 HOLD_A를 검사하지
않는다(consumer만 hold). `run_session → SW.run → freeze_a → body_0915 → SS.seal`은 계속 수행된다.
공개 source/runtime producer hash 동일. 실제 archived producer report로 확인했다.

| ET session | A sweep s | Kiwoom calls 합 | snapshot raw / gzip bytes | seal wall s |
|---|---:|---:|---:|---:|
| 10/19 | 530.72 | 5,192 | 4,967,157 / 1,036,356 | 2.53 |
| 10/20 | 526.41 | 5,150 | 4,673,495 / 960,973 | 2.46 |
| 10/21 | 532.65 | 5,203 | 4,726,524 / 976,964 | 2.92 |

約5,000 symbols / 88k~96k bars를 freeze하며 dict와 bar list를 복사하고 JSON/gzip를 만든다.
A parity 산출물도 약 211KB gzip가 추가된다. 별도 CPU/heap/physical write 귀속값은 UNKNOWN:
seal wall에는 IO 대기도 포함되고 전체 producer RSS peak를 A 비용으로 취급할 수 없다.
rolling 자체도 5천여 union symbols를 돌며 일일 약84k~92k minute calls지만 E와 공유한다.

A-only sweep pool은 약2,471~2,498종목이다. E overlap/tick shard freshness에도 sweep이 관여하므로
**5,200 requests 전부를 안전 제거량으로 잡으면 안 된다**. snapshot 봉인과 A-only acquisition을
분리하는 후속 설계/측정은 가능하나 E finalization 계약을 따로 증명해야 한다. 이번 제거 0.

## P. Grouped/H schedule analysis

기준 시각은 America/New_York이다. 현재 DST KST 표기는 10/22 기준이고 DST 종료 뒤 1시간 변한다.

| job | 현재 ET / KST | 검토 후보 ET / KST |
|---|---|---|
| E rvol | 00:35 / 13:35 | 유지 |
| grouped | 00:40 / 13:40 | 01:50 / 14:50 |
| splits | 00:55 / 13:55 | 유지 |
| H-forward | 01:10 / 14:10 | 02:05 / 15:05 |

후보는 통상 55~70분 걸리는 RVOL의 종료 뒤 grouped/H를 15분 간격으로 놓는다.
RVOL 지연 시 충돌은 여전히 가능하다. 새로운 실행 의존성은 이번에 구현하지 않았다.

grouped는 D-1 publishable session과 mirror를 갱신하고 E/H/I가 읽는다. H는 일 단위 completed
session/outcome, SEC fetched_at/checked_at, freshness gate를 사용하며 놓친 session은 gap으로
남긴다. 정시 분 자체가 가격 계산 파라미터는 아니나 실행을 늦추면 관측/가용 시각이 늦어진다.
E universe(00:20)는 먼저 실행되고 원천을 별도 수집한다. E producer 03:45 전, 향후 I 02:15 전
필요한 grouped/splits가 완료되어야 한다. H 후보 02:05는 I와 10분 간격일 뿐 보장은 아니다.

현재 H 평가 중지 상태/소비 경로와 as-of 날짜를 유지한다면 검토 가능한 후보지만,
**완전 semantics 동일 또는 freshness SLA 충족을 확정하지 않는다**. 최악 소요·I와 중복·Massive
공용 5/min budget을 실측한 뒤 별도 승인해야 한다. 이번 timer 변경 0.

## Q. Tests

최종 운영 동등 테스트: **1,047 passed / 23 skipped / 0 failures**, 24.65s.
격리 cwd: `/tmp/usb-io-opt-runtime-20261022`; HEAD backend/docs/frontend + 후보 두 파일,
운영 버전의 두 chart 파일, 공개 instrument/risk-limit fixture 5개(hash 별도).
범위는 paper/tape/rotation/segment/lifecycle/HOT/LIVE/order/position/sizing/manual UX/
chart/history/15s candles/wallet/account/polling/replay다.

23 skips: 과거 D2 데이터 기반 replay 13개, fee capture 의존 9개, paper run 표시 환율 의존 1개.
이와 별개로 고정 synthetic replay, wallet recovery, LONG/SHORT 주문 fixture, strict byte parity는
실행되어 통과했다. repository 버전의 만료 캐시 제거 전용 2개 테스트는 앞선 repo 회귀에서는
통과했지만 운영에는 그 구현을 넣지 않으므로 최종 runtime suite에서 제외했다.

추가 결과:
- 회전 29 passed, HOT 최종 56 passed; 수정 전 신규 race 2개는 DID NOT RAISE로 재현 실패.
- 초기 필수 repo 회귀 807 passed/1 skipped. 열린 SHORT retirement 거부 테스트도 추가 PASS.
- 확장 전체 crypto 실행은 2,982 passed / 132 skipped / 13 failed / 342 errors였다.
  격리 archive에서 data/research 계약·parquet·reference가 빠진 오류가 대부분이며,
  C1 장시간 테스트 1개는 로컬 30초 deadline 초과다. shared cwd의 WAL hot_slot을 읽는
  multisymbol 2개 실패는 최종 **cwd까지 격리**한 suite에서 해소했다.
  전체 crypto 연구 회귀를 PASS라고 보고하지 않는다. 연구 asset은 변경/보충/배포하지 않았다.

환경 한계: 빈 FastAPI TestClient조차 local sandbox의 thread-to-loop wakeup에서 멈췄다.
테스트 전용 event loop 10ms wakeup과 개별 30s deadline을 적용했다. app, exchange mock,
assertion, fsync를 바꾸지 않았다. 벤치마크는 이 wakeup shim을 쓰지 않았다.
실제 Binance 주문/서명 REST/arm/leverage 변경/운영 HTTP 호출은 수행하지 않았다.

## R. Expected resource savings

운영 표본은 15.00초와 15.88초 두 번이다. 긴 표본/장기 평균으로 오해하면 안 된다.

| session | records/s (둘째 표본) | bytes/min (첫째~둘째) | 첫 표본 active tape bytes / segments |
|---|---:|---:|---:|
| BTC | 0.819 | 22,464 / 21,246 | 2,124,507 / 108 |
| ETH | 0.819 | 21,392 / 20,203 | 6,241,539 / 65 |
| SOL | 0.819 | 22,168 / 20,883 | 4,810,846 / 63 |
| APR | 0.945 | 21,312 / 25,199 | 2,701,259 / 12 |
| GTC | 0.945 | 20,764 / 24,466 | 5,461,556 / 12 |
| BR | 0.882 | 20,084 / 22,263 | 8,306,090 / 14 |

테이프 seq 증가 × InputTape.record의 fsync 1회로 세션별 append/fsync를 유도했다. kernel trace
실측은 아니다. BR은 후속 표본 전에 정상 회전했다. 각 세션 CPU/RAM·실제 연결 수는 process
밖에서 정확히 분리하지 못했다. 주입/heap dump/strace attach는 하지 않았다.

| 후보 | 절감 추정 / 한계 |
|---|---|
| tape OPT | 첫 표본 rchar 24.34MB/s, 둘째 18.47MB/s: 1.60~2.10TB/day **논리** 재읽기 상당 부분 제거. 정상 steady check는 row+1,024 bytes/session 수준. boot/rotation/storage 읽기는 남음 |
| tape OPT write/fsync | **변화 0**. 관측 physical write 21.85~22.70KB/s ≈1.89~1.96GB/day는 그대로일 수 있음 |
| tape OPT CPU/latency | 로컬 check 크게 감소. process 전체 CPU/IO PSI의 동일 비율 감소는 주장하지 않음 |
| GTC/BR retirement | append file growth 약58.8~67.3MB/day(비압축), fsync 약1.83/s(최대 계약 2/s) 제거 예상 |
| GTC/BR physical write | 6개 중 2개 단순 비율 모델 약0.6~0.7GB/day; 실제 배포 후 재측정 필요 |
| GTC/BR WS | BinancePublicFeed는 심볼당 /public + /market 두 연결: **4개 감소 예상**. 15초봉 추가 연결은 이미 release hook이 정리하므로 별도 절감에 미산입 |
| GTC/BR memory/disk | feed/book/ticker/candle/세션 객체 제거 기대. 정확한 MiB UNKNOWN, RAM credit 0. 기존 segments 삭제 0, archive 증가량은 gzip에 따라 달라 별도 미산정 |
| BTC strip stop | PSS 약2.08MiB, SwapPss 약33.37MiB. structure/flow 46/14MB burst와 2,880 launches/day 제거 |
| manual stop | 상주 회수 0, 33MB burst와 1,440 launches/day 제거. cursor 재쓰기/log 증가 제거 기대, 정확 bytes UNKNOWN |
| safe daemons | 합계 PSS 약23.07MiB / SwapPss 약10.39MiB. 승인 전 회수 0 |

physical read 표본은 0~1.91KB/s로 rchar보다 매우 작다. 페이지 캐시 재독과 실제 디스크 IO를
구별해야 한다. 세션 제거 효과와 tape 최적화의 logical read 감소는 겹치므로 합산하지 않는다.
세 idle oneshot의 46+14+33MB를 상주 회수량으로 합산하지 않는다.

## S. Strategy I impact estimate

이번 표본 MemAvailable는 240,508~241,280KiB = 약234.9~235.6MiB였다.
I는 여전히 상주 0 / 자동 실행 없음. 이번 코드만 배포한다면 검증된 상주 RAM 회수 credit은 0:
논리 IO/CPU 낭비와 stale feed를 줄이는 변화다.

추후 strip+daemon stop까지 별도로 승인되더라도 현재 PSS 기반 단순 상한은 약25MiB이므로
같은 표본의 가상 MemAvailable는 약260MiB 정도다. swap 약44MiB 제거는 RAM 가산치가 아니다.
manual/structure/flow stop은 분당 cold starts 4,320회/day를 없애지만, 상주 93MB 확보가 아니다.

I local 목표 약204.5MiB를 260MiB에서 빼면 여유는 약55MiB뿐이다. grouped/H burst와
공유 cache/서비스 요청/재시작 비용을 견딘다는 보장이 없다. 앞선 I Capacity Recovery V2의
보수적 guard 후보450MiB에도 미달한다. IO PSI 개선률/새 swap pressure는 모델만으로 수치화
불가다. 기존 155MB 저점도 안전하다고 바뀌지 않는다.

**I 실행 가능 판정은 내리지 않는다.** Q123/context를 계속 보존하며, 승인된 적용 후 장기
실서버 MemAvailable/PSI/swap-in/out과 매분·grouped/H burst를 재측정해야 한다.

## T. Production deployment plan — 실행 전 STOP

1. 별도 승인 후 유지보수 창에서 preflight/PSI/디스크 확인. 운영 수동 LIVE 작업 상태와
   paper recovery 가능성을 확인하고 서비스 재시작 시 LIVE가 disarmed로 돌아옴을 안내한다.
2. source 두 파일과 해시, 필요한 run metadata/checkpoint/manifest를 rollback 위치에 보존한다.
   1.1GB 여유를 고려해 전체 장기 tape tree를 무제한 tar하지 않는다.
3. `source_hashes.json`의 운영 두 SHA와 실제 파일을 다시 대조한다. 다르면 STOP하고 재감사한다.
4. `runtime_candidate.patch`의 **두 파일만** staging tree에 적용하고 SHA/compile/회귀 확인한다.
   market.py/chart_history.py, LIVE/strategy 코드, unit/env/data는 교체하지 않는다.
5. Python에 이미 import된 모듈이므로 승인된 crypto-paper 단독 restart가 필요하다.
   Q123/context/backend/frontend는 재시작하지 않는다. 운영 적용은 이 문서 작성 단계에서 STOP.
6. 승인 후 smoke: recovery 거절/중복 RUN_START/ledger divergence 없음, BTC/ETH/SOL/APR
   관측과 chart/candles 정상, 기존 paper position/account/wallet 유지, GTC/BR 비관측,
   현재 HOT/held-symbol 노출 유지, LIVE는 주문 발행 없이 기존 상태 조회만 확인한다.
7. 60초 이상 동일 축 process IO, tape seq, connection, RSS/PSS, PSI/swap 표본을 비교한다.
   이어 분당 job과 grouped/H 구간을 관찰한다. fsync 의미/20k 경계/압축은 그대로인지 확인한다.
8. BTC context/manual timers stop과 safe daemons는 **별도 승인 작업**이다. I timer는 disabled 유지.

최소 후보 SHA256:

```text
segments.py 2346e39071ac6164b1f746894603935f67d9f404e5cd83d30cc92d4e893fb85a
api.py      fe9f88a661f09272910aab99993fd2630269b3f76114cf2669ec53adeea4e0ae
```

## U. Rollback

승인 후 적용 때 smoke가 실패하면 보존한 source 두 파일만 이전 SHA로 복구하고 crypto-paper만
승인된 rollback restart한다. tape/ledger/checkpoint/segments는 삭제·재작성·역재생하지 않는다.
형식/경계/fsync를 바꾸지 않아 구버전 reader가 읽을 수 있다. stale 세션의 기록이 재개될 수는
있지만 이미 기록된 돈/체결을 되돌리는 rollback이 아니다. 다른 서비스/타이머는 그대로 둔다.

## V. Files changed

이번 tracked 수정:

- `backend/app/crypto/terminal/api.py`
- `backend/tests/crypto/test_hot_symbol_v1.py`
- `backend/tests/crypto/test_tape_rotation_count_v1.py`

이번 신규 문서/증거:

- 이 보고서, `benchmark.py`, `benchmark.json`
- `runtime_candidate.patch`, `source_hashes.json`, `runtime_source_manifest.json`
- `runtime_aggregates.json`, `public_fixture_hashes.json`, `tests_summary.json`, `SHA256SUMS`

기존 segments 정본은 재사용했고 repo 파일을 새로 수정하지 않았다. 다른 세션 파일의
stage/restore/reset/stash/checkout은 하지 않았다. 최초 감사 단계는 commit 직전 STOP하여 stage/commit 없이 마쳤다.
V2 전략 기능 추가, Strategy I/E/H/A semantics 변경, LIVE 주문 로직 변경은 없다.

초기 원문 거래 데이터 export 조회는 자동 승인 검토가 범위 과다/민감 거래 정보 위험으로
거절하여 실행되지 않았다. 이후 원문 없이 허용된 상태·시각·카운터 집계와 source/hash만
조회해 본 감사를 완료했다. 그 원문 조회에 대한 추가 승인을 요청할 필요는 남아 있지 않다.

## W. COMMIT ONLY final gate — 2026-10-22 KST

사용자가 이번 코드/테스트/문서만 명시적 staging 및 local commit하도록 승인했다.
시작 branch는 `main`, HEAD는 `6682429`, 로컬 `origin/main...HEAD`는 behind 0 / ahead 0,
기존 index는 비어 있었다. fetch/push와 운영 접근은 이번 단계에서 수행하지 않았다.

### Exact file inventory

아래 13개만 commit 대상이다. benchmark 도구도 IO 검증 범위의 CODE로 분류한다.
문서의 기존 운영 조회/중지 후보는 근거와 후속 승인 계획이며 실행 설정 파일이 아니다.

| 분류 | 파일 |
|---|---|
| CODE | `backend/app/crypto/terminal/api.py` |
| CODE | `docs/operations/crypto_paper_io_opt_v1/benchmark.py` |
| TEST | `backend/tests/crypto/test_hot_symbol_v1.py` |
| TEST | `backend/tests/crypto/test_tape_rotation_count_v1.py` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/CRYPTO_PAPER_IO_OPT_V1_REPORT.md` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/benchmark.json` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/public_fixture_hashes.json` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/runtime_aggregates.json` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/runtime_candidate.patch` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/runtime_source_manifest.json` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/source_hashes.json` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/tests_summary.json` |
| DOC | `docs/operations/crypto_paper_io_opt_v1/SHA256SUMS` |

`segments.py`는 이미 main에 있는 `7043004` 정본과 같으므로 신규 변경/staging 대상이 아니다.
`runtime_candidate.patch`는 운영 원본 대비 두 파일의 최소 후보를 문서로 보존한 것이며,
운영에서 적용하지 않았다. Strategy I/E/H/A source/test, 다른 세션 WIP, raw runtime data,
credential/secret, tape/data artifact, 서버 전용 실행·설정 파일은 대상에 없다.

### Diff gate

실제 runtime source 변경은 `retire_dynamic` import와 첫 await 이전 호출뿐이다.
기존 열린 포지션 backstop 뒤에서 admission을 취소하므로 열린 HOT 포지션은 계속 관측·조회된다.
기존 마지막 discard를 앞당겨 await 중 미거래 세션 재생성과 거래 이력 세션 부활을 차단한다.
BTC/ETH/SOL 기본 whitelist와 현재 HOT 선정/admission 알고리즘은 수정하지 않았다.
LIVE Binance position/order/client, 주문/leverage/account/position/fee/funding/auto close/
LIVE ARM 계산·실행 코드도 변경하지 않았다. wallet membership 및 retired 이력 보존은 기존 경로다.

Tape event bytes/seq/append 순서, 20,000-record segment boundary, recording interval,
write/flush/fsync와 rotate/checkpoint/manifest 계약은 기존 코드 그대로다.
신규 plain/gzip strict parity 2건은 tape·segment·manifest·checkpoint·ledger 원시 bytes 및
동일 cut boundary를 검증한다. chart/history/15s candle과 기본 심볼 회귀도 아래 suite에 포함된다.
기존 crypto 최적화의 검증·commit 범위이며 V2 전략 기능은 추가하지 않았다.

staged whitespace 검사는 patch 이외 전부 PASS다. `runtime_candidate.patch`의 빈 context
행 9개는 unified diff 형식상 필요한 단일 공백이며 보존했다. 해당 파일은 blank-at-eol만
제외한 검사와 격리 원본 대상 `git apply --check`/apply를 통과했고 결과 두 SHA가 후보와 일치한다.

### Final regression

공용 dirty tree를 사용하지 않고 새 격리 트리를 HEAD archive로 구성했다.
위 tracked 후보 3개를 그대로 복사하고 운영 버전 chart 파일 2개와 공개 reference fixture 5개를
기존 SHA로 검증해 넣었다. 운영 source manifest 143개와 비교한 차이는 `segments.py`, `api.py`
두 개뿐이다. 기존과 같은 runtime suite 선택과 chart-cache-expiry 2건 제외를 유지했다.
테스트 전용 10ms selector wakeup 및 30초/test watchdog도 기존 harness 그대로다.

```text
cwd: /tmp/usb-io-opt-commit-20261022/runtime
PYTHONPATH=backend /home/tjd618/usb/.venv/bin/python /tmp/usb-io-opt-commit-20261022/runtime_tests.py
1047 passed / 23 skipped / 0 failed — 52.16s
```

로그: `/tmp/usb-io-opt-commit-20261022/regression.log`; JUnit: 같은 디렉터리의 `tests.xml`.
Skip 23개 node ID는 최초 최종 로그와 `tests_summary.json`의 목록에 정확히 일치한다.
기존과 같은 테스트 소스/skip 조건이며 실제 이유는 아래와 같다.

- D2 historical/kline_1m 데이터 없음: 13건.
- captured fee verification record 없음: 9건.
- paper run 미설정으로 display rate 없음: 1건.

HOT 56 passed, rotation 29 passed를 포함한다. 새 socket close race/resurrection 방지 2건,
열린 SHORT retirement 거부 1건, plain/gzip tape·segment strict parity 2건 모두 PASS다.
전체 crypto 연구 suite의 과거 실패를 PASS로 바꾸어 해석하지 않는다(Q절 참조).

### Final isolated benchmark

같은 격리 트리의 최종 `segments.py`로 `PYTHONPATH=backend`와 project Python을 사용해
`benchmark.py`를 다시 실행했다. 원본 측정은 유지하고 `benchmark.json.commit_only_rerun`에
최종 결과를 추가했다. 각 크기/모드의 논리 read bytes는 최초 측정과 정확히 같다.

| 초기 lines | old → opt logical read bytes | 감소 |
|---:|---:|---:|
| 1,000 | 9,261,360 → 39,840 | 99.569826% |
| 10,000 | 91,341,360 → 39,840 | 99.956383% |
| 50,000 | 456,141,360 → 39,840 | 99.991266% |
| 100,000 | 912,141,360 → 39,840 | 99.995632% |

각 실행 write 30회 / 9,120 bytes / fsync 30회 동일. optimized steady pread 150회,
full scans baseline 31 / optimized 1이다. tape/segment equality는 위 strict parity로 별도 증명한다.
kernel 전체 syscall·physical bytes는 여전히 UNKNOWN이며 논리 감소율만 판정한다.

### Operational changes remain separate

BTC Context Strip, structure/flow/manual-research timers, multipathd/rpcbind 등 daemon 중지는
이 commit에 섞지 않는다. 이후 production deployment 승인 단계에서 별도로 판단·적용한다.
Q123 / crypto-context는 유지한다. DEPLOY / SERVICE STOP / SERVICE RESTART / TIMER CHANGE /
PUSH = NO. 이번 단계는 local commit gate만 PASS다.
