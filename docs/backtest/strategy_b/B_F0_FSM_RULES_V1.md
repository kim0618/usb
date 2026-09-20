# Strategy B F0 FSM / Setup / Entry / Exit Rules V1

작성 2026-09-19. 연구 단계 문서다. 이 문서는 B의 **판정 규칙**을 결과보다 먼저 고정한다.

- Strategy ID: `REALTIME_MOMENTUM_V1`
- 규칙 정본: `b_fsm_rules_v1.json`
- canonical sha256: `54c6063015999ac56d33c69c21d1463e68d9727c13d65c3ff8d47db3ec5e2c13` (기록 파일 `b_fsm_rules_v1.sha256`)
  (기존 US-B 방식 `app.backtest.strategy_c_selection.rules.canonical_checksum`)
- 선언 시각: 2026-09-20 11:52 KST (최종). 이전 초안 3개는 전부 B 결과 0건 상태에서 폐기, 사유와 해시는 `.sha256`에 기록
- 구현: `backend/app/strategy_b/{scanner,eligibility,setups,fsm,exits,sizing}.py`
- 선언 시점 저장소: `main` HEAD `a2b91ee`
- 선언 시점 B 결과: 0건. B 백테스트는 아직 한 번도 실행된 적이 없다.
- 상위 문서: `backend/app/strategy_b/README.md`(피처 계층 계약), `docs/backtest/COMMON_HISTORICAL_STORE_V2.md` 7절

## 0. 이 문서의 범위

포함: 후보 FSM 상태·전이, scanner gate, score 정의, `HOD_BREAKOUT` 셋업 탐지, 진입 신호,
청산 규칙, UNKNOWN 처리, 결정성 규칙.

제외(다음 단계 문서로): 백테스트 실행 계약(B-E0)과 성공/실패 판정 기준, 포지션 사이징의
계좌 연동, 실시간 어댑터(Kiwoom), 수수료·슬리피지 모델 수치.

이 문서가 정의하는 것은 "어떤 조건에서 상태가 바뀌는가"이고, "그래서 B가 수익이 나는가"는
백테스트 사전등록(B-E0)에서 판정한다. 규칙을 먼저 고정하는 이유는 C·D와 같다: 결과를 본 뒤
규칙을 고치면 그 연구는 반증 시도가 아니게 된다.

## 1. 시작 상태 (2026-09-19 실측)

| 자산 | 상태 |
| --- | --- |
| `backend/app/strategy_b/` 피처 계층 | 있음 (PIT 계약, 테스트 포함) |
| `backend/app/backtest/strategy_b/dataset.py` | 있음 (`sparse_minute_bars` 데이터 kind) |
| `StrategyBConfig` 파라미터 | 있음, 전부 `RESEARCH_DEFAULT_UNOPTIMIZED` |
| `CandidateState` enum | 있음 (9개 상태). 전이 로직은 이 F0에서 `fsm.py`로 구현 |
| `SetupType` enum | 있음 (2종). `HOD_BREAKOUT` 탐지는 이 F0에서 `setups.py`로 구현 |
| 진입 신호 | 이 F0 범위(8.1~8.2). 체결·포지션·청산 실행은 엔진(다음 단계) |
| 청산·포지션·사이징 실행 | 없음. 규칙만 이 문서 8.4·9절에 선언 |
| 분봉 데이터(최근 4개월, Drive) | 수집 중 (2026-09-19 재개, 2026-09-20 CON 크래시 수정 후 계속) |

데이터 한계 하나를 미리 기록한다: 티커 `CON`은 Windows 예약 장치명이라 Drive 저장소가 폴더를 만들 수
없어 수집 대상에서 빠진다(`COMMON_HISTORICAL_STORE_V2.md` 7절). 3,883종목 중 1종목이며, 모멘텀 특성과
무관한 파일시스템 제약이므로 선택 편향으로 보지 않되 결과 보고에 명시한다.

`score_threshold = 0.6`은 config에 있으나 **score를 계산하는 코드가 없었다**. 이 문서 5절이
그 정의를 처음으로 고정한다.

