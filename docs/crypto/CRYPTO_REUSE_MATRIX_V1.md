# US-B CRYPTO 공통 자산 재사용 판정 V1

작성 2026-09-23. `main` HEAD `b6afd95`, working tree dirty 상태에서 **읽기만 하고** 판정했다.
리팩터링·공통화·이동은 하나도 하지 않았다.

> **HEAD 이동 기록**: 이 문서 작성 중 다른 세션이 Strategy E-MAX 커밋 1건(`9984260`,
> E-RT2.1 가용성 계약 + E-RT3 키움 RVOL 분모 저장소)을 올려 HEAD가 `b6afd95`에서 `9984260`으로
> 옮겨갔다. 그 커밋이 건드린 파일은 `strategy_e_max_rt/{availability,rvol_bootstrap,rvol_store}.py`,
> `dev/run_e_rt2_dryrun.py`, `dev/run_e_rt3_rvol.py`, `tests/strategy_e_max/test_e_rt2_1_rt3.py`,
> `docs/backtest/strategy_e_max/*` 뿐이고, **이 문서가 인용한 파일은 하나도 포함되지 않는다.**
> 따라서 여기 적힌 판정과 실측값은 전부 그대로 유효하다.


이 문서는 "쓸 수 있을 것 같다"가 아니라 **실제로 import 해 보고 import graph를 측정한 결과**를 적는다.
분류는 세 가지다.

| 분류 | 의미 |
|---|---|
| `REUSE` | 그대로 import 한다. 수정도, 어댑터도 필요 없다 |
| `REUSE_WITH_ADAPTER` | 규칙·수식·불변식은 맞지만 주식 전제가 섞여 있어 감싸거나 다시 뿌리내려야 한다 |
| `DO_NOT_REUSE` | crypto에 쓰면 틀린다. 개념만 참고하고 코드는 가져오지 않는다 |

---

## 0. 판정의 핵심 근거: import graph 실측

논리적으로 generic한 모듈이 실제로는 주식 모듈을 끌고 온다. `PYTHONPATH=backend`에서 각 모듈을
단독 import 하고 새로 로드된 `app.*` 모듈을 셌다.

| 모듈 | 끌려오는 `app.*` 모듈 수 | 주식 결합 모듈 |
|---|---|---|
| `app.backtest.workspace.safe_write` | 14 | **없음** |
| `app.core.timezone` | 3 | **없음** |
| `app.execution.costs` | 19 | kiwoom 6, market 6 |
| `app.broker.accounting` | 22 | kiwoom 6, market 6 |
| `app.backtest.engine.identity` | 34 | kiwoom 6, massive 2, market 6, strategy 5, calendar |
| `app.backtest.baseline.analytics` | **148** | kiwoom·massive·market·scanner·strategy·services 전 계열 |

**이것이 이 문서의 가장 중요한 사실이다.** `identity.py`는 내용상 완전히 generic한데
(`run_identity`, `code_digest`, `source_provenance`) `app.backtest.research.contract.checksum`을
import 하고, 그 모듈이 `app.integrations.massive.minute_bars.ET`를 import 한다. 따라서 crypto가
run identity를 쓰려고 `identity.py`를 import 하면 **Kiwoom 클라이언트와 NYSE 캘린더가 함께 로드된다.**

`analytics.py`는 148개 모듈을 끌고 온다. 수식은 정확히 우리가 원하는 것이지만 import는 불가능하다.

---

