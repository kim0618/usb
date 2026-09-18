# Strategy D D2 Implementation Design V1

작성 2026-09-18. **설계 문서만 있고 코드는 0이다.** D1 Data/PIT Gate가 PASS하기 전에는 이 문서의 어떤 모듈도 만들지 않는다.

- 규칙 정본: `d_analog_rules_v1.json`, canonical sha256 `680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3`
  (2026-09-18 재계산해서 일치 확인. `strategy_c_selection.rules.canonical_checksum`과 같은 recipe를 hashlib로 따로 계산했고, app 모듈은 import하지 않음)
- 기준 저장소: `main` HEAD `0a96bd7`. 미커밋 A/B/C 변경과 진행 중인 공통화 작업은 **읽기만** 했다.
- 선행 문서: `D_CONCEPT_V1.md`, `D_PIT_CONTRACT_V1.md`, `D_REUSE_MATRIX_V1.md`
- 짝 문서: `D_D2_TEST_VECTORS_V1.md` (테스트 벡터 25개와 기대값)

이 문서가 D0와 부딪히면 D0가 이긴다. 요청문의 초안 값과 D0가 다른 곳은 §0에 모아 두었다.

---

## 0. 요청문 초안과 D0 동결 규칙이 다른 곳 (D0를 따름)

| 항목 | 요청문 초안 | D0 동결값 (이 설계가 따르는 값) |
| --- | --- | --- |
| 창 길이 | W개 세션, `window_start = D-(W-1)` | **W+1개 종가** `P(D-W..D)`. 수익률은 W개. `window_start = D-W` |
| 표현 B 길이 | W 또는 W-1, 첫 0 포함 여부 미정 | `c_i = ln(P(D-W+i)/P(D-W))`, i=1..W, **길이 W**, 첫 0은 넣지 않음 |
| std | population/sample 미정 | **ddof=0** (population) |
| 동률 처리 | similarity DESC, pattern_end ASC, stable_identity ASC | similarity 순서, 그다음 **라이브러리 종료 index 오름차순, ticker 오름차순**. stable_identity로 바꾸면 규칙 변경이다 |
| embargo 불변식 | neighbor window end < query window start | **`d + h <= D - W`**. 이 식은 `d + h = D - W`(이웃 label의 마지막 세션이 query 창의 첫 세션과 같은 경우)를 **허용한다**. §10.2 참고 |
| 종목 cap | symbol 기준 | **ticker 기준**, 종목당 최대 1창 (`max_windows_per_neighbor_ticker`) |
| Euclidean on A | 미정 | 따로 검정하지 않음. `||z_q-z_n||^2 = 2(W+1)(1-rho)`라서 순위가 Pearson과 같다 |

---

## 1. 시작 상태 (2026-09-18 실측)

| 확인 | 결과 |
| --- | --- |
| 브랜치 / HEAD | `main` / `0a96bd77873e7a88d09346fd873b9468833d6dbe` |
| D0 canonical checksum | **MATCH** `680bf113...0cd3` |
| D 코드 | 없음 (`backend/app/backtest/strategy_d_analog/` 없음) |
| 공통화 진행 | `backend/app/backtest/historical_store/` (`c_freeze.py`, `coverage.py`, `raw_fetch.py`, 10:07~10:09 KST 작성), `app.dev.fetch_common_historical_raw` 실행 중(per_symbol_daily 8/244), 다른 프로세스가 `c_freeze.promote`로 C raw를 Drive `1_US-B`에 바이트 복사하는 중 |
| C raw freeze | `data/runtime/common_hist/STRATEGY_C_RAW_FREEZE_V1.json`: 511 파일, grouped 502개 중 쓸 수 있는 세션 501개 (2024-09-17..2026-09-16), 쓸 수 없는 1개 `grouped/2024-09-16`, `c_raw_digest adc4f191...`, `freeze_digest 9ebd6c29...` |
| C run panel 규모 (`cmsel1-855b6a0c` summary) | 세션 501, panel 종목 6,341, 세션당 grouped 행 5,053~5,242, 분기 CS 스냅샷 8개(5,133~5,305) |
| C 기본 적격(날짜당) | 431일에 걸쳐 2,372~2,803, 중앙값 2,607 (`features_base_eligible.parquet` 메타만 읽음). D universe는 여기에 61봉 연속과 60세션 CA/분할 제외를 더하므로 이 값 이하 |
| 메모리 | 총 9.7 GiB, 사용 가능 5.2 GiB, swap 1.1 GiB 사용 중 (다른 세션의 수집기와 성능 harness가 실행 중) |
| 라이브러리 | numpy 2.5.2 (scipy-openblas 0.3.34, DYNAMIC_ARCH), pandas 2.3.3, pyarrow 23.0.1. **scipy, numba, faiss, polars, duckdb 없음** |
| 합성 gemm 측정 | 50x61 @ 61x200k float64 한 번에 0.028초, 약 44 GFLOPS (난수 행렬, 다른 프로세스와 CPU 공유 중) |
| argpartition 측정 | 200k float64 한 행에 약 2.4 ms |

공통화 세션이 바꾸는 파일(`historical_store/*`, `workspace/manifest.py`, `integrations/massive/*`, C raw 캐시, `data/runtime/common_hist/*`)은 이 작업에서 읽기 전용이다.

---

## 2. D2의 역할

```text
PIT-safe Historical Daily Data  (source.py: data adapter, D에서 유일하게 경로를 아는 곳)
        |
Universe mask  (universe.py)   +  Neighbor label validity mask  (label_extension.py)
        |
Pattern Window Builder + Encoder  (encoder.py)
        |
Historical Pattern Library  (library.py: 행렬 + 메타데이터 열)
        |
Similarity  (similarity.py)
        |
Neighbor Search: embargo, same ticker/FIGI, cap, Top-K  (neighbor_search.py)
        |
Eligible Top-K Historical Analogs  -> artifacts (D3 입력)
```

D2 출력은 **이웃의 신원(identity)**까지다. 이웃의 forward return 값, `S(q)`, `sigma(q)`, IC, 기준선은 D3/D4 몫이다.
D2에는 BUY/SELL, entry, exit, sizing, stop, portfolio, broker, PnL이 없다.

---

## 3. 재사용 자산 경계 (CURRENT / TARGET / D2 ASSUMPTION)

경로는 `backend/app/` 기준이다. Alpha 로직(C `features`, `rules.SelectionRules`, `evaluate`, `run`, `pit_audit`)은 재사용하지 않는다.

