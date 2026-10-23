# Strategy E Provider Error Taxonomy / Mapping Recovery

2026-10-23 KST. **READY_FOR_PRODUCTION_DEPLOYMENT — 운영 배포 승인 대기.**

CECO·IBOC의 판정은 **MAPPING_ERROR**다. 읽기 전용 probe에서 두 종목은 Kiwoom NA listing에 존재하고, NA의 두 chart API가 두 번 모두 정상 응답했다. 따라서 permanent source refusal로 추가하지 않았다. 10/22 원본 replay는 DAR/TRV/VRTX와 전략 계산 전체를 그대로 재현하지만, 누락된 봉을 오류 재분류만으로 복원할 수 없으므로 BLOCK을 유지한다.

## A. Root Cause Code Path

실제 unit은 `usb-ae-producer.service`, 실행 소스는 `/root/usb_runtime/ae_common_producer/src/backend`다. 별도 A consumer의 운영 트리는 `/root/usb_runtime/strategy_e_paper/src`다. 서버 git HEAD 대신 실제 unit과 배포 소스 해시를 확인했다.

1. `backend/app/dev/run_ae_common_producer.py:run` → `ae_common.producer.run_session`: 시작 시 ND/NY/NA의 `usa10099` listing으로 exchange map을 만들고 cache를 생성한다.
2. rolling: `strategy_e_max_rt.finalizer.refresh` → `Lane.page` → `KiwoomMarketDataClient.request`.
3. A 09:15: `ae_common.sweep.run` → `finalizer.refresh` 또는 `strategy_a_mover_live.acquisition.refresh_from_ticks` → client. 기존 `sweep.error_label`은 code와 고정 message를 보고 `SOURCE_REFUSAL:INVALID_SYMBOL`을 만들었다.
4. E 09:25: `producer._finalize_e.worker_a/worker_b` → `finalizer.finalize_minute/finalize_ticks` → client. 두 catch가 `type(error).__name__`만 저장해 code를 잃었다.
5. `snapshot.body_0924` → `seal` → `load` → `e_caches_from`: 기존에는 legacy error만 직렬화/복원했다.
6. `e_shadow.decide` → `statuses_of` → `availability.outcome_from_cache/classify`: `MarketDataError` 클래스명은 refusal code가 아니므로 STALE.
7. `run_e_rt2_dryrun.execution_gate`: documented PS를 제외한 CECO·IBOC 2개가 unexpected STALE → `DECISION_INCOMPLETE_UNIVERSE`, authorized=false.
8. 기존 status/UI 경로에서 DATA_ERROR → ISSUE / 데이터 오류. 이 경로는 수정하지 않았다.

10/22 원본 증거:

| symbol | A error | E error | exchange | E bars | finalized_at / contiguous | complete_through |
|---|---|---|---|---:|---|---:|
| CECO | SOURCE_REFUSAL:INVALID_SYMBOL | MarketDataError | ND | 0 | null / null | 311 |
| IBOC | SOURCE_REFUSAL:INVALID_SYMBOL | MarketDataError | ND | 0 | null / null | 315 |
| PS | MarketDataError:MARKET_DATA_UNAVAILABLE | MarketDataError | NY | 0 | null / null | null |

A에서 CECO·IBOC의 **status는 FAILED**였다. 이전 rolling에서 빈 페이지를 받았던 흔적 때문에 A의 never-served 조건도 만족하지 않았다. `SOURCE_REFUSAL:` 문자열은 당시 classifier의 결과이며 영구 거절의 증명이 아니다.

E status counts: FEATURE_COMPLETE 2,353 / SPARSE_NO_PREMARKET 181 / STALE 3. PS 제외 unexpected stale=2. 결정 시각은 09:29:31.434666 ET, open까지 28.565334초였다.

## B. Error Taxonomy Before

