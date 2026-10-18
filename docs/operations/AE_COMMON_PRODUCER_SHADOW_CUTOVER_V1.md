# A/E Common Producer: 1-session live shadow cutover V1

상태: **READY_FOR_ONE_SESSION_SHADOW_CUTOVER** (구현과 테스트는 완료, 운영 배포와 arm은 미실행)

전략 규칙은 바꾸지 않는다. A의 union, 09:15 컷, snapshot, scan, handoff는 운영 EP 트리에 이미 배포된 코드를 그대로 쓴다.
E의 cutoff, 유니버스, 피처, H5/R1/Top3/B2, execution gate는 E-RUNTIME-R1 코드다. 바뀌는 것은 키움 수집 계층 하나다.
그 수집을 한 프로세스가 맡고, 각 전략은 봉인된 스냅샷 파일만 읽는다.

## 1. 구조

| ET | producer (`usb-ae-producer`, 새 트리) | A consumer (`usb-a-snapshot-consumer`, EP 트리) |
|---|---|---|
| 03:45 | 시작. listing(usa10099), A union(`--a-repo` EP 트리의 `universe.build`), E artifact(identity 검사) | |
| 04:00-09:15 | 공유 rolling. minute lane 4.9/s, A union ∪ E-only. 대상 종목마다 1회 | |
| 09:15-09:25 | A sweep, 두 lane 사용. minute lane은 E-overlap 다음 A-only 순서(각각 sha256 순), tick lane은 E tick shard를 처리한 뒤 A-only를 뒤에서부터 가져감 | 09:16 기동, import와 attach 후 대기 |
| ≤09:25 | sweep이 끝난 시점의 캐시를 복사(freeze)하고, 별도 스레드에서 `snapshot_0915`를 봉인 | 봉인을 감지하면 검증 후 scan과 handoff 실행(Nice 10). 키움 0회 |
| 09:25:00 | E finalization 시작. 시계로만 시작하며 A 상태와 무관 | |
| ≤09:29:45 | `snapshot_0924` 봉인. E shadow 결정은 파일 기준으로 계산하고, parity용으로 라이브 캐시 기준으로도 계산. RVOL은 `mode=ro`로 읽음 | |
| 이후 | A parity: legacy(라이브 캐시, filter-at-read)와 파일을 비교하고 `a_parity_0915`를 봉인 | |

- **요청 단위 deadline**: `app/ae_common/deadline.py`. 매 HTTP 시도 직전의 `acquire()`에서 pacing 대기가 끝난 뒤 시계를 다시 읽는다.
  그 시점이 deadline 이후면 요청을 보내지 않는다. phase별 deadline은 rolling 09:15:00, sweep 09:25:00, E와 그 이후 09:29:45다.
  phase마다 `last_sent_before_deadline`을 기록해 run record에 남긴다.
- **불변 스냅샷**: `app/ae_common/snapshot.py`.
  - canonical JSON을 gzip(mtime 0)으로 쓰고, sha256을 manifest에 남긴다.
  - 임시 파일에서 최종 이름으로 hardlink하므로, 같은 이름이 있으면 실패하고 덮어쓰지 않는다. 파일 권한은 0444다.
  - 0915에는 09:15 이전 분봉만, 0924에는 09:24까지의 분봉만 들어 있다.
- **A consumer**: `app/ae_common/a_consumer.py`. `integration.run_cut`에서 `run_a_pass`를 호출한 뒤의 부분과 같은 순서를 그대로 따른다.
  `tests/ae_common/test_a_consumer.py`가 HEAD판과 운영판 `run_cut` 양쪽과 일치함을 고정한다.
  운영 트리에는 새 파일 3개만 추가하고 기존 파일은 교체하지 않는다.