| 자산 | CURRENT (2026-09-18 실측) | TARGET (공통화 진행 방향, 미확정) | D2 ASSUMPTION |
| --- | --- | --- | --- |
| 달력 | `market/calendar.py` `MarketCalendar.is_trading_day/session` | 그대로 | REUSE. 세션 grid의 교차 검증에만 씀 |
| 세션 grid | C `run.usable_sessions`(앞쪽 403 제거, 내부 결손 거부) + freeze `usable_range` | freeze manifest의 `GROUPED_DAILY` + `status=OK` 행 | **D grid = STRATEGY_C_RAW_FREEZE_V1의 쓸 수 있는 세션 501개, index 0 = 2024-09-17**. D가 직접 만든다(`app.dev.*`는 import하지 않음) |
| grouped daily raw | `data/runtime/strategy_c/raw/grouped/<date>.json.gz` (wrapped payload `{format, session, body}`) | `1_US-B/market_data/raw/massive/grouped_daily/<YYYY>/<date>.json.gz`, 같은 바이트(`c_freeze.promote`) | 두 위치 모두 freeze의 `relative_path`와 sha256으로 찾는다. 경로 규칙은 `source.py` 안에만 둔다 |
| 참조 스냅샷 | `raw/tickers/CS_<as_of>.json.gz` 8개, `TICKER_FIELDS`에 `composite_figi`, `share_class_figi` 있음 | `market_data/raw/massive/reference_tickers/` | C `load_snapshots`는 ticker 집합만 반환한다. FIGI는 D `source.py`가 같은 파일을 따로 읽는다 |
| 분할 | `raw/splits/splits_2024-09-16_2026-09-16.json.gz`, C `panel.load_splits`, `Panel.split_arrays()` -> `F(t)` | `market_data/raw/massive/splits/` | REUSE `SplitEvent`, `load_splits`, `Panel.split_arrays` |
| Panel | `strategy_c_selection/panel.py` `Panel`, `load_panel`, `truncate`, `with_changes` (import는 `raw_fetch` 경로 함수뿐) | 공통화 쪽에서 전략 중립 reader가 생길 수 있음 | REUSE `Panel` 타입과 `truncate`/`with_changes`. **`load_panel`은 CURRENT 배치(`root/grouped/<date>`)만 읽는다**. TARGET 배치(`grouped_daily/<YYYY>/`)에서 읽으려면 adapter가 파일 목록을 따로 넘겨야 함 (§23, BLOCKER B5) |
| label | `strategy_c_selection/labels.py` `compute_labels`, `LabelSet.no_entry_bar`, `.label_ca_suspect`, `LABEL_CA_HORIZON=10` | C 판정 후 중립화 가능 | D2는 **유효성 mask만** 쓴다. `compute_labels(panel, horizons=(1,), ca_ratio=3.0)`로 horizon과 무관한 두 mask를 얻고, `d+h` 봉 존재와 20D CA 확장은 D가 계산 (§10.3) |
| canonical checksum | `strategy_c_selection/rules.py` `canonical_checksum` (2줄) | - | D `config.py`가 같은 2줄을 따로 구현하고, 테스트에서만 C 함수와 같은 값인지 확인한다. D 런타임 코드는 C `rules`를 import하지 않음 (D0 §4 금지 목록) |
| safe write | `backtest/workspace/safe_write.py` `write_bytes_atomic`, `safe_write`, `sha256_file` | 그대로 | REUSE (산출물 쓰기) |
| writer lock | `backtest/workspace/lock.py` `writer_lock` | 그대로 | D2는 로컬 산출물만 쓰므로 쓰지 않는다. Drive 게시는 D4 `engine/store.write_run` 경유 |
| run store | `backtest/engine/store.py` `write_run`, `load_complete`, `RESEARCH_RUN_SCHEMA` | 그대로 | D2는 쓰지 않음 (D4) |
| run identity | `backtest/engine/identity.py` `run_identity`, `code_digest`, `package_files`, `source_provenance` | 그대로 | D0 §2.3 권고대로 REUSE하되 AST 직접 import 기준으로 둔다. 전이 로드(`app.strategy.engine`)는 스냅샷 테스트로 고정한다 |
| raw digest recipe | C `run.raw_digest` (`relpath\tsha256\n` 누적) | freeze `c_raw_digest`, `freeze_digest` | D `source.py`가 같은 recipe를 구현한다(C `run` import 금지). freeze 행 sha256과 파일별로 대조 |
| table digest recipe | C `run.table_digest` (CSV `%.17g`) | - | 작은 표에만 같은 recipe를 쓰고, 이웃 표(수백만 행)는 열 바이트 digest를 쓴다 (§20.3) |

---

## 4. 패키지와 모듈

### 4.1 위치

`backend/app/backtest/strategy_d_analog/`, 테스트 `backend/tests/strategy_d/`, CLI `backend/app/dev/run_strategy_d_neighbors.py`.

근거: 현재 `backend/app/backtest/`에 `strategy_b/`, `strategy_c_selection/`, `strategy_c_v2/`가 있다. 형식은 "전략 + 연구 목적" snake_case이고, 테스트는 `backend/tests/strategy_c/`, `backend/tests/strategy_b/`이며, CLI는 `app/dev/run_strategy_c_selection.py` 형식이다. D0 후보 경로가 이 규칙과 맞으므로 그대로 쓴다.

### 4.2 D2 모듈 (D0 §3 목록 대비 변경점 표시)

| Module | Responsibility | Inputs | Outputs | Dependencies | Must NOT import | Pure / IO | D phase |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `config.py` | 규칙 JSON 로드, canonical checksum 재계산, 선언값과 다르면 `RulesChanged`, 타입 있는 접근자(W, h 조합, K, stride, cap, universe 문턱, seed) | 규칙 JSON 경로 | `DRules` (frozen) | stdlib(json, hashlib) | C `rules`, 전 app 모듈 | IO(읽기 1회) | D1/D2 |
| `models.py` | `PatternKey(ticker, end_idx, window)`, `TestId(window, horizon, rep)`, `Exclusion` enum, `QueryStatus` enum, `NeighborSet` 배열 묶음, `LibraryManifest` | - | dataclass/enum | stdlib, numpy | 전 app 모듈 | Pure | D2 |
| `source.py` **(신규)** | Data adapter. freeze manifest 검증(파일 sha256), 세션 grid 구성, `Panel` 생성, FIGI 표 읽기, `dataset_digest` | freeze manifest, raw 파일 | `DailyHistory` (§23) | C `panel`(Panel, load_splits, load_snapshots), `market.calendar`, `workspace.safe_write.sha256_file` | 나머지 C 모듈, `historical_store.raw_fetch`(수집 코드), Massive client | **IO** (D에서 입력 경로를 아는 유일한 곳) | D2 |
| `universe.py` | `(T, N)` 적격 mask와 사유 코드: 스냅샷 소속, 거래소, close >= 3 (raw), ADV20 >= 5M (D-20..D-1 raw), 61봉 연속, 분할 `(D-60, D]`, CA 의심 `D-59..D` | `DailyHistory` | `UniverseMask(eligible, reason)` | numpy, models | `label_extension`, `neighbor_search`, C `features` | Pure | D2 (D0는 D1) |
| `label_extension.py` **(D3 -> D2로 앞당김)** | 이웃 label **유효성** mask만: `d+1` 봉, `d+h` 세션 봉, label CA 의심(h<=10은 C 그대로, h=20은 D+11..D+20 추가) | `Panel` | `LabelValidity[h] (T, N) bool` | C `labels.compute_labels`, numpy | encoder, similarity, universe | Pure | D2 |
| `sampling.py` **(신규)** | 날짜별 query 표본: 적격 ticker를 `sha256('Q|20260917|<D>|<ticker>')` 순으로 정렬해 앞 300개 | 적격 mask, 세션, ticker | `(date_idx, ticker_col, sample_rank)` | hashlib, numpy | label 전부 | Pure | D2 |
| `encoder.py` | 창 gather와 표현 A/B 벡터, 벡터 정의 여부 | `P = close/F` (as_of 이하 행만), 창 키 목록 | `float64 (n, W+1)` / `(n, W)` + `defined` | numpy, `pit` | `label_extension`, `neighbor_search`, C `labels` | Pure | D2 |
| `library.py` **(신규)** | W별 라이브러리: stride 5, 적격, 행 정렬 `(end_idx, ticker)`, 표현별 행렬, 메타데이터 열, horizon별 유효 mask | universe, validity, encoder | `PatternLibrary` (§13) | encoder, models, numpy | C `labels`(validity는 인자로 받음) | Pure | D2 |
| `similarity.py` | Pearson(A), Euclidean(B), 청크 행렬곱, `rank_score` | query 행렬, 라이브러리 slice | `metric_value`, `rank_score` 2D 배열 | numpy | label, 날짜, universe | Pure | D2 |
| `neighbor_search.py` | query 날짜별 prefix cut(embargo), same ticker/FIGI 제거, Top-M pool, 결정적 정렬, cap greedy, 사후 불변식 | 라이브러리, query 벡터, `EmbargoView` | `NeighborSet` | similarity, pit, models | C `labels`, `signal`, `baselines` | Pure | D2 |
| `pit.py` **(신규)** | 런타임 불변식: `AsOfView`, `EmbargoView`, `PointInTimeViolation`, 사후 검사 함수 | - | 예외, 검사기 | numpy, models | 전략 모듈 | Pure | D2 |
| `pit_audit.py` | 합성 fixture mutation audit (D2), 실데이터 감사(D4) | 전 모듈 | audit dict | D 모듈, C `panel.truncate/with_changes` | C `pit_audit`, C `features` | Pure | D2~D4 |
| `artifacts.py` **(신규)** | D2 산출물 쓰기/읽기, content digest, 경로는 인자로 받음 | NeighborSet, 라이브러리 메타 | parquet/json 파일 | pyarrow, `workspace.safe_write` | engine/store (D4에서만) | IO | D2 |

