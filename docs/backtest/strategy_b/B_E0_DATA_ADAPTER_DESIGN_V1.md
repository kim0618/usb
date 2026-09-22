# Strategy B E0 데이터 어댑터 설계 V1

작성 2026-09-21. **설계 문서다. 구현은 아직 없다.** B-E0 러너(`backend/app/backtest/strategy_b_e0/`)와
Drive의 Common Raw 사이를 잇는 `SessionSource`·`DatasetFacts`와 데이터 준비 게이트를 정한다.

- 전략 로직 변경: **없음**. 이 문서는 FSM·셋업·진입·청산·리스크·임계값을 하나도 건드리지 않는다
- 상위 계약: `B_E0_BACKTEST_CONTRACT_V1.md` (현재 `DRAFT_PENDING_APPROVAL`)
- 유니버스: `data/runtime/strategy_b_e0/b_e0_run_universe_v1.json`, sha256 `513a540d...`
- 근거: 이 문서의 사실 주장은 전부 2026-09-21 코드·파일 조사와 로컬 실측에서 나왔다. 추정치는 **추정**이라고 적는다

## 0. 한 줄 요약

**추가 API 수집 없이 실행 가능하다.** 부족해 보였던 입력(종목별 일봉 383종목, reference 번들 0개)은
실행 경로에서 필요 없거나 grouped daily에서 파생된다. 다만 **결정 6개와 계약 개정**이 먼저다(12절).
그리고 조사 중 **엔진 결함 1건**(조기 마감일 크래시)을 발견했다. 이번 창에는 조기 마감일이 없어 막히지
않지만 준비 게이트가 막아야 한다(9절).

## 1. 현재 데이터 인벤토리

| 데이터 | 위치 | 스키마 | 조정 | 4개월 창 커버리지 | 로컬 인덱스 |
| --- | --- | --- | --- | --- | --- |
| 분봉 (Common Raw) | Drive `market_data/raw/massive/minute/<SYM>/` 페이지 `.pNN.json.gz` + 원장 `.request.json` | 봉 `t`(int, ms UTC 시작) `o h l c` `v`(int 또는 **소수 float**) `vw` `n`(int, 로컬 2,533만 봉 전부 존재) | false | 유니버스 3,882종목 전부 디렉터리 있음, 마지막 실행 실패 0, 검증 PASS | **없음** (원장만) |
| grouped daily | Drive `grouped_daily/<YYYY>/<date>.json.gz` (로컬 사본 `data/runtime/strategy_c/raw/grouped/`) | 래퍼 `{format,session,body{results}}`, 행 `T o h l c v(float) vw? n? t` | false | 104세션 전부(502/502, 2024-09-16은 NOT_AUTHORIZED 스텁) | **있음** (로컬 사본 + freeze sha256) |
| splits | Drive `splits/splits_2024-09-16_2026-09-16.json.gz` (+ 로컬 사본) | `execution_date id split_from split_to ticker` | - | 3,328건, 창 안 유니버스 종목 134건(121종목) | **있음** |
| CS 스냅샷 | Drive `reference_tickers/CS_<date>.json.gz` (+ 로컬) | `active cik composite_figi delisted_utc last_updated_utc locale market primary_exchange share_class_figi ticker type` | - | 분기 8개, 창에는 2026-04-01·2026-07-01 | 있음 |
| 종목별 일봉 | Drive `per_symbol_daily/<SYM>/` | 봉 `t o h l c v vw n` | false | **383종목뿐** (B 실행이 370/5485에서 중단) | 없음 |
| B reference 번들 | Drive `metadata/strategy_b/reference/` | `daily metadata splits checksum` | - | **디렉터리 자체 없음** | - |
| `sparse_minute_bars` parquet | Drive `normalized/minute_sparse/` | - | - | **없음** (manifest 항목 0) | - |
| 달력 | `app.market.calendar.MarketCalendar` (exchange_calendars XNYS) | open/close/조기마감 | - | 전 구간 | 코드 |
| 유니버스 | 로컬 `b_e0_run_universe_v1.json` | 종목별 `scope_sessions` `fetch_start` `fetch_end` | - | 84세션, 3,882종목, 285,175쌍 | 로컬 (**요구량**이지 보유량 아님) |