## 2. 가설

> 장중에 비정상적 가격 변화와 거래량 증가가 동시에 나타난 미국 보통주는, 그 직후 형성되는
> 좁은 consolidation을 상향 돌파할 때 짧은(분~시간) 추세 참여 구간을 제공한다.

반증 형태: 같은 scanner gate를 통과했지만 셋업이 성립하지 않은 종목, 또는 돌파 대신 임의
시점에 진입한 경우와 비교해 기대값 우위가 없으면 B-V1은 기각이다.

## 3. FSM

### 3.1 상태

`CandidateState` (`models.py`)를 그대로 쓴다. 새 상태를 만들지 않는다.

| 상태 | 뜻 |
| --- | --- |
| `DETECTED` | scanner gate 통과 (4절) |
| `QUALIFIED` | score >= threshold + 적격성 통과 (5·6절) |
| `WATCHING` | 셋업 탐색 중 (candidate TTL 내) |
| `SETUP_READY` | 셋업 패턴 성립, trigger price 확정 (7절) |
| `ENTRY_SIGNALLED` | trigger 도달, 주문 조건 충족 (8절) |
| `ENTERED` | 체결 (백테스트 엔진이 판정) |
| `REJECTED` | 적격성 실패로 탈락 (재진입 없음) |
| `EXPIRED` | TTL 초과 |
| `CANCELLED` | 신호 후 조건 붕괴 (가격 드리프트, 셋업 무효화) |

`ENTERED`, `REJECTED`, `EXPIRED`, `CANCELLED`는 terminal이다(`TERMINAL_CANDIDATE_STATES`).

### 3.2 전이표

한 tick(= 1분 bar availability 시각)에 한 후보는 최대 한 번 전이한다. 여러 조건이 동시에
성립하면 아래 우선순위를 따른다: **terminal 탈락 > 전진**. 즉 TTL 만료와 셋업 성립이 같은
tick에 걸리면 `EXPIRED`가 이긴다.

| From | To | 조건 |
| --- | --- | --- |
| (없음) | `DETECTED` | scanner gate 통과 (4절) |
| `DETECTED` | `QUALIFIED` | 적격성 통과 + score >= `candidate.score_threshold` |
| `DETECTED` | `REJECTED` | 적격성 실패 (6절, 사유 기록) |
| `DETECTED` | `EXPIRED` | 같은 tick에 score 미달이면 즉시 `EXPIRED` (다음 tick 재평가 가능) |
| `QUALIFIED` | `WATCHING` | 같은 tick에 이어서 기록 (아래 설명) |
| `WATCHING` | `SETUP_READY` | 셋업 성립 (7절) |
| `WATCHING` | `EXPIRED` | `detected_at + candidate.candidate_ttl_minutes` 경과 |
| `WATCHING` | `REJECTED` | 적격성 재검사 실패 (6.3) |
| `SETUP_READY` | `ENTRY_SIGNALLED` | trigger 도달 (8.1) |
| `SETUP_READY` | `EXPIRED` | `setup_ready_at + candidate.setup_ttl_minutes` 경과 |
| `SETUP_READY` | `REJECTED` | 적격성 재검사 실패 (6.3) |
| `SETUP_READY` | `CANCELLED` | 셋업 하향 무효화: 종가가 `initial_stop` 아래 (7.4) |
| `SETUP_READY` | `WATCHING` | 셋업 소멸(무효화 아님). 후보 TTL 내 재탐색 (7.4) |
| `ENTRY_SIGNALLED` | `REJECTED` | 적격성 재검사 실패 (6.3) |
| `ENTRY_SIGNALLED` | `ENTERED` | 엔진이 체결을 회신 (8.3) |
| `ENTRY_SIGNALLED` | `EXPIRED` | `signal_at + candidate.signal_ttl_minutes` 경과 |
| `ENTRY_SIGNALLED` | `CANCELLED` | 가격 드리프트 초과(8.2), 또는 엔진이 수량·한도 사유로 거절(8.4) |