## 1. Atomic write / safe write / checksum

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/backtest/workspace/safe_write.py` | **REUSE** | 전문을 읽었다. 순수 stdlib + `workspace.errors`만 쓴다. 주식 개념이 한 글자도 없고 import graph에 주식 모듈이 0개다. `safe_write()` 컨텍스트매니저(`.partial` -> fsync -> sha256 -> `os.replace`), `write_json_atomic`, `write_bytes_atomic`, `sha256_file`, `atomic_replace`, `utc_now`, `utc_now_iso`를 전부 그대로 쓴다 |
| `.partial` 쓰기 규약 자체 | **REUSE** | `.gitignore`에 `*.partial`이 이미 등록되어 있다. crypto 수집기도 같은 규약을 따르면 커밋 위험이 없다 |
| `app/backtest/research/contract.checksum` | **REUSE_WITH_ADAPTER** | 레시피(`sha256(namespace:payload)`)는 옳다. 그러나 `CHECKSUM_NAMESPACE = "usb.research.v1"`가 주식 연구 네임스페이스이고, 이 모듈이 `massive.minute_bars.ET`를 끌고 온다. crypto는 **동일한 레시피를 `usb.crypto.v1` 네임스페이스로 crypto 패키지 안에 다시 구현한다.** 12줄짜리 함수이므로 공통화보다 재구현이 싸다 |

## 2. Manifest / store / workspace layout

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/backtest/workspace/manifest.py` | **DO_NOT_REUSE (스키마)** / **REUSE_WITH_ADAPTER (불변식)** | 스키마가 주식 전용이다: `expected_sessions`, `actual_sessions`, `regular_complete_sessions`, `premarket_sessions`, `listing_state IN (COMPLETE, PRE_LISTING, MISSING_DATA, PROVIDER_FAILURE)`, `start_date`/`end_date`가 **날짜**다. crypto는 세션도 프리마켓도 거래일도 없고 범위가 **UTC ms**다. 반면 지킬 불변식은 그대로 옳다: (a) `status='COMPLETE'`인 행은 `relative_path`·`checksum`·`row_count`가 NOT NULL, (b) `relative_path`에 `.partial` 금지, (c) `complete_entry`가 디스크 바이트를 다시 읽어 checksum을 계산, (d) **WAL 금지**(Drive가 WAL 사이드카를 따로 동기화하면 다른 PC에서 깨진 DB가 된다), (e) 읽기 전용 열기에서는 마이그레이션 금지. crypto는 **자체 manifest 테이블**을 만들고 이 다섯 불변식을 복제한다 |
| `app/backtest/workspace/layout.py` | **REUSE_WITH_ADAPTER** | `Workspace` dataclass + `ensure_directories` 패턴은 좋다. 그러나 `WORKSPACE_DIRECTORIES`가 `market_data/raw/massive`, `snapshots/paper_trading` 등 주식 이름이다. crypto는 **자체 디렉터리 상수**를 두고 같은 handle 패턴을 쓴다 |
| `app/backtest/workspace/safe_write.PARTIAL_SUFFIX` | **REUSE** | 상수 하나. 두 서브시스템이 같은 접미사를 쓰는 편이 안전하다 |
| writer lock (`state/writer.lock.json`) | **REUSE_WITH_ADAPTER** | 개념은 필요하다. 다만 메모리에 기록된 실패 사례가 있다: B 수집기의 drive tier가 writer lock을 독점해 UI 배치가 멈췄다. **crypto 수집기는 주식 workspace의 writer lock을 절대 잡지 않는다.** 별도 lock 파일을 쓴다 |

