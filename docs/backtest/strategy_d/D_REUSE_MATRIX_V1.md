# Strategy D Reuse Matrix V1 (D0)

작성 2026-09-17. 기준: `main` HEAD `0a96bd7` + 작업 트리(미커밋 A/B/C 파일 포함). 파일과 심볼은 모두 이 시점의
실제 코드에서 확인했다. 규칙 정본: `d_analog_rules_v1.json` (canonical sha256 `680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3`).

원칙: D가 재사용하는 것은 **공통 인프라**다. A/B/C의 **Alpha 로직**은 가져오지 않는다.

Action 정의:

- `REUSE`: 수정 없이 import하거나 같은 파일을 읽기 전용으로 쓴다.
- `EXTEND`: 기존 코드를 최소 확장해야 한다. 확장 시점과 방식은 표에 적은 단계에서 결정한다(D0에서는 설계만).
- `NEW`: D 패키지에 새로 만든다. D0에서는 만들지 않는다.
- `DO_NOT_USE`: D의 import나 신호 생성에 쓰지 않는다. 전략 로직이거나, D 단계와 계약이 맞지 않는다.

## 1. 매트릭스

경로는 `backend/app/` 기준.

| Existing Asset | Existing File | Existing Function/Class | Source Strategy / Common | D Phase Used | Action | Reason | Modification Required |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Calendar | `market/calendar.py` | `MarketCalendar` (`is_trading_day`, `session`, `next_trading_day`, `previous_trading_day`), `TradingSessionWindow` | Common | D1~D4 | REUSE | XNYS 세션 grid와 조기폐장의 단일 원천. C raw 수집도 같은 달력 | NO |
| Massive Client | `integrations/massive/client.py` | `MassiveAggregatesClient.grouped_daily`, `.reference_tickers`, `._get` (splits), `build_massive_client` | Common | D1 (캐시 결손일 때만) | REUSE | Bearer 인증, 5콜/분 limiter, 403/429 typed error. D0~D4 목표 호출 수는 0 (C 캐시 사용) | NO |
| Daily Collector | `backtest/collector/daily_collector.py` | `collect_daily_symbol`, `collect_universe`, `DAILY_COLLECTOR_VERSION` | Common (선언 universe용) | 없음 (D6 재검토) | DO_NOT_USE | 종목당 1콜, 1 Parquet 구조. 전 시장 약 5,000종목이면 약 5,000콜이고 grouped daily는 세션당 1콜. 전략 로직 문제가 아니라 grain 불일치 | NO |
| Grouped Daily Raw | `backtest/strategy_c_selection/raw_fetch.py`, 캐시 `data/runtime/strategy_c/raw/grouped/` | `fetch_grouped`, `grouped_path`, `RAW_FORMAT_VERSION` | C 소유, 내용은 전략 중립 | D1~D4 | REUSE (읽기 전용) | `adjusted=false` echo 검증된 provider 원본 gzip. 롤링 2년이라 캐시가 정본. C 수집 완료 뒤에만 읽음 | NO |
| Ticker Reference | `raw_fetch.py`, `strategy_c_selection/panel.py` | `fetch_tickers`, `tickers_path`, `TICKER_FIELDS`, `load_snapshots` | C 소유, 전략 중립 | D1~D4 | REUSE | 분기 CS 스냅샷(as-of `active`). `load_snapshots`는 ticker 집합만 반환하므로 `composite_figi`(same-symbol 규칙)는 D가 같은 raw 파일을 따로 읽음(D 패키지 신규 reader, C 변경 없음) | NO |
| Splits | `raw_fetch.py`, `panel.py` | `fetch_splits`, `splits_path`, `SplitEvent`, `load_splits`, `Panel.split_arrays` (F(t)) | C 소유, 전략 중립 | D1~D4 | REUSE | 분할 정보가 `F(t)`(실행일 <= t) 한 형태로만 들어가는 PIT 구조. D 경로 벡터와 label 모두 이 F를 씀 | NO |
| Splits (B 쪽) | `strategy_b/split_adjustment.py` | `SplitRecord`, `known_splits`, `split_adjustment` | B | 없음 | DO_NOT_USE | B 내부 순수 계층(분봉 기준 전일종가 조정). D는 C의 F(t) 한 형태로 통일 | NO |
| Manifest | `backtest/workspace/manifest.py` | `open_manifest`, `plan_entry`, `complete_entry`, `entries_of_kind`, `MANIFEST_SCHEMA_VERSION=3` | Common (collector registry) | 없음 (D6 재검토) | DO_NOT_USE | 수집기 레지스트리다. D1~D4 입력은 C 로컬 캐시이고 무결성은 raw 파일 sha256 digest가 run identity에 들어가는 방식(C `run.raw_digest`와 같은 형태) | NO |
| Writer Lock | `backtest/workspace/lock.py` | `writer_lock`, `acquire`, `heartbeat`, `release`, `DEFAULT_LEASE_SECONDS` | Common | D4 (workspace 게시) | REUSE | 집/회사 PC 단일 writer. `engine/store.write_run` 경유로만 사용 | NO |
| Safe Write | `backtest/workspace/safe_write.py` | `safe_write`, `write_bytes_atomic`, `sha256_file`, `utc_now_iso` | Common | D2~D4 | REUSE | `.partial` 후 원자적 rename. Drive 동기화 중 반쪽 파일 방지 | NO |
| Secret Guard | `backtest/workspace/guards.py` | `assert_no_secret_like` | Common | D4 | REUSE | 게시 산출물에 키 유입 방지 | NO |
| Panel | `backtest/strategy_c_selection/panel.py` | `Panel`, `load_panel`, `truncate`, `with_changes`, `BENCHMARK` | C 소유, 전략 중립 | D1~D4 | REUSE | (sessions x tickers) raw OHLCV + 분할 + 스냅샷. `truncate(panel, D)`가 PIT 재실행의 기준. import는 `raw_fetch`뿐이고 C feature를 끌어오지 않음(실측) | NO |
| Forward Labels | `backtest/strategy_c_selection/labels.py` | `compute_labels`, `LabelSet`, `LABEL_CA_HORIZON = 10` | C | D3 | EXTEND | `compute_labels`는 k=20도 MFE/MAE/close_return/DISAPPEARED를 계산하지만 label CA 의심 검사는 D+10에서 멈춘다. 합성 panel 실측: D+15에 10배 점프를 넣으면 20D label이 valid, close_return_20 = +900%. D 규칙은 D+11..D+20에 같은 비율 규칙을 추가로 요구 (§2.1). 또 C `DISAPPEARED`는 D+h 이후 데이터셋 끝까지 읽어 이웃 유효성에 쓰면 look-ahead라, D는 "D+h 세션 봉 존재" mask를 D 쪽에서 씀(C 변경 없음) | YES (D3에서 방식 결정, D0 변경 0) |
| PIT Audit | `backtest/strategy_c_selection/pit_audit.py`, `tests/strategy_c/test_strategy_c_selection_pit.py` | `audit`, `audit_dates`, `_row_mismatches`, `test_1_...` ~ `test_6_...` | C | D2~D4 | NEW (패턴 재사용) | 절단 재실행, 미래 bar 변조, 미래 분할 주입, 이후 상폐 삭제, 양성 대조, 결정성의 **방식**을 그대로 따름. 코드는 C `FEATURE_COLUMNS`와 C-M 변형에 묶여 있어 import 불가. `truncate`, `with_changes`는 panel에서 REUSE | NO |
| Bootstrap | `backtest/strategy_c_selection/evaluate.py` | `block_indices` | C | D4 | EXTEND | 함수 자체는 전략 중립(seed, 블록 연속). 하지만 모듈이 `features.FeatureSet`, `rules`를 import해 D가 import하면 C Momentum feature 모듈이 D 프로세스에 들어옴 (§2.2) | YES (D4에서 결정) |
| Rules Checksum | `backtest/strategy_c_selection/rules.py` | `canonical_checksum`, `load_rules` 거부 패턴(`RulesChanged`, `DECLARED_RULES_CHECKSUM`) | C 소유, 전략 중립 recipe | D0 (이미 사용), D2~D4 | REUSE | D0 checksum을 이 함수로 생성(독립 hashlib 계산과 일치 확인). `rules.py`는 app 모듈을 import하지 않음(실측). D `config.py`는 같은 거부 패턴을 D 규칙 파일에 적용 | NO |
| Run Identity | `backtest/engine/identity.py`, `backtest/research/contract.py` | `run_identity`, `RunIdentity`, `code_digest`, `package_files`, `source_provenance`, `checksum` | Common | D2~D4 | EXTEND | recipe는 그대로 쓸 수 있다. 다만 `import app.backtest.engine.identity`만으로 `research.contract -> authority.contract -> app.strategy.lifecycle` 경로로 `app.strategy.engine`(`StrategyV0Engine`)이 로드됨(실측). D import 경계 테스트 기준에 따라 선택 (§2.3) | YES 또는 NO (D2에서 결정) |
| Run Store | `backtest/engine/store.py` | `write_run`, `load_complete`, `run_dir`, `json_bytes`, `RESEARCH_RUN_SCHEMA = "research-run-v1"` | Common | D4 (명시적 게시 단계) | REUSE | `backtest/research/<run_id>/` + `COMPLETE.json` 마지막 기록, 같은 run id 재기록 거부. import 전이 깨끗함(실측) | NO |
| StrategyAdapter | `backtest/engine/adapter.py` | `StrategyAdapter`, `ExecutionMode`, `SessionPart`, `ReplayTick` | Common (B가 첫 adapter) | GATE-D-OOS 이후 재감사 | DO_NOT_USE | 분봉 tick 계약. D0~D6은 Alpha 연구라 adapter가 없음 | NO |
| Engine Driver / Clock | `backtest/engine/driver.py`, `backtest/engine/clock.py` | `ChronologicalReplayDriver`, `ExtendedSessionClock`, `ENGINE_DRIVER_VERSION` | Common (분봉) | GATE-D-OOS 이후 재감사 | DO_NOT_USE | 1분 경계 tick. `import driver`만으로 `services.entry_drift_observer`, `strategy.engine`, `risk.engine` 로드(실측, `replay.clock` 경유) | NO |
| SimBroker | `broker/sim.py` | `SimBroker`, `generate_execution_scope` | Common (A에서 출발) | GATE-D-OOS 이후 재사용 후보 | DO_NOT_USE | `MinuteBar` 기준 next-bar long-only 체결. 일봉 체결 계약은 Trading 단계에서 기존 SimBroker 확장으로 설계. D 전용 broker 신규 금지 | NO |
| Accounting | `broker/accounting.py` | `fill_cash_charges`, `fill_cash_flow`, `net_pnl`, `unrealized_pnl`, `reconciles` | Common | GATE-D-OOS 이후 재사용 후보 | DO_NOT_USE | PnL 단일 authority. Alpha 단계에는 체결과 비용이 없음 | NO |
| Portfolio | `backtest/portfolio/replay.py`, `account.py`, `ledger.py` | `MultiSymbolPortfolioReplay`, `PortfolioReplayAccount`, `PortfolioLedger` | A (Production entry 계약) | GATE-D-OOS 이후 재감사 | DO_NOT_USE | `EntrySessionRunner`, `entry_capacity`, `RiskConfig`, `SimBroker`에 묶임. `PortfolioLedger`는 재사용 후보로만 기록 | NO |
| Metrics | `backtest/baseline/analytics.py`, `backtest/strategy_b/result.py` | `funnel`, `candidate_outcomes`, `trade_records`, `StrategyBScannerResult.metrics` | A / B | 없음 | DO_NOT_USE | A 지표는 `StrategyReason`, `RiskRejectionReason` 퍼널이고 B 지표는 스캐너 탐지 통계. D4 Alpha 통계(IC, 기준선 차이, 분위 spread)는 D `evaluate.py`에 새로 작성 | NO |
| A Strategy Engine | `strategy/engine.py`, `strategy/indicators.py`, `risk/engine.py`, `backtest/replay/position_replay.py`, `backtest/replay/session_replay.py`, `research/domain.py`, `services/strategy.py` | `StrategyV0Engine` (`premarket_gate`, `StrategyReason.GAP_TOO_LOW`), `opening_range`, `session_vwap`, `RiskEngine`, `PositionCarry`, `EntrySessionRunner`, `GPTResearchResult`, `StrategyLifecycleService.apply_human_gate` | A | 없음 | DO_NOT_USE | premarket gate, gap rule, Opening Range, VWAP entry, GPT approval, Human approval, A risk 규칙, PositionCarry는 A의 Alpha/실행 로직 | NO |
| B Scanner | `strategy_b/scanner.py`, `backtest/strategy_b/adapter.py` | `evaluate_scanner`, `ScannerSessionPolicy`, `Trigger`, `ScannerDecision`, `StrategyBScannerAdapter` | B | 없음 | DO_NOT_USE | 1m/3m/5m 모멘텀 탐지, RVOL 문턱 | NO |
| B FSM | `strategy_b/models.py`, `strategy_b/rvol.py`, `strategy_b/spread.py`, `strategy_b/halt_inference.py` | `CandidateState`, `SetupType.HOD_BREAKOUT`, `SetupType.FIRST_PULLBACK`, `VolumeProfile`, `infer_halt` | B | 없음 | DO_NOT_USE | 후보 FSM, 셋업, 장중 spread/halt 로직 | NO |
| C Momentum Feature | `backtest/strategy_c_selection/features.py`, `rules.py`, `evaluate.py`, `run.py` | `compute`, `FeatureSet`, `FEATURE_COLUMNS`, `SelectionRules.variants` (C-M0/M1/M2), `build_rows`, `attach_matched_base`, `summarize`, `gate`, `execute` | C | 없음 | DO_NOT_USE | C Momentum feature, M0/M1/M2 후보 로직, C 매칭 점수, C 선택 규칙. N2의 `return_1d`, `return_5d`는 이름이 겹치지만 D 규칙 JSON 정의로 D 쪽에서 계산하고 C `features.py`를 import하지 않음 | NO |
| Import 경계 테스트 패턴 | `tests/strategy_c/test_strategy_c_selection_pit.py`, `tests/strategy_b/test_strategy_b_contracts.py` | `test_selection_package_has_no_trading_imports`, `test_package_imports_only_itself_and_the_standard_library` | C / B | D2 | REUSE (패턴) | AST로 금지 prefix 검사. D는 금지 목록에 A/B/C Alpha 모듈을 추가 (§4) | NO |

