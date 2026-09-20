# Strategy B Engine Design V1

작성 2026-09-20. `B_F0_FSM_RULES_V1.md`가 고정한 규칙을 **어떻게 실행할지**만 다룬다.
새 판정 규칙을 만들지 않는다. 수수료·슬리피지 수치, 초기 자본, 성공/실패 판정 기준은 이 문서가
아니라 백테스트 사전등록(B-E0)에서 정한다.

- 규칙 정본: `b_fsm_rules_v1.json` (canonical `54c60630...`)
- 순수 계층: `backend/app/strategy_b/` (scanner, eligibility, setups, fsm, exits, sizing)
- 이 문서의 결과물: `backend/app/backtest/strategy_b/` 아래 엔진

## 1. 시작 상태 (2026-09-20 실측)

| 자산 | 상태 | B 엔진에서의 처지 |
| --- | --- | --- |
| `app/strategy_b/` 순수 계층 | 있음, 테스트 173개 | 판정의 유일한 권위 |
| `app/backtest/strategy_b/dataset.py` | 있음 (`SparseMinuteDataset`, 심볼별·세션별 bar) | 입력 |
| `app/backtest/engine/identity.py` | 있음 (`run_identity`, `code_digest`, `source_provenance`) | 재사용 |
| `app/backtest/workspace/` | 있음 (manifest, writer lock, safe_write) | 재사용 |
| `app/backtest/historical_store/session_audit.py` | 있음 (HYBRID-S 사후 검증) | `CA_SUSPECT` 입력(D-1 이전 세션만) |
| `app/market/calendar.py` | 있음 | 세션 그리드·조기 마감 |
| `app/backtest/replay/clock.py`, `position_replay.py` | **없음**(회사 PC 미커밋) | **의존 금지** |
| `app/backtest/portfolio/` | 없음 | B가 자체 구현 |
| `app/backtest/engine/` driver·store | 없음(docstring만 존재) | B가 첫 어댑터로 만든다 |

마지막 두 줄이 이 설계의 제약이다. `baseline/runner.py`는 저장소에 없는 `replay.clock`을
import하므로 이 PC에서 실행되지 않는다(메모 `project_usb_strategy_a_home_blocked`). B 엔진이
같은 모듈에 기대면 B도 한 대의 PC에서만 돌아가게 된다. 그래서 B는 A의 replay 내부를 쓰지 않고,
가용성 계약을 자기 것(`models.AVAILABILITY_DELAY`, bar 시작 + 1분)으로 쓴다. B가 A에서 가져오는
것은 **데이터와 저장소 계약뿐**이고 실행 규칙은 하나도 가져오지 않는다.

## 2. 실행 모델

세션 단위로 처리한다. 심볼 하나의 4개월치를 전부 메모리에 올리지 않고, 하루치 bar만 올린다.

```text
for session in sessions:                 # 2026-05-18 .. 2026-09-16, XNYS
    for symbol in scope(session):        # D-1 기준 scope 통과 심볼
        bars = dataset.bars(session)     # 실제 bar만, 없으면 ()
        prefilter -> 후보 분(minute) 목록  # 3절
    tick loop over 09:35..15:55 ET       # 4절
```

- 세션 사이에 상태를 이월하지 않는다. B는 장중 전략이고 오버나이트가 없다(F0 9절).
  이월되는 것은 계좌 equity와 누적 거래 기록뿐이다.
- RVOL은 직전 20세션의 프로파일이 필요하다. 세션을 날짜 순으로 처리하면서 심볼별 프로파일 링을
  굴린다(세션 하나 처리 후 그 세션의 프로파일을 append, 가장 오래된 것 pop).
- scope는 D-1 메타데이터·일봉만 본다. 세션 D의 루프에 들어가기 전에 확정한다.

## 3. 두 단계 평가

### 3.1 왜 필요한가 (실측)

| 경로 | 측정값 | 전체 그리드 환산 |
| --- | --- | --- |
| 순수 계층 `compute_feature_snapshot` + `scan` 1회 | **176 us** (390 bar 밀집 tape, RVOL 20세션) | 3,883종목 x 374분 x 104세션 = **151M회 = 7.4시간**(단일 코어) |
| `prefilter.cheap_gate_minutes` 1 심볼-세션 (구현 실측) | **487 us** (390 bar, 381 tick) | 404k 심볼-세션 = **약 3.3분** |

7.4시간은 한 번 돌리기엔 견딜 만하지만 반복 실험에는 못 쓴다. 사전필터는 그 비용을 100배 이상
줄인다. 단 정확성 조건이 하나 붙는다.

구현 실측 487us는 numpy 계산 자체가 아니라 `MomentumBar` 객체 390개를 네 번 순회해 배열로 바꾸는
비용이 대부분이다(순수 numpy 프로토타입은 99us였다). 더 필요해지면 `SparseMinuteDataset`이
객체 대신 열 배열을 함께 내주면 되고, 판정 규칙은 건드리지 않는다. 지금은 3.3분이 예산 안이라
최적화하지 않는다.

