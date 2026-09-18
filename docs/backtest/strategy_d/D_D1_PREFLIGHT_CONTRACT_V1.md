# Strategy D D1 Pre-flight Contract V1

작성 2026-09-18. **설계 문서만 있다.** 코드, 데이터 읽기, API 호출, D0/D2 문서 수정은 0이다.

목적: D1 실행 전에, 데이터 결과와 무관하게 정할 수 있는 D 전용 기술 계약을 모두 고정한다. 공통 Historical Store가 완료되면 D1을 바로 실행할 수 있게 하는 것이 목표다.

- 규칙 정본: `d_analog_rules_v1.json`, canonical sha256 `680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3`
- 선행 문서: `D_CONCEPT_V1.md`, `D_PIT_CONTRACT_V1.md`, `D_REUSE_MATRIX_V1.md`, `D_D2_IMPLEMENTATION_DESIGN_V1.md`, `D_D2_TEST_VECTORS_V1.md`
- 우선순위: **D0 규칙 JSON > D0 PIT 계약 > 이 문서 > D2 설계 문서.** 이 문서가 D2 설계와 다른 곳은 §12에 모았다. D2 설계 파일은 고치지 않고, 이 문서가 그 항목을 대체한다.

---

## 1. 시작 검증 (2026-09-18 실측)

| 확인 | 결과 |
| --- | --- |
| 브랜치 / HEAD | `main` / `0a96bd77873e7a88d09346fd873b9468833d6dbe` (origin보다 2커밋 앞섬, 미커밋 A/B/C/공통화 변경 다수. 이 작업은 건드리지 않음) |
| D0 canonical checksum | **MATCH** `680bf113...0cd3`. stdlib `json` + `hashlib`만으로 다시 계산했고 app 모듈은 import하지 않았다 |
| D0 파일 sha256 | `b6309ae4...52fd5`, `.sha256` 파일의 `file` 값과 일치 |
| D 코드 | 없음 (`backend/app/backtest/strategy_d_analog/` 없음) |
| 읽은 코드 (데이터 아님) | `historical_store/c_freeze.py`, `historical_store/snapshot.py`, `app/dev/build_historical_snapshot.py` 일부, `docs/backtest/COMMON_HISTORICAL_STORE_V1.md` |

### 1.1 Blocker 분류

| # | 내용 (D2 설계 §24) | 분류 | 이 문서에서 |
| --- | --- | --- | --- |
| B1 | D1 미실행, 공통화 진행 중 | **WAIT_FOR_COMMON_DATA** | 해소 조건만 §11에 명시 |
| B2 | 세션 grid 고정 | **RESOLVE_NOW** (의미 확정). 실제 값 `grid_start/end/count`는 D1이 기록 | §2 |
| B3 | 20D label CA 확장 | **WAIT_FOR_D2_IMPLEMENTATION** | 손대지 않음 |
| B4 | query 해시의 날짜 표기 | **RESOLVE_NOW** | §5 |
| B5 | C `load_panel`과 공통 배치 차이 | **WAIT_FOR_COMMON_DATA** (adapter 계약은 §8에서 확정, 배치 실측은 공통화 완료 후) | §8 |
| B6 | `engine.identity` import가 A 전략을 전이 로드 | **RESOLVE_NOW** (방향 확정). 구현은 D2 | §7 |
| B7 | 실행 RAM | **WAIT_FOR_D2_IMPLEMENTATION** | 손대지 않음 |
| B8 | grouped 중복 ticker 행 | **WAIT_FOR_D1** (측정). 기본 동작만 §10에서 HARD FAIL로 고정 | §10 |

---

## 2. B2: Canonical Session Grid 계약

### 2.1 정의

```text
grid = GROUPED_DAILY 파일 중 status = OK 인 세션을 날짜 오름차순으로 나열한 것
       (고정된 한 개의 공통 frozen dataset 안에서)
grid[0]   = 그 dataset의 첫 usable 세션
grid[i+1] = grid[i] 바로 다음 XNYS 세션 (같은 dataset 안)
index(date) = grid 안의 위치 (0부터)
```

D의 모든 세션 오프셋(창 `e-W..e`, label `d+1..d+h`, embargo `d+h <= D-W`, stride `d % 5`, `eval_start_idx = 260`, `eval_end_idx = N-1-20`, ADV20 `e-20..e-1`)은 이 index로만 계산한다. 달력일 산술은 쓰지 않는다.

### 2.2 grid 원천: snapshot의 `sessions`가 아니다

코드를 읽고 확인한 사실(2026-09-18):

- `build_historical_snapshot.py:80`은 `sessions_between(calendar, args.start, args.end)`로 snapshot의 `sessions`, `start_date`, `end_date`를 만든다. **달력에서 만든 값**이지 grouped 파일이 있는 세션이 아니다.
- 같은 파일의 coverage는 `grouped_daily.required = len(sessions)`, `existing = freeze["grouped_usable_sessions"]`로 둘을 따로 센다. 즉 snapshot 창은 usable grouped 세션보다 넓을 수 있다(예: 403으로 받지 못한 앞쪽 세션 포함).