D3/D4로 미루는 것: `signal.py`, `baselines.py`, `evaluate.py`, `run.py` (D0 §3 그대로).

import 경계 테스트(D0 §4)는 D2 첫 커밋에 들어간다. 모듈별 금지 import는 위 표의 "Must NOT import" 열을 AST 테스트로 고정한다.

---

## 5. PatternWindow 데이터 모델

### 5.1 논리 모델

한 창은 `PatternKey(ticker, end_idx, window)` 하나로 정해진다. 나머지는 모두 여기서 계산하거나 run 수준 값이다.

| 요청 필드 | 결정 | 저장 위치 |
| --- | --- | --- |
| `symbol` | `ticker_col` (int32, `Panel.tickers` index) | 라이브러리 메타 열 |
| `stable_identity` | `(ticker, composite_figi as of end_idx 스냅샷)`, FIGI는 `figi_code` int32 (-1 = null) | 메타 열 + FIGI 문자열 표 1개 |
| `window_start` | 저장 안 함. `end_idx - W` | 계산 |
| `window_end` | `end_idx` int32 (세션 grid index) | 메타 열 |
| `window_length` | 라이브러리 단위 상수 W | manifest |
| `query_available_at` | 저장 안 함. 규칙: `session(end_idx)+1` 04:00 ET | run 수준 규칙, 검사는 §18 |
| `close_raw[]` | **저장 안 함**. Panel과 키로 다시 만든다 | - |
| `representation_A[]`, `_B[]` | W별 dense 행렬(메모리). 파일로 저장하지 않음 (D3는 벡터가 필요 없음) | 메모리 |
| `split_flags` | 저장 안 함. 적격 창에는 `(D-60, D]` 분할이 없다 (§6.3) | 제외 사유 집계 |
| `eligibility_status` | 적격 창만 라이브러리 행이 된다. 부적격은 사유별 개수만 | manifest |
| `source_dataset_digest` | 행마다 넣지 않고 run identity에 한 번 | `run_identity.json` |

### 5.2 저장형 vs 계산형

| 안 | 내용 | 판정 |
| --- | --- | --- |
| A. 정규화 벡터 전부 저장 | 6개 행렬(3 W x 2 표현)을 파일로 | 기각. 다시 만드는 데 몇 초면 되고, 파일 digest 관리 부담만 늘어남 |
| B. raw index만 저장, 필요할 때 계산 | 검색마다 창 재계산 | 기각. query 날짜마다 같은 창을 다시 계산함 |
| **C. dense 행렬(메모리) + 메타데이터 열 분리** | W마다 한 번 encode, 행렬은 메모리에만. 메타는 parquet 산출물 | **채택** |

dtype은 **float64**다. float32로 줄여도 절약은 약 240 MB뿐이고, 대신 1e-7 수준 동률이 늘어나 동률 처리 규칙이 결과를 좌우하는 경우가 생긴다. C Panel도 float64다.

---

## 6. Window Builder 계약

### 6.1 정의

- query/라이브러리 공통: 종료 세션 `e`(query는 D, 라이브러리는 d), 창 = 세션 grid index `e-W .. e`, **종가 W+1개**.
- 달력일은 쓰지 않는다. index는 §3의 D grid 기준이다.
- 가격: `P(t) = raw close(t) / F(t)`. F는 `Panel.split_arrays()[0]`.

### 6.2 적격 (universe, `e` 시점 값만 씀)

| 조건 | 식 | 실패 사유 코드 |
| --- | --- | --- |
| 스냅샷 소속 | 최신 스냅샷 `as_of <= e`에 CS로 있음, `market=stocks` | `NOT_MEMBER` |
| 거래소 | `primary_exchange` in {XNYS, XNAS, XASE, ARCX, BATS} | `NOT_MEMBER` (C `load_snapshots`가 함께 거름) |
| 이력 | 세션 `e-60..e` 61개 전부 close 존재 | `NO_HISTORY` |
| 가격 | raw close(e) >= 3.0 | `LOW_PRICE` |
| 유동성 | mean(raw close x raw volume, `e-20..e-1`) >= 5,000,000 | `LOW_ADV` |
| 분할 | 실행일이 `(session e-60, e]` 안에 있는 분할 없음 | `SPLIT_WINDOW` |
| CA 의심 | `t in e-59..e`에서 `P(t)/P(t-1) >= 3` 또는 `<= 1/3` 없음 | `CA_SUSPECT` |
| 라이브러리 전용 | `e % 5 == 0`, `e >= 60` | (stride 밖은 라이브러리 후보가 아님) |

close 기준은 D0 PIT 계약 §3 표의 "close >= $3 | D close"다. C `features.hard_filter`와 같게 **raw close**로 해석했다. `P`는 F가 grid 시작점부터 누적되므로 경제적 가격이 아니다. 이 해석은 D1 보고서에 명시한다.

### 6.3 결측과 분할

- 보간, forward fill, back fill은 **금지**다. 61봉 중 하나라도 없으면 `NO_HISTORY`이고 벡터를 만들지 않는다.
- 61봉 조건이 W=20/40/60 모두에서 같다. 그래서 **세 W의 라이브러리 행 집합은 같고** 벡터 차원만 다르다(표현 A의 상수 창 제외는 §7).
- 분할 제외가 `(e-60, e]`라서 적격 창 안에서는 F가 일정하다. W=60이고 분할이 정확히 `e-60`에 실행된 경우도, F가 창 전체에 이미 들어가 있어 일정하다. 그래서 적격 창의 경로는 raw close 비율과 같다(반올림 차이만).
- 적용 순서: raw -> `P = close/F` (Panel 전체에 한 번) -> 창 gather -> 정규화.
- encoder가 NaN이나 `as_of` 뒤의 행을 받으면 **hard fail**이다. universe를 통과한 창에 NaN이 있다는 것은 계약 위반이다.

### 6.4 gather 방식 (결정성)

창 행렬은 `P[end_idx[:, None] + arange(-W, 1)[None, :], ticker_col[:, None]]`로 C-contiguous `(n, W+1)` float64를 만든다. 축소 연산은 항상 `axis=1`이다. query 창과 같은 (ticker, d) 라이브러리 창은 같은 함수와 같은 배치 순서로 계산되므로 bit 단위로 같아야 한다(테스트 V4b).

---

## 7. Representation A: z-normalized close path

```text
x_i   = P(e-W+i),  i = 0..W   (n = W+1)
mu    = mean(x)                      numpy mean, axis=1, float64
sd    = std(x, ddof=0)               population std
z_i   = (x_i - mu) / sd
```

