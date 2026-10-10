# Production Context Collector V1.1 보고서

    identity: ctx-collector.v1.1   contract: CONTRACT_CTX_V1_1.md (sha256 a9f8becf...)
    branch:   research/crypto-context-collector-v1-1   base: origin/main 5722f96
    scope:    BTCUSDT 전용, 로컬 격리, 배포 0 / push 0 / merge 0

V1에서 확인된 결함 세 가지(재시작 워밍업의 LIVE/NONE, 표시 순위 탈락을 소멸로 읽는 문제, 상태파일
쓰기 증폭)를 고치고, V0를 V1.5 정본으로 맞춘 뒤 46분 실스트림과 강제 시나리오로 검증했다.
판정은 **C. READY_FOR_PRODUCTION_READ_ONLY_DEPLOY**이고, 전제조건과 적용 절차는 S절이다.
배포는 하지 않았다.

---

## A. 격리

| 항목 | 값 |
|---|---|
| 복원 | `~/usb-crypto-context-collector-v1-evidence`의 번들에서 `bbaad9e`를 새 clone으로 가져와 `5722f96`(현재 origin/main) 위로 cherry-pick. 결과 트리는 `bbaad9e`와 바이트 동일 |
| 원 repo | HEAD `5058f41`, origin/main `5722f96`, staged 0, stash 0, worktree 5개(변화 없음). 쓰기 / staging / commit 0 |
| 원 repo dirty와 교집합 | **V1.5 파일 14개뿐**이며 의도된 것(B절). 14 / 14 모두 원 repo 작업트리와 바이트 동일, 그래서 소유 세션이 V1.5를 커밋하면 이 커밋은 내용 없이 합쳐진다 |
| A/H 관련 경로 | 교집합 0. 변경 경로에 strategy_a / strategy_h / mover / services / api / main.py 없음 |
| 운영 | ssh / scp / systemctl / nginx 0. Binance 공개 시세만, 키 0 |

---

## B. V1.5 정본 동기화

V1.5는 아직 원 repo의 미커밋 변경이다(파일 최종 수정 2026-10-04, 6일간 불변). 값 로직을 새로 만들지
않고 그 파일들을 **그대로** 복사해 별도 커밋(`c2c1090`)으로 고정했다. 출처 hash는 커밋 메시지와
evidence의 `v15_source/SOURCE_SHA256_MTIME.txt`에 있다(collector.py `f7283837...`, checkpoint.py
`7795f10b...`, continuity.py `c029b0a8...`, view.py `a35e4012...`, WALL_CONTINUITY_V1_2.md
`c9d9659c...` = 기록된 `lm-continuity.v5`).

| 항목 | V1.4 (origin/main) | V1.5 | Collector V1 영향 |
|---|---|---|---|
| request ownership | 응답에 소유자 없음. `on_snapshot`은 "refresh 중인가"만 확인 | `SnapshotRequest`(id `snapshot-N`, purpose SOFT_REFRESH / HARD_RECOVERY / INITIAL_SYNC, state ACTIVE / APPLIED / ABORTED / EXPIRED), 러너가 id를 왕복 | V1은 V1.4 위라 REST가 1초를 넘으면 141k id 롤백에 노출. V1.1은 서브클래스라 코드 변경 없이 상속 |
| late snapshot discard | 데드라인으로 포기한 시도의 늦은 응답이 복구 경로로 승격돼 건강한 북에 설치 | DISCARDED_ABORTED / EXPIRED / WRONG_OWNER, 북 불변, `snapshot_discarded` telemetry | raw snapshot은 원래 버림. telemetry는 저장. 재생기는 기록된 `snapshot_request_id`를 넘겨 판정을 재현 |
| coverage refresh floor | coverage-edge 300초 쿨다운 | 트리거별 최소간격: coverage 10초, safety 300초. `refresh_ineffective` | SOFT 전이가 늘 수 있음. SOFT는 워밍업 게이트를 닫지 않으므로 벽 표시는 끊기지 않음 |
| continuity classification | `lm-continuity.v4` | `v5`: S1~S5 게이트·300 ms·면제조건 불변, S4 문구 교정, 소유권 추가 | 뷰어 라벨만 바뀜. carry 판정 동일 |
| state schema | `ms-v0-state.v1-2` | `v1-3`: resnapshot 절에서 `coverage_cooldown_s` 삭제, 트리거별 floor와 소유권 필드 추가 | 뷰어(V1.5 checkpoint)가 v1-3을 읽음. MC V1 복사본은 `resnapshot.generation`만 읽어 영향 없음. 엔진은 `continuity.last_transition`만 읽음(유지) |
| wall carry | v4 규칙 | 변경 없음(walls.py 미변경) | 없음 |
| refresh telemetry | refresh_rejected / applied / storm | + snapshot_discarded, refresh_ineffective, 소유권 필드 | 저장됨. 바이트 영향 미미 |