크기: Drive 분봉 전체 약 2.0GB gz, 비압축 JSON 약 10GB(**추정**, 마지막 실행의 17.1B/행 비율 환산).
로컬 여유 공간 877GB.

## 2. 엔진이 실제로 소비하는 것

`engine.run_session(symbols: Sequence[SymbolSession], ...)`가 한 세션의 모든 종목을 받아 자체 tick
루프를 돈다. **다른 입력은 읽지 않는다**(`engine.py:40-52` 주석). 따라서 어댑터의 일은 세션마다
`SymbolSession` 목록을 정확히 채우는 것 하나다.

| `SymbolSession` 필드 | 비어 있으면 | 실행 필수 | 최소 원천 |
| --- | --- | --- | --- |
| `tape: SessionTape` | - | **예** | 그날 분봉 + 달력 |
| `scope: ScopeDecision` | `None`이면 모든 후보가 `INELIGIBLE/OUT_OF_SCOPE`로 즉시 탈락 | **예** | 유니버스 아티팩트 |
| `rvol_history: tuple[VolumeProfile]` | 5개 미만이면 `RVOL_UNKNOWN`으로 **후보가 하나도 안 열림** | **예** | 이전 세션 **분봉** |
| `splits: tuple[SplitRecord]` | 공유 계수 1 | 창 안 split이 있을 때만 | splits 덤프 |
| `corporate_action_flags` | 아무것도 막지 않음(검사 없이 통과) | **예** (6개 중 5개가 탈락 사유) | splits, 상장일, D-1 감사 |

`halt_inferred`·`sparse_status`는 엔진이 **그날 tape에서** 계산한다. 일봉·메타데이터를 런타임에
읽는 경로는 없다.

**주의(조용한 실패 두 개)**: `rvol_history`와 `corporate_action_flags`는 기본값이 비어 있어서, 어댑터가
빠뜨려도 **에러 없이** "후보 0건" 또는 "플래그 무검사"로 돈다. 테스트가 반드시 막아야 한다(11절).

## 3. `SessionSource` 계약

### 3.1 메서드

현재 `run.py`의 Protocol(`boundaries`, `symbol_sessions`, `dataset_identity`, `dataset_digest`)을
유지한다. 러너가 이미 세션 단위로 부르므로 **전역 시간순 이터레이터는 만들지 않는다**
(`run_session`이 한 세션의 다종목 시간순 처리를 이미 한다. 옛 `ChronologicalReplayDriver`는 불필요).

| 메서드 | 입력 | 출력 | 책임 |
| --- | --- | --- | --- |
| `boundaries(D)` | 세션 날짜 | `SessionBoundaries` | `dataset.boundaries_for(calendar, D)` 재사용. **달력 기반**(조기 마감 반영) |
| `symbol_sessions(D, symbols)` | 세션, 러너가 준 종목 | `SymbolSession` 목록 | 아래 3.2의 다섯 필드를 **전부** 채움. 요청 안 한 종목 추가 금지 |
| `dataset_identity()` | - | 문자열 | 로컬 미러 식별자 |
| `dataset_digest()` | - | sha256 | 미러된 전 페이지 `file_sha256`의 정렬 해시 |

### 3.2 `symbol_sessions`가 채우는 방법

| 필드 | 방법 | 재사용 |
| --- | --- | --- |
| `tape` | 로컬 캐시의 D 행 → `MomentumBar` → `SessionTape(symbol, boundaries_for(D), bars)` | `SparseMinuteDataset.__init__`/`bars()` 또는 `a_view.py:249-261` 파싱 패턴 |
| `scope` | 유니버스 멤버 → `ScopeDecision(symbol, D, included=True, exclusion_reasons=(), reference_price=None, median_dollar_volume=None, history_sessions=0)` | 결정 D1 |
| `rvol_history` | D 이전 최대 20세션의 `build_volume_profile(tape, SESSION_LOCAL)`. 종목별 링을 세션마다 굴림 | `adapter.py:140-151,172-177` 로직 복사(파일은 import 불가), `preparation.warmup_dates` |
| `splits` | 그 종목의 split 중 `execution_date <= D` | `known_splits` |
| `corporate_action_flags` | `derive_corporate_action_flags(D, splits, prior_sessions_since_listing, prior_audit)` | `corporate_actions.py` 그대로, 입력은 결정 D3·D4 |