| 원인 | 기존 client class / code / message | A 09:15 | E 09:25 | 수정 후 category |
|---|---|---|---|---|
| symbol/exchange pair 없음 (1903) | MarketDataError / INVALID_SYMBOL / query was rejected | SOURCE_REFUSAL:INVALID_SYMBOL (잘못된 의미) | MarketDataError | MAPPING_ERROR, provider_code=1903 |
| 명시적인 source refusal | INVALID_SYMBOL 또는 MARKET_DATA_UNAVAILABLE / rejected | 고정 message로 추정 | MarketDataError | SOURCE_REFUSAL: 명시적 provider error_code만 인정 |
| timeout / retry exhaustion | MarketDataError / PROVIDER_TIMEOUT / timed out | MarketDataError:PROVIDER_TIMEOUT | MarketDataError | TRANSIENT_PROVIDER_ERROR |
| connection/reset | MarketDataError / MARKET_DATA_UNAVAILABLE / unavailable | MarketDataError:MARKET_DATA_UNAVAILABLE | MarketDataError | TRANSIENT_PROVIDER_ERROR |
| HTTP 5xx / retry exhaustion | MarketDataError / MARKET_DATA_UNAVAILABLE / request failed | MarketDataError:MARKET_DATA_UNAVAILABLE | MarketDataError | TRANSIENT_PROVIDER_ERROR, provider_code=HTTP status |
| unresolved 429 | MarketDataError / RATE_LIMITED / request failed | MarketDataError:RATE_LIMITED | MarketDataError | TRANSIENT_PROVIDER_ERROR |
| unmapped / unsupported exchange | 별도 처리 또는 INVALID_SYMBOL / UNSUPPORTED_EXCHANGE | UNMAPPED는 기존 known unavailable | unmapped는 기존 MARKET_DATA_UNAVAILABLE | 예외는 MAPPING_ERROR, 기존 unmapped contract 유지 |
| deadline | DeadlineReached / KIWOOM_DEADLINE_REACHED | INTERRUPTED | 상세 없는 미완료 cache | DATA_INCOMPLETE |
| pagination / history 부족 | 불완전 cache 또는 INSUFFICIENT_HISTORY | STALE/FAILED | contiguous=false → STALE | DATA_INCOMPLETE, BLOCK 유지 |
| 불명확한 nonzero return_code / malformed response | 대체로 MARKET_DATA_UNAVAILABLE | message로 구별 | MarketDataError | 증거 불충분: TRANSIENT_PROVIDER_ERROR |

모든 예외는 공통 모델로 class/code/category/provider/sanitized message를 보존할 수 있다. 예외 없이 contiguous=false로 끝나는 기존 페이지 부족은 그 verdict로 계속 STALE이 된다. raw message나 클래스명만으로 SOURCE_REFUSAL로 승격하지 않는다.

## C. CECO/IBOC Probe

증거: [provider-probe.json](provider-probe.json). 시작 시각 10/23 09:49:50 KST. producer inactive, PSI avg10=0을 확인하고 진행했다. market-data-only allowlist에 tick chart만 추가했으며 주문 경로는 차단돼 있다.

3시장×2 chart API×3대상×2회=36 chart requests, AAPL 대조군 2회, listing 3회. **주문 요청 0.**

| canonical / Kiwoom code | 10/22 cache | 현재 listing | ND / NY: usa06010, usa06011 | NA: usa06010, usa06011 | 판정 |
|---|---|---|---|---|---|
| CECO / CECO | ND | NA에 존재 | 모든 회차 HTTP200, return_code=7, subcode1903 | 모든 회차 return_code=0, 100 rows | MAPPING_ERROR |
| IBOC / IBOC | ND | NA에 존재 | 모든 회차 HTTP200, return_code=7, subcode1903 | 모든 회차 return_code=0, 100 rows | MAPPING_ERROR |
| PS / PS | NY | NY에 존재 | 전 시장·양 API·양 회차 HTTP400, 파싱 가능한 return_code/message 없음 | 동일 | 현재 probe 단독으로는 UNKNOWN. 기존 documented refusal 유지 |
| AAPL / AAPL | 대조군 | ND에 존재 | ND의 양 API return_code=0, 100 rows | 대상 아님 | provider 정상 응답 대조군 |

1903 message: `종목 정보가 없습니다. 입력한 종목코드, 거래소구분 값을 확인바랍니다.` 요청한 ND/NY와 해당 symbol도 포함된다. NA의 성공은 symbol 자체가 무효이거나 provider가 영구적으로 제공하지 않는다는 가설을 반박한다.

현재 listing이 언제 바뀌었는지, 10/22 09:25 당시 NA가 필요한 모든 페이지를 반환했을지는 이번 probe로 단정하지 않는다. 수정 대상은 **mapping 재확인이 없는 세션 고정 map**과 **error detail loss**다. CECO/IBOC 예외는 하드코딩하지 않는다.

## D. Classification Decision