그래서 D grid는 snapshot의 `sessions`/`start_date`에서 만들지 않는다. **snapshot이 담고 있는 C raw freeze 문서(`c_raw_freeze.json`)의 `files` 중 `file_type = GROUPED_DAILY`, `status = OK` 행의 `session_date`**에서 만든다. snapshot의 `start_date..end_date`는 "grid가 이 범위 안에 있다"는 교차 검증에만 쓴다.

### 2.3 검증 (D1이 실행, 하나라도 어기면 HARD FAIL)

| # | 검사 | 실패 |
| --- | --- | --- |
| G1 | 각 grid 날짜가 `MarketCalendar.is_trading_day` (XNYS) | HARD FAIL |
| G2 | 날짜가 엄격히 오름차순, 중복 없음 (date -> index 일대일) | HARD FAIL |
| G3 | 연속한 두 grid 날짜 사이에 빠진 XNYS 세션이 없음 (내부 결손 0) | HARD FAIL |
| G4 | `status != OK`인 grouped 세션은 **첫 usable 세션보다 앞**에만 있을 수 있다 (롤링 2년 창 밖 403). 이 세션은 grid에 넣지 않는다 | 앞이 아닌 위치(중간, 끝)에 있으면 HARD FAIL |
| G5 | 파일 안 `session` 값 == 행 `session_date` == 파일 이름 날짜 | HARD FAIL |
| G6 | 파일 sha256 == freeze 행 sha256 (읽는 위치가 C 로컬이든 공통 Drive든) | HARD FAIL |
| G7 | grid 전체가 snapshot `start_date..end_date` 안 | HARD FAIL |
| G8 | `grid_count` == freeze `grouped_usable_sessions`, `[grid_start, grid_end]` == freeze `usable_range` | HARD FAIL |

### 2.4 D1이 기록하는 값

```text
grid_start   = grid[0].isoformat()
grid_end     = grid[N-1].isoformat()
grid_count   = N
grid_digest  = sha256( "".join(d.isoformat() + "\n" for d in grid) ).hexdigest()
```

`FREEZE_V1`의 예상값(2024-09-17..2026-09-16, N=501)은 **참고값**이다. D2 설계 §1이 freeze 메타데이터에서 옮겨 적은 값이며, 이 문서는 이를 사실로 다시 선언하지 않는다. 확정값은 D1이 위 G1~G8을 통과시킨 뒤 기록한다.

### 2.5 grid가 바뀌면

stride 기준점(§4)과 `eval_start_idx`가 grid index에 묶여 있으므로 grid가 하루만 밀려도 라이브러리 전체가 바뀐다. 그래서:

- D1~D4 V1은 **하나의 `(freeze_digest, grid_digest)`**에 묶인다.
- 공통 store가 새 snapshot(USB-HIST-V2, 기간 연장 등)을 내도 D V1 run은 따라가지 않는다. 새 grid로 D를 돌리는 것은 새 데이터 버전의 새 study이며, D2 설계 §23.2대로 새 규칙 버전 선언이 필요하다.

---

## 3. Pattern Window Index 계약

query 종료 index를 `q`, 라이브러리 종료 index를 `d`라 하고 둘을 합쳐 `e`로 쓴다.

```text
창 입력 index     : [e-W, e-W+1, ..., e]      (W+1개)
표현 A 길이       : W+1      z_i = (P[e-W+i] - mean) / std_ddof0,   i = 0..W
표현 B 길이       : W        c_i = ln(P[e-W+i] / P[e-W]),           i = 1..W
P[t]              : raw close[t] / F[t]
```

| 조건 | 계약 |
| --- | --- |
| 창을 만들 수 있는 최소 index | 산술 조건은 `e >= W`. 그러나 D0 universe의 61봉 조건(`e-60..e`)이 모든 W에 걸리므로 **실제 적격 창은 `e >= 60`** (W=20/40도 같음) |
| 유효 close | 그 세션에 봉이 있고 close가 유한하며 `> 0`. W+1개 전부 필요. 61봉 조건이 이미 이를 포함 |
| 결측 | 보간, forward fill, back fill **금지**. 하나라도 없으면 창을 만들지 않음 (`NO_HISTORY`, 정상 제외) |
| 창 안 F | 적격 창은 `(e-60, e]`에 분할이 없으므로 F가 일정 (D2 설계 §6.3) |
| index 범위 | `e <= as_of_idx`. query는 `as_of = q`, 라이브러리 창은 `as_of = d`. 넘으면 HARD FAIL (R4) |
| 대응 | PatternKey의 `end_idx`는 이 `e`다 (§6) |

---

## 4. Library Sampling Index 계약

### 4.1 anchor는 이미 D0에 있다

D0 `library.library_stride_rule`: `"library end session index d with d % 5 == 0 on the session grid"`.

즉 anchor = 0이 **D0 규칙 원문**이다. 이 문서가 새로 고르는 값이 아니고, supplemental 계약도 필요 없다. 여기서는 index 기준만 명시한다.