`QUALIFIED`는 "score 통과"를, `WATCHING`은 "셋업 탐색 시작"을 기록한다. 둘은 같은 tick에
연속으로 찍히므로 어떤 tick도 `QUALIFIED`에 머무는 후보를 관측하지 않는다. 한 전이로 센다.

`SETUP_READY -> WATCHING`은 유일한 후퇴 전이다. 셋업이 사라진 것(창이 너무 길어짐, 새 고가로
창 리셋)은 후보 자체가 틀렸다는 뜻이 아니므로, 후보 TTL이 남아 있는 한 탐색을 계속한다.
하향 무효화(종가가 stop 아래)만 `CANCELLED`로 끊는다.

`DETECTED -> EXPIRED`(score 미달)는 같은 심볼이 뒤 tick에 다시 `DETECTED`로 들어오는 것을
막지 않는다. 하루 심볼당 진입 횟수는 `risk.max_entries_per_symbol = 1`로 제한된다.
`ENTERED`에 도달한 심볼은 그날 다시 후보가 되지 않는다.

TTL은 전부 wall-clock 분이다(README "Time Units"). 조용한 tape가 TTL을 늘리지 않는다.

## 4. Scanner gate (`DETECTED`)

as_of 시각 `FeatureSnapshot` 하나만 본다. scope는 값마다 다르며 `FeatureConfig`가 정본이다:
수익률은 `EXTENDED_DAY`(프리마켓 가격도 기준점이 된다), 거래대금·HOD·밀도는 `SESSION_LOCAL`
(정규장 09:30부터만 집계). 이 조합이 B의 기본값이고 이 문서는 그 값을 바꾸지 않는다.

### 4.1 통과 조건

모멘텀 레그(OR) - 하나 이상 충족:

| 조건 | 기준 |
| --- | --- |
| `return_1m >= scanner.return_1m_threshold` | 2.0% |
| `return_3m >= scanner.return_3m_threshold` | 4.0% |
| `return_5m >= scanner.return_5m_threshold` | 6.0% |

유동성 게이트(AND) - 전부 충족:

| 조건 | 기준 |
| --- | --- |
| `rolling_dollar_volume >= scanner.min_dollar_volume` | 250,000 USD / 5분 |
| `rvol >= scanner.min_rvol` | 3.0 |

세션: `session == REGULAR`만 gate를 통과한다. 프리마켓·애프터의 분봉은 피처 계산(VWAP, RVOL,
HOD)에는 쓰이되 진입 후보를 만들지 않는다. V1에서 프리마켓 진입은 없다.

시간창: `09:35 ET <= as_of <= 15:30 ET`. 앞의 5분은 RVOL·consolidation이 성립할 최소 관측을
확보하기 위해, 뒤 30분은 time stop(30분)과 EOD 청산(15:55 ET)이 겹치는 구간을 피하기 위해
자른다. 둘 다 규칙이며 결과를 보고 바꾸지 않는다.

### 4.2 spread gate (V1 미적용)

`scanner.max_spread_pct = 1.0`은 **V1에서 적용하지 않는다**. Massive Basic 분봉에는 호가가
없고 `spread.py`는 추정값을 만들지 않는다(README "Not implemented"). 스프레드 게이트는
`applied: false`로 기록하고, 대신 유동성은 거래대금·RVOL·`transactions`로만 판단한다.
백테스트 결과에는 "스프레드 필터 없음"이 한계로 남는다.

### 4.3 UNKNOWN 처리 (fail-closed)

게이트가 참조하는 `Measured`가 `AVAILABLE`이 아니면 그 조건은 **실패**다. 값이 없다는 이유로
통과시키지 않는다.

| 값 | 상태 | 처리 |
| --- | --- | --- |
| `return_*` | `NO_DATA` / `INSUFFICIENT_HISTORY` | 그 레그는 미충족 |
| `rolling_dollar_volume` | 결측 | gate 실패 |
| `rvol` | `RvolStatus.UNKNOWN` | gate 실패 |
| `rvol` | `RvolStatus.PARTIAL` | **통과 허용** (`rvol.min_partial_sessions = 5` 이상이면 값이 존재) |
| `session_vwap` | `NO_SOURCE_VWAP` | gate 조건이 아니므로 무관 |