검증: V1.5의 request-ownership 테스트 19개 포함 MS V0 + LM 스위트 통과. 늦은 스냅샷을 context
수집기에 직접 넣는 테스트에서 북(generation, last_update_id, 호가)과 context 판독이 바뀌지 않음.
V0 `Runner._sampler`는 V1.4와 V1.5가 같아 V1의 러너 덮어쓰기가 그대로 유효함을 확인했다.

---

## C. 워밍업 수정

결과 보기 전에 `CONTRACT_CTX_V1_1.md` 1절로 동결했다. 새 threshold 없음, `lm-wall.v2` R4의
`MIN_PERSISTENCE_MS`(10,000)만 쓴다.

* `observation_since_ms` = 세션 시작 또는 관측 단절(북이 SYNCED가 아닌 표본, 새 HARD 전이) 뒤
  첫 SYNCED 표본의 시각. SOFT 전이는 단절이 아니다.
* 게이트는 `sample_ms - observation_since_ms >= 10,000`일 때 열린다. 첫 표본부터 있던 벽이 R4를
  만족하는 바로 그 순간이라, 열린 뒤의 `NONE`은 "관측된 구간 내내 자격 있는 벽이 없음"이다.
* 닫혀 있는 동안: LIQUIDITY LIVE / PARTIAL은 `UNKNOWN`, 벽 `NONE`은 `UNKNOWN`
  (사유 `WALL_PERSISTENCE_WARMUP`), `warmup` 블록 공개, `collector.state = SYNCING`. 이는
  `mc-display.v1` 4절("데이터가 모자란 레이어는 UNKNOWN") 그대로다. FLOW는 패널 원래 payload로
  계산하고 게이트를 걸지 않는다.

| 측정 | 결과 |
|---|---|
| 46분 실측: 패널이 직접 냈을 LIVE + NONE (게이트가 막은 것) | 9 표본 |
| 46분 실측: 게시된 false LIVE / NONE | **0** (API 폴링 1,381회 중 0) |
| 강제 시나리오 330초: 막은 것 / 게시된 false | 33 / **0** |
| 테스트 | t=0, t<R4, t=R4(그 순간 OK로 전환), 벽 없는 실제 NONE, 워밍업 중 stale, 워밍업 중 재접속, HARD resync 후 재워밍업 |

알려진 보수적 동작: 콜드 스타트 첫 표본은 세션 기록 flush 전이라 관측 시작이 1표본 늦게 잡혀,
게이트가 1초 늦게 열린다(그 1초는 벽 OK, 레이어 UNKNOWN). false LIVE 방향이 아니다.

---

## D. 벽 종료 분류

`CONTRACT_CTX_V1_1.md` 2절로 결과 전에 동결. 같은 표본의 **전체** `lm-wall.v2` 선택(뷰어의
`side_walls`, 표시 필터 0, 한도 없음)과 북으로 판정한다.