## 2. EXTEND 상세

### 2.1 Forward Label 20D

현재: `compute_labels(panel, horizons, ca_ratio)`는 horizon 목록을 일반적으로 처리하지만, CA 의심 루프가
`range(2, LABEL_CA_HORIZON + 1)`로 D+10에 고정돼 있다. 1/3/5/10D는 D+10까지 보므로 C보다 더 엄격해지는 쪽이라
문제없고, 20D만 D+11..D+20이 비어 있다.

C가 안정화 중이라 D0에서 C 코드를 바꾸지 않는다. D3에서 두 방식을 비교해 정한다.

| 방식 | 내용 | 장점 | 단점 |
| --- | --- | --- | --- |
| Temporary | D 패키지 `label_extension.py`가 C `LabelSet`을 받아 h=20일 때 D+11..D+20에 같은 비율 규칙을 적용한 추가 mask를 만든다. C 파일 무변경 | C run identity(code_digest) 불변, C 진행과 충돌 0 | 같은 규칙이 두 곳에 존재. D 테스트로 "C 창 D..D+10 결과와 일치 + 11..20만 추가"를 고정해야 함 |
| Final | 전략 중립 label API: `compute_labels(panel, horizons=(1, 3, 5, 10, 20), ca_ratio, ca_horizon=None)`에서 `ca_horizon` 기본값을 `max(10, max(horizons))`로 두거나 명시 인자로 받음 | 규칙 한 곳 | C `labels.py` 수정 -> C `code_digest` 변경 -> C run identity 변경. C GATE 판정 확정 후에만 가능 |