## 5. Score

score는 gate를 통과한 후보를 한 번 더 거르고 순위를 매긴다. gate와 같은 값만 쓴다.

```text
momentum_ratio  = max(return_1m / t1, return_3m / t3, return_5m / t5)   # 결측 레그는 제외
rvol_ratio      = rvol / scanner.min_rvol
liquidity_ratio = rolling_dollar_volume / scanner.min_dollar_volume

cap(x)          = min(x, 2.0) / 2.0                                     # [0, 1]
score           = 0.5 * cap(momentum_ratio) + 0.3 * cap(rvol_ratio) + 0.2 * cap(liquidity_ratio)
```

- 세 항 모두 기준값에 정확히 걸친 후보(`ratio = 1`)의 score는 `cap(1) = 0.5`이므로
  `0.5*0.5 + 0.3*0.5 + 0.2*0.5 = 0.5`다. `score_threshold = 0.6`을 넘으려면 가중평균으로
  기준의 1.2배가 필요하다. 즉 gate는 "관찰 대상", score는 "실제 후보"를 가른다.
- `cap`은 2배에서 포화한다. 400% 급등 한 종목이 순위를 독점하지 못하게 한다.
- 결측 레그는 분자에서 빠질 뿐 0으로 치지 않는다. 세 레그가 모두 결측이면 4.3에 의해 gate에서
  이미 탈락한다.
- 가중치 0.5 / 0.3 / 0.2는 `RESEARCH_DEFAULT_UNOPTIMIZED`다. 결과를 본 뒤 튜닝하면 v2다.

동점 처리(결정성): score가 같으면 `rolling_dollar_volume` 내림차순, 그래도 같으면 심볼
오름차순. 난수·해시 순서·dict 삽입 순서에 의존하지 않는다.

## 6. 적격성 (`QUALIFIED` / `REJECTED`)

### 6.1 Scope (D-1 정보만)

`scope.evaluate_research_scope`의 `ScopeDecision.eligible`이 참이어야 한다. scope는 D-1까지의
메타데이터와 일봉만 본다(README "PIT guarantees"). scope 탈락은 `REJECTED`이며 사유는
`ScopeExclusion` 값을 그대로 기록한다.

### 6.2 기업행위 플래그

| 플래그 | 처리 |
| --- | --- |
| `SPLIT_ON_DAY` | `REJECTED` |
| `IPO_WARMUP` | `REJECTED` |
| `DELISTING_WINDOW` | `REJECTED` |
| `SYMBOL_CHANGE` | `REJECTED` |
| `CA_SUSPECT` | `REJECTED` |
| `RECENT_SPLIT` | 통과 (PIT 분할 계수가 이미 적용됨) |

### 6.3 Halt와 tape density

| 값 | 처리 |
| --- | --- |
| `halt_inferred == HALT_INFERRED` | `REJECTED` (재개 직후 갭은 V1 대상 아님) |
| `halt_inferred == UNKNOWN` | 통과 (`UNKNOWN != HALT`, README) |
| `sparse_status == VERY_SPARSE` | `REJECTED` |
| `sparse_status == UNKNOWN` | `REJECTED` (fail-closed) |
| `sparse_status in (DENSE, SPARSE)` | 통과 |

적격성은 terminal이 아닌 모든 상태에서 매 tick 재검사한다(`DETECTED`, `WATCHING`,
`SETUP_READY`, `ENTRY_SIGNALLED`). 도중에 `HALT_INFERRED`나 `VERY_SPARSE`로 바뀌면 그 tick에
`REJECTED`로 떨어지며, 이는 신호가 이미 나온 뒤에도 마찬가지다. 거래 정지가 추정되는 종목에
신호가 남아 있다는 이유로 진입하지 않는다.