| 결정 | 값 | 근거 |
| --- | --- | --- |
| std | ddof=0 | D0 `vector` 식 |
| 상수 창 판정 | **`np.ptp(x) == 0`** 이면 `VECTOR_UNDEFINED` | D0는 `std_ddof0 == 0`이다. 그런데 부동소수점에서 상수 창의 std가 0이 아니다. 2026-09-18 실측: 61개가 전부 10.07이면 std = 5.3e-15, 7.77 x 21이면 2.7e-15. 이대로 나누면 z가 반올림 잡음을 +-1 크기로 키운다. 정확한 산술에서 `std == 0`과 `모든 값이 같음`은 동치이므로, 규칙 변경이 아니라 규칙을 부동소수점에서 정확히 구현하는 방법이다 |
| 거의 상수인 창 | 제외하지 않음 | D0 PIT §9 알려진 한계. 문턱을 넣으면 규칙 변경 |
| dtype | float64 | §5.2 |
| NaN | 입력에 있으면 hard fail. 출력은 정의상 유한 | §6.3 |
| 허용 오차 (테스트) | `abs(sum(z)) <= 1e-9`, `abs(sum(z^2) - n) <= 1e-9 * n` | ddof0 z의 항등식 |
| 분할 순서 | P를 먼저 만든 뒤 정규화 | §6.3 |

성질: 양수 a와 임의의 b에 대해 `P -> aP + b`로 바꿔도 z는 같다. **모양만** 본다.

---

## 8. Representation B: cumulative log-return path

```text
c_i = ln( P(e-W+i) / P(e-W) ),  i = 1..W    (길이 W)
```

| 결정 | 값 | 근거 |
| --- | --- | --- |
| 길이 | W | D0 `vector` 식 |
| 첫 0 | 넣지 않음. 모든 벡터에서 0이라 거리에 영향이 없음 | D0 |
| 계산 식 | `np.log(P[:, 1:] / P[:, :1])` 그대로. `log(P_i) - log(P_0)`로 바꾸지 않음(반올림이 다름) | 식 하나로 고정 |
| scale 정규화 | 없음. 진폭 보존이 표현 B의 목적 | D0 §4.1 |
| 정의 안 됨 | 값 하나라도 유한하지 않으면 `VECTOR_UNDEFINED` (적격 창은 P > 0이라 실제로는 안 생김, 방어용) | D0 |
| 상수 창 | **정의됨** (영벡터). A에서는 빠지고 B에는 남는다 | D0 식을 그대로 따른 결과 |
| 분할 | A와 같음 | §6.3 |

A와 B를 둘 다 두는 이유(단순 중복이 아님):

| 변환 | A | B |
| --- | --- | --- |
| `P -> aP` (가격 수준) | 불변 | 불변 |
| `P -> P + b` (평행 이동) | 불변 | **바뀜** |
| 진폭 (+200% 직선 vs +20% 직선, W=20) | rho = 1.0 (구분 못 함) | d = 2.805 (멀다) |
| 방향이 반대인 직선 | rho = -1.0 | d = 5.835 |

표현별 라이브러리 행 집합은 상수 창 때문에 조금 다를 수 있다. 그래서 `defined_A`, `defined_B` 열을 따로 둔다.

---

## 9. Similarity API

### 9.1 인터페이스

```python
# similarity.py  (pure)
class Metric(Enum):
    PEARSON = "pearson"      # representation A
    EUCLIDEAN = "euclidean"  # representation B

@dataclass(frozen=True)
class Scores:
    metric_value: np.ndarray   # (q, L) float64: rho (A) 또는 d (B), 원래 단위
    rank_score: np.ndarray     # (q, L) float64: 클수록 비슷함. A: rho, B: -d

def score_block(metric: Metric, queries: np.ndarray, library: np.ndarray,
                library_sq_norm: np.ndarray | None) -> Scores: ...
```

- A: `rho = (Q @ L.T) / (W+1)`. 클리핑하지 않는다(1을 1e-16 넘는 값을 자르면 인위적 동률이 생김).
- B: `d2 = max(||q||^2 + ||l||^2 - 2 q.l, 0)`, `d = sqrt(d2)`. `||l||^2`는 라이브러리를 만들 때 `np.einsum('ij,ij->i', L, L)`로 한 번 계산한다. 행마다 차이 벡터를 만드는 방식은 query당 약 0.05초라 14검정 전체에 시간 단위가 걸려 쓰지 않는다.
- 외부로는 두 값을 모두 내보낸다. **정렬은 `rank_score` 하나로만** 한다(A: rho 내림차순, B: d 오름차순. D0 `order`와 같음).
- DTW: 없음 (`dtw.enabled=false`). `Metric`에 DTW 항목을 만들지 않는다.

### 9.2 결정

| 항목 | 결정 |
| --- | --- |
| 방향 | 외부 `rank_score`는 클수록 비슷함. 원래 값(`rho`, `d`)도 함께 저장 |
| NaN / 상수 벡터 | 라이브러리와 query에 정의된 벡터만 들어온다. 유한하지 않은 score가 나오면 **hard fail** |
| 수치 허용 오차 (테스트 기준) | A: 기대값과 차이 `<= 1e-12`. B: `d >= 1e-3`이면 `<= 1e-9`, 같은 벡터면 `d <= 1e-6` (전개식의 상쇄 오차, sqrt가 1e-12를 1e-6으로 키움) |
| 동률 처리 | `rank_score` 내림차순, 그다음 라이브러리 `end_idx` 오름차순, `ticker` 오름차순 (D0). 라이브러리 행은 `(end_idx, ticker)` 순으로 정렬돼 있으므로 **행 index 오름차순과 같다**. 구현: 행 순서 배열에 `np.argsort(-rank_score, kind="stable")` |
| BLAS | numpy 2.5.2 + scipy-openblas 0.3.34. `OPENBLAS_NUM_THREADS`를 고정하고 run identity에 기록한다. query 청크 크기 `QUERY_CHUNK`도 상수로 고정 |

### 9.3 행렬곱 결정성

BLAS 결과는 행렬 모양과 스레드 수에 따라 마지막 비트가 달라질 수 있다. D2는 모양이 항상 같게 만든다.