권고: D3 시점에 C 판정이 확정되지 않았으면 Temporary, 확정됐으면 Final(C 테스트 전체 회귀 + C run 재현 digest 비교).
어느 쪽이든 D 규칙 JSON의 `labels.label_ca_suspect` 정의는 바뀌지 않는다.

### 2.2 Bootstrap

`evaluate.block_indices(n_dates, replicates, seed, block)`는 전략 중립이지만 같은 모듈이 C feature를 import한다.

| 방식 | 내용 |
| --- | --- |
| Temporary | D `evaluate.py`가 같은 알고리즘을 쓰고, D 테스트(테스트 코드만 C evaluate import 허용)가 같은 인자에서 C `block_indices`와 배열이 완전히 같음을 고정 |
| Final | `block_indices`를 전략 중립 leaf 모듈로 옮기고 C `evaluate.py`는 re-export. C code_digest가 바뀌므로 C 판정 확정 후 |

D 규칙의 블록 길이(20), 반복(10,000), seed(20260917)는 C 기본값(10, 2,000)과 다르며 인자로 넘긴다.

### 2.3 Run Identity import 경로

`run_identity`, `code_digest`, `source_provenance`는 수정 없이 쓸 수 있다. 문제는 import 전이다.

| D import 경계 기준 | 결과 |
| --- | --- |
| AST 직접 import만 검사 (C, B 테스트와 같은 방식) | REUSE. 전이 로드되는 A 모듈은 `KNOWN_TRANSITIVE_IMPORT`로 문서화하고 D 코드는 A 심볼을 참조하지 않음 |
| `sys.modules` 전이 검사 | EXTEND. `research.contract.checksum`을 의존성 없는 leaf 모듈로 분리하고 `research.contract`는 re-export. A/B/C 회귀 전체 필요 |