순서상 적격성은 TTL보다 먼저 본다: 같은 tick에 TTL도 끝나고 부적격도 되었다면 사유는
`REJECTED`(부적격)로 남는다. 두 사유 모두 terminal이고, 왜 빠졌는지는 부적격 쪽이 더 많은
정보를 준다.

## 7. `HOD_BREAKOUT` 셋업

`FIRST_PULLBACK`은 V1에서 **비활성**이다(`enabled: false`). 정의는 `config.FirstPullbackConfig`에
남아 있으나 탐지 코드도, 백테스트 대상도 아니다. 한 번에 한 셋업만 검증한다.

### 7.1 입력

실제 관측 bar만 쓴다. `SessionTape`가 생성 시점에 synthetic bar를 거부하므로(`InvalidTape`),
셋업 입력에는 실제 체결 bar만 들어간다. `minute_clock_view`가 만드는 synthetic bar를 패턴
판정에 쓰려는 코드는 `require_actual_bars`에서 `SyntheticBarMisuse`로 막힌다.

### 7.2 성립 조건

as_of 기준 available한 실제 bar들을 `b[0..n-1]`(시간 오름차순)이라 하고, `hod`를 그 구간의
running high라 한다.

1. `hod`를 만든 bar의 인덱스를 `p`라 한다(동일 고가가 여러 개면 **가장 이른** 인덱스).
2. consolidation 창 = `b[p+1 .. n-1]`, 길이 `k = n - 1 - p`.
3. `hod_breakout.consolidation_min_bars <= k <= hod_breakout.consolidation_max_bars` (3..15 실제 bar)
4. 창 안의 어떤 bar도 `hod`를 갱신하지 않는다: 모든 `i > p`에 대해 `b[i].high <= hod`.
   동일 고가 재터치는 갱신이 아니므로 허용된다(`p`는 그 고가에 **최초** 도달한 인덱스다).
   창 안에서 `hod`를 넘는 bar가 나왔다면 그 bar가 새 `p`가 되고 창은 다시 시작한다.
5. 창의 최저가가 `hod` 대비 `hod_breakout.max_pullback_pct`(3.0%) 이내:
   `min(b[i].low for i > p) >= hod * (1 - 0.03)`
6. `p` 시점이 scanner gate 통과 시각(`detected_at`) **이후**일 필요는 없다. 당일 HOD가 감지
   이전에 형성돼 있어도 된다. 다만 `hod`는 as_of까지의 running extreme이며 당일 최종 고가가
   아니다(README PIT).

### 7.3 trigger price와 stop

```text
trigger_price = hod * (1 + hod_breakout.breakout_buffer_pct / 100)      # 0.1%
initial_stop  = min(b[i].low for i in consolidation window)             # 창 최저가
risk_per_share (1R) = entry_price - initial_stop
```

`initial_stop >= trigger_price`이면 셋업 무효(성립하지 않음). `1R <= 0`인 상태는 존재할 수
없다.

### 7.4 매 tick 재평가

`SETUP_READY`에서는 매 tick 다음 순서로 판정한다. 순서 자체가 규칙이다.

1. 적격성 재검사 실패 → `REJECTED` (6.3, 모든 live 상태 공통)
2. setup TTL 경과 → `EXPIRED`
3. **trigger 도달 → `ENTRY_SIGNALLED`** (8.1). 셋업이 armed된 뒤 available해진 bar 전부를
   본다. tick을 건너뛰어도 돌파를 놓치지 않는다.
4. 마지막 실제 bar의 종가가 `initial_stop` 아래 → `CANCELLED`(하향 무효화)
5. 셋업 재탐지(7.2). 유효한 셋업이 없으면 `WATCHING`으로 후퇴, `trigger_price`나
   `initial_stop`이 달라졌으면 새 셋업으로 교체하고 setup TTL을 다시 센다.

3이 4보다 앞에 있는 이유: 한 bar 안에서 trigger를 건드리고 stop 아래로 마감한 bar는 "진입한
적 없음"이 아니라 "진입 후 같은 분에 손절"이다(10절 4항). 4를 먼저 보면 실제로 났을 손실이
연구에서 사라진다.