| 분류 | 조건 (이 순서) |
|---|---|
| UNKNOWN | MC V1이 이미 UNKNOWN이라 했거나, mid / 관측 구간이 없음 |
| RANK_EVICTED | 그 bin이 이 표본의 전체 선택에 아직 있음. 사유 `BELOW_DISPLAY_FILTER` / `BEYOND_DISPLAY_LIMIT` |
| OUT_OF_COVERAGE | 선택에 없고, bin이 관측 구간 밖이거나 mid에서 V0 후보 밴드(1%) 밖 |
| TRUE_ENDED | 그 밖: 관측되는 북 안에서 더는 자격이 없음 |

`vanished`에는 TRUE_ENDED와 UNKNOWN만 남고 MC V1 판정(CANCEL_LIKE / CONSUMED_CANDIDATE /
UNKNOWN)을 그대로 가진다. RANK_EVICTED와 OUT_OF_COVERAGE는 `display_exits`로 가고, V1이었으면
냈을 판정은 감사용 `mc_v1_raw_state`로만 남는다. MC V1의 어휘와 CANCEL_LIKE 정의는 바꾸지 않았다.

| 데이터 | 이벤트 | TRUE_ENDED | RANK_EVICTED | OUT_OF_COVERAGE | UNKNOWN |
|---|---|---|---|---|---|
| **V1 실측의 1,229건 재분류** (V1 저널 그대로) | 1,229 | 469 (CANCEL_LIKE 468, CONSUMED_CANDIDATE 1) | **741** (한도 411, 필터 330) | 12 | 7 |
| V1.1 실측 46분 (운영 수집기) | 948 | 365 | 569 (필터 334, 한도 235) | 9 | 5 |
| 같은 46분 녹화의 재생 | 975 | 376 | 584 | 10 | 5 |

V1의 1,229건 재분류에서 막대 분류(membership)는 정확하다. 다만 필터 / 한도 하위사유는 V1 `wall_v2`가
전이 사이 notional을 갱신하지 않아 근사다. V1.1 실측에서 RANK_EVICTED는 이제 한 건도 CANCEL_LIKE로
게시되지 않는다. 테스트에서 MC 원본 tracker 합계는 그대로이고(`totals`, 원시값으로 표시), 분류
합계는 `classified_totals`에 따로 있다.

---

## E. Absorption 한계 (수정 안 함)

규칙, 입력, threshold 모두 그대로. 46분 실측 ABSORPTION_CANDIDATE **1,454 / 2,759 표본(52.7%)**
(V1: 48.5%). LONG / SHORT와 연결 없음. 별도 연구 대상인 Known Limitation으로만 기록한다.

---

## F. tmpfs 상태 캐시

`--state-cache-dir`(또는 `CTX_V1_STATE_CACHE`)를 주면 `<root>/state`가 휘발성 디렉터리의
`ctx-state-<root hash>`를 가리키는 링크가 된다. 뷰어 / 패널 / API는 경로를 바꾸지 않는다. 실제
디렉터리가 있으면 `state.disk-<ms>`로 옮기고 지우지 않는다. 매 쓰기 전에 대상이 없으면 다시 만든다.
정본 저널은 디스크에 남는다.

| 시나리오 | 결과 |
|---|---|
| A. 정상 재시작 | SYNCING -> 약 10초 뒤 LIQUIDITY LIVE |
| B. 실행 중 캐시 삭제 (t=280 s) | 같은 초 안에 다시 써져 API 폴링에 변화 없음. 단위 테스트에서 재생성 1회, 판독 LIVE 유지 |
| C. kill -9 + 캐시 디렉터리 전체 삭제 (재부팅 모사) | API `UNKNOWN / NO_CONTEXT_FILE` 1폴링 -> 재시작이 고아 `.open` 5개 봉인(189행 보존, 0바이트 손실), 캐시 재생성, SYNCING -> LIVE |
| D. 캐시 손상 (t=300 s) | 다음 초에 덮어써져 변화 없음. 테스트에서 손상 파일 즉시 교체 |
| E. 저널 무결성 | 캐시 삭제 / 손상 전후 `reader.seconds()` 결과 동일(테스트). 재생 패리티(J, K절)는 저널만 사용 |
| 두 번째 writer | exit 3 `WRITER_LOCK_HELD` |