- CECO/IBOC: MAPPING_ERROR. permanent refusal로 추가하지 않는다. 1903에서 mapping 복구를 시도하고, 실패하면 unexpected STALE로 남긴다.
- SOURCE_REFUSAL: 명시적인 stable provider `error_code`를 받은 경우만 인정한다. 불명확한 return_code나 `종목`이라는 단어만으로 판단하지 않는다.
- current-session structural refusal: 같은 code의 명시적 거절이 연속 2회 이상이고, 받은 bars가 없으며 E finalization에서도 실제 시도한 경우다. 성공·transient·mapping error가 끼면 연속 증거를 초기화한다. 단발 거절과 finalization 미도달은 STALE이다.
- PS: 기존 RVOL collection state의 documented refusal을 유지한다. 이번 HTTP400을 새로운 영구 거절 증거로 승격하지 않는다.
- 처음부터 unmapped: 기존 known unavailable contract를 유지한다. mapping이 있던 종목에서 1903이 발생한 경우와 구별한다.

## E. Shared Error Model

`backend/app/core/provider_errors.py`의 `ProviderError`, `classify`, `is_source_refusal`을 A/E가 공유한다. 기존 `MarketDataError(code, message)` 호출은 호환되며 optional provider/error_category/provider_code를 추가했다.

```json
{
  "error": "MarketDataError",
  "provider": "KIWOOM",
  "error_class": "MarketDataError",
  "error_code": "INVALID_SYMBOL",
  "error_category": "MAPPING_ERROR",
  "provider_code": "1903",
  "error_message": "mapping error"
}
```

error_message는 고정 category 설명이다. raw text, credentials, account data를 새 snapshot/session detail로 복사하지 않는다. 동일 legacy MARKET_DATA_UNAVAILABLE code를 사용하는 500/connection도 category로 거절과 구별한다. historical classifier의 `MarketDataError로 시작하면 거절` fallback도 제거했다.

## F. Finalization Serialization Fix

`SymbolCache.record_error`가 legacy 클래스명과 detail을 함께 기록한다. rolling, A sweep, E 양 worker가 공통 분류를 사용한다. A sweep의 scratch/commit도 mapping/detail/연속 거절 증거를 전달하며 마감 후 late response는 기존처럼 폐기한다.

A snapshot에 outcome detail을 flat 추가한다. E snapshot에는 detail/source_refusal_observations/final_attempted를 추가한다. 기존 format/version과 legacy error는 유지하고, 옛 snapshot은 optional 필드 없이도 읽는다. manifest는 추가 detail을 포함한 body 전체의 digest를 보호한다.

E public result/session_record에 source_refusal_count/examples, unexpected_stale_count/examples를 추가한다. example은 symbol/code/category/documented를 포함한다. session_record에는 provider_errors도 저장한다. 기존 gate의 string examples는 그대로 유지한다.

## G. Gate Behavior Before/After

| fixture | classification | unexpected stale | gate |
|---|---|---:|---|
| 명시적·반복 structural refusal, bars 없음 | SOURCE_REFUSAL → MARKET_DATA_UNAVAILABLE / H5_UNKNOWN | 0 | 나머지 조건 충족 시 AUTHORIZED |
| 1903 mapping 미해결 | MAPPING_ERROR → STALE | 1 이상 | BLOCK |
| timeout / 500 / connection / unresolved429 | TRANSIENT_PROVIDER_ERROR → STALE | 1 이상 | BLOCK |
| deadline / partial pagination | DATA_INCOMPLETE → STALE | 1 이상 | BLOCK |
| PS documented refusal | 기존 documented exception | 0 | 기존 동작 |
| 처음부터 unmapped | 기존 known unavailable | 0 | 기존 동작 |

gate 함수 자체는 수정하지 않았다. unexpected stale>0 fail-closed 및 Dashboard/status/UI production code를 유지한다.

mapping 복구는 common producer의 chart client에만 켠다. 첫 페이지의 1903에서 최신 ND/NY/NA listing을 완전히 읽고, 유일한 다른 exchange가 확인되면 해당 exchange로 1회 재시도한다. listing은 client별 60초 cache, 시장별 최대 50 pages다. 중복·누락·불완전 listing·transient·deadline에서는 추측하지 않는다. query도 chart와 같은 phase deadline limiter를 통과한다. mapping 변경 시 cache를 비우고 04:00부터 coverage를 다시 확보한다. tick에서도 이전 complete_through를 재사용하지 않는다.

## H. 10/22 Replay

원본 snapshot_0924 gzip을 그대로 fixture로 복사했다. 압축 원본 SHA256:

`c658894d4f05240d83431a1d97fb2aa6c57f204510fe8b92eaa9869611957d9b`

context fixture에는 원본 A의 3대상 entry, universe/decision, RVOL denominator 읽기 값과 probe를 담았다. RVOL은 mode=ro로 읽은 10/22 이전 staged/complete 이력에서 기존 minimum=5/window=20/median을 사용했다. export는 10/23 시점이며 10/22 당시 DB 전체 backup은 아니다. 다만 계산한 모든 frozen decision fields가 원본과 완전히 같음을 assert한다. pending=0/documented=PS는 원본 gate에 근거한 fixture다.

결과: [replay-result.json](replay-result.json).

- Before: unexpected_stale=2, DECISION_INCOMPLETE_UNIVERSE, authorized=false.
- After (정확한 mapping detail 추가): 동일한 2, 동일한 BLOCK. 과거 누락 bars를 만들지 않는다.
- 940 eligible rows, 21 H5 candidates, selected **DAR / TRV / VRTX**.
- universe_rows, H5 count/rate, breadth, exposure, candidates, R1 order, Top3, reference prices, 모든 features와 rule/seal digest가 완전히 같다.
- source_refusal_count=1 (PS), unexpected_stale_count=2 (CECO/IBOC, MAPPING_ERROR).
- AUTHORIZED counterfactual은 적용하지 않았다. 요청의 조건인 `structural refusal 확인`이 probe에서 반박됐기 때문이다.

10/22 snapshot_0915/snapshot_0924/e_shadow.json 및 배포 producer/client의 작업 전 복사본과 작업 후 운영 해시가 같다. [production-readonly-after.txt](production-readonly-after.txt)에 증거를 남겼다. 운영 원본·book·collection state에 쓰기 작업을 하지 않았다.

## I. Negative Regression Tests

`backend/tests/ae_common/test_provider_errors.py`에 30 cases를 추가했다.

- explicit structural INVALID_SYMBOL, 단발 거절, 기존 bars 존재, E 미도달, 거절 후 transient.
- timeout, HTTP500, connection, deadline, pagination, unmapped/unsupported exchange, 1903, 모호한 legacy message.
- 실제 client boundary의 stable code/category/provider, HTTP status, 429, unknown rejection.
- mapping 복구의 유일 성공, 중복, symbol 부재, pagination 중단, deadline, HTTP500.
- 동일한 6종 예외로 A sweep→E finalization→snapshot→session record→live/file parity 검증.
- PS existing documented refusal과 기존 unmapped contract.
- 원본 10/22 replay와 양 API·양 회차 NA 성공 probe fixture.

## J. Full Test Results

공유 트리에서 아래 관련 backend 전체 suite가 **618 passed**, 기존 deprecation warning 4개로 완료됐다(89.34초).

```bash
.venv/bin/pytest -q backend/tests/ae_common backend/tests/strategy_e_max \
  backend/tests/test_kiwoom_stage10a.py backend/tests/test_kiwoom_token_recovery.py \
  backend/tests/test_kiwoom_availability_contract.py backend/tests/test_kiwoom_historical_regression.py \
  backend/tests/test_kiwoom_timestamp_contract.py backend/tests/test_dashboard_ops.py \
  -o faulthandler_timeout=30
```

Frontend 회귀: **3 files / 49 tests passed**.

```bash
cd frontend
npm test -- --reporter=dot components/dashboard-strategy-card.test.tsx \
  components/strategy-runtime.test.tsx components/strategy-e-operations.test.tsx
```

sandbox socket 제한에서 기존 asyncio thread 테스트가 종료 대기로 멈췄다. 해당 테스트를 sandbox 밖에서 단독 성공시킨 후 전체 backend suite도 같은 환경에서 완주했다. warning은 기존 exchange_calendars/NumPy DeprecationWarning이다.

별도로 `git archive HEAD` + 명시 stage한 18개 경로만 반영한 clean 소스에서 같은 618개를 검증했다. 최초 실행은 **610 passed / 8 failed**였다. 실패 7개는 archive에 없는 Git 과거 객체를 읽는 기존 frozen-artifact 테스트, 1개는 초기화 전 격리 SQLite schema를 사용하는 기존 A endpoint 테스트였다. 과거 객체는 원본 GIT_DIR에서 읽기 전용으로 참조하고, `/tmp/.../clean/data/runtime/usb.sqlite3`에 빈 schema를 생성한 뒤 해당 **8개 모두 passed**(1.48초)를 확인했다. 따라서 clean 소스에서도 618개 전부 통과했다. 운영/공유 DB 및 다른 세션의 dirty 소스는 가져오지 않았다.