새 고가가 trigger 아래에서 형성되면(`breakout_buffer_pct > 0`일 때 가능) 창이 리셋되어 보통
`WINDOW_TOO_SHORT`가 되고, 후보는 `WATCHING`으로 돌아가 창이 다시 자랄 때까지 기다린다.

## 8. 진입

### 8.1 신호 (`ENTRY_SIGNALLED`)

실제 bar의 `high >= trigger_price`인 첫 bar가 신호 bar다. 이 판정은 그 bar가 available해진
시각(`bar.timestamp + 1분`)에 이루어진다. bar 내부의 체결 순서는 관측할 수 없으므로 V1은
"그 분 안에 trigger를 건드렸다"까지만 안다.

### 8.2 가격 드리프트 (`CANCELLED`)

신호 시점의 참조가격(`trigger_price`) 대비 체결 판정 시점의 최신 가격이
`candidate.price_drift_tolerance_pct`(1.0%)를 초과해 벌어지면 `CANCELLED`다. 이미 1% 넘게
떠난 돌파는 쫓지 않는다.

### 8.3 체결 모델 (백테스트)

stop-limit이 아니라 stop 주문 가정이다.

| 상황 | 체결가 |
| --- | --- |
| 신호 bar의 `open <= trigger_price <= high` | `trigger_price` |
| 신호 bar의 `open > trigger_price` (갭) | `open` (단, 8.2 드리프트 검사 적용) |
| 그 외 | 체결 없음 |

체결은 신호 bar 자체에서 일어난다고 본다. 이 가정은 낙관적이며, B-E0에서 "다음 bar 시가
체결" 보수 시나리오와 함께 두 번 계산해 민감도를 남긴다.

FSM은 체결을 스스로 판정하지 않는다. 신호를 낸 tick의 **다음** tick에 엔진이 체결 여부와
체결가를 회신하고, 그 tick에서 `ENTERED`가 된다. 그래서 `entered_at`은 "엔진 회신이 기록된
tick"이고, 체결가가 가리키는 bar는 신호 bar다. signal TTL 2분은 엔진에게 두 tick을 준다.

수수료·슬리피지 수치는 B-E0에서 정한다. 이 문서는 모델 형태만 고정한다.

### 8.4 리스크와 수량

```text
risk_budget = equity * risk.risk_per_trade_pct / 100        # 0.5%
shares      = floor(risk_budget / (entry_price - initial_stop))
position_value <= equity * risk.max_position_pct / 100      # 20%
```

- `shares == 0`이면 진입하지 않고 `CANCELLED`(사유 `SIZE_ZERO`).
- 동시 보유 `risk.max_open_positions = 3`. 초과하면 새 신호는 `CANCELLED`(사유 `MAX_POSITIONS`),
  대기열에 넣지 않는다.
- 일일 손실 `risk.daily_loss_limit_r = 3.0R` 도달 시 그날 신규 진입 중단(기존 포지션은 규칙대로
  청산). 후보는 `CANCELLED`(사유 `DAILY_LOSS_LIMIT`).

## 9. 청산

우선순위(한 bar에서 여러 조건이 걸릴 때): `HARD_STOP` > `EOD_EXIT` > `TIME_STOP` >
`PARTIAL_TRAIL`/`TRAILING_STOP`. 최악을 먼저 가정한다.

| 사유 | 규칙 | 체결가 |
| --- | --- | --- |
| `HARD_STOP` | 실제 bar의 `low <= stop` | `stop` (bar가 stop 아래로 갭이면 `open`) |
| `PARTIAL_TRAIL` | `high >= entry + exit.partial_take_profit_r * 1R` (2R) | `exit.partial_exit_fraction`(50%)을 그 가격에 청산, 잔량 stop을 entry(본전)로 올림 |
| `TRAILING_STOP` | 부분청산 이후, stop = `max(현재 stop, 직전 실제 bar의 low)` (`TrailingModel.PREVIOUS_ACTUAL_BAR_LOW`) | 갱신된 stop |
| `TIME_STOP` | 진입 후 `exit.time_stop_minutes`(30 wall-clock분) 경과 시점까지 부분청산에 도달하지 못한 경우 | 그 시점 마지막 실제 bar의 종가 |
| `EOD_EXIT` | 15:55 ET | 그 시각 마지막 실제 bar의 종가 |