권고: D2는 AST 직접 import 기준(REUSE)으로 시작하고, 전이 로드 목록을 D 테스트가 스냅샷으로 기록해 늘어나면 실패하게 한다.

## 3. NEW: Strategy D 패키지 (D0에서는 만들지 않음)

위치 후보: `backend/app/backtest/strategy_d_analog/`, 테스트 `backend/tests/strategy_d/`.

| 모듈 | 책임 | 읽는 것 | 쓰지 않는 것 | 단계 |
| --- | --- | --- | --- | --- |
| `config.py` | `d_analog_rules_v1.json` 로드, canonical checksum이 선언값과 다르면 거부, 타입 있는 접근자 | 규칙 JSON | 하드코딩 문턱 | D1 |
| `models.py` | `PatternKey(ticker, end_idx, W)`, `Neighbor`, `QueryResult`, 제외 사유 enum(`INSUFFICIENT_NEIGHBORS`, `CA_EXCLUDED`, `NO_HISTORY` 등) | - | - | D1 |
| `universe.py` | 날짜별 적격 mask(CS 스냅샷, 거래소, close, ADV20, 61봉 연속, 분할/CA 제외), FIGI reader | `Panel`, raw tickers 파일 | label | D1 |
| `encoder.py` | 표현 A(z 정규화 종가), B(누적 로그수익률) 벡터. `P(t)=close/F(t)` | `Panel` (D-W..D 행만) | `labels`, D 이후 행 | D2 |
| `similarity.py` | Pearson(A), Euclidean(B), 청크 행렬 연산, 결정적 tie-break | 벡터 | label, 날짜 정보 | D2 |
| `neighbor_search.py` | 라이브러리 구성(stride 5, expanding), same-symbol/FIGI 제외, 엄격 embargo, 종목 1개/날짜 5개 cap, Top-50 greedy | encoder/similarity 결과, 적격 mask, label 유효성 mask(d+h <= D-W인 것만) | query의 미래 label | D2 |
| `label_extension.py` | 20D label CA 확장(§2.1 Temporary일 때만) | C `LabelSet`, `Panel` | feature | D3 |
| `signal.py` | 이웃 excess return 분포 -> `S(q)`, `sigma(q)`, secondary 통계 | 이웃 label (embargo 검증된 view) | query label | D3 |
| `baselines.py` | N1(변동성 5분위 무작위, 20 replicate), N1b, N2 feature 5개, N2a kNN, N2b 잔차화 | `Panel`, 적격 mask, 이웃 정책 | C `features.py` | D3 |
| `evaluate.py` | 날짜별 Spearman IC, 기준선 차이, 분위 spread, time block, horizon 일관성, block bootstrap, 10조건 gate, 결정 | 신호, query label | 규칙 밖 문턱 | D4 |
| `pit_audit.py` | 절단 재실행, 미래 bar/분할 변조, 이후 상폐 삭제(생존편향 양성 대조), embargo 경계, same-symbol planted 창, 결정성 | 전 모듈 | - | D2~D4 |
| `run.py` | run identity(규칙 checksum, raw digest, code digest, 창), 로컬 산출물, 선택적 workspace 게시(`engine/store.write_run`) | 전 모듈 | 주문, broker, adapter | D4 |