```text
library end_idx 후보 : d ∈ {0, 5, 10, ...} (grid index 기준, anchor 0)
실제 적격 최소값     : d = 60 (61봉 조건. 60 % 5 == 0)
실제 상한 (W별)      : d <= eval_end_idx - W - min(h of W)   (가장 늦은 query의 embargo, D2 설계 §13)
query 날짜           : stride 없음. [260, N-1-20]의 모든 index
```

### 4.2 anchor가 데이터와 무관한 이유

- anchor는 grid index 0에 고정되고, grid index 0은 §2.1대로 "dataset 첫 usable 세션"이다. 가격, 수익률, label 값은 anchor 결정에 들어가지 않는다.
- 다만 grid 시작이 바뀌면 같은 달력일의 stride 소속이 바뀐다. 그래서 anchor의 의미는 `grid_digest`와 함께만 유효하다(§2.5, §9).

---

## 5. B4: Query / Hash 날짜 serialization

### 5.1 규칙

| 대상 | 표기 |
| --- | --- |
| 모든 D identity, key, 해시 입력의 날짜 | ISO 달력일 `YYYY-MM-DD` (`datetime.date.isoformat()`, 0 채움, 구분자 `-`) |
| 금지 | 시각, 시간대, epoch, `YYYYMMDD`, 로케일 형식, timestamp 문자열 |
| 기준 | grid 날짜 = XNYS 세션의 달력일 (ET). Massive grouped의 ms timestamp는 쓰지 않고 파일의 `session` 값을 쓴다 |

### 5.2 Query 표본 해시 (D0 `evaluation.query_sampling`의 구현)

```text
key_bytes = ("Q|20260917|" + D.isoformat() + "|" + ticker).encode("utf-8")
order_key = hashlib.sha256(key_bytes).hexdigest()       # 소문자 16진 64자
표본      = 그날 적격 ticker를 order_key 오름차순으로 정렬해 앞 300개
```

- `20260917`은 D0 문자열 그대로(10진수, 날짜가 아니라 seed 리터럴).
- `ticker`는 grouped 행의 `T` 값 그대로. 대소문자 변환, `.`/`-` 치환 등 정규화를 하지 않는다.
- `hexdigest` 문자열 순서는 digest 바이트 순서와 같다.
- `order_key`가 같은 두 ticker는 sha256 충돌이라 실제로 생기지 않는다. 방어용으로만 ticker 오름차순을 2차 키로 둔다(결과를 바꿀 수 없음).
- 예: `D = 2025-03-04`, `ticker = AAPL` -> `Q|20260917|2025-03-04|AAPL`.

### 5.3 N1 해시 (D3에서 쓰지만 같은 문제라 지금 고정)

D0 `baseline_N1.draw`: `sha256('N1|seed|replicate|query_date|query_ticker|lib_date|lib_ticker')`

```text
"N1|20260917|" + str(r) + "|" + D.isoformat() + "|" + query_ticker + "|" + d.isoformat() + "|" + lib_ticker
r = 0..19 (0부터, 10진수, 0 채움 없음)
```

replicate 번호의 시작값(0)은 D0에 없다. label을 보기 전에 정해야 결과와 무관하므로 여기서 고정한다. D3가 다른 값을 쓰려면 이 문서의 V2가 필요하다.

---

## 6. Logical Identity Model

### 6.1 원칙

- **행 수준 key**와 **run 수준 identity**를 나눈다. `freeze_digest`, `rules_checksum` 같은 run 공통 값은 행 key에 넣지 않고 run identity(§7)에 한 번만 넣는다. 완전한 이름은 `(run identity digest, 행 key)`다.
- key는 index(`end_idx`)로 둔다. ISO 날짜는 사람이 읽는 속성 열이다. 둘은 grid로 일대일이다.
- `stable_identity`(FIGI)는 **key가 아니다**. `(ticker, end_idx)`와 freeze가 정해지면 FIGI는 그로부터 결정되는 속성이다. D0는 동률 처리와 ticker cap을 ticker 문자열로 정했고 FIGI는 same-symbol 제외에만 쓴다.
- `metric`은 `representation`에서 결정된다(A -> Pearson, B -> Euclidean, D0 `similarity_methods`). 별도 key 필드로 두지 않는다. 둘을 따로 두면 "A + Euclidean" 같은 D0에 없는 조합이 key 공간에 생긴다.

### 6.2 확정 key

```text
PatternKey
  ticker      : str   (grouped T, 정규화 없음)
  end_idx     : int   (grid index, §2)
  window      : int   (20 | 40 | 60)
  속성        : end_date (ISO), figi_as_of_end (str | null)

QuerySampleKey          # horizon, representation과 무관. 14검정 공통
  query_date_idx : int
  ticker         : str
  속성           : sample_rank (0..299), order_key (§5.2), figi_as_of_D

TestId
  window, horizon, representation      # 문자열 W{W}_H{h}_{A|B}, D0 combinations x {A, B} = 14개

QueryKey
  test_id     : TestId
  query       : PatternKey  (window == test_id.window)

NeighborKey
  query_key   : QueryKey
  neighbor    : PatternKey  (window == query window)
  속성        : rank (1..50), metric_value, rank_score, label_end_idx = neighbor.end_idx + h
```

### 6.3 QueryKey에 horizon이 들어가는 이유