**어댑터가 하지 않는 것**: scanner 판정, RVOL 임계 비교, HOD, 후보, 진입. 어댑터는 입력을 채우기만 한다.

### 3.3 `MomentumBar` 매핑

| raw | `MomentumBar` | 변환 |
| --- | --- | --- |
| `t` | `timestamp` | ms UTC → ET aware datetime (봉 시작) |
| `o h l c` | `open high low close` | `float()` |
| `v` | `volume` | `float()` (**소수 허용**, 모델이 허용) |
| `vw` | `vwap` | `float()` 또는 `None` |
| `n` | `transactions` | `float()` 또는 `None` |
| - | `synthetic` | 항상 `False` |
| - | `session` | `boundaries.classify(timestamp)` (`SessionTape`가 일치를 검사) |

04:00~20:00 ET 밖의 행은 `SessionTape`가 거부하므로 로더가 먼저 걸러 **감사 카운트로 기록**한다.

### 3.4 수치 정책

순수 계층은 전부 `float`이다(`MomentumBar`, `SplitRecord`, `Portfolio`). Decimal을 넣으면
`features.py`의 `0.0` 누적과 섞여 TypeError가 날 수 있다(추론, 미검증). **float를 유지한다.**
raw JSON의 int/float는 `float()` 한 번으로 바꾸며 그 외 변환은 없다. 계약의 자본 문자열
`"7428.92"`는 `Portfolio` 생성 시 한 번 `float`가 된다(현재 러너가 이미 그렇게 한다).

### 3.5 Sparse 유지

로더는 **실제 봉만** 넣는다. 09:31·09:34·09:39만 있으면 tape에는 3개다. 빈 분을 채우지 않고
synthetic 봉을 만들지 않는다(`SessionTape`가 생성 시 synthetic을 거부하므로 구조적으로도 불가).

## 4. 결정이 필요한 6가지 (추천안 포함)

### D1. scope의 권위: **유니버스 아티팩트** (추천)

두 규칙이 존재하고 **다르다**:

| | `b_universe.scope_membership` (아티팩트) | `evaluate_research_scope` (preparation) |
| --- | --- | --- |
| 일봉 창 | 격자 20세션(결측은 NaN) | 존재하는 최근 20봉(결측이면 더 과거로 감) |
| OTC·`market`·`active` | 검사 안 함(거래소 화이트리스트로 사실상 대체) | 검사 |
| 메타데이터 | CS 스냅샷 | 종목별 ticker details(0개 존재) |

런타임에 다시 계산하면 **이중 권위**가 되고 아티팩트와 어긋나는 종목이 생긴다. 계약은 이미 아티팩트를
권위로 선언했다. 따라서 멤버에게 `included=True`의 `ScopeDecision`을 직접 만든다. 멤버가 아닌 종목은
애초에 넘기지 않는다(러너가 `symbols_for(D)`로 거른다). `ScopeDecision`에는 포함 사유 필드가 없어서
출처는 run manifest에 적는다.

### D2. 일봉 원천: **grouped daily** (추천, 추가 수집 0)

종목별 일봉 383종목 문제는 **실행을 막지 않는다**. 실행 경로에서 일봉은 딱 한 곳에만 쓰인다.
CA_SUSPECT용 D-1 HYBRID-S 감사(`validate_sparse_session`)의 `OfficialDailyBar`인데, 필요한 OHLCV를
grouped daily가 전 종목·전 세션에 대해 갖고 있다(로컬 사본 502/502, 유니버스 멤버 누락 0).