### 3.2 동치 계약

사전필터는 **게이트를 통과할 수 있는 분을 하나도 버리지 않는다**(superset). 구체적으로 F0 4.1의
값싼 조건만 계산한다.

- 모멘텀 레그 3개(`return_1m/3m/5m`): wall-clock 기준 직전 체결가 대비. sparse tape에서도
  "N칸 앞의 bar"가 아니라 "N분 전 마지막 체결"이므로 `searchsorted`로 같은 의미를 계산한다.
- 5분 이동 거래대금: `close x volume`의 누적합 차분.

RVOL, halt 추론, tape density, 기업행위, scope는 사전필터에 넣지 않는다. 이들은 통과한 분에서만
순수 계층이 계산한다. 즉 사전필터는 게이트의 **부분집합 조건**만 적용하므로 결과는 superset이다.

검증: `test_strategy_b_prefilter.py`가 무작위 tape(밀도 8~100%, 프리마켓 유무, 급등 포함)에서
그리드의 **모든 분**에 대해 사전필터 판정과 순수 계층의 값싼 조건 판정이 **정확히 같음**을
단언한다(superset보다 강한 조건). 빈 세션, bar 1개, 프리마켓만 있는 세션, 그리드 뒤의 bar도
같이 고정했다. 이 테스트가 두 경로의 유일한 접착제다.

scope 주의: 수익률은 `EXTENDED_DAY`(프리마켓 포함), 거래대금은 `SESSION_LOCAL`(09:30부터)이다.
사전필터는 이 조합을 하드코딩하지 않고 다른 조합이 들어오면 거절한다.

### 3.3 순수 계층이 권위다

사전필터가 남긴 분에서만 `compute_feature_snapshot` -> `scan` -> FSM을 돌린다. 최종 판정은
언제나 순수 계층의 값이다. 사전필터 결과는 어떤 결과 파일에도 들어가지 않는다.

## 4. Tick 루프 (한 세션)

한 tick은 bar 가용 시각이다(bar 시작 + 1분). 09:36부터 15:56까지 1분 간격.

```text
1. 만기·청산 먼저: 보유 포지션 advance_position(...)  -> 체결·부분청산 기록, equity 갱신
2. 후보 전진:      살아 있는 후보 advance(candidate, tick, config)
3. 신규 감지:      사전필터가 지목한 (심볼, 분)에서만 snapshot -> scan -> open_candidate
4. 랭킹:           score desc, 거래대금 desc, 심볼 asc (F0 5절)
5. 체결 회신:      ENTRY_SIGNALLED 후보에 대해 포트폴리오가 FillOutcome 생성 (5절)
```

1이 2·3보다 먼저인 이유: 같은 tick에 자리가 나야 새 진입이 가능한지 판단할 수 있고, 손실이
확정돼야 일일 손실 한도가 정확해진다. 포지션은 후보보다 항상 앞선다.

2가 3보다 먼저인 이유: 이미 추적 중인 심볼이 그 tick에 새 후보로 또 열리면 같은 심볼에 두 개의
후보가 생긴다. 기존 후보를 먼저 전진시키고, 살아 있는 후보가 있는 심볼은 3에서 건너뛴다.

## 5. 포트폴리오

엔진이 가진 유일한 가변 상태다. F0 8.4의 세 한도를 여기서 강제한다.

| 한도 | 판정 | 후보 처리 |
| --- | --- | --- |
| `max_open_positions` (3) | 보유 수 >= 3 | `FillOutcome(filled=False, MAX_POSITIONS)` |
| `max_entries_per_symbol` (1) | 그날 이미 진입한 심볼 | 후보를 아예 열지 않는다 |
| `daily_loss_limit_r` (3.0R) | 당일 실현손익 <= -3R | `FillOutcome(filled=False, DAILY_LOSS_LIMIT)` |
| 수량 0 | `sizing.position_size` 거절 | `FillOutcome(filled=False, SIZE_ZERO)` |

- R 단위 손익은 거래별 `initial_stop` 기준으로 계산한다(진입 시 고정, F0 9.2).
- 체결가는 F0 8.3의 모델을 엔진이 적용한다. 신호 bar의 `open`과 `trigger_price` 비교로 결정되며,
  보수 시나리오(다음 bar 시가)는 같은 입력으로 한 번 더 돌린다.
- 수수료·슬리피지는 **인자로 받되 기본값을 두지 않는다**. B-E0가 값을 정하기 전에는 엔진이
  0을 가정하지 않고 호출자가 명시하도록 강제한다.

## 6. 산출물

`backtest/strategy_b/<run_id>/` 아래 append-only로 쓴다(기존 workspace safe_write 계약).