§10에서 분석한다. 요약: D0 embargo와 라이브러리 label 유효성이 h에 따라 후보 집합을 바꾸므로 같은 `(query, W, rep)`라도 h가 다르면 이웃 목록이 다르다. 그래서 horizon은 **검색 결과의 key 일부**이고 "평가용 부가 정보"가 아니다.

---

## 7. B6: D Run Identity import 격리

### 7.1 결정

| 구분 | 내용 |
| --- | --- |
| **D1-D4 TEMPORARY IDENTITY** | `backend/app/backtest/strategy_d_analog/identity.py` 순수 모듈 (D2 구현 시 작성). `app.backtest.engine.identity`를 **import하지 않는다** |
| **FINAL COMMON IDENTITY TARGET** | 공통 `research identity`가 A 전략 전이 import 없이 leaf 모듈로 분리된 뒤(예: `research.contract.checksum` 분리, D_REUSE_MATRIX §2.3 EXTEND 안) 그쪽으로 이동. A/B/C 회귀 통과 후에만. 이동 시 identity 값이 바뀌면 `identity_schema`를 올리고 기존 run은 그대로 둔다 |

D2 설계 §3 표의 run identity 행("REUSE, AST 직접 import 기준, 전이 로드는 스냅샷 테스트로 고정")은 이 결정으로 **대체**된다. 이유: 전이 로드를 스냅샷으로 "고정"해도 D 프로세스 안에 `app.strategy.engine`이 올라온다는 사실은 그대로이고, 이 identity가 D1부터 모든 산출물에 들어가므로 격리를 먼저 하는 편이 싸다. recipe가 짧아 따로 구현하는 비용이 작다.

### 7.2 Temporary identity 모듈 계약

| 항목 | 계약 |
| --- | --- |
| import 허용 | stdlib(`json`, `hashlib`, `pathlib`, `dataclasses`), D 내부 `models` |
| import 금지 (AST) | `app.backtest.engine.*`, `app.backtest.research.*`, `app.backtest.authority.*`, `app.strategy*`, `app.strategy_b*`, `app.services*`, `app.risk*`, `app.broker*`, `app.backtest.strategy_c_selection.*`, `app.backtest.strategy_c_v2.*` |
| 전이 검사 (`sys.modules`) | D 패키지 전체 import 후 `app.strategy`, `app.strategy.`, `app.strategy_b`, `app.services`, `app.risk`, `app.broker`, `app.backtest.engine.driver`, `app.backtest.engine.identity`로 시작하는 모듈 0. (C `panel`/`labels`가 `market.calendar`를 거쳐 `app.integrations.kiwoom.client`를 로드하는 것은 D_REUSE_MATRIX §5 실측대로 인프라 전이이므로 허용 목록에 명시) |
| canonical JSON | `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")` (D0 checksum recipe와 같음) |
| 숫자 | identity payload에는 정수와 문자열만. float가 필요하면 `repr` 문자열로 |
| code digest | D 패키지(`strategy_d_analog/`)의 `*.py`를 상대 경로 오름차순으로 `"{relpath}\t{sha256}\n"` 누적 sha256. `__pycache__` 제외. 테스트, 문서, 데이터 제외 |
| 제외 (nondeterministic) | 생성 시각, 호스트명, PID, 절대 경로, 사용자명, git dirty 여부. 이 값들은 identity에 넣지 않고 `run_context.json`(참고용, digest 대상 아님)에 둔다 |
| run id | `"<prefix>-" + sha256(canonical(payload)).hexdigest()[:12]`, 전체 digest는 `run_identity.json`에 함께 기록. prefix: D1 `dpit1`, D2 `dneigh1`, D3 `dsig1`, D4 `deval1` |

### 7.3 공통 필드 (D1~D4 모든 run identity)

```text
identity_schema     : "d-run-identity-v1"
strategy_id         : "HISTORICAL_ANALOG_V1"
phase               : "D1" | "D2" | "D3" | "D4"
rules_checksum      : 680bf113...0cd3
data                : §9 FreezeIdentity 전체
code                : { package: "app.backtest.strategy_d_analog", code_digest }
serialization       : { date: "iso-8601-date", query_hash: "Q|20260917|{D}|{ticker}",
                        n1_hash: "N1|20260917|{r}|{D}|{qt}|{d}|{lt}", replicate_base: 0 }
parent              : 이전 단계 run의 full digest (D1은 null)
```

단계별 추가 필드는 §10.3.

---

## 8. Common Historical Store -> D Adapter 계약

### 8.1 경계

- 저장 경로를 아는 D 모듈은 `source.py` **하나**다. 나머지 D 모듈은 Google Drive 경로, Parquet/JSON 배치, C raw 경로, freeze/snapshot 파일 구조, manifest 표를 모른다(AST 테스트: `source.py` 외 모듈에 `pathlib` 경로 리터럴, `historical_store` import 0).
- 공통화 쪽이 아래 API를 직접 제공할 필요는 없다. `source.py`가 무엇을 받든 이 형태로 바꾼다.
- 공통화 구현을 추측해 D 쪽에 복제하지 않는다. 확인된 사실만 쓴다: grouped 파일은 C 캐시와 **같은 바이트**로 `grouped_daily/<YYYY>/<date>.json.gz`에 복사되고(`c_freeze.promote`), freeze 행에 `relative_path`(C 로컬)와 `common_path`(공통)가 둘 다 있다.