## 3. Run identity

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/backtest/engine/identity.py` 의 **규칙** | **REUSE** (개념) | 두 규칙이 정확히 옳고 crypto에 그대로 적용된다: (1) 산출 바이트를 바꿀 수 있는 모든 것은 digest에 넣는다 (config fingerprint, 버전, dataset identity, 범위, `code_digest`), (2) 바꿀 수 없는 것은 넣지 않는다 (wall clock, 머신, git 커밋 여부). git 상태는 digest가 아니라 `source_provenance`로 **옆에** 기록하고, clean + remote 반영일 때만 실제 해시, 아니면 `UNCOMMITTED` |
| 같은 파일의 **코드** | **REUSE_WITH_ADAPTER** | 위 import graph 실측대로 34개 모듈(Kiwoom, Massive, NYSE 캘린더, strategy)을 끌고 온다. `run_identity`/`code_digest`/`package_files`/`source_provenance` 네 함수는 40줄 남짓이므로 crypto 패키지에 **checksum 네임스페이스만 바꿔 재구현**한다 |
| `code_digest`의 정의 | **REUSE** | `sha256((root 기준 상대경로, 파일 sha256))` 정렬. 버전 번호를 올리지 않은 수정도 다른 run으로 만든다는 성질이 crypto 연구에도 필요하다 |
| 메모리에 기록된 함정 | 계승 | `code_digest`가 패키지 전체이면 부모 run id를 재현할 수 없다. crypto는 **digest 대상 파일 목록을 artifact에 명시 기록**한다 |

## 4. PIT / 시간 처리

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/core/timezone.py` | **REUSE** | 전문 확인. `UTC`, `KST`, `require_aware`, `convert_timezone`. import graph 3개, 주식 결합 0. **naive datetime을 거부하는 `require_aware`가 crypto에 더 중요하다** (전 시계열이 UTC이므로) |
| `app/market/calendar.py` `MarketCalendar` | **DO_NOT_REUSE** | `xcals.get_calendar("XNYS")`. 거래일·조기폐장·개장/폐장 시각 개념. **crypto는 24/7 무중단이며 휴장일이 없다** |
| `app/backtest/engine/clock.py` `ExtendedSessionClock` | **DO_NOT_REUSE** | 04:00-20:00 ET를 프리마켓/정규/애프터로 쪼갠다. `__post_init__`이 `calendar.is_trading_day(day)`가 아니면 예외를 던진다. crypto에는 대응 개념이 없다 |
| `app/backtest/replay/clock.py` `TICK_EPSILON` / `MINUTE` | **REUSE_WITH_ADAPTER** | **개념은 crypto에 그대로 필요하다**: tick은 분 경계 + epsilon이며, 그래서 (0, 1분) 사이 어떤 지연이든 같은 bar를 보고 **bar가 자기 시작 시각에 보이지 않는다.** 이것이 PIT 위반을 막는 핵심 규칙이다. crypto는 거래일 루프 없이 연속 UTC 분 격자에 같은 규칙을 적용한다 |
| PIT 불변식 자체 | **REUSE** | AGENTS.md 절대규칙 6번. **단 crypto에는 주식에 없는 PIT 함정이 하나 더 있다**: WS `kline.1`의 `confirm=false` bar는 미완성인데 OHLC가 다 채워져 있어서, 그대로 저장하면 미래 정보 누출이 된다. `confirm=true`만 확정으로 취급한다 |
| `assert_completed_session` / `COMPLETED_SESSION_RESEARCH` | **DO_NOT_REUSE** | 세션 종료(20:00 ET tape end) 기준 PIT 컷. crypto에 세션이 없다 |