**디스크 쓰기 (같은 46분, `/proc/<pid>/io` write_bytes)**

| 프로세스 | 영구 디스크 쓰기 / day | 논리 쓰기 / day |
|---|---|---|
| 연구 수집기 | 19,281 MB | 18,888 MB |
| V1.1, 캐시 디스크 (V1과 같은 배치) | 18,050 MB | 17,592 MB |
| **V1.1, 캐시 tmpfs** | **760.5 MB** | 17,574 MB (대부분 RAM) |

상태파일 쓰기 증폭이 영구 디스크에서 사라졌다(**23.7배 감소**). 남은 760 MB/day는 저널의 압축 전
평문 시간 파일, 봉인 시 gzip 출력, fsync / 파일시스템 메타데이터다. 논리 상태 갱신은 하루 약
18.4 GB(상태파일 190 KB + latest 23 KB, 초당 1회)로 tmpfs에서 일어난다.

---

## G. 저장 스키마

V1 구조 유지. 영구: `context`(1초), `wall_v2`, `wall_r0`, `session` / `telemetry`(gap / resync /
reconnect / snapshot_discarded 포함) / `storage_stats`. 휘발: 상태파일 2개. raw depth / trade 저장 없음.

추가 필드: `context.warmup`, `context.display_exits[]`, `vanished[].end_class / end_reason`,
`flow.vanished.classified_totals`, 세션 config의 `wall_roles`, `contract_sha256`, `state_cache`.
바이트 영향: context 행 3,940 -> 4,120 B(+4.4%, 압축 전 +14.8 MB/day), 디스크 +0.3 MB/day.

벽 역할(`CONTRACT_CTX_V1_1.md` 4절): **PRIMARY = `wall_r0`(OPENED 행 값, R0 재현)**,
SECONDARY_DIAGNOSTIC = `wall_v2`(현재 값). reader는 `walls_primary` / `walls_secondary_current`로 노출.
PRIMARY 변경 없음.

---

## H. API

GET 3개(`/health`, `/snapshot`, `/status`). POST / PUT / PATCH / DELETE 405(테스트). `/status`에
`state_cache`(링크 여부, 대상, 존재) 추가. 워밍업은 같은 payload에서 LIQUIDITY `UNKNOWN`, 벽
`UNKNOWN`, `collector.state = SYNCING`으로 일관되게 보인다. 재시작 시 API 기록:
`SYNCING (FLOW_WINDOW_WARMUP, WALL_PERSISTENCE_WARMUP)` -> 약 10초 뒤 LIQUIDITY LIVE ->
60초 뒤 FLOW LIVE(F절 A).

---

## I. 단기 실측 (46분 + 강제 시나리오)

2026-10-10 19:27:58~20:14 KST, 연구 수집기(V1.5) / V1.1(tmpfs) / V1.1(디스크 캐시)를 독립 연결로
동시에. 자연 사건: gap 0, reconnect 0, resync 2(시작 + SOFT 1), trade stale 0.

| 항목 | V1.1 tmpfs | 비고 |
|---|---|---|
| 디스크 점유 bytes / hour | 2.30 MB | |
| projected MB / day (디스크 점유) | **55.3** | 연구 3,905 (70.7배) |
| 영구 디스크 쓰기 / day | 760.5 MB | 디스크 캐시 18,050 |
| RSS | 41.4~43.8 MB | |
| CPU 평균 / p95 / 최대 | 4.04 / 5.5 / 8.5% | 연구 3.68% |
| queue 최대 | 645 / 8,192 | dropped 0 |
| API `/snapshot` p50 / p95 / p99 | 3.42 / 4.73 / 5.42 ms | 오류 0 / 1,381 |
| freshness p95 / p99 | 1,009 / 1,023 ms | 실행 중 표본 간격 최대 1,010 ms. 1.1초 넘는 폴링은 전부 종료 단계(STALE이 정답) |
| false LIVE / NONE | **0** | 게이트가 막은 것 9 |
| RANK_EVICTED / TRUE_ENDED | 569 / 365 | |