### 8.2 D가 요구하는 논리 API (최소)

| API | 반환 | 계약 |
| --- | --- | --- |
| `freeze_metadata()` | `FreezeIdentity` (§9) | 로드 시 한 번 검증 끝난 값 |
| `session_grid()` | `SessionGrid(dates: tuple[date, ...], digest: str)` | §2의 G1~G8 통과분 |
| `membership(as_of_idx)` | `(N,) bool` + 거래소 | 최신 스냅샷 `as_of <= session(as_of_idx)`의 CS, D0 `allowed_primary_exchanges`. 스냅샷이 없는 앞쪽 날짜는 전부 False (정상 제외). **요청문의 최소 목록에 없지만 D0 universe에 필요해서 추가** |
| `panel_view(as_of_idx)` | `AsOfView`: close/open/high/low/volume `(T, N)`의 `rows <= as_of_idx`만 | 넘는 행 접근은 `PointInTimeViolation`. label 경로만 `EmbargoView`로 D 이후 행을 읽음 |
| `split_factor(ticker, session_idx, as_of_idx)` | `F(t)` | 실행일 `<= session(t)`인 분할만. encoder 경로에서 `t > as_of_idx`면 HARD FAIL. 벡터판은 `Panel.split_arrays()[0]` |
| `stable_identity(ticker, session_idx)` | `SymbolIdentity(ticker, composite_figi | None, as_of_snapshot)` | 최신 스냅샷 `as_of <= session` 기준 |

volume은 D V1에서 ADV20 필터에만 쓰지만 raw 필드로 둔다.

### 8.3 `source.py`의 입력

```text
load_daily_history(workspace_root, snapshot_id, *, expected: FreezeIdentity | None) -> DailyHistory
```

- `snapshot_id`는 **명시 인자**다. `state/historical/CURRENT_HISTORICAL_SNAPSHOT.json` 포인터를 따라가 암묵적으로 고르지 않는다(포인터는 나중에 V2로 움직일 수 있음). 포인터는 "지금 CURRENT가 무엇인가"를 run_context에 기록하는 데만 쓴다.
- 파일 위치: freeze 행의 `common_path`를 먼저, 없으면 `relative_path`(C 로컬 캐시). 어느 쪽이든 sha256이 freeze 행과 같아야 한다(G6). 두 위치가 다 있으면 공통 쪽을 읽고 run_context에 어느 쪽을 읽었는지 기록한다. 바이트가 같으므로 결과는 같다.
- grouped payload는 `wrapped-json-sort-keys` (`{format, session, body}`)다. provider 원본 바이트로 가정하지 않는다.
- daily authority는 `MASSIVE_GROUPED_DAILY` 하나. `per_symbol_daily`(`MASSIVE_TICKER_AGGREGATE`)로 결손을 채우지 않는다(공통 store §3 규칙과 같음).
- `expected`가 주어지면(D2~D4) 로드 결과와 필드별로 같아야 한다. 다르면 HARD FAIL.

### 8.4 B5 남은 부분

C `load_panel`이 공통 배치를 못 읽는 문제는 `source.py`가 파일 목록을 직접 넘기는 방식으로 흡수한다(D2 설계 §23.2). **실제 배치에서 C 로컬 경로와 공통 경로로 만든 Panel 배열이 bit 단위로 같은지**는 공통화 완료 후 D1에서 실측한다(WAIT_FOR_COMMON_DATA).

---

## 9. Data Freeze Identity 계약

### 9.1 FreezeIdentity 필드

| 필드 | 원천 | 비고 |
| --- | --- | --- |
| `snapshot_id` | 공통 snapshot (`USB-HIST-V1` 등) | 사람이 읽는 이름 |
| `snapshot_sha256` | `snapshot.json` 파일 sha256 | snapshot 내용 고정 |
| `freeze_id` | `c_raw_freeze.json`의 `freeze_id` (`STRATEGY_C_RAW_FREEZE_V1` 예상) | |
| `freeze_digest` | freeze의 `freeze_digest` (`relative_path\tsha256\tsize\n` 누적) | 경로가 C 로컬 상대경로라 **읽는 위치와 무관**하게 같은 값 |
| `source_digest` | freeze의 `c_raw_digest` | C `run.raw_digest` recipe |
| `d_read_digest` | D가 실제로 읽은 파일의 `relative_path\tsha256\n` 누적 | D가 freeze의 일부만 읽어도 무엇을 읽었는지 고정 |
| `daily_authority` | `"MASSIVE_GROUPED_DAILY"` | 공통 store 계약 |
| `first_session`, `last_session`, `session_count` | §2.4 `grid_start`, `grid_end`, `grid_count` | |
| `grid_digest` | §2.4 | |

### 9.2 규칙