Frozen source 검증: [frozen-source-checks.json](frozen-source-checks.json). decision/config/engine/cost/paper, V1.1 universe, H5 seal, execution_gate를 포함한 기존 runner가 전부 HEAD와 byte-identical이다. H5/RVOL=3.0/R1/Top3/B2/exposure/09:25 cutoff/09:30 entry/09:34 close/COST_10BP/V2 book contract는 변경하지 않았다. backend 전체가 아니라 요청된 관련 suite 전체를 실행했다.

## K. Changed Files

Production source 11개는 [RELEASE_FILES.txt](RELEASE_FILES.txt), [SOURCE_SHA256SUMS](SOURCE_SHA256SUMS)에 명시했다.

- core: optional exception metadata와 공통 provider_errors.
- Kiwoom client: stable taxonomy와 bounded authoritative exchange 복구.
- finalizer/acquisition: detail/mapping 보존, mapping 변경 후 coverage 재확보.
- ae_common: 양 snapshot 직렬화, 양 lane catch, 공통 분류와 telemetry.
- availability: detail에 근거한 structural 분류, class명 기반 historical 승격 제거.
- tests: 공통 fixture/import allowlist/legacy classifier 검증 수정, 신규 30 cases와 원본/context gzip fixtures.
- 본 보고서, read-only probe/운영 해시/replay/source manifest.

Frontend production code 변경은 없다. 다른 세션의 dirty/untracked 파일은 stage/commit에 넣지 않는다.

## L. Git Commit

Branch: **main**. 시작 HEAD: `af4004970a4f3d6eab5abe10102e14c02a860ff5`.

관련 경로만 명시 stage하고 cached diff를 확인해 `fix(strategy-e): preserve provider refusal semantics`로 commit한다. 이 보고서를 포함한 commit ID는 `git log -1 --format=%H -- docs/operations/e_provider_taxonomy_20261023/REPORT.md`로 확인할 수 있으며 최종 응답에도 명시한다. push 없음. 공유 트리의 다른 세션 dirty는 그대로 남긴다.

## M. Production Deployment Plan — STOP

**운영 copy, unit 변경, restart, timer 변경, arm, book 쓰기를 실행하지 않았다.**

승인 후 절차:

1. clean commit에서 source overlay를 만들고 RELEASE_FILES/SHA와 대조한다. 공용 dirty 트리 전체를 복사하지 않는다. Frontend build는 필요 없다.
2. producer/A consumer 비실행, 다음 세션 시각, PSI와 disk 여유를 확인한다. 마지막 read-only 확인에서 PSI some avg10=9.60/full=9.19로 상승했으므로 승인 후 재측정하고 높으면 작업을 미룬다.
3. 대상 두 트리의 이전 파일/신규 module 존재 여부/권한/hash를 rollback manifest에 저장한다. PROD 11개와 EP의 snapshot.py + 새 core/provider_errors.py가 대상이다. 기존 stage_release.sh의 NEW-only 최초 도입 절차를 그대로 재실행하지 않는다.
4. PROD clean release를 준비하고 EP에는 필요한 두 파일만 맞춘다. units/ACTIVE_FROM/HOLD_A/HOLD_E, 전략 설정/universe/RVOL DB/book/과거 artifact는 변경하지 않는다.
5. 주문·세션 실행 없는 import/serialization smoke, hash 검증과 read-only provider probe로 1903→listing→NA 성공을 확인한다. 복구돼도 연속성이 미완료이면 계속 BLOCK이어야 한다.
6. 다음 정상 timer 실행 후 A/E snapshot, live/file parity, mapping/error detail, source/unexpected 집계, gate, 선택/cutoff/V2 book readback을 확인한다. 10/22 과거 paper 거래는 보충하지 않는다.
7. 문제 시 저장한 대상 파일만 되돌린다. 신규 module은 참조를 확인한 후 처리한다. 기존 unit/book/DB/artifact를 유지하고 과거 이력을 고쳐 쓰지 않는다.

전략 변경, universe 축소, stale 허용치, CECO/IBOC 고정 제외, LIVE order path 변경, V2 backlog 기능 추가는 없다. 기존 V1 구조의 provider adapter/common snapshot/availability contract 수정이다.
