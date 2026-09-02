# USB Runtime Safety — Stage 8

## 목적과 범위

`operations_v0`는 단일 Naver Cloud 서버와 단일 worker에서 장애 발생 시 잘못된
신규 매매를 중단하고 상태와 원인을 보존하는 fail-safe foundation이다. 자동 복구,
Kiwoom, 외부 Broker/API, Telegram/Kakao/Slack, UI, systemd 설치, HA는 포함하지 않는다.

## Runtime mode와 거래 정책

- `NORMAL`: Actual/Paper ENTER, ADD 및 기존 포지션 관리 가능.
- `SAFE_MODE`: Actual/Paper ENTER와 ADD 금지. 정확한 데이터가 있는 기존 포지션의
  정상 EXIT, risk-reducing partial sell, mandatory Day2 exit, reconciliation은 허용한다.
- `HALTED`: 전략 자동 action을 중단한다. Kill Switch liquidation과 상태 확인만 허용하며
  별도 operator release가 필요하다.

Runtime orchestration은 `operational_safe_mode(mode)`를 기존 `TradingEligibility.safe_mode`에
전달한다. Risk 공식을 복제하지 않는다. Offline Shadow/Replay에는 운영 mode를 전달하지
않으므로 research path는 독립적이다.

시세 장애 중에는 임의 가격으로 EXIT intent나 blind market sell을 만들지 않는다.
Safe Mode 진입, failure 기록, operator 확인이 V0 정책이다. Broker-native protective order는
Kiwoom Spike에서 확인한다.

## Failure 정책

Severity는 `WARNING`, `ERROR`, `CRITICAL`이다. WARNING은 기록 중심, ERROR는 현재 작업
실패, CRITICAL은 기본적으로 Safe Mode 또는 명시적 HALTED 전환이다. Severity와 mode는
완전한 1:1 관계가 아니며 아래 정책이 결정론적으로 적용된다.

| Code | 기본 severity/처리 | Operator action |
|---|---|---|
| MARKET_DATA_STALE / UNAVAILABLE | CRITICAL, SAFE_MODE | 시세와 세션 확인 |
| MARKET_DATA_INVALID | ERROR 또는 CRITICAL policy | 데이터 확인 |
| EXECUTION_UNAVAILABLE | ERROR/CRITICAL, SAFE_MODE | Broker 상태 확인 |
| EXECUTION_TIMEOUT / REJECTED | ERROR; 최근 window threshold 시 SAFE_MODE | 주문/인프라 확인 |
| POSITION_MISMATCH / ORDER_MISMATCH / STRATEGY_STATE_MISMATCH | CRITICAL, SAFE_MODE | reconciliation |
| DATABASE_ERROR | CRITICAL, SAFE_MODE 전환 시도 후 예외 재전파 | DB 확인/재시작 |
| RUNTIME_INVARIANT_VIOLATION | CRITICAL, SAFE_MODE | money/state 점검 |
| HEARTBEAT_MISSED | 외부 watchdog 판단, failure 기록 | process 확인 |
| STARTUP_RECONCILIATION_FAILED | CRITICAL, SAFE_MODE | reconciliation |
| MANUAL_SAFE_MODE | CRITICAL, SAFE_MODE | 수동 복구 |
| MANUAL_HALT | CRITICAL, HALTED | 별도 halt release |
| KILL_SWITCH_ACTIVATED | CRITICAL, HALTED | liquidation 결과 확인 |

일반 domain validation 거부(예: `SELL_EXCEEDS_POSITION`)는 호출자가 infrastructure failure로
분류하지 않는 한 execution threshold에 포함하지 않는다. Tracker는 300초 sliding window와
기본 threshold 3을 사용한다. 성공은 아직 window 안의 failure를 지우지 않으며 시간 경과로
제거한다.

## Heartbeat와 freshness

Heartbeat는 실제 aware wall-clock으로 singleton row의 `last_heartbeat_at`을 갱신한다.
기본 interval은 60초, stale threshold는 180초다. 죽은 process가 자기 죽음을 감지할 수
없으므로 실제 missed-heartbeat 판정은 향후 systemd/Naver Cloud watchdog 책임이다.

Market freshness는 `market_as_of`와 `latest_data_available_at`을 분리한다. XNYS Calendar의
실제 regular open/close(holiday, DST, early close 포함) 안에서 REGULAR 데이터만 기본 감시한다.
기본 stale threshold는 120초다. 폐장 후 데이터 정지는 failure가 아니다. PREMARKET 감시는
provider coverage가 확인될 때까지 기본 off이며 config로 선택 가능하다.

## Reconciliation과 restart

Broker position이 execution truth다. Strategy state와 다르면 내부 상태를 조용히 수정하지
않고 mismatch, failure event, SAFE_MODE를 만든다. 비교 가능한 V0 항목은 terminal/open
position, active state/no position, broker-only position, terminal/no-state open order다.
Strategy가 quantity truth를 저장하지 않으므로 정밀 quantity 비교는 향후 adapter snapshot
계약에서 추가한다.

Startup은 persisted mode를 먼저 읽는다. SAFE_MODE/HALTED는 restart 후 유지된다. NORMAL도
startup reconciliation이 실패하면 SAFE_MODE가 된다. HALTED는 reconciliation 성공으로
자동 해제되지 않는다.

## Kill Switch와 recovery

Kill Switch는 먼저 HALTED를 atomic persistence하고 Broker positions별 full SELL EXIT intent를
생성한다. Position이 없으면 halt 성공이다. SimBroker full fills 후에도 HALTED를 유지한다.
일부 rejection/exception이면 failure를 추가하고 HALTED를 유지한다. 실제 Kiwoom liquidation은
Stage 11 이후 adapter로 검증한다.

SAFE_MODE의 `recover_to_normal`은 manual acknowledgement, reconciliation PASS, unresolved
CRITICAL 0건을 모두 요구한다. Failure resolve만으로 자동 NORMAL이 되지 않는다. HALTED는
별도 `release_halt` command와 같은 조건을 요구한다.

## Persistence, notification, status

`runtime_state`는 check constraint로 `id=1`만 허용하며 첫 service bootstrap 때 NORMAL row를
생성한다. mode, 진입 원인, heartbeat/market/execution/reconciliation audit를 보존한다.
`runtime_failures`는 사건마다 새 row를 추가하고 resolve flag만 갱신한다. metadata는 canonical
JSON이며 secret/token을 넣어서는 안 된다. Failure event와 mode transition은 한 Session commit에
저장한다. persistence 실패는 rollback 후 예외를 숨기지 않는다.

`NotificationSink`는 failure domain만 알며 현재 `LoggingNotificationSink`와 테스트용
`InMemoryNotificationSink`만 있다. Heartbeat 자체는 사용자에게 매분 알리지 않는다.
`RuntimeStatusSnapshot`은 mode, health, heartbeat, market/execution 상태, unresolved count,
open position/order count, 마지막 reconciliation/failure를 UI 독립 domain output으로 제공한다.

## Failure injection과 현재 한계

테스트는 deterministic SimBroker timeout/unavailable injection, stale/missing data, state mismatch,
liquidation rejection을 재현한다. 단일 worker 가정이며 distributed lock, DB failover/WAL 복구,
자동 self-healing은 없다. SimBroker state 자체는 in-memory이므로 restart test에서는 새 Broker
snapshot을 명시적으로 공급한다. 실제 position quantity/open-order semantics, broker error taxonomy,
protective orders, authentication/token, rate limits는 Kiwoom Spike와 Paper 단계에서 확인한다.