- D1 실행은 정확히 하나의 `FreezeIdentity`에 묶인다. D1 PASS 후 이 값이 D2~D4의 `expected`가 된다.
- **모든 D1~D4 산출물 파일**은 최소 `freeze_id`, `freeze_digest`, `grid_digest`, `rules_checksum`을 담는다. JSON은 최상위 필드, Parquet은 schema metadata 키 `d_identity`(canonical JSON).
- 산출물을 읽을 때 이 네 값이 현재 run의 값과 다르면 HARD FAIL. 다른 freeze/grid/규칙의 산출물을 섞을 수 없다.
- `grid_digest`를 요청문의 필수 목록에 더한 이유: 같은 freeze라도 grid 구성 코드가 바뀌면 index가 바뀔 수 있는데, 그 차이를 `freeze_digest`는 잡지 못한다.

---

## 10. D2 / D3 identity와 horizon

### 10.1 질문

같은 D2 NeighborSet을 horizon마다 다시 계산하지 않고 D3가 재사용할 수 있는가? 즉 D2 결과가 horizon과 무관(horizon-neutral)할 수 있는가?

### 10.2 D0에 따른 답: **불가능하다**

D0 규칙에서 horizon `h`는 이웃 후보 집합에 두 번 들어간다.

1. **embargo**: `effective_rule: d + h <= D - W`. 같은 (D, W)라도 h가 크면 후보 prefix가 짧아진다.
2. **라이브러리 정의**: `library.definition: "... has a valid label for the tested horizon h ..."`, 유효성 `valid_h(d) = bar[d+1] & bar[d+h] & ~label_ca_suspect_h(d)`. `bar[d+5]`는 `bar[d+3]`에서 나오지 않고 h=20은 CA 창도 다르므로 **h끼리 포함 관계조차 없다.**

Top-K는 후보 집합의 함수이고 cap greedy는 순서에 의존하므로(D2 설계 §10.4), 후보 집합이 다르면 이웃 목록이 달라진다.

horizon-neutral 우회안도 검토했고 모두 D0 위반이라 기각한다.

| 우회안 | 문제 |
| --- | --- |
| W별 `h_max`로 embargo를 한 번만 적용 | h가 작은 검정의 라이브러리를 D0보다 줄임. `d + h <= D - W`는 "이 h"에 대한 식이다 |
| 모든 h의 유효성을 AND한 라이브러리 | "tested horizon h"에 대해 유효한 창을 뺌. 규칙 변경 |
| label 유효성을 검색 뒤에 적용 | 무효 창이 cap을 소모해 greedy 결과가 달라짐 |

### 10.3 결정

| 단계 | identity에 들어가는 것 | 결과 key |
| --- | --- | --- |
| **D2** (run 1개 = 14검정 전체) | 공통 필드(§7.3) + `tests` = 14개 `TestId` 목록(W, h, rep; rep이 metric을 정함) + `top_k=50` + library policy(`stride=5`, `anchor=0`, `ticker_cap=1`, `date_cap=5`, `same_symbol=ticker+figi`, `embargo="d+h<=D-W"`) + label **유효성** 계약 id(`d-label-validity-v1`: D0 validity + 20D CA 확장) + 구현 상수(`M0`, `QUERY_CHUNK`, `OPENBLAS_NUM_THREADS`, numpy/openblas 버전) | `QueryKey`, `NeighborKey`가 `TestId`를 통해 **h를 포함** |
| **D3** | 공통 필드 + `parent` = D2 run digest + label **값** 계약 id(`d-label-value-v1`: P0=O(D+1), clip, excess = 같은 날짜 universe 중앙값 차감, MFE/MAE) + N1 버전(`seed=20260917`, 20 replicate, 5분위, 해시 §5.3) + N2 버전(feature 5개, 백분위 표준화) | D2 key 그대로 + 신호/기준선 열 |

- 대부분의 규칙 값(K, stride, cap)은 이미 `rules_checksum`이 고정한다. 그래도 identity에 풀어 쓰는 이유는 사람이 run 디렉터리만 보고 무엇을 돌렸는지 알 수 있게 하려는 것이다. 판정은 `rules_checksum`으로 한다.
- label **유효성**은 D2에 이미 쓰이므로(20D CA 확장 포함, B3) D2 identity에 들어간다. label **값**은 D3부터다. D2 산출물에는 label 값이 없다(D2 설계 §12.3).
- **중복 계산 문제는 실제로 생기지 않는다.** D0 `combinations`는 7개로 동결돼 있고 각 (W, h) 쌍은 한 번씩만 나온다. D3는 D2 run 하나를 검정별로 1:1 소비하고, D2에 없는 horizon을 추가하지 않는다(추가하면 새 규칙 버전). 재사용은 "D3를 여러 번 돌려도 D2는 한 번"이라는 형태로 이뤄진다: D3 identity가 바뀌어도(N1/N2 구현 수정 등) `parent`가 같으면 D2를 다시 돌리지 않는다.
- horizon-neutral로 재사용 가능한 것은 **query 표본**(`QuerySampleKey`, 14검정 공통)과 **라이브러리 벡터 행렬**(W, rep별)이다. 같은 (W, rep)의 horizon끼리 score 행렬을 공유하는 최적화는 D2 설계 §15.4 1번 그대로 "결과 digest 동일" 테스트 뒤에만 가능하며 identity에는 영향이 없다(결과가 같아야 하므로).