- 부분청산에 도달했으면 time stop은 적용하지 않는다. 그 뒤로는 트레일링이 관리한다.
- 오버나이트 없음. 모든 포지션은 당일 청산한다(B는 장중 전략이다).
- 갭 청산에서 체결가가 stop보다 불리하면 불리한 값을 쓴다. 유리한 쪽으로 반올림하지 않는다.

### 9.1 구현이 확정한 세부 규칙

| 항목 | 규칙 | 이유 |
| --- | --- | --- |
| 청산 스캔 시작점 | **진입 bar부터**. 진입 bar의 저가도 stop과 비교한다 | 8.3에서 체결은 신호 bar이고 `entered_at`은 그 다음 tick이다. 진입 bar를 건너뛰면 "같은 분에 돌파 후 손절"(10절 4항)이 연구에서 사라진다 |
| `HARD_STOP` vs `TRAILING_STOP` | stop이 아직 최초 `initial_stop`이면 `HARD_STOP`, 한 번이라도 올라갔으면(본전 포함) `TRAILING_STOP` | 두 사유의 구분 기준을 값으로 고정 |
| 트레일 갱신 시점 | bar `i`를 판정하기 **전에** `stop = max(stop, bar[i-1].low)` | `PREVIOUS_ACTUAL_BAR_LOW`의 정의. stop은 내려가지 않는다 |
| 부분청산 수량 | `floor(보유수량 × exit.partial_exit_fraction)`. 0이면(1주 등) **전량**을 target에서 청산하고 포지션 종료 | 1주를 반으로 나눌 수 없다. 2R 도달은 사실이므로 이익을 실현하되, 없는 분할을 지어내지 않는다 |
| 데드라인 체결가 | time stop·EOD는 **데드라인 이전** 마지막 실제 bar의 종가. 데드라인 뒤에 찍힌 bar는 쓰지 않는다 | 조용한 tape가 데드라인을 미루지 못하고, 데드라인 후 가격이 체결가가 되지도 않는다 |
| tick 건너뜀 | 한 번에 여러 bar가 들어와도 bar 단위로 순서대로 판정한다. tick 단위 호출과 결과가 같아야 한다 | 엔진 호출 주기가 결과를 바꾸면 결정성이 깨진다 |

### 9.2 사이징 세부 (8.4 구현)

- `shares = floor(risk_budget / risk_per_share)`를 먼저 구하고, `max_position_pct` 한도로
  **낮추기만** 한다. 한도가 수량을 올리는 경우는 없다.
- 한도에 걸렸는지는 결과에 기록한다(`capped_by_position_limit`). 기본값(리스크 0.5%, 한도 20%)에서
  손절폭이 2.5% 이내면 한도가 먼저 걸린다. 이 사실은 결과 해석에 필요하다.
- 1R은 진입 시점에 고정된다. 트레일은 stop을 올리지만 R을 다시 정의하지 않는다.

## 10. PIT 계약 (FSM 계층 추가분)

피처 계층 계약(README)에 더해 FSM이 지키는 것:

1. 한 tick의 입력은 `available_at <= as_of`인 bar와 D-1 이하 메타데이터뿐이다. 셋업 탐지·신호·
   청산 판정은 전부 그 컷을 거친 tape에서만 계산한다.
2. `hod`는 running extreme이다. 당일 최종 고가를 참조하는 경로는 없다.
3. synthetic bar는 패턴·ATR·돌파·체결 판정에 들어가지 못한다(`require_actual_bars`).
4. 청산은 진입 bar보다 이른 bar를 볼 수 없다. 같은 bar에서 진입과 `HARD_STOP`이 동시에
   성립하면, bar 내부 순서를 모르므로 **손실 쪽(`HARD_STOP` 즉시 체결)** 으로 판정한다.