## 5. Broker abstraction / simulation account

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/broker/contract.py` `Broker` ABC | **REUSE_WITH_ADAPTER** | 8개 메서드 시그니처(`submit_order`, `cancel_order`, `get_order`, `get_open_orders`, `get_positions`, `get_position`, `get_fills`, `reset`)의 **모양은 crypto에도 맞다.** 그러나 `submit_order(intent, market_bars: Sequence[MinuteBar])`가 주식 분봉을 요구한다. crypto는 호가(bid/ask)로 체결하므로 시장 인자 타입이 다르다. **crypto 전용 Broker ABC를 같은 메서드 모양으로 새로 둔다** |
| `app/broker/sim.py` `SimBroker` | **DO_NOT_REUSE** | 모듈 docstring이 스스로 "Deterministic next-bar **long-only** SimBroker"라고 말한다. (a) long-only, **SHORT 불가**, (b) `fill_delay_bars=1` next-bar 체결, (c) `MinuteBar` 종가 기반, (d) **레버리지 개념 없음**, (e) **funding 개념 없음**, (f) **청산 개념 없음**, (g) `Currency.USD`/`KRW`만. crypto 요구사항 7개 중 7개가 빠져 있다 |
| `app/broker/accounting.py` 의 **비용 분류 체계** | **REUSE** (개념) | `PRICE_EMBEDDED` (spread, slippage: 체결가에 이미 반영됨) 대 `CASH_SEPARATE` (commission, fx: 현금에서 따로 차감) 구분이 **crypto에 그대로, 그리고 더 절실하게** 필요하다. 이 구분이 없으면 비용을 두 번 차감한다. 메모리에 `net_pnl` 이중차감 사고가 기록되어 있다. **funding은 새 `CASH_SEPARATE` 항목으로 추가한다** |
| 같은 파일의 `net_pnl`, `reconciles`, `RECONCILIATION_TOLERANCE` | **REUSE_WITH_ADAPTER** | `net_pnl(gross, charges) = gross - charges`는 그대로 옳다. `reconciles`/tolerance도 옳다. 다만 import가 kiwoom/market 12개를 끌고 온다 |
| 같은 파일의 `unrealized_pnl` | **DO_NOT_REUSE** | `(mark - average_price) * quantity`. **long 전용 부호다.** SHORT는 `(average_price - mark) * quantity`. crypto는 부호 있는 포지션으로 재정의한다 |
| 같은 파일의 `fill_cash_flow` | **DO_NOT_REUSE** | BUY가 notional 전액을 현금에서 빼는 spot/cash 모델이다. **선물은 notional이 아니라 margin이 묶인다.** crypto는 margin 모델로 다시 쓴다 |
| 회계 authority 규칙 | **REUSE** (개념) | 메모리 기록: `net_pnl` 단일 authority는 `accounting.py`, baseline authority는 ledger `POSITION_CLOSE.net_pnl`. **한 비용을 한 곳에서만 차감한다**는 규칙을 crypto도 문서로 고정한다 |
| `app/risk/domain.Currency` | **DO_NOT_REUSE** | `KRW`, `USD`뿐. **`USDT`가 없다.** crypto는 정산통화 `USDT`와 표시통화 `KRW`를 모두 다뤄야 한다 |
| `app/risk/domain.decimal_from` | **REUSE** | float를 `Decimal(str(value))`로 받는 경계 헬퍼. 10줄, 순수. crypto도 money 경계에서 같은 규칙이 필요하다 |
| `app/monitoring/kill_switch.py` | **DO_NOT_REUSE (코드)** / **REUSE_WITH_ADAPTER (패턴)** | `MinuteBar`, `Currency.USD`, `RiskEngine`, `StrategyDecision`에 전부 결합되어 있다. 그러나 패턴은 EMERGENCY 상태에 그대로 필요하다: (1) 확인 게이트(`kill_switch_requires_confirmation`), (2) **먼저 halt 하고 그 다음 청산**, (3) 실패 심볼을 `failed_symbols`로 **삼켜 버리지 않고 보고**, (4) 결과를 `KillSwitchResult`로 반환 |

## 6. Execution cost model

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/execution/config.py` `ExecutionConfig` | **REUSE_WITH_ADAPTER** | 버전 문자열(`execution_v0`) + bps 파라미터 + `__post_init__` 검증 패턴이 좋다. 그러나 `fill_delay_bars`, `ambiguous_bar_policy`, `partial_fill_ratio`는 주식 분봉 replay 개념이다. crypto는 `taker_fee_bps`, `maker_fee_bps`, `leverage`, `funding` 처리를 담은 자체 config를 둔다 |
| `app/execution/costs.py` `execution_price` | **DO_NOT_REUSE** | `raw + direction * raw * (spread_bps + slippage_bps)/BPS`. **spread를 bps 추정치로 대체하는 모델이다.** crypto는 실제 호가창이 있고 실측 spread가 정확히 1 tick(0.1)이었다. **추정 spread를 쓰면 실제보다 나쁘거나 좋게 왜곡된다.** market LONG은 ask, market SHORT는 bid로 체결하는 실호가 모델을 쓴다 |
| 같은 파일의 bps 산술 | **REUSE** (개념) | 수수료·슬리피지를 bps로 표현하고 `BPS = Decimal("10000")`로 나누는 방식은 그대로 쓴다 |
| "Risk와 broker가 같은 config를 읽어 bps 산술을 두 번 쓰지 않는다" | **REUSE** (규칙) | 모듈 docstring이 명시한 규칙. crypto도 사이징과 체결이 같은 수수료 원본을 읽어야 한다 |