주의: A는 USB-DAILY-AUTHORITY-V1에 따라 grouped daily를 권위로 쓰지 않는다(`a_view.py:21-22`).
B가 쓰려면 **계약에 선언**해야 한다. 이미 scope 계산이 grouped daily를 쓰므로 B 안에서는 일관된다.

### D3. `IPO_WARMUP`: **grouped daily 첫 등장 대리지표** (추천) vs `list_date` 수집

CS 스냅샷에 `list_date`가 **없다**. 그대로 두면 `IPO_WARMUP`이 한 번도 켜지지 않는다(옛 파이프라인도 같았다).

| 안 | 방법 | 비용 | 실측 영향 |
| --- | --- | --- | --- |
| A (추천) | `prior_sessions_since_listing` = D 이전 세션 중 그 티커가 grouped daily에 처음 나타난 날부터의 세션 수 | API 0 | **44종목, 336쌍**이 `IPO_WARMUP`으로 탈락 |
| B | ticker details로 `list_date` 수집 | 약 3,882콜, 5콜/분이면 약 13시간(**추정**) | 정확한 상장일 |
| C | 적용 안 함, 한계로만 기록 | 0 | 336쌍이 F0 의도와 달리 통과 |

A는 D 이전 데이터만 보므로 PIT 안전하다. 한계: 티커 변경으로 grouped에 늦게 나타난 오래된 종목은
IPO로 오판된다(보수 쪽 오류). 반대로 재사용 티커의 신규 상장은 놓친다. 3,361종목은 격자 첫날부터
등장하므로 대리지표가 무의미하지만, 어차피 20세션 이상이라 판정은 같다.

### D4. `DELISTING_WINDOW`·`SYMBOL_CHANGE`: **NOT_APPLIED** (추천)

원천이 없다. CS 스냅샷은 `active=true`로 받아서 `delisted_utc`가 사실상 비고, 공시일(`announced_on`)이
필요한 `DelistingNotice`는 PIT로 재구성할 방법이 없다. 옛 파이프라인도 이 둘을 한 번도 만들지 않았다.
**플래그를 적용하지 않고 결과 한계로 공시한다.** 나중에 원천이 생기면 v2다.

### D5. TCI 2026-08-25: **쌍 제외 + 사유 기록** (추천)

grouped daily에는 거래가 있는데 분봉이 0이다. HYBRID-S 판정은 `API_LOSS_SUSPECT` / `MINUTE_TAPE_EMPTY`
(`sparse_session.py:96-99`)이므로 **새 enum 없이 기존 판정을 쓴다.**

| 안 | 내용 | 평가 |
| --- | --- | --- |
| A. 전체 실행 차단 | 1쌍 때문에 285,175쌍을 버림 | 과도 |
| **B. 쌍 제외** | 그 (종목, 세션)을 세션에 넘기지 않고 `DATA_GAP_SUSPECT`로 기록 | **추천** |
| C. 빈 세션 취급 | 빈 tape로 넘김 | 결과는 B와 **같다**(빈 tape는 `VERY_SPARSE`라 어차피 탈락). 그러나 "거래 없던 날"로 **위장**된다 |
| D. 보간 | 가짜 봉 생성 | **금지** |

B와 C는 거래 결과가 같다. B를 고르는 이유는 **기록의 정직성**이다. 데이터 결손을 조용한 날로 적지 않는다.
08-25는 TCI의 마지막 scope 세션이라 뒤 세션의 CA_SUSPECT에도 영향이 없다.

**일반 규칙**: grouped 거래량 > 0 이면서 그날 정규장 분봉이 0인 scope 쌍은 제외한다. 이것이 체계적으로
나타나면 막는다(D6·8절 임계).

### D6. 워밍업 결손(BACC 2026-06-23): **기준선에서 제외** (추천)

06-23은 BACC의 워밍업 구간이지만(scope 07-13~08-03) **RVOL 기준선에 들어간다.** 실측: BACC scope
세션 중 **6개(07-13~07-22)**의 20세션 기준선에 06-23이 포함된다.