5. 미래 bar를 주입하는 mutation 테스트가 FSM 결과를 바꾸지 못해야 한다(피처 계층 테스트와
   같은 방식으로 FSM 계층에도 추가한다).

## 11. 결정성

- 같은 입력(tape, config, rules)에서 같은 상태 전이 순서가 나와야 한다.
- 심볼 처리 순서는 score 내림차순, 동점은 5절 tie-break. 집합·딕셔너리 순회 순서에 의존하지
  않는다.
- 부동소수 비교는 값 그대로 비교한다. 임의의 epsilon을 넣지 않는다(임계값 자체가 연구
  파라미터이므로, 경계에서 흔들리는 것은 데이터의 성질이지 버그가 아니다).

## 12. 변경 정책

이 규칙을 바꾸면 canonical checksum이 바뀌고, 그 시점부터 다른 연구다. 결과를 본 뒤의 수정은
`b_fsm_rules_v2.json`과 새 선언으로만 한다. 결과를 보기 전의 수정(설계 오류 발견 등)은 같은
V1 안에서 허용되며, 사유와 이전 checksum을 `.sha256` 파일에 남긴다(D가 쓴 방식과 동일).

## 13. 이 문서가 처음 정한 결정들

기존 config·README에 답이 없어 F0에서 새로 정한 항목이다. 되돌릴 때는 여기를 본다.

| # | 결정 | 근거 |
| --- | --- | --- |
| 1 | score 공식(5절)과 가중치 0.5/0.3/0.2 | config에 threshold만 있고 공식이 없었다 |
| 2 | 모멘텀 레그는 OR, 유동성은 AND | 모멘텀은 시간축마다 다르게 나타나고, 유동성은 전부 필요하다 |
| 3 | scanner 시간창 09:35~15:30 ET | 개장 직후 관측 부족, 마감 직전 time stop·EOD 충돌 회피 |
| 4 | spread gate 미적용 | Basic 분봉에 호가 없음, 추정 스프레드 만들지 않음 |
| 5 | UNKNOWN fail-closed (RVOL PARTIAL만 예외) | 값 없음을 통과로 해석하지 않는다 |
| 6 | consolidation 창 = HOD 직후부터 현재까지 | "HOD를 찍고 눌린 자리"라는 셋업 정의에 직결 |
| 7 | 진입·손절 동시 성립 시 손절 우선 | bar 내부 순서 미관측, 낙관 금지 |
| 8 | 부분청산 도달 시 time stop 해제 | 두 규칙이 같은 포지션을 반대로 관리하는 것을 막는다 |
| 9 | `FIRST_PULLBACK` V1 비활성 | 한 번에 한 셋업만 검증 |
| 10 | 진입 bar 체결 가정 + B-E0 보수 시나리오 병행 | 낙관 가정을 숨기지 않고 민감도로 남긴다 |
| 11 | `SETUP_READY` 매 tick 재평가, 셋업 소멸 시 `WATCHING` 후퇴 | 기계적 사유로 멀쩡한 후보를 버리지 않는다 |
| 12 | trigger 판정을 무효화보다 먼저 | 같은 분에 돌파 후 손절된 거래를 연구에서 지우지 않는다 |
| 13 | 체결은 엔진이 다음 tick에 회신 | 순수 계층은 돈·체결을 모른다는 기존 경계 유지 |
| 14 | 청산 스캔을 진입 bar부터 시작 | 7번 결정을 실제로 성립시키는 구현 조건 |
| 15 | 부분청산 수량이 0이면 전량 청산 | 1주 포지션에서 없는 분할을 지어내지 않는다 |
| 16 | 데드라인 체결가 = 데드라인 이전 마지막 종가 | 조용한 tape와 데드라인 이후 가격을 모두 배제 |
| 17 | stop이 올라간 뒤의 청산은 `TRAILING_STOP` | 사유 구분을 값 기준으로 고정 |
| 18 | 사이징은 한도로 낮추기만 | 한도가 리스크 기준 수량을 키우지 않는다 |
