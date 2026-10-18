# A/E Common Producer: first paper canary V1 (2026-10-19 ET~)

상태: **READY_FOR_FIRST_PAPER_CANARY**. 구현과 테스트는 완료했다. 서버에는 이전 shadow 릴리스(3eea562)가 stage되어 있고
10/19 하루만 shadow로 arm된 상태다. 이번 릴리스로 `update`와 `arm`을 다시 해야 하며, 둘 다 승인 전에는 실행하지 않는다.

이 문서는 `AE_COMMON_PRODUCER_SHADOW_CUTOVER_V1.md`를 대체한다. 별도 shadow 세션은 두지 않는다(2026-10-18 사용자 결정).
10/19 ET부터 새 공통 producer로 A official paper와 E official paper를 직접 돌린다. 첫 세션이 정상이면 재배포 없이 다음 거래일에도 계속 돈다.

## 1. 실행 구조

| ET | 무엇 | 결과물 |
|---|---|---|
| 03:45 | producer 시작(`usb-ae-producer`, 새 트리). AppKey 1개, listing, A union, E artifact, E documented refusals 읽기 | |
| 04:00-09:15 | 공유 rolling(A union ∪ E-only). raw 수집은 1회 | |
| 09:15-09:25 | A sweep. 두 lane, hash 순서, tick lane이 A-only 종목을 가져감, 요청 단위 deadline | `snapshot_0915` 봉인 |
| 09:16~ | A consumer(`usb-a-snapshot-consumer`, EP 트리, Nice 10). 키움 0회. expected-processable 100%가 아니면 NO_SCAN. 기존 A 후처리 그대로 | ScannerRun → A official |
| 09:25:00 | E finalization(시계로만 시작) | `snapshot_0924` 봉인 |
| <09:30 | E 결정은 파일 기준. RVOL은 `mode=ro`. H5(RVOL ≥ 3.0) / R1 / Top3 / B2는 동결 규칙 그대로. execution gate 적용 | |
| 09:30-09:50 | gate 통과 시 E-RUNTIME-R1 `_paper_stage`. SimBroker, 09:30 시가 진입, **09:34 종가 청산**, 09:34 봉이 없으면 `UNRESOLVED_EXIT`(fallback 없음). gate 불허 시 장부에 NO_DECISION만 기록 | V2 official 장부 |

- **E 장부**: `/root/usb_runtime/strategy_e_paper/run/paper_state_v2/OFFICIAL_KIWOOM_PAPER/STRATEGY_E_MAX_V1/`
  - strategy_id는 `STRATEGY_E_MAX_V1`, execution은 `e_cost_v2_exact_0934_close`, initial_equity는 10000이다.
  - 아직 아무 런타임도 쓰지 않은 장부다. V0(`paper_state`), V1(`paper_state_v1`, PROVISIONAL 포함), invalid runtime 기록은 열지 않고 합산하지 않으며 삭제하지 않는다.
  - engine은 다른 execution 버전의 장부를 이어 쓰기를 거부한다(R1 테스트).
- **실주문 경로 없음**: producer의 chart lane은 읽기 전용 allowlist다. 진입·청산 가격은 별도 client(같은 AppKey, 3/s)로 받는다. `real_order_requests`를 기록한다.

## 2. 게이트와 전략별 fail-closed

- **A**: expected-processable 전 종목이 USABLE이어야 scan한다.
  - 영구 예외는 세 가지다: UNMAPPED, MARKET_DATA_UNAVAILABLE(출처 거부 + 그날 한 번도 받지 못함), E store DOCUMENTED_REFUSAL.
  - 그 밖의 종목이 하나라도 STALE / FAILED / INTERRUPTED / NOT_REACHED면 SCANNER_NOT_RUN이다.
- **E**: 다음 중 하나라도 해당하면 NO_DECISION이고 장부에는 기록만 한다.
  - 예상 밖 STALE이 1개 이상
  - seal이 09:30 이후
  - RVOL 예상 밖 누락
  - bootstrap 미완료
  - 스냅샷이나 유니버스가 없을 때는 장부에 쓰지 않는다.
- **전략별 보류**: 다른 전략은 계속 돈다.
  - `stage_release.sh hold-e`: E는 결정만 하고 장부에 쓰지 않는다.
  - `hold-a`: A consumer를 건너뛴다(A NO_SCAN, 다른 경로로 대체하지 않음).
  - 해제는 `release-e` / `release-a`.

## 3. 배포 절차 (각 단계는 승인 후 실행)

1. `update <release commit>`
   - 서버의 3eea562 트리와 unit을 `/root/usb_runtime/ae_common_producer/backups/<stamp>`로 옮긴다.
   - 새 트리(git archive)를 만들고 unit 5개를 교체한다. EP 파일 3개는 해시를 확인한다.
   - import, E 규칙 로드, unit 문법을 점검한다.
2. `arm 2026-10-19`
   - `ACTIVE_FROM`을 쓰고, 기존 shadow용 `ACTIVE_SESSION`은 지운다.
   - 10/19부터 producer와 A consumer가 매 거래일 돌고 usb-e-paper는 skip된다.
3. 10/19 세션 후 `verify 2026-10-19`로 A, E, COMMON 판정을 각각 낸다.
   - PASS면 아무것도 하지 않는다. 다음 거래일에도 그대로 돈다.
   - 한 전략이 FAIL이면 그 전략만 `hold-*`하고 원인을 보고한다.

## 4. 롤백

- **전체**: `disarm`(ACTIVE_FROM 삭제)을 하면 다음 시작부터 usb-e-paper(A-in-E, E decision-only)가 이전 그대로 돈다.
  `rollback`은 unit, drop-in, EP 파일 3개를 제거한다. 장부, 스냅샷, run 기록, producer 트리는 보존한다.
- **이전 릴리스 복귀**: `update` 백업 디렉터리의 `src`와 `units`를 되돌린다.
- **E V2 장부**: 삭제하지 않는다. 롤백 후에는 새 세션이 쌓이지 않을 뿐이다.

## 5. 알려진 제약

- **운영 대시보드는 V0/V1 장부만 읽는다**(배포된 readback이 R1 이전판). V2 official 장부는 R1의 USB API 배포 전까지 화면에 나오지 않는다.
  장부 파일과 `verify` 결과로 확인한다. API 배포는 usb-backend 재시작이 따르는 별도 승인 사항이다.
- **R1 `_paper_stage`의 반환 요약**에서 `book` 필드는 장부 이름이 아니라 시작 시점의 장부 dict다(변수 재사용). 실행에는 영향이 없고 `book_dir`는 정확하다.
  R1 코드는 바꾸지 않았다.
- **A sweep 실측 용량은 첫 세션에서 처음 측정된다**(계산상 여유 28~71초). 넘치면 그날 A는 NO_SCAN이다.
- **메모리**: 09:16~09:50에 producer(MemoryMax 350M)와 A consumer(300M)가 동시에 상주한다. `resources.json`과 consumer `peak_rss_mb`로 측정한다.