현재 규약("덮인 세션에 봉이 없으면 거래량 0")을 그대로 쓰면, 결손일이 0으로 평균에 들어가 기준선을
낮추고 그만큼 **RVOL이 부풀려진다**(1/20, 최대 약 5%).

추천: `MINUTE_TAPE_EMPTY`이면서 grouped 거래량이 0보다 큰 세션만 기준선에서 뺀다. 그러면 해당 6세션은
19개 프로필로 `PARTIAL`이 된다(5개 이상이라 유효). `rvol.py:15-18`은 이 선택을 호출자에게 명시적으로
맡기고 있다.

**좁힌 이유**: HYBRID-S의 `API_LOSS_SUSPECT`는 가격 불일치(허용오차 1e-6)나 거래량 초과로도 켜진다.
grouped 종가와 분봉 종가가 다를 수 있다는 것은 이미 측정됐다(9/17). 넓은 규칙은 기준선을 광범위하게
흔든다. 그래서 "봉이 통째로 없는 날"만 뺀다.

## 5. `DatasetFacts` 계약 (정적/동적 분리)

### 5.1 정적 사실 (preflight, 실행 전 1회)

로컬 미러를 만들 때 원장을 한 번 읽어 **커버리지 인덱스**를 만든다. preflight는 그 인덱스만 본다.

| 필드 | 의미 |
| --- | --- |
| `universe_sha256` | 아티팩트 해시 일치 |
| `coverage[symbol]` | COMPLETE 원장이 덮는 세션 구간(`raw_fetch.ledger_sessions`: 원장이 덮으면 봉 유무 무관) |
| `pages[path] = file_sha256` | 미러 페이지 해시(원장 대조) |
| `grouped_present[D]`, `grouped_volume[symbol, D]` | grouped daily 존재·거래량 |
| `split_conflicts` | 충돌 중복 split 키(유니버스 교집합) |
| `early_close_sessions` | 창 안 조기 마감일 |
| `known_gap_suspects` | 검증기의 `truncation_suspects`(grouped 거래 > 0, 봉 0) |

### 5.2 동적 사실 (세션 로드 시, 산출물에 기록)

| 필드 | 의미 |
| --- | --- |
| `minute_rows`, `minute_first_ts`, `minute_last_ts`, `minute_volume_sum` | 그날 실제 적재 |
| `outside_window_rows` | 04:00~20:00 밖이라 버린 행 |
| `hybrid_s_verdict`(D-1 포함) | `validate_sparse_session` 결과 |
| `warmup_profiles_used`, `warmup_excluded_gap_days` | D6 적용 결과 |
| `data_quality_flags` | `DATA_GAP_SUSPECT` 등 |

## 6. 필수 입력 vs 감사 입력

| 분류 | 입력 |
| --- | --- |
| **실행 필수** | 유니버스 멤버십, D와 워밍업 세션의 분봉(또는 원장이 덮는 빈 세션), 달력, splits 덤프, grouped daily(D-1 감사와 D3 대리지표) |
| **품질 감사** | D의 grouped daily 대조(HYBRID-S 당일 판정), 분봉/일봉 거래량 비율, OHLC 일치, `historical_store/session_audit` 행 |
| **없어도 됨** | 종목별 일봉, B reference 번들, ticker details, `sparse_minute_bars` parquet, 공시·티커 변경 데이터 |

## 7. PIT 안전 규칙 (어댑터가 지킬 것)

1. D의 tape는 그 세션 분봉 전체를 담되, 모든 판단은 엔진이 `available_at <= as_of`로 자른다(기존 계약).
2. `rvol_history`는 D **이전** 세션 프로필만. `time_of_day_rvol`이 D 이후 프로필을 `PointInTimeViolation`으로 거부한다.
3. `splits`는 `execution_date <= D`만. D-1 감사는 `execution_date <= D-1`만(`preparation.py:134` 규칙).
4. CA_SUSPECT는 **D-1** 판정만. D의 grouped daily는 D의 결정에 들어가지 않고 사후 감사에만 쓴다.
5. IPO 대리지표는 D **이전** 날짜의 grouped 등장만 센다.
6. **scope를 런타임에 다시 계산하지 않는다.** 아티팩트 세션 목록만 따른다.
7. 워밍업 결손 판정(D6)은 그 **과거** 세션의 분봉과 grouped만 본다(D 이후 정보 없음).