강제 시나리오(실 Binance, 별도 루트, 330초 + 재시작 단계): 프레임 누락 gap -> HARD resync -> SYNCING
10초 -> LIVE / depth 재접속(종료 핸드셰이크 동안 V0 stale -> REST resync 반복, 내내 SYNCING) /
중복 trade 무시 / trade 재접속 -> FLOW UNKNOWN -> PARTIAL -> 60초 뒤 LIVE / 캐시 삭제·손상 무영향 /
정상 재시작 / kill -9 / 캐시 전체 삭제 / 두 번째 writer 거부. false LIVE/NONE 0.

---

## J. Wall / Flow 패리티 (V1 대비 회귀)

46분 V1.5 녹화를 seq 순서로 재생(기록된 `snapshot_request_id` 전달).

| 검사 | 결과 |
|---|---|
| 재생기 충실도: derived / seq / wall 행 | 2,759 / 2,759, 2,759 / 2,759, 73,981 / 73,981 |
| A. Wall V2: context 읽기 = 실제 뷰어 라우트 | 2,758 / 2,759 |
| B. Flow (5 / 15 / 60초 창, 상태) = 원본 MC V1 (vanished 외) | 2,758 / 2,759 |
| C. MC V1 LIQUIDITY = 원본에 게이트 적용 | 2,758 / 2,759 (게이트로 라벨이 바뀐 11표본 포함) |
| C. 소멸 분할 완전성 (원본 this_reading = vanished + display_exits) | 2,759 / 2,759 |
| V1 엔진(bbaad9e) vs V1.1 엔진, 같은 루트 같은 순간 | book / depth / flow / absorption / open bins 2,758 / 2,759 동일, liquidity는 게이트 차이 외 동일, vanished는 분할 외 동일 |

어긋난 1건은 모두 첫 표본이다(세션 flush 전에 읽음, V1과 같은 읽기 시점 차이). **값이 달라진
경우는 0이다.** 의도된 차이는 게이트 라벨 11표본과 소멸 분할뿐이다.

---

## K. Q1~Q3 패리티

같은 재생에서 R0의 `journal.replay()`와 reader의 `walls_primary`를 비교했다.

| 검사 | 결과 |
|---|---|
| 측면-초 V2 벽 목록(dict 전체) | 5,516 / 5,516 동일 |
| T2 에피소드 | 240 = 240, 동일 |
| G1~G6 행(판정·제외 사유) | 6 / 6 동일 (G1 61, G2 0, G3 17, G4 18, G5 1, G6 14 발화) |

Q1(저항 + ask 벽 유지 / 보충 + 매수 공격 + 진행 실패), Q2(돌파 + ask 소비 + 매수 공격 + 얇은 위쪽),
Q3(지지 대칭)에 필요한 필드(초별 벽 존재와 bin, 60초 flow imbalance / coverage, mid 경로, ±0.1% 밴드
양측, book 상태 / generation)가 모두 있다. 구조 레벨(T1)은 공개 klines로 만든다. 빠진 필드 없음.

---

## L. MB/day

| | V1 | V1.1 |
|---|---|---|
| 디스크 점유 / day | 58.4 MB | 55.3 MB (같은 시간대 연구 3,905 MB, 70.7배) |
| 직렬화 / day (압축 전) | 638.5 MB | 629.5 MB |
| 영구 디스크 쓰기 / day | 측정 안 함(캐시 디스크) | 760.5 MB (캐시 디스크면 18,050) |
| 논리 상태 갱신 / day | | 약 18.4 GB, tmpfs |