- **fail-closed (expected-processable 100%)**: A는 **expected-processable universe**의 모든 종목이 USABLE일 때만 scan한다.
  expected-processable = A union에서 아래 문서화된 영구 예외를 뺀 집합이다(`snapshot.PERMANENT_EXCEPTIONS`).

  | 예외 | 판정 |
  |---|---|
  | `UNMAPPED` | 키움 listing(usa10099)에 코드가 없다. 04:00 전에 확정된다 |
  | `MARKET_DATA_UNAVAILABLE` | E-RT2.1 계약 상태. 키움이 chart 쿼리를 **거부**했고(return_code≠0, 코드 MARKET_DATA_UNAVAILABLE/INVALID_SYMBOL), 그날 아침 그 종목의 페이지가 한 번도 성공하지 않았다. 통신 장애, 5xx, 비정상 응답은 코드가 같아도 해당하지 않는다(`sweep.error_label`, client 문자열 drift는 테스트로 고정) |
  | `DOCUMENTED_REFUSAL` | E RVOL 저장소에 영구 거부로 기록된 종목(E-RUNTIME-R1 `DOCUMENTED_REFUSALS`, 현재 PS). 04:00 전에 읽기 전용으로 읽고, 그날 받아지지 않은 경우에만 예외로 친다 |

  그 밖의 종목이 하나라도 STALE / FAILED / INTERRUPTED / NOT_REACHED이면 scan하지 않는다. 기록은 `SCANNER_NOT_RUN` / `COMMON_SNAPSHOT_INCOMPLETE`이고 후보 0, GPT 0이다.
  스냅샷이 없거나(09:40까지 대기), digest가 틀리거나, 세션이 다르거나, union/unmapped가 다른 경우도 같다. 부분 snapshot으로 A official scan을 하지 않는다.
  E는 스냅샷이나 유니버스가 없으면 NO_DECISION이다.

## 2. 실측 기반 용량 (capacity gate 2026-10-17)

- **rolling**: 5,041콜/사이클, 1,030초, 4.89 req/s. A-in-E 운영(10/05~10/16)에서 19사이클로 측정됐다.
- **sweep 계산**
  - 합집합(매핑된 것) 약 5,040종목을 처리해야 한다.
  - 처리 단가: minute lane 1.0콜/종목, tick lane 1.057페이지/종목(실측).
  - 소요 시간: 약 529초, T1 약 09:23:49로 여유 71초다. A-only의 tick 페이지를 1.5로 가정하면 572초, 여유 28초다.
  - 기존 코드는 tick worker가 약 330초를 놀아서 매일 RPGL~ZYME 849종목이 잘렸다.
- **E 확정**: 같은 조건의 7세션 실측에서 265~268초, T1 09:29:25~27이었다.

## 3. 배포 절차 (각 단계는 사용자 승인 후 실행)

전제: 릴리스 커밋이 있어야 한다. 지금은 커밋하지 않았다. `stage`는 `git archive <commit>`으로 clean source만 올린다.

1. `deploy/ae_common_shadow/stage_release.sh preflight` (읽기 전용)
   - 대상 파일이 없는지 확인
   - **배포된 A 코드가 테스트한 코드(`EP_A_CODE_TESTED.md5`)와 같은지 확인**
   - RVOL 저장소, `r1-paper-off.conf`, 디스크 150MB 이상
2. `stage <commit>`
   - producer 트리(`/root/usb_runtime/ae_common_producer/src`, 약 23MB) 생성.
     `backend/app`에 더해 `docs/backtest/strategy_e_candidate`와 `docs/backtest/strategy_e_max`(1.4MB)를 함께 올린다.
     E 결정이 동결 규칙을 `<tree>/docs`에서 읽기 때문에, 없으면 shadow 결정이 실패한다. overlay 테스트에서 이 실패를 재현해 확인했다.
     stage 직후 producer 트리에서 import와 `load_rules()` 스모크를 실행한다.
   - EP 트리에 새 파일 3개 추가
   - unit 5개 설치, timer enable
   - `ACTIVE_SESSION`이 없으므로 모든 unit이 skip되고 usb-e-paper는 그대로 돈다.