## 7. Metrics

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/backtest/baseline/analytics.py` 의 **수식과 authority 규칙** | **REUSE** (정의만) | docstring이 정한 authority가 crypto에도 옳다: Net PnL/Total Return은 `ending_equity` 대 starting cash, 청산 손익은 ledger `POSITION_CLOSE`에서, **equity curve와 drawdown은 체결 시퀀스가 아니라 `SESSION_CLOSE` equity에서.** 그리고 "본 적 없는 reason은 자기 이름으로 세고, 이웃에 접어 넣지 않으며, 없으면 `NONE`"이라는 규칙도 그대로 쓴다. `drawdown`, `_streaks`, `equity_curve`, `trade_performance`, `monthly`, `ratio`, `median`, `zero_normal`의 **정의를 문서로 옮겨 적고** crypto ledger 위에 다시 구현한다 |
| 같은 파일의 **코드** | **DO_NOT_REUSE** | import 148개 모듈. `PortfolioReplayResult`, `LedgerEvent`, `StrategyReason`, `EntryCapacityReason`, `RiskRejectionReason`, `DailyCandidates`에 결합. crypto에는 프리마켓 게이트도 gap도 스캐너 후보도 없다 |
| `Stage` funnel 분류 | **DO_NOT_REUSE** | `PREMARKET_GATE`, `ENTRY_SIGNAL`, `CAPACITY` 등 Strategy A의 단계다. crypto는 자체 funnel을 정의한다 |
| `BASELINE_VERSION` bump 규칙 | **REUSE** (규칙) | 메모리 기록: 집계 코드를 바꾸면 버전을 올린다. crypto도 같은 규칙을 둔다 |
| `zero_normal` | **REUSE** (개념) | `0E-10`과 `-0`을 `0`으로 정규화. Decimal 산술 산출물을 저장할 때 필요하다 |

## 8. Replay

| 대상 | 판정 | 근거 |
|---|---|---|
| differential replay **방법론** | **REUSE** (개념) | 메모리에 기록된 성과: PIT 위반 0, 차분 45세션 불일치 0, 포트폴리오 differential 불일치 0, 그리고 이 방법으로 **SimBroker `net_pnl` 이중차감을 발견**했다. 같은 입력으로 두 경로를 돌려 산출을 바이트 대조하는 방식은 crypto paper 엔진 검증에 그대로 쓴다 |
| `app/backtest/replay/*` 코드 | **DO_NOT_REUSE** | 세션·거래일·프리마켓 구조에 결합 |
| `app/replay_smoke/*` | **DO_NOT_REUSE** | 메모리 기록: 스모크 오탐이 기본이다. crypto는 별도 스모크 기준을 만든다 |
| `app/backtest/engine/driver.py` `ChronologicalReplayDriver` | **REUSE_WITH_ADAPTER (설계만)** | 시간순 tick 주입 드라이버라는 모양은 맞다. 클록이 세션 기반이므로 crypto는 연속 UTC 클록으로 다시 만든다 |
| parity / recorded-session reader | **REUSE** (개념) | 메모리 기록의 `canonical inputs` / `decision authority` / `recorded-session reader` / `session differential` 4층 구조가 crypto paper 대 backtest 검증에 그대로 필요하다 |

## 9. Storage / recorder

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/recorder/parquet.py` merge-on-write 패턴 | **REUSE_WITH_ADAPTER** | "stable key ordering + last-write-wins upsert"가 옳다. 컬럼이 `MinuteBar`/`DailyBar`(symbol, trading_date, session)라서 crypto 스키마로 바꿔야 한다. 파티션도 `daily/{year}/{symbol}.parquet`인데 crypto는 1종목이라 시간 파티션이 맞다 |
| Parquet + zstd 선택 | **REUSE** | 실측: kline 10,000행 zstd 33.65 B/row 대 snappy 49.79 B/row. **zstd가 32% 작다.** AGENTS.md도 Market Data File = Parquet |
| `pyarrow` 23.0.1 / `pandas` 가용 | 확인됨 | `.venv`에 이미 있다. 새 의존성 불필요 |

## 10. Logging / config / exceptions

| 대상 | 판정 | 근거 |
|---|---|---|
| `app/core/logging.py` | **REUSE** | `dictConfig` 콘솔 로깅. "settings나 secret을 직렬화하지 않는다"는 성질이 중요하다. `Settings` 하나만 의존 |
| `app/core/exceptions.py` | **REUSE** | `USBError` -> `ConfigurationError` / `DataError` / `MarketDataError(code, message)` / `ResearchError`. 순수 예외 계층, 의존성 0. **crypto 예외는 `USBError`를 상속해 한 계층 안에 둔다** |
| `app/core/config.py` `Settings` | **REUSE_WITH_ADAPTER** | 패턴은 쓰되 crypto 설정 키를 추가해야 한다. **`.env`에 secret을 추가할 필요가 없다** (PUBLIC API만 쓰므로) |
| `app/core/database.py` | **REUSE_WITH_ADAPTER** | SQLAlchemy + SQLite. crypto 런타임 상태에 쓸 수 있으나 별도 DB 파일로 분리한다 |

## 11. 절대 재사용 금지: 주식 전용 로직 목록

crypto에 가져오면 **틀린 답을 내는** 것들이다.

| 대상 | 왜 틀리는가 |
|---|---|
| `app/market/calendar.py` (XNYS) | crypto는 24/7. 휴장일·조기폐장·거래일 개념이 없다 |
| `app/market/domain.MinuteBar`, `MarketSession` | `session` 필드(PREMARKET/REGULAR/AFTER)가 crypto에 없다 |
| `app/market/symbols.normalize_symbol` | 주식 티커 정규화. `BTCUSDT`는 거래소 심볼 규칙이 다르다 |
| `app/integrations/massive/*` | 미국 주식 데이터 공급자 |
| `app/integrations/kiwoom/*` | 키움 증권 API |
| `app/strategy/*` (Strategy A) | gap, 프리마켓 거래량, 세션 정책 기반 |
| `app/scanner/*` | 키움 순위 endpoint 기반 유니버스 |
| `app/services/morning_scanner.py`, `premarket_volume_history.py` | 프리마켓 개념 |
| `app/services/end_of_day_lifecycle.py`, `end_of_day_runtime.py` | **장 마감 개념이 crypto에 없다.** overnight/EOD 청산 규칙이 성립하지 않는다 |
| `app/strategy/session_policy.py` | 세션 정책 |
| `app/backtest/research/contract.py` | `COMPLETED_SESSION_RESEARCH`, `TAPE_END = 20:00 ET` |
| `app/backtest/basis/*`, `app/backtest/collector/daily_*` | 주식 일봉·세션 완결성 |
| AGENTS.md 7번 "America/New_York Market Calendar를 사용한다" | **crypto 서브시스템에는 적용되지 않는다.** crypto는 UTC 연속 시간이다. 이 예외를 명시적으로 기록한다 |
| AGENTS.md 6절 Trading Safety Rules 중 "동일 종목 당일 재진입 금지", "Overnight 최대 1종목" | "당일"과 "overnight"이 24/7 시장에서 정의되지 않는다. crypto는 자체 안전규칙을 세운다 |
| FX KRW/USD 환산 (`app/risk/domain.FxRate`, `lib/fx`) | **crypto는 USDT 정산이다.** KRW 표시가 필요하면 USDT/KRW 경로가 별도로 필요하고, 메모리에 기록된 `1,346.09` 고정 환율은 주식용이다 |

## 12. 재사용 요약

| 판정 | 항목 수 | 대표 |
|---|---|---|
| `REUSE` (그대로) | 8 | `safe_write.py` 전체, `core/timezone.py`, `core/exceptions.py`, `core/logging.py`, `decimal_from`, `PARTIAL_SUFFIX`, Parquet+zstd 선택, `code_digest` 정의 |
| `REUSE_WITH_ADAPTER` | 14 | manifest 불변식 5개, workspace layout 패턴, run identity 4함수, `Broker` ABC 모양, 비용 분류 체계, `net_pnl`, tick epsilon 규칙, merge-on-write, kill switch 패턴, parity 4층 구조 |
| `DO_NOT_REUSE` | 20+ | `SimBroker`, `MarketCalendar`, `ExtendedSessionClock`, `analytics.py` 코드, `execution_price`, `unrealized_pnl`, `fill_cash_flow`, `Currency`, 주식 provider·scanner·strategy·EOD 전 계열 |

**무조건 그대로 import 할 수 있는 것은 `safe_write.py`와 `core/timezone.py`, `core/exceptions.py`,
`core/logging.py` 네 개뿐이다.** 나머지는 규칙과 정의를 문서로 옮겨 적고 crypto 패키지 안에서
다시 구현하는 것이 import graph를 깨끗하게 유지하는 유일한 방법이다.

## 13. 공통화하지 않는다는 결정

기존 코드를 crypto와 공유하려고 리팩터링하지 **않는다.**

- 근거 1: `identity.py`, `accounting.py`, `costs.py`를 주식 의존에서 떼어내려면 `research.contract`,
  `market.domain`, `execution.domain`을 건드려야 한다. 이들은 Strategy A~E 판정의 기반이고,
  메모리에 기록된 규칙대로 **게이트 레이어 구조변경은 판정을 오염시킨다.**
- 근거 2: working tree에 미커밋 변경 135건이 있다. 공유 모듈을 지금 움직이면 진행 중인 다른 작업과
  충돌한다.
- 근거 3: 재구현해야 할 총량이 작다. `run_identity` 4함수 약 40줄, checksum 6줄, 회계 함수 5개 약 30줄.
  공통화 비용이 재구현 비용보다 크다.

따라서 crypto는 **읽기 전용 참조 + 독립 구현**으로 간다. 진짜 공통인 4개 모듈만 import 한다.