보존기간은 정하지 않았다. 예상 점유: **7일 387 MB, 14일 774 MB**(진행 중 평문 한 시간분 최대 약
24 MB 별도). 운영 디스크 여유 1.1 GB 기준으로 14일은 70%다.

---

## M. RSS / CPU

| | 연구 | V1.1 tmpfs | V1.1 디스크 캐시 | API |
|---|---|---|---|---|
| RSS | 39.9~43.1 MB | 41.4~43.8 MB | 41.2~44.1 MB | 48.9~49.1 MB |
| CPU 평균 / p95 / 최대 | 3.68 / 5.0 / 9.0% | 4.04 / 5.5 / 8.5% | 4.04 / 5.5 / 9.5% | 0.41 / 0.5 / 1.0% |

context 단계 평균 6.5 ms / 표본. tmpfs는 CPU에 차이를 만들지 않았다.

---

## N. BTC-only 증명

* CLI `--symbol`: BTCUSDT가 아니면 exit 2 `REFUSED / UNSUPPORTED_SYMBOL`, 루트 생성 전(테스트:
  ETHUSDT, SOLUSDT, btcusdt, BTCUSD, 빈 문자열).
* `build()`: `UnsupportedSymbol`, 잠금 / 소켓 / 파일 전.
* 런타임: 읽은 상태파일이 다른 심볼을 말하면 `UNKNOWN / STATE_SYMBOL_IS_NOT_BTCUSDT`, 벽 미게시.
* API: ETH / SOL은 `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY`, 루트를 열지 않음.
* V0 스트림 URL 3개는 `btcusdt` 고정. 화면의 ETH / SOL 수동매매와는 import / 파일 / 프로세스 공유 0.

---

## O. Safety / Import graph

* AST: 패키지가 `app.crypto.market_structure_v0`, `app.crypto.liquidity_map`(api 제외) 외 `app.*`를
  import하지 않음.
* 새 인터프리터에서 수집기와 API를 각각 적재: 적재된 `app.*` 모듈 이름 어느 마디에도 order / account /
  leverage / arm / auto / position / live / paper / terminal / broker / execution 없음. 수집기 프로세스에
  fastapi / starlette 없음. API 프로세스에 websockets 없음.
* 환경변수는 `CTX_V1_ROOT`, `CTX_V1_STATE_CACHE` 두 개뿐. URL / 자격증명 / 방향 어휘 없음.
* API order route 0.

---

## P. 테스트

신규 `test_ctx_v1_1.py` 25개 포함 context 수집기 133 passed, 1 skipped(원본 MC V1 패키지 부재 시 직접 비교, 설계상). MS V0 + LM(V1.5 포함)과 합쳐 641 passed.
`tests/crypto` 전체 2,132 passed / 21 failed이고 21건은 V1 때와 **같은 ID**(기준 commit에서도 실패하는
freeze 파일 / multisymbol / expert_e1 / leverage / paper_feed_seed). 회귀 0.

요구 목록 대응: V1.5 sync, restart warmup, false LIVE/NONE, TRUE_ENDED, RANK_EVICTED(한도 / 필터),
OUT_OF_COVERAGE, UNKNOWN, cancel-like 격리, tmpfs 손실 / 손상 / 이동, kill -9(+캐시 손실), reconnect,
gap, duplicate trade, wall / flow parity, Q1~Q3(reader), BTC-only, GET-only, writer collision, disk full,
corrupted state.

---

## Q. 커밋 / 번들

격리 clone `research/crypto-context-collector-v1-1` (base `5722f96`):

    7298a9e research(crypto): prototype lightweight context collector   (V1 bbaad9e 복원)
    c2c1090 sync(crypto): take liquidity map V1.5 (lm-continuity.v5) from its uncommitted source
    434a714 docs(crypto): freeze context collector V1.1 contract before measurement
    f6c24a0 research(crypto): harden lightweight context collector
    (+ 이 보고서 커밋)

번들과 evidence: `~/usb-crypto-context-collector-v1-evidence/v1_1/`. push 0, merge 0.

---