D 패키지 어디에도 주문, 포지션, 리스크, 사이징, StrategyAdapter, SimBroker 참조가 없어야 한다.

## 4. D import 경계 (D2 테스트로 고정할 내용)

금지 prefix (AST 직접 import):

```text
app.strategy            (A: StrategyV0Engine, indicators, lifecycle)
app.strategy_b          (B 순수 계층, scanner, FSM)
app.services            (A Production 서비스)
app.risk
app.broker
app.execution
app.integrations.kiwoom
app.backtest.portfolio
app.backtest.replay
app.backtest.baseline
app.backtest.experiments
app.backtest.strategy_b
app.backtest.engine.adapter
app.backtest.engine.driver
app.backtest.engine.clock
app.backtest.strategy_c_selection.features
app.backtest.strategy_c_selection.rules      (canonical_checksum recipe는 예외 허용 여부를 D1에서 결정)
app.backtest.strategy_c_selection.evaluate
app.backtest.strategy_c_selection.run
app.backtest.strategy_c_selection.pit_audit
```

허용 (REUSE 행):

```text
app.market.calendar
app.integrations.massive.client          (D1 캐시 결손 시에만)
app.backtest.strategy_c_selection.panel
app.backtest.strategy_c_selection.raw_fetch   (경로 함수)
app.backtest.strategy_c_selection.labels
app.backtest.workspace.*
app.backtest.engine.store
app.backtest.engine.identity             (§2.3 결정에 따름)
```