- query D에 대한 후보는 라이브러리의 **앞부분 slice** `rows[:cut(D)]`다 (§11). 전체 실행과 `truncate(panel, D)` 실행에서 이 slice의 행, 순서, 값이 같다. 이 slice 안 행의 적격과 label 유효성은 모두 D-W 이하 행으로 정해지기 때문이다.
- query 청크(표본 순위 순서로 `QUERY_CHUNK`개씩)도 두 실행에서 같다.
- 따라서 gemm 입력의 모양과 바이트가 같고, PIT 절단 재실행을 bit 단위로 비교할 수 있다(D0 PIT mutation #1).

---

## 10. Neighbor Eligibility 계약

### 10.1 D0 원문 (그대로 옮김)

```text
forward_label_embargo    : neighbor label window d+1..d+h must end on or before D (d + h <= D)
overlapping_window_rule  : neighbor span d-W..d+h must end before the query pattern window starts: d + h <= D - W
effective_rule           : d + h <= D - W (strict; implies the forward-label embargo)
same_symbol_policy.rule  : exclude every library window whose ticker equals the query ticker, over the whole history
figi_rule                : also exclude a library window whose composite_figi (snapshot as of its end date) equals
                           the query composite_figi (snapshot as of D) when both are non-null
max_windows_per_neighbor_ticker : 1
max_neighbors_per_library_end_date : 5
selection                : greedy in similarity order, skipping a candidate that would break either cap, until top_k are accepted
library_stride_rule      : library end session index d with d % 5 == 0 on the session grid
top_k                    : 50
insufficient_neighbors   : fewer than top_k neighbors after all policies -> query excluded as INSUFFICIENT_NEIGHBORS
```

### 10.2 경계에서 정확히 무엇을 허용하는가

`d + h = D - W`는 허용된다(D0 PIT mutation #5가 "선택 가능"으로 고정). 이때 이웃 label의 마지막 종가 세션과 query 창의 첫 종가 세션이 같은 날이다. 겹치는 **수익률 구간은 없다**. 이웃 label의 마지막 수익률은 `(D-W-1) -> (D-W)`이고 query의 첫 수익률은 `(D-W) -> (D-W+1)`이다. 표현 A는 `P(D-W)`의 수준을 쓰지만 z 정규화라 창 밖 정보가 아니다. D0 식을 그대로 따르며 느슨하게도 엄격하게도 바꾸지 않는다.

### 10.3 라이브러리 창의 label 유효성 (D2가 필요한 부분만)

`valid_h(d) = has_bar[d+1] & has_bar[d+h] & ~label_ca_suspect_h(d)`

- `has_bar[d+1]`은 C `LabelSet.no_entry_bar`의 반대와 같다(시가 > 0 포함).
- `label_ca_suspect_h`: h <= 10이면 C `LabelSet.label_ca_suspect` 그대로. h = 20이면 그 값에 `D+11..D+20` 비율 규칙(close/prev close 또는 open/prev close가 3 이상이거나 1/3 이하)을 OR한다.
- C `valid()`는 쓰지 않는다. `disappeared`(데이터셋 끝까지 읽음)가 들어 있기 때문이다(D0 PIT §4.4).
- **D0 Reuse Matrix는 20D 확장을 D3에 두었지만, W60/h20 라이브러리의 유효성이 여기에 달려 있으므로 D2에서 필요하다.** D2는 Temporary 방식(D 패키지 `label_extension.py`, C 파일 무변경)으로 시작한다. C가 GATE-C2 FAIL 이후 다음 단계를 정하지 않았고 `strategy_c_v2/`가 생긴 상태라 C `labels.py`는 아직 안정 상태가 아니다.
- 메모리: `compute_labels(panel, horizons=(1,), ...)`만 호출해 horizon 무관 mask 두 개를 얻는다. 모든 horizon으로 호출하면 horizon마다 7개 `(T, N)` 배열이 생겨 약 0.9 GB가 된다.

### 10.4 적용 위치

| 정책 | 적용 시점 | 결과를 바꾸지 않는 이유 |
| --- | --- | --- |
| 적격, stride, 벡터 정의, `valid_h` | 라이브러리 compaction (similarity 전, 검정 단위) | query와 무관한 행 필터 |
| embargo `d <= D-W-h` | query 날짜마다 prefix cut (similarity 전) | 행이 `end_idx` 순이라 `searchsorted(end_idx, D-W-h, side="right")` 한 번 |
| same ticker / same FIGI | query마다, 정렬 전 해당 열 제거 | 절대 채택되지 않는 원소는 cap을 소모하지 않는다. greedy에서 "건너뛰기"와 "처음부터 없음"이 같다 |
| ticker cap 1, date cap 5 | 정렬 후 greedy | 순서에 의존한다. 반례: ticker X의 최선 창이 날짜 cap에 걸려 건너뛰어지면, X의 두 번째 창이 나중에 채택될 수 있다. 그래서 "ticker별 최선 창만 남기기" 같은 사전 축소는 **정확하지 않다** |
| Top-K | greedy 종료 조건 | - |

---

## 11. Top-K Neighbor Search pipeline

한 검정 `(W, h, rep)`, 한 query 날짜 D에 대해:

```text
0. 입력: PatternLibrary(W) 중 rep 행렬, 이 검정의 compaction 행 index (적격 & defined_rep & valid_h)
1. cut = searchsorted(lib.end_idx[compact], D - W - h, side="right");  cand = compact[:cut]
   EmbargoView(D, W, h)로 cand 전체가 d + h <= D - W 인지 확인 (위반 시 hard fail)
2. Query 준비: sampling이 고른 300 ticker 중 벡터 정의된 것 (정의 안 되면 VECTOR_UNDEFINED 기록)
3. for chunk in chunks(query, QUERY_CHUNK):
       S = score_block(metric, Q[chunk], L[cand], norm2[cand])      # (c, cut) float64
       for each query row r:
           drop = rows of cand with ticker == q.ticker  or  (figi == q.figi and both non-null)
           M = M0
           loop:
               pool = rows whose rank_score >= (M-th largest rank_score, ties included)   # np.argpartition
               order = pool sorted by (-rank_score, row index)                           # stable
               accepted = greedy(order, ticker_cap=1, date_cap=5, K=50)
               if len(accepted) == 50 or pool == all remaining rows: break
               M = 2 * M
           status = OK if len(accepted) == 50 else INSUFFICIENT_NEIGHBORS
4. 사후 불변식 (§18): 채택 이웃 전부 d + h <= D - W, same ticker/FIGI 0, cap 위반 0, rank 1..n 연속
```

- Top-M 정확성: greedy가 원소 하나를 채택할지는 정렬 순서상 **앞선 원소들**만 보고 정해진다. 그래서 정렬된 앞 M개로 돌린 greedy는 전체 greedy의 앞부분과 같다. 50개가 차면 그대로 정답이고, 못 채우면 M을 늘린다. 경계 동률은 "M번째 값 이상 전부"로 포함해 잘리지 않게 한다.
- `M0 = 8 * K = 400`. cap 때문에 400 안에서 50을 못 채우는 경우는 드물 것으로 예상한다. 확장 횟수 분포는 summary에 기록한다(조정 손잡이가 아니라 성능 진단).
- 비용: 14검정 전체 약 9.4 TFLOP (날짜당 2,600 적격, N=501, 평가일 221 가정). 44 GFLOPS면 약 3.5분. argpartition은 query-검정 928,200회 x 약 2.4 ms = 약 37분으로 **이쪽이 지배적**이다. D2 V1은 이 비용을 받아들인다. 줄이는 방법(§15.3)은 결과 동일성 테스트를 붙인 뒤에만 쓴다.

---

## 12. NeighborSet 모델

### 12.1 query 결과 (검정 x query 1행)

| 열 | 타입 | 뜻 |
| --- | --- | --- |
| `test_id` | str | `W{W}_H{h}_{A|B}` |
| `query_date_idx`, `query_date` | int32, date | D |
| `query_ticker`, `query_figi` | str, str|null | D 스냅샷 기준 |
| `sample_rank` | int16 | 0..299 (해시 순서) |
| `status` | enum | `OK`, `VECTOR_UNDEFINED`, `INSUFFICIENT_NEIGHBORS` |
| `accepted_count` | int16 | 0..50 |
| `candidates_after_embargo` | int32 | `cut` |
| `candidates_after_same_symbol` | int32 | `cut - drop` |
| `pool_final_m` | int32 | 마지막 M |
| `embargo_limit_idx` | int32 | `D - W - h` |

### 12.2 이웃 (검정 x query x rank 1행)

| 열 | 타입 | 뜻 |
| --- | --- | --- |
| `test_id`, `query_date_idx`, `sample_rank` | - | query 키 |
| `rank` | int8 | 1..50 |
| `library_row` | int32 | 라이브러리 메타의 행 |
| `neighbor_end_idx` | int32 | d |
| `neighbor_ticker`, `neighbor_figi` | str, str|null | d 스냅샷 기준 |
| `metric_value` | float64 | rho(A) 또는 d(B) |
| `rank_score` | float64 | 클수록 비슷함 |
| `label_end_idx` | int32 | `d + h`. 이 세션 종가에 label이 확정되고 `session(d+h)+1 04:00 ET`에 쓸 수 있다. `<= D - W` 보장 |

### 12.3 경계

D2는 이웃의 **신원과 유사도까지만** 낸다. forward return, excess return, MFE/MAE 값은 D3가 `EmbargoView`를 통해 계산한다. 이렇게 나누면 D2 산출물 자체가 label 값을 전혀 담지 않으므로, D2 단계의 PIT 감사는 "label 값이 이웃 선택에 들어갔는가"를 코드 경로로 확인할 수 있다. D2가 label에서 쓰는 것은 유효성 mask 하나다.

---

## 13. Historical Pattern Library 구조

| 안 | 판정 |
| --- | --- |
| A. 창마다 Python 객체 | 기각. 약 25만 창 x 6 표현, 행렬곱을 쓸 수 없음 |
| **B. dense NumPy 행렬 + 메타데이터 열** | **채택** |
| C. Arrow/Parquet 행렬 | 기각(주 저장소로). 검색에 쓰려면 결국 numpy로 옮겨야 함. 메타데이터 산출물에만 Parquet |
| D. 즉석 배치 계산 | 부분 채택: 벡터는 W마다 한 번 만들고, 점수는 query 청크 단위로 즉석 계산 |

```python
@dataclass(frozen=True)
class PatternLibrary:
    window: int
    end_idx: np.ndarray        # (L,) int32, 오름차순
    ticker_col: np.ndarray     # (L,) int32, end_idx 안에서 오름차순 (ticker 문자열 순서 = Panel.tickers 순서)
    figi_code: np.ndarray      # (L,) int32, -1 = null
    defined: dict[str, np.ndarray]          # {"A": (L,) bool, "B": (L,) bool}
    valid: dict[int, np.ndarray]            # {h: (L,) bool}  이 W의 horizon만
    vectors: dict[str, np.ndarray]          # {"A": (L, W+1) f64, "B": (L, W) f64}, C-contiguous
    sq_norm_b: np.ndarray                   # (L,) f64
    exclusions: Mapping[str, int]           # 사유별 개수
```

- `Panel.tickers`는 `sorted(universe)`라서 `ticker_col` 순서가 ticker 문자열 오름차순과 같다. 그래서 `(end_idx, ticker_col)` 정렬이 D0 동률 규칙 `(d asc, ticker asc)`와 같다. 이 가정은 테스트로 고정한다(V12).
- 중복 `(end_idx, ticker_col)` 행은 hard fail.
- 라이브러리는 W마다 **한 번**, 가장 늦은 query 날짜가 쓸 수 있는 범위(`60 <= d <= eval_end_idx - W - min(h)`, stride 5)까지 만든다. N=501이면 W20은 `d <= 459`(종료일 80개), W60은 `d <= 410`(71개)다. expanding은 query마다 prefix cut으로 구현한다. §14의 L 추정(종료일 89개)은 상한이다.

---

## 14. Memory budget

가정: 라이브러리 종료일 `d in {60, 65, ..., 500}` = 89개, 날짜당 적격 상한 2,803 -> **L <= 약 250,000 행** (세 W 공통).

| 항목 | 식 | float64 | (참고) float32 |
| --- | --- | --- | --- |
| W20 A / B | 250k x 21 / 20 x 8 | 42 / 40 MB | 21 / 20 MB |
| W40 A / B | 250k x 41 / 40 x 8 | 82 / 80 MB | 41 / 40 MB |
| W60 A / B | 250k x 61 / 60 x 8 | 122 / 120 MB | 61 / 60 MB |
| 메타데이터 | 250k x (int32 x 3 + bool 여러 개) | 약 5 MB | - |
| Panel raw OHLCV | 501 x 6,341 x 8 x 5 | 127 MB | - |
| `split_arrays` 캐시 | 3 x 25 MB | 76 MB | - |
| universe 계산 임시 | `(T, N)` 배열 6~8개 | 약 200 MB (끝나면 해제) | - |
| label 유효성 임시 | `compute_labels(horizons=(1,))` | 약 400 MB (끝나면 해제) | - |
| score 블록 | `QUERY_CHUNK` x L x 8 x 2 (metric, rank) | 50이면 200 MB | - |
| 검정당 이웃 결과 | 66,300 x 50 x (int32 + f64 x 2) | 약 66 MB | - |

- 한 번에 W 하나만 메모리에 둔다(W60 A+B = 242 MB).
- 예상 최대 RSS: Panel 약 200 MB + label 임시 약 400 MB(라이브러리 만들기 전에 해제) 또는 라이브러리 242 MB + score 200 MB + 결과 66 MB + Python 오버헤드 -> **약 1.0 GB**.
- 목표: **최대 RSS 2.0 GB 이하**. D2 첫 실행에서 `/usr/bin/time -v`로 측정해 summary에 기록한다. 넘으면 `QUERY_CHUNK`를 줄인다(결정성을 위해 새 값을 run identity에 기록).
- 현재(2026-09-18) 사용 가능 5.2 GB, swap 1.1 GB 사용 중이다. D2는 공통화 수집기와 다른 세션의 성능 harness가 끝난 뒤 돌린다.

---

## 15. Vectorized Search (pseudo-code, 구현은 D1 PASS 후)

### 15.1 Pearson

```python
# ZL: (L, W+1) z 벡터, ZQ: (c, W+1)
rho = (ZQ @ ZL[cand].T) / (W + 1)          # BLAS gemm, (c, cut)
rank_score = rho
```

### 15.2 Euclidean

```python
# CL: (L, W), CQ: (c, W), nL = einsum('ij,ij->i', CL, CL), nQ = einsum('ij,ij->i', CQ, CQ)
g = CQ @ CL[cand].T                         # (c, cut)
d2 = np.maximum(nQ[:, None] + nL[cand][None, :] - 2.0 * g, 0.0)
d = np.sqrt(d2)
rank_score = -d
```

### 15.3 선택 (query 한 행)

```python
def select(rank_score_row, cand_rows, drop_mask, lib, K=50, m0=400):
    rs = np.where(drop_mask, -np.inf, rank_score_row)    # same ticker/FIGI는 절대 채택 불가
    live = np.count_nonzero(~drop_mask)
    m = min(m0, live)
    while True:
        kth = np.partition(rs, len(rs) - m)[len(rs) - m]  # m번째로 큰 값
        pool = np.nonzero((rs >= kth) & ~drop_mask)[0]     # 경계 동률 포함
        order = pool[np.argsort(-rs[pool], kind="stable")] # pool은 행 순서 -> 동률은 (d, ticker) asc
        acc = greedy_caps(order, lib.ticker_col[cand_rows], lib.end_idx[cand_rows], K, 1, 5)
        if len(acc) == K or m == live:
            return acc, m
        m = min(2 * m, live)
```

`greedy_caps`는 순수 Python 반복이지만 query당 수백 번만 돈다. 라이브러리 전체를 Python으로 비교하는 반복은 없다.

### 15.4 결과가 같은 최적화 후보 (V1 이후, 테스트 먼저)

1. 같은 (W, rep)의 horizon들이 score를 공유: 가장 작은 h의 prefix로 한 번 계산하고, 큰 h는 더 짧은 prefix와 다른 `valid_h`로 가린다. gemm 모양이 바뀌므로 절단 재실행 bit 동일성을 다시 증명해야 한다.
2. argpartition을 청크 단위(`axis=1`)로 한 번에 처리.

어느 쪽이든 "최적화 전후 이웃 목록 digest가 같다"는 테스트를 통과해야 들어간다.

---

## 16. Query 빈도 vs 라이브러리 샘플링 빈도

| 개념 | 값 | D0 출처 | 상태 |
| --- | --- | --- | --- |
| 라이브러리 샘플링 | 종료 index `d % 5 == 0` (grid index 기준) | `library.library_stride_rule` | 동결 |
| query 평가 | 세션 index `260 .. N-1-20`의 **모든 날짜** (N=501이면 221일) | `evaluation.query_dates`, `eval_start_idx`, `eval_end_idx` | 동결 |
| 날짜당 query | 적격 ticker를 `sha256('Q|20260917|D|ticker')` 순서로 앞 300개. label 무효 query는 D2에서 빼지 않는다(D4에서 빼고 집계) | `evaluation.query_sampling` | 동결 |
| 해시 문자열의 D 표기 | **D0에 명시 없음** | - | **UNRESOLVED -> D2 결정 제안: ISO `YYYY-MM-DD`**. 데이터를 읽기 전에 D1 보고서에 기록해 고정. 결과를 본 뒤에는 못 바꾼다 |

stride의 기준점이 grid index라서 **grid 시작 세션이 하루만 바뀌어도 라이브러리 전체가 바뀐다.** D grid는 FREEZE_V1의 501세션으로 고정한다(BLOCKER B2).

---

## 17. Stable Identity

- D 내부 계약: `SymbolIdentity(ticker: str, composite_figi: str | None, as_of_snapshot: date)`.
- 값은 `source.py`가 제공한다. 창 종료 세션 e 기준 **최신 스냅샷 `as_of <= e`**의 행에서 가져온다. 적격 창은 그 스냅샷 소속이 조건이므로 행이 항상 있다.
- 쓰는 곳:
  - same-ticker 제외: ticker 문자열 비교, **전 기간**
  - same-FIGI 제외: 이웃 FIGI(d 스냅샷) == query FIGI(D 스냅샷), **둘 다 non-null일 때만**
  - ticker cap: **ticker 기준** (D0). FIGI 기준으로 바꾸지 않음
  - 동률 처리: ticker 문자열 (D0)
- FIGI null: 비교에서 빠진다(D0). null 비율은 D1에서 측정해 보고한다.
- 공통화 작업에서 새 identity(`stable_symbol_id` 등)가 생기면 `source.py` 안에서만 `SymbolIdentity`로 바꾼다. D 규칙은 "ticker + composite_figi"이므로 새 id를 **규칙 입력으로 쓰려면 V2 선언**이 필요하다.
- 알려진 한계(D0 PIT §9 그대로): FIGI가 없는 이름 변경, share class가 다른 같은 회사는 못 잡는다.

---

## 18. D2 PIT 런타임 불변식

| # | 불변식 | 검사 위치 | 위반 시 |
| --- | --- | --- | --- |
| R1 | 규칙 canonical checksum == `680bf113...` | `config.load_rules` | **HARD FAIL** (`RulesChanged`) |
| R2 | 읽은 raw 파일 sha256 == freeze 행, 결과 `dataset_digest` == run 선언값 | `source.py` | **HARD FAIL** (`DatasetDigestMismatch`), 산출물 0 |
| R3 | 세션 grid == FREEZE_V1 쓸 수 있는 세션(시작 2024-09-17, N=501), 내부 결손 없음, 달력상 거래일 | `source.py` | **HARD FAIL** |
| R4 | encoder 입력 행 `<= as_of_idx` (query는 D, 라이브러리 창은 d) | `pit.AsOfView` | **HARD FAIL** |
| R5 | encoder 입력에 NaN/0 이하 없음 (universe 통과분) | `encoder.py` | **HARD FAIL** |
| R6 | 후보 전체 `end_idx + h <= D - W` | `neighbor_search` 1단계 | **HARD FAIL** |
| R7 | 채택 이웃 전체 `d + h <= D - W` | 사후 검사 | **HARD FAIL** |
| R8 | label 유효성을 `d + h > D - W`인 (d, h)에 대해 요청 | `pit.EmbargoView` | **HARD FAIL** (`PointInTimeViolation`) |
| R9 | 채택 이웃에 same ticker / same FIGI 0 | 사후 검사 | **HARD FAIL** |
| R10 | ticker cap, date cap 위반 0, rank 1..n 연속, 이웃 중복 0 | 사후 검사 | **HARD FAIL** |
| R11 | 라이브러리 `(end_idx, ticker_col)` 유일, 정렬 | `library.py` | **HARD FAIL** |
| R12 | 적격 행 score 유한 | `similarity.py` | **HARD FAIL** |
| R13 | query 표본이 label과 무관 (`sampling`이 label 모듈을 import하지 않음) | AST 테스트 | 테스트 실패 |
| R14 | 부적격 창 (universe, stride, 상수 창, label 무효) | 라이브러리 구성 | 정상 제외, 사유별 집계 |
| R15 | query 벡터 정의 안 됨 | query 준비 | 정상 제외 `VECTOR_UNDEFINED`, 집계 |
| R16 | 정책 적용 뒤 50 미만 | 선택 | 정상 제외 `INSUFFICIENT_NEIGHBORS`, 검정별 비율 |

원칙: **데이터나 규칙에서 자연스럽게 생기는 것**(R14~R16)만 조용히 제외하고 센다. 코드가 계약을 어겨야만 생기는 것(R1~R12)은 멈춘다.

---

## 19. 결정적 동률 처리

```text
1차: rank_score 내림차순   (A: rho, B: -d)
2차: neighbor end_idx 오름차순
3차: neighbor ticker 오름차순
```

- D0 `similarity_methods.tie_break`와 같다. stable_identity는 동률 키가 아니다.
- 구현: 라이브러리 행이 `(end_idx, ticker)` 순이므로 `argsort(-rank_score, kind="stable")` 하나로 세 키가 모두 적용된다.
- B는 d로 정렬한다(d2가 아니라). sqrt 반올림으로 서로 다른 d2가 같은 d가 되면 D0 문구대로 d 동률로 처리된다.
- `-0.0`과 `0.0`은 numpy 비교에서 같다. NaN은 R12로 들어오지 않는다.

---

## 20. D2 Artifact

### 20.1 논리 스키마

| 파일 | 내용 |
| --- | --- |
| `run_identity.json` | rules checksum, freeze_id, freeze_digest, D raw digest, grid(첫날..끝날:N, index 0 날짜), code digest(`strategy_d_analog`), numpy/pyarrow/openblas 버전, `OPENBLAS_NUM_THREADS`, `QUERY_CHUNK`, `M0`, 검정 목록, query 해시 표기 |
| `library_manifest.json` | W별 L, 종료일별 행 수, 사유별 제외 수, `defined_A/B` 수, horizon별 `valid_h` 수 |
| `library_meta_W{20,40,60}.parquet` | `library_row, end_idx, end_date, ticker, figi, defined_A, defined_B, valid_h*` |
| `query_samples.parquet` | 날짜별 300 표본 (검정 공통): `query_date_idx, sample_rank, ticker, figi` |
| `query_results.parquet` | §12.1, 14검정 전체 |
| `neighbors_<test_id>.parquet` x 14 | §12.2, 검정당 최대 약 330만 행, ZSTD |
| `summary.json` | 검정별 query 수, 상태별 수, `INSUFFICIENT_NEIGHBORS` 비율, M 확장 분포, 이웃 날짜/ticker 다양성, 실행 시간, 최대 RSS, 파일별 content digest |
| `pit_runtime.json` | R1~R12 검사 횟수와 위반 0 확인 |
| `COMPLETE.json` | 마지막에 기록. 이 파일이 없으면 D3는 읽지 않는다 |

### 20.2 경로

공통화 쪽 store 규약이 아직 문서로 확정되지 않았다(`historical_store/__init__.py`에 raw/metadata/state 배치만 있고 research run 위치는 없음). 그래서 **논리 스키마만 확정**하고 경로는 정하지 않는다.
- CURRENT 관행: C는 `data/runtime/strategy_c/runs/<run_id>/`(로컬, git-ignored)에 썼다.
- D2 가정: `data/runtime/strategy_d/runs/dneigh1-<id>/`. Drive 게시는 D4에서 `engine/store.write_run`으로 summary만.
- 예상 크기: 이웃 parquet 검정당 30~50 MB, 전체 약 0.5~0.7 GB. 로컬 전용.

### 20.3 content digest

- 작은 표(`query_samples`, `query_results`, `library_meta`): C `table_digest`와 같은 recipe(행 정렬 후 CSV `%.17g`, sha256). C `run`은 import 금지라 D가 다시 구현하고, 테스트에서 C 함수와 같은 값을 내는지 비교한다.
- 이웃 표: 정해진 열 순서로 열마다 `astype('<i4'/'<f8').tobytes()`를 sha256에 넣는다. CSV로 330만 행을 만드는 비용을 피한다.
- 파일 바이트 sha256은 참고용으로만 기록한다(Parquet 메타데이터에 writer 버전이 들어감).

---

## 21. D2 테스트 설계

테스트 벡터 25개와 기대값은 `D_D2_TEST_VECTORS_V1.md`에 있다. D0 PIT mutation 16개 중 D2가 맡는 항목의 대응표도 거기에 있다.

---

## 22. D2 PASS 조건과 D2 -> D3 경계

### 22.1 D2 PASS (Alpha 판정 없음)

| # | 조건 |
| --- | --- |
| P1 | 테스트 벡터 25개 전부 통과, import 경계 AST 테스트 통과 |
| P2 | 합성 fixture mutation audit(D0 PIT #1, #2, #3, #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16 중 D2 범위) 위반 0, 양성 대조 감지 |
| P3 | 실데이터 1회 실행: R1~R12 위반 0 |
| P4 | 같은 입력 2회 실행 -> 모든 content digest 동일 |
| P5 | 실데이터 절단 재실행(감사일 12개, D0 PIT #1): 이웃 목록, metric 값 bit 단위 동일 |
| P6 | 최대 RSS <= 2.0 GB, swap 증가 없음 |
| P7 | `INSUFFICIENT_NEIGHBORS` 비율과 라이브러리 크기를 보고만 한다(D0 조건 9의 0.05 판정은 D4 몫) |

D2 PASS는 "이웃을 정확하고 결정적으로 PIT-safe하게 찾는다"는 뜻뿐이다. 이웃이 쓸모 있는지는 D4 GATE-D-ALPHA가 판정한다. D2 단계에서 이웃 label 분포를 들여다보고 규칙을 고치는 것은 금지다.

### 22.2 D2 -> D3 계약

D3가 받는 것:
1. `COMPLETE.json`이 있는 D2 run 디렉터리와 그 `run_identity`
2. `query_samples`, `query_results`, `neighbors_<test_id>` (이웃 신원, 유사도)
3. `library_meta` (end_idx, ticker, figi)

D3가 할 것: `EmbargoView`를 거쳐 이웃 label(`excess_return_h`, `mfe_h`, `mae_h`)을 계산하고, `S(q)`, `sigma(q)`, N1/N2 기준선을 만든다. D3는 D2 run identity의 rules checksum, dataset digest, grid가 자기 입력과 같지 않으면 **거부**한다.

D3가 하지 않을 것: 이웃을 다시 고르기, K나 cap 바꾸기.

---

## 23. Data Adapter Contract

D 전략 로직은 저장 경로를 모른다. 경로를 아는 것은 `source.py` 하나다.

### 23.1 D가 필요한 최소 API (요청문 목록과의 대응)

| 요청 API | D2 계약 | 구현 방식 (Panel 기반) |
| --- | --- | --- |
| `trading_sessions(start, end)` | `DailyHistory.sessions: tuple[date, ...]` (고정 grid, index 0 = 2024-09-17) | freeze의 `GROUPED_DAILY`, `status=OK` 세션. `MarketCalendar`로 교차 검증 |
| `symbols_as_of(date)` | `DailyHistory.panel.membership()` + 거래소 필터 | C `load_snapshots(allowed_exchanges)` |
| `daily_close_matrix(symbols, start, end, as_of)` | `DailyHistory.panel.close/open/high/low/volume` `(T, N)` 전체 + `pit.AsOfView(as_of_idx)` | 종목마다 호출하지 않고 행렬로. as_of는 view가 막음. 감사는 `truncate(panel, D)` |
| `stable_identity(symbol, as_of)` | `DailyHistory.identity(ticker_col, end_idx) -> SymbolIdentity` 와 벡터판 `figi_code_matrix` | 스냅샷 raw 파일의 `composite_figi` (D reader) |
| `split_factor(symbol, date, as_of)` | `DailyHistory.panel.split_arrays()[0]` (`F(t)`) | C `Panel.split_arrays`. F(t)는 실행일 `<= t` 분할만 쓴다 |
| `dataset_digest` | `DailyHistory.identity_lines` (`freeze_id`, `freeze_digest`, D raw digest) | C `raw_digest` recipe를 D가 다시 구현 |

```python
@dataclass(frozen=True)
class DailyHistory:
    sessions: tuple[date, ...]
    panel: Panel                       # C strategy_c_selection.panel.Panel (REUSE)
    figi: FigiTable                    # snapshot as_of -> {ticker: composite_figi | None}
    freeze_id: str
    freeze_digest: str
    raw_digest: str

def load_daily_history(freeze_manifest: Path, resolve: Callable[[str], Path]) -> DailyHistory: ...
#   resolve: freeze 행의 relative_path(또는 common_path) -> 실제 파일. CURRENT와 TARGET 배치를 여기서 흡수
```

### 23.2 공통화 결과가 다를 때

- Panel과 같은 `(T, N)` raw 배열 + split 기록 + 스냅샷을 주면: `load_daily_history`만 바꾼다.
- 행 단위(tidy) 표를 주면: `source.py`가 Panel로 바꾸고, CURRENT 배치에서 C `load_panel`과 배열이 bit 단위로 같은지 테스트한다.
- grid 시작이 달라지면(롤링 창 이동, 새 세션 추가): D는 FREEZE_V1 grid를 고집한다. 다른 grid는 새 D 규칙 버전이 필요하다(stride 기준점, `eval_start_idx`가 달라짐).

---

## 24. BLOCKERS

| # | 내용 | 해소 조건 |
| --- | --- | --- |
| B1 | D1 Data/PIT Gate 미실행. 공통화 세션이 C raw를 Drive로 승격하는 중(2026-09-18 10시대 실행 중) | 공통화 완료 + D1 PASS |
| B2 | 세션 grid 고정. stride(`d % 5`)와 `eval_start_idx`가 grid index 기준이라 grid 시작점이 곧 규칙의 일부 | D1이 "D grid = FREEZE_V1 501세션, index 0 = 2024-09-17"을 기록하고 R3로 강제 |
| B3 | 20D label CA 확장이 D3가 아니라 D2에 필요 (W60/h20 라이브러리 유효성) | D2 착수 시 Temporary(`label_extension.py`)로 구현, C 대비 동일성 테스트 |
| B4 | query 해시 문자열의 날짜 표기가 D0에 없음 | D1 보고서에 ISO `YYYY-MM-DD`로 기록한 뒤 고정 (데이터 읽기 전) |
| B5 | C `load_panel`은 CURRENT 배치(`root/grouped/<date>`)만 읽음. TARGET은 `grouped_daily/<YYYY>/` | D1에서 adapter 방식 결정 (§23.2) |
| B6 | `engine.identity` import가 `app.strategy.engine`을 전이 로드 (D0 §2.3) | D2 첫 커밋에서 전이 로드 스냅샷 테스트로 고정, 또는 공통화 쪽에서 leaf 모듈 분리 |
| B7 | 실행 시 메모리 여유. 현재 사용 가능 5.2 GB, swap 1.1 GB 사용 중 | D2 실행 전 `ps`와 `free`로 다른 무거운 프로세스가 없는지 확인 |
| B8 | 같은 세션에 grouped 행이 같은 ticker로 두 번 있으면 C `load_panel`이 조용히 덮어씀 | D1에서 중복 수 측정. 0이 아니면 D1 판정 사항 |

---

## 25. 다음 순서

```text
공통화 완료 -> D1 Data/PIT (grid, 적격 규모, FIGI null 비율, B2/B4/B5/B8 기록) -> D1 PASS
  -> D2 구현: config, models, source, universe, label_extension, sampling, encoder, library,
              similarity, neighbor_search, pit, pit_audit, artifacts + tests/strategy_d
  -> D2 PASS (§22.1) -> D3
```