## R. Production Readiness

판정: **C. READY_FOR_PRODUCTION_READ_ONLY_DEPLOY** (배포하지 않음).

| C 조건 | 결과 |
|---|---|
| V1.5 parity | 충족: V0가 V1.5 정본, 재생 동일 |
| false LIVE / NONE = 0 | 충족: 실측 0, 강제 0, 테스트 |
| rank eviction 분리 | 충족 |
| tmpfs state loss recoverable | 충족: 삭제 / 손상 / kill -9 + 전체 삭제 |
| Q1~Q3 재현 유지 | 충족: R0 T2 / G1~G6 동일 |
| BTC only | 충족 |
| projected storage acceptable | 55 MB/day, 14일 774 MB. 보존기간은 결정 필요 |
| mutation / order import 0 | 충족 |
| tests PASS | 충족: 신규 전부, 실패 21건은 기존 |

남는 한계(차단 아님): ABSORPTION_CANDIDATE 52.7%(E절), 콜드 스타트 게이트 1초 지연(C절), depth 소켓
종료 핸드셰이크 동안의 V0 REST resync 반복(SYNCING으로 표시), 자연 gap / reconnect는 46분 동안 0회라
강제 시나리오로만 확인.

---

## S. 정확한 운영 적용 계획 (실행하지 않음)

**선행 결정 / 조건**

1. 보존기간 N일 (7일 387 MB, 14일 774 MB).
2. V1.5 처리: 소유 세션이 원 repo에 V1.5를 커밋하는 것이 우선. 그 뒤 이 브랜치의 `c2c1090`은 내용
   없이 합쳐진다. 이 브랜치가 먼저 들어가면 V1.5가 함께 운영에 올라가므로 그 세션과 합의 필요.
3. MC V1 패널 소비는 별도 작업(운영 라우트 슬롯, 프리뷰 sentinel 제거). 이 계획은 수집기와 API만.

**절차**

1. 코드: `research/crypto-context-collector-v1-1`을 main에 병합(신규 파일 + V1.5 동일 내용), 서버
   venv에서 `tests/crypto -k "ctx_v1 or ms_v0 or liquidity_map"` 통과 확인.
2. 경로: 저널 루트 `/root/usb-data/ctx_v1`(영구 디스크), 캐시는 systemd `RuntimeDirectory=usb-ctx-v1`
   (`/run/usb-ctx-v1`, 부팅마다 tmpfs로 재생성).
3. `usb-ctx-collector.service`:
   `ExecStart=<venv>/python -m app.crypto.context_collector_v1 collect --root /root/usb-data/ctx_v1
   --state-cache-dir /run/usb-ctx-v1 --symbol BTCUSDT --duration 0`,
   `Restart=on-failure`, `RestartSec=10`, `RestartPreventExitStatus=2 3`(심볼 거부, lock 보유),
   `MemoryMax=200M`, `CPUQuota=25%`, `Nice=10`, `IOSchedulingClass=idle`. 키 / 시크릿 환경변수 없음.
4. `usb-ctx-api.service`: `serve --root /root/usb-data/ctx_v1 --host 127.0.0.1 --port 8013`,
   `MemoryMax=150M`. 처음에는 nginx 미변경, localhost 전용.
5. 확인: 70초 안에 `/health` LIVE, `/status`의 `state_cache.volatile = true`, 디스크 증가 약 2.3 MB/h,
   `storage_stats` dropped 0, `ss -ltnp`에 127.0.0.1:8013만, 외부 연결은 fstream / fapi 공개 엔드포인트만.
6. 기존 트레이딩 서비스 영향 확인: 그 서비스들의 프로세스 / 포트 / 경로 미변경.
7. 롤백: 두 서비스 `systemctl stop` + `disable`, 유닛 파일 제거. 데이터는 남김. 트레이딩 서비스 무관.
8. 보존: N일보다 오래된 `.jsonl.gz` 정리(타이머 또는 수동, 자동 삭제는 결정 후).