검증 방법: 미래 봉·미래 split·미래 grouped를 주입해도 D의 `SymbolSession`이 바이트 단위로 같아야 한다(11절).

## 8. Preflight 설계 (285,175쌍을 싸게)

**금지**: 285,175쌍 × 파일 열기. 실제로는 분봉 파일이 **종목당 4개월 1개**라 파일은 약 4,000개지만
Drive 콜드 읽기가 느리다(수집기 계획 단계가 원장 읽기에만 약 20분 걸렸다).

설계:

```text
1. mirror   Drive 분봉 페이지 → 로컬 data/runtime/strategy_b_e0/mirror/  (1회)
            각 페이지를 원장의 file_sha256과 대조, 불일치면 중단
            같은 패스에서 원장을 읽어 coverage_index.json 생성
            dataset_digest = sha256(정렬된 (경로, file_sha256) 목록)
2. cache    미러 → 세션별 로컬 캐시 (104세션)                      (1회)
3. preflight  coverage_index + 로컬 grouped/splits + 아티팩트만으로 판정  (수 초)
4. replay   세션별 캐시에서 lazy load                               (매 실행)
```

미러 비용(**추정**): 약 2GB gz, 검증기가 측정한 Drive 읽기 속도 분당 약 190MB 기준 **10~20분**.
B_ENGINE_DESIGN 8절이 이미 "Drive가 아니라 로컬 복사본에서 읽는다"고 정했으므로 그 결정의 구현이다.
미러는 Drive를 **읽기만** 하며 원본을 바꾸지 않는다.

### 8.1 준비 게이트 판정 (결정적)

| 상태 | 조건 |
| --- | --- |
| `DATASET_NOT_READY` | 아래 차단 조건 중 하나 |
| `DATA_QUALITY_BLOCKED` | 제외 쌍이 필요 쌍의 **0.5%** 초과(> 1,425), 또는 한 세션에서 그 세션 멤버의 **2%** 초과가 제외 |
| `PASS_WITH_WARNINGS` | 차단 없음, 경고 1개 이상(TCI 같은 쌍 제외, 워밍업 결손 제외, PARTIAL 워밍업, 무관한 split 충돌) |
| `PASS` | 차단·경고 없음 |

**차단 조건**(`DATASET_NOT_READY`):

- 유니버스 sha256, 계약 canonical, 규칙 canonical 중 하나라도 불일치
- `contract_matches_config` 실패
- 유니버스 종목의 `fetch_start..fetch_end`를 COMPLETE 원장이 덮지 않음
- 미러 페이지 해시 불일치
- 104세션 중 grouped daily 누락
- splits 덤프 없음 또는 파싱 불가
- 달력 세션 ≠ 아티팩트 세션
- **창 안에 조기 마감일 존재** (9절 엔진 결함 가드)
- 유니버스 종목에 걸린 split 충돌이 있는데 제외 규칙으로 처리 안 됨

0.5%와 2%는 **제안값**이다. "체계적 결손"과 "국소 결손"을 가르는 선이며, 결과를 보기 전에 계약에 고정해야 한다.

러너 판정과의 대응: `PASS`·`PASS_WITH_WARNINGS`면 진행하고, 나머지 둘은 러너 판정 `DATASET_NOT_READY`로 끝난다.

### 8.2 산출물 `dataset_preflight.json`

```text
universe_sha256, contract_canonical, rules_canonical, dataset_digest
required_pairs, covered_pairs, excluded_pairs[{symbol, session, reason}]
warmup_gap_days[{symbol, session}], warmup_partial_symbols
split_conflicts[{ticker, date, records}], outside_window_rows
early_close_sessions, grouped_missing_sessions
checks[{name, passed, detail, items}], status
```

결정적 바이트(정렬 키, 시각 없음). 시각은 provenance에만 둔다.