---

## 11. Hard Fail / Normal Ineligible 경계

원칙(D2 설계 §18 그대로): **데이터나 규칙에서 자연스럽게 생기는 것**은 조용히 빼고 사유별로 센다. **코드나 입력이 계약을 어겨야만 생기는 것**은 멈춘다. `skip`으로 버그를 숨기지 않는다.

### 11.1 HARD FAIL (실행 중단, 산출물 COMPLETE 없음)

| 코드 | 조건 | 출처 |
| --- | --- | --- |
| R1 | 규칙 canonical checksum != `680bf113...` | D2 §18 |
| R2 | raw 파일 sha256 != freeze 행, `d_read_digest`/`freeze_digest`가 `expected`와 다름 | D2 §18, 이 문서 §9 |
| R3 | grid 검사 G1~G8 실패 (비거래일, 중복 세션, 내부 결손, 앞쪽 외 NOT_AVAILABLE, 파일/행 날짜 불일치, 범위 밖) | D2 §18 + 이 문서 §2.3 |
| R4 | encoder/universe 입력 행 `> as_of_idx` (future data view) | D2 §18 |
| R5 | universe를 통과한 창에 NaN 또는 `<= 0` 가격 | D2 §18 |
| R6 | embargo 후 후보에 `end_idx + h > D - W` | D2 §18 |
| R7 | 채택 이웃에 `d + h > D - W` | D2 §18 |
| R8 | `d + h > D - W`인 (d, h)의 label 유효성/값 요청 (`EmbargoView`) | D2 §18 |
| R9 | 채택 이웃에 same ticker / same FIGI | D2 §18 |
| R10 | cap 위반, rank 불연속, 이웃 중복 | D2 §18 |
| R11 | 라이브러리 `(end_idx, ticker_col)` 중복 또는 비정렬, `end_idx % 5 != 0` 또는 `< 60`인 라이브러리 행 | D2 §18 + 이 문서 §4 |
| R12 | 적격 행 score 비유한 | D2 §18 |
| F1 | 산출물의 `freeze_id`/`freeze_digest`/`grid_digest`/`rules_checksum`이 현재 run과 다름 (freeze 혼합) | 이 문서 §9.2 |
| F2 | D3가 `COMPLETE.json` 없는 D2 run을 읽으려 함, 또는 `parent` digest 불일치 | D2 §22.2 |
| F3 | 같은 세션 grouped 파일에 같은 ticker 행이 2개 이상 (B8). **기본 동작은 HARD FAIL**이다. C `load_panel`처럼 조용히 덮어쓰지 않는다. D1이 개수를 측정하고, 0이 아니면 D1 BLOCKED로 두고 처리 방식은 사용자 결정 | D2 §24 B8 |
| F4 | query 날짜로 `[260, N-1-20]` 밖 index 요청 | D0 `evaluation` |
| F5 | query 해시 문자열이 §5.2 형식과 다름 (serialization 테스트 벡터 불일치) | 이 문서 §5 |

### 11.2 NORMAL INELIGIBLE (제외 후 사유별 집계)

| 코드 | 조건 | 단위 | 출처 |
| --- | --- | --- | --- |
| `NOT_MEMBER` | 스냅샷 소속 아님, 거래소 밖, 해당 날짜에 `as_of <= D` 스냅샷 없음 | ticker-date | D2 §6.2 |
| `NO_HISTORY` | `e-60..e` 61봉 중 결측 (W+1 close 부족 포함) | ticker-date | D2 §6.2 |
| `LOW_PRICE` | raw close(e) < 3.0 | ticker-date | D2 §6.2 |
| `LOW_ADV` | ADV20 < 5,000,000 | ticker-date | D2 §6.2 |
| `SPLIT_WINDOW` | `(e-60, e]` 분할 | ticker-date | D2 §6.2 |
| `CA_SUSPECT` | `e-59..e` 비율 3배 이상/1/3 이하 | ticker-date | D2 §6.2 |
| `VECTOR_UNDEFINED` | 표현 A 상수 창(`np.ptp == 0`), 표현 B 비유한 | 창 x rep | D2 §7, §8 (R14/R15) |
| `LABEL_INVALID_h` | 라이브러리 창의 `NO_ENTRY_BAR`, `MISSING_HORIZON_BAR`, label CA 의심 | 라이브러리 창 x h | D2 §10.3 (R14) |
| `OFF_STRIDE` | 라이브러리 후보가 `d % 5 != 0` | 라이브러리 후보 | D0 (구성 단계에서 애초에 만들지 않음, R11과 구별: 만들어진 행에 있으면 HARD FAIL) |
| `INSUFFICIENT_NEIGHBORS` | 정책 적용 후 채택 < 50 | query x 검정 | D0, R16 |
| query label 무효 | query 쪽 `NO_ENTRY_BAR`/`MISSING_HORIZON_BAR`/CA | query x h | D0 `query_sampling` (D2는 빼지 않고 D4가 빼서 셈) |
| FIGI null | FIGI 비교에서 빠짐. 제외가 아니라 비교 생략 | 창 | D0 `figi_rule` |
| 평가일 query < 100 | `min_valid_queries_per_date` 미달 날짜 | 날짜 x 검정 | D0 (D4) |