D 내부 경로 분리: `encoder.py`, `similarity.py`, `universe.py`는 `labels`와 `signal`을 import하지 않는다(AST 테스트).

## 5. 실측 기록 (2026-09-17 17:14~17:20 KST, 코드 변경 없음)

| 확인 | 방법 | 결과 |
| --- | --- | --- |
| C panel/labels/evaluate import 전이 | 새 프로세스에서 모듈 import 후 `sys.modules` 검사 | `app.strategy*`, `app.services*` 없음. 모두 `app.integrations.kiwoom.client`는 로드됨(`market.calendar` 등 공통 경로의 인프라 전이) |
| `workspace.lock`, `safe_write`, `manifest`, `engine.store` | 같은 방법 | clean |
| `engine.identity` | 같은 방법 | `app.strategy.engine`, `app.strategy.lifecycle` 등 로드 |
| `engine.driver` | 같은 방법 | `app.services.entry_drift_observer`, `app.strategy.engine`, `app.risk.engine` 로드 |
| `engine.adapter`, `broker.sim`, `broker.accounting`, `strategy_c_selection.rules` | 같은 방법 | 금지 목록 모듈 없음 |
| C label 20D CA | 합성 2종목 40세션 panel, API/캐시 미사용 | D+8 점프: suspect=True / D+15 점프: suspect=False, valid20=True, close_return_20=9.00 |
| C raw 캐시 | 파일 수 | grouped 142/502(수집 진행 중), tickers 8, splits 1 |