## 9. 조사 중 발견한 엔진 결함 (이번 창에서는 비차단)

**조기 마감일에 `run_session`이 크래시한다.**
`SessionBoundaries.standard(D, regular_close=13:00)`에 종목 하나를 넣으면
`ValueError: every tick must be a regular-session moment`가 난다(조사 에이전트 재현).

- 원인: 탐지 tick 격자(`engine.py:203-214`)에 13:00이 포함되는데 `session.py:67`이 `close <= t`를 AFTER로 분류하고, `prefilter.py:67-68`이 이를 거부한다.
- 종목 0개면 돌고, 표준 16:00일은 정상이다.

이번 창은 **조기 마감일 0일**(실측)이라 E0는 막히지 않는다. 그러나 엔진 코드이므로 이 설계 범위에서 고치지 않는다.
**준비 게이트가 창 안 조기 마감일을 차단 조건으로 둔다.** 수정은 별도 작업으로 등록한다(향후 기간 확장 시 필요).

## 10. Lazy loading · 캐시 · 메모리

- 세션 단위로 처리한다. 한 세션의 멤버 약 3,400종목의 tape만 올린다.
- 캐시 원천은 로컬 미러다. 분봉 파일이 종목당 4개월 1개라 세션마다 원본을 다시 파싱하면 84 × 3,882번 해제가 된다. 그래서 **세션별 캐시**를 1회 만든다(종목 순으로 한 번 읽고 104개 세션 파일로 나눔). 이것은 B 어댑터 소유의 로컬 파생물이며 Canonical Store가 아니다.
- 크기(**추정**): 필요 종목-세션 368,105 × 세션당 약 256행 ≈ 9,400만 행. 세션당 약 90만 행이며 float64 9열 기준 메모리 약 65MB.
- 프로필 링: 종목별 최근 20개 `VolumeProfile`. D 처리 뒤 D 프로필을 추가하고 가장 오래된 것을 뺀다. 메모리는 수백 MB 수준(**추정**, 구현 시 측정).
- 인메모리 캐시: 세션 경계로 한정한다(세션 tape는 다음 세션에 버림). grouped/splits/달력은 작아서 전체 상주한다.
- **Canonical Store에 B 전용 산출물을 저장하지 않는다**(FeatureSnapshot, RVOL, HOD, 후보 금지). 미러·캐시·preflight는 `data/runtime/strategy_b_e0/`에만 둔다.

## 11. 테스트 계획 (구현 단계)

| 테스트 | 고정할 것 |
| --- | --- |
| 정상 sparse 세션 | 실제 봉 수 그대로, synthetic 0 |
| 원장이 덮는 빈 세션 | 빈 tape, grouped 거래 0이면 제외 아님 |
| TCI형 결손 | grouped > 0 & 봉 0 → 쌍 제외, 사유 기록, 세션에 안 넘어감 |
| 워밍업 결손 | BACC형 날짜가 기준선에서 빠지고 프로필 19개 PARTIAL |
| 워밍업 부족 | 5개 미만이면 RVOL UNKNOWN이 그대로 보고됨(숨기지 않음) |
| split 당일 | `SPLIT_ON_DAY` 플래그, 공유 계수는 D까지 알려진 split만 |
| split 충돌 | 충돌 키 제외·보고, 결정적 |
| IPO 대리지표 | 첫 등장 후 20세션 미만이면 `IPO_WARMUP` |
| **필드 누락 가드** | `rvol_history`·`corporate_action_flags`·`scope`가 비면 어댑터가 예외(조용한 실패 차단) |
| 유니버스 해시 불일치 | `DATASET_NOT_READY` |
| 미러 페이지 해시 불일치 | `DATASET_NOT_READY` |
| 조기 마감일 가드 | 창에 있으면 `DATASET_NOT_READY` |
| lazy load | 한 세션 처리 중 다른 세션 캐시를 열지 않음 |
| synthetic 금지 | 로더 산출에 `synthetic=True` 0 |
| **PIT mutation** | D 이후 봉·split·grouped 주입해도 D의 `SymbolSession` 동일 |
| preflight 결정성 | 같은 입력이면 `dataset_preflight.json` 바이트 동일 |
| 멤버십 준수 | 아티팩트에 없는 종목을 요청하면 거부, 추가 반환 금지 |
| scope 재계산 금지 | 어댑터가 CS 스냅샷·`evaluate_research_scope`를 호출하지 않음 |