### 11.3 경계에서 혼동하기 쉬운 쌍

| 겉모습 | 판정 | 이유 |
| --- | --- | --- |
| 창에 결측 봉 | universe 전이면 `NO_HISTORY`, universe 통과 후 encoder에서 보이면 R5 HARD FAIL | 같은 사실이 어느 단계에서 보이느냐로 갈린다 |
| stride 밖 d | 후보 생성 전이면 `OFF_STRIDE`(애초에 없음), 라이브러리 행에 있으면 R11 | |
| `d + h > D - W` | 후보 prefix cut으로 빠지는 것은 정상(집계도 안 함, 정의상 후보가 아님). cut 뒤에 남아 있으면 R6 | |
| grid 앞쪽 403 세션 | grid에 넣지 않음(정상). 중간/끝에 있으면 R3 | §2.3 G4 |
| grouped 중복 행 | 측정 전 기본값 HARD FAIL (F3) | 조용한 덮어쓰기는 결정성은 있어도 어느 행이 이겼는지 기록되지 않음 |

---

## 12. D2 설계 문서 대비 변경/보충 (cross-reference)

D2 설계 파일은 수정하지 않는다. 아래는 이 문서가 대체하거나 보충하는 항목이다.

| D2 설계 위치 | 기존 | 이 문서 |
| --- | --- | --- |
| §3 세션 grid 행, §16, §24 B2 | "D grid = FREEZE_V1 501세션, index 0 = 2024-09-17" (사실처럼 기술) | 의미만 확정(§2). 값은 D1이 G1~G8 뒤에 기록. grid 원천은 snapshot `sessions`가 아니라 freeze GROUPED_DAILY OK 행 |
| §3 run identity 행, §24 B6 | `engine.identity` REUSE + 전이 로드 스냅샷 테스트 | D 전용 Temporary identity 모듈, `engine.identity` import 금지(§7) |
| §16, §24 B4 | ISO 제안, D1에서 고정 | ISO 확정, 해시 바이트 형식까지 고정(§5). N1 replicate 0부터도 고정 |
| §12.1 `test_id` | `W{W}_H{h}_{A|B}` | 그대로. horizon이 key에 필요한 근거 추가(§10) |
| §20.1 `run_identity.json` | 필드 목록 | §7.3, §9.1, §10.3 필드로 구체화. `grid_digest`, `snapshot_id`, `snapshot_sha256`, `d_read_digest` 추가 |
| §18 R1~R16 | 표 | F1~F5 추가, 경계 쌍 명시(§11) |
| §23 `load_daily_history(freeze_manifest, resolve)` | freeze 경로 + resolver | `(workspace_root, snapshot_id, expected)`, 포인터 암묵 추종 금지, `membership` API 추가(§8) |
| §24 B8 | D1 판정 사항 | 측정 전 기본 동작 HARD FAIL로 고정(§11.1 F3) |

---

## 13. D1 실행 전제 (Pre-flight 이후 남은 조건)

| # | 조건 | 분류 |
| --- | --- | --- |
| P1 | 공통 snapshot이 `status = FROZEN`이고 그 `c_raw_freeze.json`의 grouped 파일이 공통 경로에서 sha256 일치 (promotion `verified = true`) | WAIT_FOR_COMMON_DATA (B1, B5) |
| P2 | 공통화 수집/승격 프로세스가 끝남 (`ps` 확인. 같은 Massive 키 동시 호출 금지는 D가 API를 안 쓰므로 해당 없음, 파일 쓰기 경합만 확인) | WAIT_FOR_COMMON_DATA |
| P3 | D0 checksum 재계산 MATCH | 매 단계 |
| P4 | 이 문서의 sha256을 D1 run identity의 `preflight_contract_sha256`에 기록 | D1 |

D1이 기록할 것: `FreezeIdentity` 전체(§9.1), G1~G8 결과, 적격 규모, FIGI null 비율, grouped 중복 행 수(B8), 스냅샷 없는 앞쪽 날짜 수, C 로컬 vs 공통 경로 Panel bit 동일성(B5).

---

## 14. Pre-flight 판정

| 완료 기준 | 상태 |
| --- | --- |
| B2 index/grid 의미 확정 | DONE (§2) |
| stride anchor | DONE. anchor 0은 D0 원문(§4) |
| B4 날짜 serialization | DONE (§5) |
| Pattern/Query/Neighbor key | DONE (§6) |
| B6 identity 격리 방향 | DONE (§7) |
| Data adapter 계약 | DONE (§8). 배치 실측만 공통화 후 |
| Freeze identity | DONE (§9) |
| D2/D3 horizon 분석 | DONE (§10). D2 NeighborSet은 horizon-neutral 불가, h는 key의 일부 |
| Hard fail / ineligible | DONE (§11) |
| D0 checksum 변경 | 0 |
| 전략 코드 변경 / 데이터 읽기 | 0 / 0 |

**PREFLIGHT GATE: PASS.** D1은 §13 P1, P2가 충족되면 실행할 수 있다.