3. `arm <YYYY-MM-DD>`: 해당 세션 03:45 ET 전(DST 기준 16:45 KST 전)에 실행한다.
   - 그날만 e-paper가 skip되고 producer와 consumer가 돈다.
   - 다음 날은 flag가 남아 있어도 날짜가 달라 자동으로 e-paper로 돌아간다.
4. 세션 종료 후 `verify <YYYY-MM-DD>`: 읽기 전용으로 `run/<D>/live_gate.json`을 쓴다.
5. `disarm`. 결과와 무관하게 실행하며, 1세션 한정을 명시적으로 닫는 단계다.
6. `rollback`: unit, drop-in, EP 파일 3개를 제거한다. producer 트리, 스냅샷, run 기록은 증거로 보존한다.

서비스 재시작은 없다. usb-backend는 건드리지 않는다. E paper는 계속 OFF다(`r1-paper-off.conf` 유지, producer에 paper 코드 경로 없음).

## 4. Live gate (`app/dev/verify_ae_shadow.py`)

| 구분 | 항목 | 기준 |
|---|---|---|
| A | `A_no_request_at_or_after_0925` | sweep phase의 마지막 송신이 09:25:00 이전 |
| A | `A_full_coverage` | expected-processable 전 종목 USABLE (unexpected 0), union/unmapped 일치 |
| A | `A_snapshot_0915_parity` | legacy와 파일의 per-symbol snapshot row digest가 exact, 파일에 09:15 이후 봉 0 |
| A | `A_consumer_fed_identical_snapshots` | consumer가 scan에 넣은 row digest가 producer legacy와 exact |
| A | `A_selected_candidate_result` | 위 항목이 exact이고 scan이 실행됨(같은 입력을 같은 결정적 scan에 넣음) |
| A | `A_official_continuity` | handoff persisted, 해당 run_id의 `scanner_runs`가 COMPLETED |
| E | `E_T0_at_0925` | T0 지연 1초 미만 |
| E | `E_T1_before_0930`, `E_seal_before_0930` | T1과 seal이 모두 09:30 이전 |
| E | `E_unexpected_stale_zero`, `E_cutoff_violation_zero` | 예상 밖 stale 0(문서화된 예외 제외), cutoff 위반 0 |
| E | `E_snapshot_0924_parity` | 파일 기준과 라이브 캐시 기준이 상태 분류, 피처 행, 결정 필드 모두 일치 |
| E | `E_no_execution` | orders 0, SimBroker 미사용, book 미기록 |
| Shared | `S_single_app_key`, `S_duplicate_rolling_zero`, `S_429` | AppKey 1개, 중복 rolling 0, 429 = 0 |
| Shared | 기록 항목 | calls(phase별), retries, deadline 거부 수, RSS 피크(producer와 consumer), swap delta, iowait, PSI |

## 5. 남은 위험

- **A sweep의 실측 용량은 미검증이다.** 계산상 여유는 28~71초다. expected-processable에 미처리나 STALE이 하나라도 남으면
  **그날 A는 scan하지 않는다(후보 0)**. 사용자 지시대로이며, A official 하루치 공백이 될 수 있다.
  참고로 A-in-E 운영(10/05~10/16)의 `stale_or_failed`는 하루 1~14였다. 그중 PS 1건은 위 예외에 해당한다.
- **메모리**: 09:16~09:31에 producer(MemoryMax 350M)와 consumer(300M)가 동시에 상주한다.
  10/16 A-in-E 실측에서 09:30 ET 표본은 호스트 사용 637MB, iowait 45%였다. 이번에는 프로세스가 두 개라 파이썬 기본 메모리가 한 번 더 든다.
  resources.json으로 측정한다.
- **E UI**: 그날 E의 공식 run 디렉터리에 세션이 없다(shadow는 별도 경로). E paper는 원래 OFF라 장부 영향은 없다.
- **디스크**: 340MB 여유(97%). 추가분은 약 25MB다.
- **같은 워킹트리의 다른 세션 미커밋 변경**(`lane_share.py`, `test_e_runtime_r1_isolation.py`)은 이 릴리스에 포함하지 않는다.
  해당 테스트 2건은 현재 실패 상태다.