## 12. 계약 개정 필요 사항 (동결 전)

이 설계를 구현하면 B-E0 계약에 다음을 반영해야 한다(체크섬 변경, 여전히 초안).

1. 일봉 권위 = grouped daily (D2), A 정책과 다름을 명시
2. `IPO_WARMUP` 원천 = 첫 등장 대리지표 (D3) 또는 수집 결정
3. `DELISTING_WINDOW`·`SYMBOL_CHANGE` = NOT_APPLIED, 한계 공시 (D4)
4. 쌍 제외 규칙과 사유 (D5), 워밍업 결손 기준선 제외 (D6)
5. 준비 게이트 4상태와 0.5%·2% 임계
6. 조기 마감일 차단 조건
7. `dataset_digest` 정의(미러 페이지 해시)
8. `DatasetFacts` Protocol 개정(현재 7개 메서드 → 5절의 정적 사실 기반)
9. 계약 준비 게이트의 "모든 유니버스 종목에 split 레코드 존재"를 "split 원천이 전 종목을 덮는다(없음 = split 없음)"로 정정. 현재 문구 그대로면 split이 없는 대다수 종목이 실패한다

## 13. 추가 수집

| 입력 | 현재 | 파생 가능 | 추가 수집 | API 콜 | 디스크 | 차단 |
| --- | --- | --- | --- | --- | --- | --- |
| 분봉 | 완료 | - | 불필요 | 0 | - | 아니오 |
| grouped daily | 104/104 | - | 불필요 | 0 | - | 아니오 |
| 종목별 일봉 | 383종목 | **grouped에서** | 불필요 | 0 | - | 아니오 |
| ticker 메타데이터 | 0 | scope는 아티팩트, IPO는 대리지표 | 선택(D3-B) | 약 3,882 | 작음 | 아니오 |
| splits | 덤프 완료 | - | 불필요 | 0 | - | 아니오 |
| 공시·티커 변경 | 없음 | 불가 | 원천 없음 | - | - | 아니오(한계) |
| 달력 | 코드 | - | 불필요 | 0 | - | 아니오 |

**필수 추가 수집: 0건.** 로컬 작업은 미러 약 2GB와 세션 캐시(**추정** 수 GB)뿐이며, 로컬 여유 공간은 877GB다.

## 14. 구현 파일 (다음 단계, 최소)

```text
backend/app/backtest/strategy_b_e0/
    mirror.py          Drive 분봉 → 로컬, 해시 대조, coverage_index, dataset_digest
    market_inputs.py   grouped daily 로더(일봉 뷰·첫 등장·거래량), splits 로더(충돌 처리)
    session_cache.py   미러 → 세션별 캐시, 세션 단위 lazy 읽기
    session_source.py  SessionSource 구현: tape·scope·rvol 링·splits·CA 플래그
    dataset_facts.py   정적 사실 + dataset_preflight.json
    preflight.py       (수정) 4상태 판정, 새 DatasetFacts 사용
backend/app/dev/run_strategy_b_e0.py   (수정) mirror / cache / preflight 명령 추가, DatasetNotWired 제거
```

재사용(수정 없음): `dataset.boundaries_for`, `preparation.warmup_dates`, `rvol.build_volume_profile`,
`corporate_actions.derive_corporate_action_flags`, `sparse_session.validate_sparse_session`,
`split_adjustment.known_splits`, `historical_store/raw_fetch.read_ledger`·`ledger_sessions`.
쓰지 않음: `dataset.materialize`, `preparation.plan_symbol_session`(scope를 버리고, 이중 권위가 되므로),
`strategy_b/adapter.py`·`run.py`·`result.py`(import 불가, 로직만 복사), `engine/driver.py`.