| 파일 | 내용 |
| --- | --- |
| `identity.json` | `run_identity` 결과 + `source_provenance` + 규칙 checksum + config fingerprint + dataset identity |
| `trades.jsonl` | 체결 1건 1줄: 심볼, 셋업, 진입/청산 시각·가격·수량, 사유, R배수, 수수료 |
| `candidates.jsonl` | 후보 생애 1건 1줄: 상태 전이 시각, score, drop 사유 |
| `sessions.jsonl` | 세션별 후보 수, 진입 수, 한도로 막힌 수, 사전필터 통과 분 수 |
| `metrics.json` | 거래 수, 승률, 평균 R, 기대값, 최대 낙폭 (판정은 B-E0) |

`candidates.jsonl`이 중요하다. 진입까지 못 간 후보의 탈락 사유 분포가 "규칙이 너무 빡빡한가"를
답하는 유일한 근거이고, 결과가 나쁠 때 규칙을 고치는 대신 무엇이 걸렀는지 먼저 보게 한다.

## 7. 결정성

- run identity에 들어가는 것: strategy id, 규칙 canonical checksum, config fingerprint,
  dataset identity(심볼별 entry digest), 세션 범위, 엔진 `code_digest`, 체결 시나리오.
- 들어가지 않는 것: 실행 시각, 머신, git 상태(provenance로 따로 기록).
- 심볼 순회 순서는 정렬로 고정한다. 딕셔너리·집합 순회 순서에 의존하지 않는다.
- 부동소수 누적(equity)은 거래 단위로만 갱신하고, 같은 입력에서 같은 순서로 더한다.

## 8. 성능 예산

| 단계 | 예산 | 근거 |
| --- | --- | --- |
| 사전필터 전체 | 약 3.3분 | 구현 실측 487 us x 404k 심볼-세션 |
| parquet 로드 | 미측정 | 심볼별 entry, 세션 단위 캐시. Drive가 아니라 로컬 복사본에서 읽는다 |
| 순수 계층 확정 | 통과 분 수에 비례 | 실측 176 us/분 |
| 목표 | 전체 1회 실행 30분 이내(단일 코어) | 반복 실험이 가능해야 규칙을 안 고치고 버틴다 |

목표를 넘으면 병렬화(세션 단위 프로세스 분할)를 먼저 검토한다. 판정 규칙을 성능 때문에 바꾸지
않는다.

## 8.1 구현이 확정한 것 (2026-09-20)

설계대로 만들면서 확정한 항목이다. 코드는 `backtest/strategy_b/{costs,portfolio,engine}.py`.

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 체결 시점 | 신호 tick에서는 체결하지 않는다. 다음 tick에 후보를 **먼저 무조건** 전진시키고(TTL·적격성·드리프트 통과 확인) 그 뒤에 계좌에 묻는다 | 계좌가 먼저 사버리면 FSM이 같은 tick에 그 후보를 떨어뜨렸을 때 아무 후보도 진입하지 않은 포지션이 남는다 |
| 계좌 거절 | `Portfolio.enter`는 성공할 때만 상태를 바꾸고, 거절이면 `DropReason`을 돌려준다. 그 값이 그대로 `FillOutcome(filled=False)`가 된다 | 거절 사유가 후보 기록과 계좌 기록에서 같은 값이 된다 |
| equity | 실현 손익만 반영한다. 보유 포지션을 시가평가하지 않는다 | 평가하면 새 포지션 크기가 기존 포지션의 미실현 이익에 연동돼, 운 좋은 오전 하나가 복리 효과로 부풀려진다 |
| 재진입 | 하루 진입 한도를 쓴 심볼은 엔진이 후보로 열지 않는다. `enter`가 그런 호출을 받으면 거절이 아니라 **예외**다 | F0 3.2의 계약 위반이므로 조용히 다른 사유로 기록되면 안 된다 |
| 비용 | `CostModel(fee_bps_per_side, slippage_bps_per_side)`에 기본값 없음. 슬리피지는 체결가를, 수수료는 현금을 움직인다 | 값을 안 정한 채 0으로 돌아가는 사고를 막는다 |
| R 계산 | 1R은 **슬리피지가 반영된 체결가** 기준. 실현 R은 수수료까지 뺀 순값 | 비용 전 R을 보고하는 연구는 사실이 아니다 |
| 조기 마감 | EOD 청산 = `min(15:55, 정규 마감 - 5분)`. F0 9절에 선언 반영 | 반일장에서 15:55는 장 종료 후다 |
| tick 그리드 | 09:35부터 정규 마감까지. 감지는 15:30까지만, 그 뒤 tick은 기존 후보·포지션 전용 | 감지 창과 관리 창은 다른 것이다 |

## 9. B-E0로 미루는 것

이 문서가 답하지 않는 것, 즉 데이터를 보기 전에 사용자가 정해야 하는 값:

1. 수수료·슬리피지 수치
2. 초기 자본과 통화 처리(KRW 계좌가 USD 주식을 거래한다)
3. 성공/실패 판정 기준과 최소 표본 수
4. 보수 시나리오(다음 bar 시가 체결)를 주 결과로 볼지 민감도로 볼지

엔진은 이 값들을 인자로 받도록 만들고, 기본값을 두지 않는다.
