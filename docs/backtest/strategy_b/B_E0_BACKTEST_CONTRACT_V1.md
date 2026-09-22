# Strategy B E0 백테스트 사전등록 V1

작성 2026-09-21. `B_F0_FSM_RULES_V1.md`가 고정한 판정 규칙을 **데이터에 적용할 때의 실행 계약과
판정 기준**을 정한다. 새 판정 규칙을 만들지 않는다.

- Strategy ID: `REALTIME_MOMENTUM_V1`
- 계약 정본: `b_e0_contract_v1.json` (canonical sha256은 `b_e0_contract_v1.sha256`)
- 규칙 정본: `b_fsm_rules_v1.json` (canonical `a082b998...`), 이 문서는 그 값을 하나도 바꾸지 않는다
- 선언 시점 저장소: `main` HEAD `1ba0773`
  (초안 작성 중 다른 세션이 Strategy E 커밋 3건을 올려 HEAD가 `758e9b1`에서 옮겨갔다.
  이 계약이 의존하는 `strategy_b`, `execution/config`, `bootstrap_paper_account`,
  `strategy_c_e0`는 그 커밋들에 포함되지 않았으므로 여기 적힌 값은 전부 그대로 유효하다.)
- **선언 시점 B 결과: 0건.** B 백테스트는 아직 한 번도 실행된 적이 없다
- 결과 문서(미작성): `B_E0_RESULTS_V1.md`

## 0. 이 문서가 답하는 것

`B_ENGINE_DESIGN_V1.md` 9절이 "데이터를 보기 전에 사용자가 정해야 한다"고 미뤄 둔 값들이다.

| # | 미뤄 둔 질문 | 이 문서의 답 | 절 |
| --- | --- | --- | --- |
| 1 | 수수료·슬리피지 수치 | 편도 all-in 25bps (수수료 10 + 체결비용 15), 왕복 50bps | 3 |
| 2 | 초기 자본과 통화 | **7,428.92 USD** (A/B/C/D 공통), 환전 미모형 | 2 |
| 3 | 성공/실패 판정 기준과 최소 표본 | 거래 30건·세션 20일 게이트, 평균 net R >= 0.10, CI 하한 > 0, lift 게이트 | 5·6 |
| 4 | 보수 시나리오를 주 결과로 볼지 | **`NEXT_BAR_OPEN`을 주 결과로** 한다 | 4 |

규칙을 결과보다 먼저 고정하는 이유는 F0·C·D와 같다. 결과를 본 뒤 기준을 고치면 그 연구는
반증 시도가 아니게 된다. 비용·자본·판정 기준은 규칙만큼이나 결론을 좌우하므로 같이 동결한다.

## 1. 실행 창

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 달력 | XNYS | AGENTS 3절 |
| scope 구간 | 2026-05-18 ~ 2026-09-16, **84 세션** | 저장소 달력으로 실측 |
| 워밍업 | 2026-04-20부터 **20 세션** | `rvol.lookback_sessions = 20` |
| 조기 마감일 | **0일** | 실측. 2026-07-03은 조기 마감이 아니라 휴장이다 |

조기 마감일이 0이므로 F0 9절이 확정한 `min(15:55, 정규 마감 - 5분)` 규칙은 이 창에서 한 번도
작동하지 않는다. 그 규칙이 맞는지를 이 백테스트는 검증하지 못한다. 결과에 한계로 싣는다.

## 2. 결정 1: 공통 초기자본 정책

```text
initial_capital_usd = 7428.92
policy              = COMMON_STRATEGY_INITIAL_CAPITAL
fx_model            = NONE
```

### 2.1 정책

> 모든 전략은 동일 초기자본으로 시작한다. 각 전략은 독립 포트폴리오다. A/B/C/D 간 자금 공유는
> 없다. 전략별 수익률·손익을 비교할 때 초기자본 차이를 두지 않는다.

A, B, C, D가 7,428.92달러를 **나누는 것이 아니다.** 각 전략이 각각 7,428.92달러로 시작하는
독립 가상 계좌를 갖는다. 이 정책은 C·D에도 그대로 재사용한다.

### 2.2 Source of Truth

| 항목 | 값 |
| --- | --- |
| 상수 | `app.dev.bootstrap_paper_account.PAPER_INITIAL_CASH = Decimal("7428.92")` |
| 사본 | `app.backtest.ui.fx.PAPER_INITIAL_CASH_USD` |
| 의미 | 페이퍼 계좌의 시작 현금. 고정 환율에서 KRW 10,000,000에 해당한다 |

### 2.3 기록해야 할 충돌

**A의 백테스트 baseline은 지금 7,428.92가 아니다.**

| 위치 | 값 | 출처 표기 |
| --- | --- | --- |
| A 페이퍼 계좌 | 7,428.92 USD | `PAPER_INITIAL_CASH` |
| **A 백테스트 baseline** | **10,000 USD** | `baseline.contract.STARTING_CASH`, `STARTING_CASH_SOURCE = ASSUMED_RESEARCH` |

`baseline/contract.py`는 `PAPER_INITIAL_CASH`를 후보로 검토한 뒤 "paper account bootstrap cash;
not a backtest contract"라며 **명시적으로 기각**해 두었다. 즉 A의 기존 baseline 실행 결과는
10,000달러에서 나온 것이다.

따라서:

- B를 7,428.92로 맞추는 것만으로 A와 B의 **달러 금액**이 비교 가능해지지는 않는다. A baseline을
  같은 자본으로 재실행하기 전까지, A와 B의 비교는 R 배수·퍼센트로만 해야 한다.
- **이 계약은 A를 바꾸지 않는다.** A baseline 재실행은 별도 승인 작업이다.

### 2.4 자본 불변성 (왜 이 변경이 판정을 흔들지 않는가)

판정 지표인 **거래당 평균 net R은 초기자본에 거의 불변**이다.

```text
R 배수 = (청산가 - 진입가) / (진입가 - initial_stop)        # 수량이 들어가지 않는다
수수료 = 가격 x 수량 x bps
1R 금액 = (진입가 - initial_stop) x 수량
수수료를 R로 = 가격 x bps / (진입가 - initial_stop)          # 수량이 약분된다
```

리스크 예산과 포지션 상한이 둘 다 equity에 비례하므로, **둘 중 어느 쪽이 먼저 걸리는지도
자본에 불변**이다. 남는 것은 `floor()` 반올림 잔차와 `SIZE_ZERO` 경계뿐이다.

자본을 10,000에서 7,428.92로 낮추면 `SIZE_ZERO`가 아주 조금 더 자주 나온다. 주당 손절폭이
37.14달러를 넘을 때 발생한다. 그 외에는 달러 표시 보고값만 바뀐다.

### 2.5 환전

`fx_model = NONE`을 유지한다. 계좌는 USD 표시다. 원화 환전은 거래마다가 아니라 입금마다 한 번
일어나므로 거래당 기대값과 R 분포를 바꾸지 못한다. 배포 시점의 비용이지 연구의 비용이 아니다.
`fx_model`은 run identity에 명시적으로 들어간다.

### 2.6 파생값

`RiskConfig` 기본값(`risk_per_trade_pct = 0.5`, `max_position_pct = 20.0`)을 코드에서 읽은 값이다.
이 계약은 이 값을 바꾸지 않는다.

| 항목 | 첫 거래 시점 값 |
| --- | --- |
| 1거래 리스크 | 7428.92 x 0.005 = **37.1446 USD** (표시 $37.14) |
| 포지션 상한 | 7428.92 x 0.20 = **1485.784 USD** (표시 $1,485.78) |

**중요: 이 두 값은 첫 거래에서만 성립한다.** 엔진은 진입마다 **현재 equity**에서 다시 계산한다.

```text
risk_amount  = current_equity x risk_per_trade_pct / 100
position_cap = current_equity x max_position_pct / 100
```

`current_equity`는 실현 손익만 반영한다(보유 포지션 시가평가 없음). 근거는
`Portfolio.enter`가 `position_size(self.equity, ...)`를 호출한다는 것이다. 이 계약은 비율 기반
정의를 정본으로 두고, 위 달러 값은 보고용 표시일 뿐 어떤 입력도 아니다.

기존 초안의 `1R = $50`, `max position = $2,000`은 10,000달러에서 나온 파생값이었다. 삭제했다.

## 3. 결정 2: 비용

```text
commission_bps_per_side     = 10
execution_cost_bps_per_side = 15
base_all_in_cost_bps_per_side = 25
base_round_trip_cost_bps      = 50
```

`CostModel`에는 가격 손잡이 하나와 현금 손잡이 하나뿐이다. A가 스프레드와 슬리피지로 나눠 둔
가격 항(10 + 5)을 체결비용 15로 합친다. 분해는 계약에 기록해 두어 나중에 A와 대조할 수 있게 한다.

| 항목 | B | A (`ExecutionConfig`) |
| --- | --- | --- |
| 수수료 | 10bps/편도 | `commission_bps = 10` |
| 가격 항 | 15bps/편도 | `default_spread_bps 10 + default_slippage_bps 5` |
| 편도 합계 | **25bps** | **25bps** |
| 환전 | 미모형 | `fx_cost_bps = 0` |

**이 수치는 측정값이 아니라 선언값이다.** Massive Basic에는 호가가 없고(NBBO 403), F0 4.2는
이미 스프레드 게이트를 적용하지 않는다고 기록했다. B는 스프레드를 거르지도, 측정하지도 못한
채 가정한다.

### 3.1 비용 민감도의 정의

민감도 수준은 전부 **편도 all-in** 기준이다. 왕복은 그 두 배다.

| 수준 | 편도 all-in | 왕복 | 수수료/편도 | 체결비용/편도 |
| --- | --- | --- | --- | --- |
| `BASE` | 25bps | 50bps | 10 | 15 |
| `STRESS_30` | 30bps | 60bps | 10 | 20 |
| `STRESS_50` | 50bps | 100bps | 10 | 40 |
| `ZERO_COST` | 0 | 0 | 0 | 0 |

모든 수준에서 수수료 10bps를 고정하고 **체결비용만 움직인다.** 증권사 수수료는 알려진 고시
체계이고, 모르는 값은 스프레드이기 때문이다.

F0는 체결 모델의 형태만 고정하고 수치는 E0로 미뤘으므로(8.3, 16절), 이 표가 F0의 어떤 값과도
충돌하지 않는다.

주의: `STRESS_30`은 `BASE`보다 편도 5bps 높을 뿐이라 실질적으로 완만한 단계다. 결론을 실제로
시험하는 것은 `STRESS_50`(왕복 100bps)이다.

## 4. 결정 3: 체결 모델

### 4.1 주 결과는 `NEXT_BAR_OPEN`

F0 8.3은 신호 bar에서 `trigger_price`에 체결된다고 보고, 스스로 그 가정을 "낙관적"이라고
적었다. E0는 **`NEXT_BAR_OPEN`을 주 결과로** 정한다.

근거는 보수성이 아니라 **실시간 경로와의 일치**다.

- B의 런타임은 완결된 분봉에 반응한다(`AVAILABILITY_DELAY` 1분). Kiwoom 실측에서 분봉은
  분 마감 후 약 0.25초에 도착했다. 주문은 신호 bar가 닫힌 뒤에 나가고, 체결은 다음 봉 시가
  근처에서 일어난다.
- `SIGNAL_BAR`는 **미리 걸어 둔 stop 주문**을 모형한다. 런타임에는 그 기능이 없다.

따라서 `SIGNAL_BAR`는 주 결과가 아니라 **counterfactual**이다. "B가 얼마를 벌었나"가 아니라
"stop 주문 기능을 만들면 얼마가 더해지나"를 재는 실행이다.

### 4.2 체결 규칙과 signal TTL

sparse tape에서 한참 뒤의 봉에 체결되는 것을 막는 규칙을 명시한다.

```text
신호 bar S      = high가 trigger_price에 처음 도달한 실제 bar (F0 8.1)
signal_at       = ENTRY_SIGNALLED가 기록된 tick = S.timestamp + 1분(가용성 지연)
signal_expiry   = signal_at + candidate.signal_ttl_minutes

N = 같은 세션에서 S.timestamp보다 뒤인 첫 번째 실제 bar

if N이 존재하고 signal_expiry 이전에 available:
    체결가(비용 전) = N.open
else:
    체결 없음 -> 후보는 자기 signal TTL로 종료
```

### 4.2.1 `signal_ttl_minutes = 2` 확정

```text
signal_ttl_minutes = 2
status = RESEARCH_DEFAULT_PRECOMMITTED
units  = WALL_CLOCK_MINUTES
anchor = ENTRY_SIGNALLED
expiry = signal_at + signal_ttl_minutes      (경계 포함)
```

- 결과를 보고 최적화한 값이 **아니다.** 결과가 존재하기 전에 선약한 값이다. 동결 후 변경은
  B-E0 V1 수정이 아니라 **새 contract 버전**이다.
- **이미 코드에 있고 이미 2다.** `CandidateConfig.signal_ttl_minutes = 2`이며 주석에도
  wall-clock으로 적혀 있다. 이 계약은 그 값을 확인할 뿐 config를 고치지 않았다.
- `*_minutes`는 wall-clock, `*_bars`는 실제 관측 봉이라는 B의 시간 계약을 유지한다.
  `fsm._elapsed`는 단순 wall-clock 차이이므로 **조용한 tape가 TTL을 늘리지 못한다.**
- 옛 이름 `signal_ttl_bars`는 존재하지 않으며 alias로도 되살리지 않는다(패키지·테스트 전체에
  문자열 0건).

### 4.2.2 선언 창

```text
next_actual_bar.open_time <= signal_expiry   ->  체결 허용
```

`signal_at = S + 1`, `ttl = 2`이므로 `signal_expiry = S + 3`이고, 선언 창은 `S+1`, `S+2`, `S+3`에
열리는 다음 실제 봉을 허용한다.

예시: 신호 봉 10:00 → `signal_at` 10:01 → `expiry` 10:03. 다음 실제 봉이 10:01이나 10:02면 체결,
10:04면 체결 없음.

### 4.2.3 구현 정합 (해소 완료, 2026-09-21)

선언 창과 구현이 어긋나 있던 것을 **구현을 고쳐 해소했다.** 계약의 의미는 바뀌지 않았다.

**이전 동작**: 엔진이 도달하는 봉은 `S+1` 하나뿐이었다. 두 메커니즘이 겹친 결과다.

| 메커니즘 | 효과 |
| --- | --- |
| 엔진은 **직전 tick에 이미 signalled**였던 후보에만 계좌에 묻는다(F0 8.3) | 첫 체결 시도가 `S+2` |
| `fsm._entry_signalled`가 `_elapsed >= ttl`에서 만료 | 체결 시도 tick이 하나뿐 |

**수정**: `fsm._entry_signalled`의 만료 조건만 바꿨다.

```text
before:  _elapsed(signal_at, tick) >= signal_ttl_minutes
after:   _elapsed(signal_at, tick) >  signal_ttl_minutes + AVAILABILITY_DELAY
```

**왜 단순히 `>`가 아닌가.** 선언 창의 경계(`signal_at + ttl`)에 **열리는** 봉은 그 1분 뒤에야
관측된다. 그 한 tick을 주지 않으면 선언 창의 마지막 1분은 영원히 체결될 수 없다. 즉 추가된
1분은 창을 넓히는 것이 아니라 **경계 봉을 볼 수 있게 하는 것**이고, 봉 기준 창 폭은 정확히
`signal_ttl_minutes`로 유지된다.

**왜 다른 두 TTL은 `>=`인가.** 서로 다른 것을 재기 때문이다.

| TTL | 무엇을 제한하나 | 경계 |
| --- | --- | --- |
| `candidate_ttl_minutes` | 우리가 셋업을 **찾는** 시간 | `>= ttl` (tick 기준) |
| `setup_ttl_minutes` | 우리가 트리거를 **기다리는** 시간 | `>= ttl` (tick 기준) |
| `signal_ttl_minutes` | **주문이 시장에 살아 있는** 창 | 봉 open 기준, 관측 지연 1분 허용 |

앞의 둘은 우리의 판단 시간이고, 마지막은 주문의 유효 시간이다.

**수정 후 실측**(신호 봉 10:00, `signal_at` 10:01, `expiry` 10:03):

```text
체결 시도 tick: 10:02, 10:03, 10:04

다음 실제 봉 10:01 -> 체결 (tick 10:02)
다음 실제 봉 10:02 -> 체결 (tick 10:03)
다음 실제 봉 10:03 -> 체결 (tick 10:04)   <- 경계, 포함
다음 실제 봉 10:04 -> 체결 없음
```

조용한 tape는 여전히 창을 늘리지 못한다(`_elapsed`가 wall-clock이므로 봉이 없어도 시간은 간다).

**전략 규칙은 하나도 바뀌지 않았다.** scanner 임계, score 가중치, 셋업 파라미터, 리스크 비율,
비용, 자본, lift 정의, 진입 세션 모두 그대로이고 `signal_ttl_minutes`도 여전히 2다.

**F0 8.3 문구와의 잔여 불일치**: F0는 "signal TTL 2분은 엔진에게 **두 tick**을 준다"고 적었는데
이제 세 tick이다(그중 하나는 경계 봉 관측 전용). **규칙 JSON에는 tick 수가 없으므로 F0 canonical
체크섬은 그대로다.** 어긋난 것은 F0 문서의 산문 한 문장이며, F0는 동결된 선언이라 임의로 고치지
않고 사용자 판단에 남긴다.

검증: `test_the_declared_fill_window_is_what_the_engine_actually_reaches`,
`test_the_window_tracks_the_ttl_with_no_off_by_one`(ttl 1/2/3/5 파라미터화)

### 4.3 체결 실패는 새 규칙이 아니다

체결할 봉이 없으면 엔진은 **아무 답도 하지 않고** 후보를 그대로 돌려준다. 그러면 FSM이 자기
`signal_ttl_minutes`로 후보를 끝낸다.

```text
state       = EXPIRED
drop_reason = SIGNAL_TTL      # 이미 존재하는 값
```

계좌 거절(`FillOutcome(filled=False)`)로 기록하지 **않는다.** 계좌 거절 사유는
`SIZE_ZERO`, `MAX_POSITIONS`, `DAILY_LOSS_LIMIT` 셋뿐이며(`ENGINE_DROP_REASONS`), 체결할 봉이
없는 것은 계좌의 거절이 아니다.

이 동작은 이미 구현돼 있다(`engine._next_available_bar`, `costs.signal_fill`, 그리고
`if raw is None: return candidate`). **이 계약은 기존 동작을 문서화할 뿐 코드 경로를 바꾸지
않는다.** 새 상태도, 새 사유도, 새 enum도 만들지 않는다.

### 4.4 등록하는 경계 사례 (전부 일치)

| 상황 | 선언 | 실측 | 일치 |
| --- | --- | --- | --- |
| 다음 실제 봉 `S+1` | 체결 | 체결 | O |
| 공백 1분 뒤 `S+2` | 체결 | 체결 | O |
| 정확히 `expiry`(`S+3`) | 체결(경계 포함) | 체결 | O |
| `expiry` 이후(`S+4`) | 체결 없음 | 체결 없음 | O |
| 다음 세션 | 체결 없음 | 체결 없음(tape가 하루치라 구조상 불가) | O |
| 중간 synthetic 봉 | 무시 | 구조상 성립(`SessionTape`가 거부) | O |
| 신호 봉이 세션 마지막 실제 봉 | 체결 없음 | 체결 없음 | O |
| 창 내내 무거래 | 체결 없음(wall-clock) | 체결 없음 | O |

세션 경계 사유: `DropReason`에 `SESSION_END`는 **없고 새로 만들지 않았다.** 기록되는 사유는
`SIGNAL_TTL`이며, tape가 하루치여서 다음 세션 봉에 체결되는 경로 자체가 존재하지 않는다.

### 4.5 사전등록 실행 5개

| 라벨 | 체결 | 비용 수준 | 판정 입력 | 묻는 것 |
| --- | --- | --- | --- | --- |
| `BASE` | `NEXT_BAR_OPEN` | `BASE` | **예** | B의 성과 |
| `CF_SIGNAL_BAR` | `SIGNAL_BAR` | `BASE` | 아니오 | stop 주문 기능의 가치 |
| `STRESS_30` | `NEXT_BAR_OPEN` | `STRESS_30` | 아니오 | 편도 30bps에서 버티나 |
| `STRESS_50` | `NEXT_BAR_OPEN` | `STRESS_50` | 아니오 | 우위가 사라지는 지점 |
| `DIAG_ZERO_COST` | `NEXT_BAR_OPEN` | `ZERO_COST` | 아니오 | 비용 전에도 음수면 실패한 것은 비용 가정이 아니다 |

규칙·데이터·유니버스는 다섯 실행이 모두 같다.

### 4.6 실행 원장

- 판정에 쓰는 authoritative 실행은 **1회**(`BASE`)다.
- 기본 모드는 `STRICT`다. `--allow-partial`, `--ignore-checksum`, `--skip-missing`, `--force`
  같은 옵션으로 authoritative 결과를 만들 수 없다. 연구용은 `--preflight-only`만 둔다.
- 재실행 허용: 엔진·실행 코드 결함을 고쳤을 때, 데이터셋을 정정했을 때.
- 재실행 불가: 결과가 마음에 들지 않을 때, 결과가 시사하는 파라미터를 넣고 싶을 때.
- 폐기분을 포함한 **모든 실행**을 `B_E0_RESULTS_V1.md` 원장에 남긴다. 진단 실행은 `DIAGNOSTIC`
  으로 표시하고 판정 근거로 인용하지 않는다.

## 5. 결정 4: 표본과 판정

주 지표는 **거래당 평균 net R**(비용 차감 후, 종료된 거래만)이다. AGENTS 7절이 "비용 차감 후
Net R을 공식 성과로 사용"이라고 이미 정해 두었다.

### 5.1 표본 게이트

| 조건 | 값 |
| --- | --- |
| 종료 거래 수 | >= 30 |
| 거래가 발생한 세션 수 | >= 20 |

이 값은 **평가 가능한 최소 표본 기준(`MINIMUM_EVALUABLE_SAMPLE`)이지 성과 목표가 아니다.**
언제부터 판정을 계산해도 되는지를 말할 뿐, 좋은 결과가 무엇인지는 말하지 않는다.

세션 조건을 둔 이유: 30건이 며칠에 몰렸다면 그것은 표본이 아니라 한 번의 국면이다.

### 5.2 통계

```text
도구       = app.backtest.strategy_c_e0.stats.block_indices / percentiles
재표집 단위 = 거래 세션
block      = 1
replicates = 10,000
seed       = 20260921
CI         = 95% 양측 퍼센타일 (BCa 아님)
```

- C-E0가 쓰던 이동 블록 부트스트랩과 퍼센타일 구간을 그대로 재사용한다. **새 의존성을 추가하지
  않는다.**
- `block = 1`이면 `block_indices`는 세션의 독립 복원추출이 된다. C-E0는 490일에 block 10을
  썼지만, 이 창은 84세션이라 10일 블록이면 유효 블록이 8개뿐이라 구간이 너무 거칠어진다.
  B의 군집은 하루 안(같은 날 움직임을 공유하며 최대 3개가 동시에 열린다)에 있고, 세션 블록이
  붙잡는 것이 바로 그것이다.
- 빈 재표집은 NaN으로 나오며 `np.nanpercentile`이 그대로 처리한다(기존 `percentiles` 동작).

### 5.3 판정 (순서대로, 먼저 맞는 것이 이긴다)

| 순위 | 판정 | 조건 |
| --- | --- | --- |
| 1 | `DATASET_NOT_READY` | 데이터 준비 게이트 미통과 |
| 2 | `ERROR` | 실행 미완료, 또는 산출물 자체 검증 실패 |
| 3 | `INSUFFICIENT_SAMPLE` | 거래 < 30 또는 거래 세션 < 20 |
| 4 | `FAIL` | 평균 net R의 95% CI **상한 <= 0** |
| 5 | `PASS` | 평균 net R >= 0.10 **그리고** CI 하한 > 0 **그리고** lift = `LIFT_PASS` |
| 6 | `BORDERLINE` | 위 어디에도 안 걸리는 나머지 |

순서대로 평가하므로 여섯 상태는 서로 배타적이고 빠짐이 없다. `BORDERLINE`은 "표본은 됐고,
유의하게 나쁘지도 않으며, PASS 조건을 전부 채우지도 못한" 구간이다. 숫자 기준이 위 표에
전부 들어 있으므로 결과를 본 뒤 정의할 여지가 없다.

`min_mean_net_r = 0.10`의 근거: 1R은 자본의 0.5%다. 거래당 0.10R은 크지 않은 우위지만, 이
연구가 **측정하지 못한 비용**(스프레드, 체결 대기열, 부분체결)이 들어갈 자리를 남긴다. 기준을
0으로 두면 측정 못 한 비용만큼 자동으로 틀린다.

### 5.4 판정별 후속 (미리 정한다)

| 판정 | 허용되는 다음 행동 |
| --- | --- |
| `PASS` | 실시간 어댑터로 진행. 가는 길에 규칙을 다시 튜닝하지 않는다 |
| `FAIL` | B-V1 종결. 후속은 `b_fsm_rules_v2.json`과 새 선언으로만 |
| `BORDERLINE` | V1을 건드리는 문제에서는 `FAIL`과 같이 취급한다. 같은 규칙으로 기간을 늘리거나 B를 종결한다 |
| `INSUFFICIENT_SAMPLE` | 같은 규칙으로 기간을 늘리거나(계획된 유료 5~10년 데이터) B를 종결한다. 거래를 만들어 내려고 임계값을 낮추는 것은 선택지가 아니다 |
| `DATASET_NOT_READY` | 데이터를 고치고 다시 실행. 부분 데이터에서 판정을 계산하지 않는다 |
| `ERROR` | 판정 없음. 실패한 실행을 원장에 기록 |

## 6. Lift 게이트 (반증 대상)

F0 2절이 정한 반증 형태를 완전히 결정적인 비교로 옮긴다.

> 같은 scanner gate를 통과했지만 셋업이 성립하지 않은 종목과 비교해 기대값 우위가 없으면
> B-V1은 기각이다.

### 6.1 두 군

| 항목 | 처리군 | 대조군 |
| --- | --- | --- |
| 모집단 | `ENTRY_SIGNALLED`에 한 번이라도 도달한 **모든** 후보 (체결 여부 무관) | `QUALIFIED` 도달 후 `SETUP_READY`에 한 번도 못 가고 `EXPIRED` / `CANDIDATE_TTL`로 끝난 후보 |
| 기준 분 | `ENTRY_SIGNALLED`가 처음 기록된 tick | `EXPIRED`가 기록된 tick |
| 단위 | 후보 1개 = 앵커 1개 | 후보 1개 = 앵커 1개 |

처리군을 체결된 거래가 아니라 **신호 전체**로 잡는 이유: 체결은 계좌 한도(동시 3개, 심볼당 1회)로
걸러진다. 그것은 지갑에 관한 사실이지 셋업에 관한 사실이 아니다.

### 6.2 대조군에서 제외하는 것

| 제외 대상 | 이유 |
| --- | --- |
| `REJECTED` / `INELIGIBLE` | halt 추정, `VERY_SPARSE`·`UNKNOWN`, 기업행위, scope 탈락. 다른 모집단이다 |
| `EXPIRED` / `SCORE_BELOW_THRESHOLD` | 애초에 `QUALIFIED`가 아니다 |
| `EXPIRED` / `SETUP_TTL` | 셋업이 성립했다. 무셋업 대조가 아니다 |
| `EXPIRED` / `SIGNAL_TTL` | 신호가 났다. 처리군 소속이다 |
| `CANCELLED` / `SETUP_INVALIDATED`, `PRICE_DRIFT` | 셋업이 성립했다 |
| `SIZE_ZERO`, `MAX_POSITIONS`, `DAILY_LOSS_LIMIT` | 계좌 용량 문제이지 종목에 대한 진술이 아니다 |
| 세션이 데이터셋에 없는 후보 | 측정 불가 |

대조군은 "같은 모집단에서 셋업만 빠진 것"이어야 한다. 위 제외는 전부 셋업이 성립했거나 가설과
무관한 이유로 빠진 후보를 걷어낸다.

### 6.3 측정

```text
horizon_minutes = 30                                     # exit.time_stop_minutes와 동일
horizon_end     = min(기준분 + 30분, 그날 EOD 청산 시각)
                  # EOD = min(15:55 ET, 정규 마감 - eod_min_margin_minutes)

reference_price = 기준 분 이전 마지막 실제 bar의 종가
endpoint_price  = horizon_end 이전 마지막 실제 bar의 종가

forward_return  = endpoint_price / reference_price - 1   # 퍼센트포인트로 표기
```

horizon을 30분으로 잡은 이유는 B가 실제로 포지션을 들고 있는 창(`time_stop_minutes`)이기
때문이다.

**sparse tape 처리:** `기준 + 30분`에 정확히 bar가 있을 필요가 없다. 두 가격 모두 "그 시각
이전 마지막 실제 bar의 종가"다. 이것은 F0 9.1이 time stop과 EOD 체결가에 대해 이미 고정한
"데드라인 이전 마지막 종가" 규약과 같은 것이다. 새 규약을 만들지 않았다.

**결측 처리:**

| 상황 | 처리 |
| --- | --- |
| 기준 분 이전에 실제 bar가 없음 | 앵커 자체가 불가능. 두 군 모두에서 제외하고 `DROPPED_NO_REFERENCE_BAR`로 집계 |
| 기준 bar는 있으나 이후 `horizon_end`까지 bar가 없음 | endpoint = 기준 bar, `forward_return = 0.0`. `ZERO_BY_NO_FORWARD_BAR`로 **따로** 보고 |

두 번째를 따로 세는 이유: 어느 한 군이 대부분 이것으로 채워졌다면 그것은 결과가 아니라 조용한
tape이고, 그 사실이 비교를 무효로 만들 수 있다.

synthetic bar는 두 가격 어디에도 쓰지 않는다. 전방 수익률은 가격 비율이므로 수수료·슬리피지가
붙지 않는다. 거래 가능한 성과가 아니라 **움직임**을 재는 값이다.

**전방 수익률은 분석 전용이다.** 결정 이후의 bar를 읽으므로 어떤 판정 입력도 될 수 없다.
FSM·scanner·사이징으로 되돌아가는 경로는 없어야 한다(기존 `session_audit`의 AUDIT ONLY와 같은
지위다).

### 6.4 비교와 판정

```text
통계량 = 평균(처리군 forward_return) - 평균(대조군 forward_return)
집계   = 세션별로 각 군의 합과 개수를 만들고,
         부트스트랩 draw 안에서 두 평균의 차이를 다시 계산한다
         (기존 stats.difference와 같은 방식. 독립적으로 뽑은 두 구간을 빼지 않는다)
재표집 = 5.2와 동일한 세션 블록·replicates·seed·퍼센타일
```

| 순위 | 결과 | 조건 |
| --- | --- | --- |
| 1 | `LIFT_INSUFFICIENT` | 처리군 앵커 < 30 또는 대조군 앵커 < 30 |
| 2 | `LIFT_PASS` | 차이의 95% CI 하한 > 0 |
| 3 | `LIFT_FAIL` | 그 외 |

`LIFT_PASS`만 `PASS`를 허용한다. `LIFT_FAIL`과 `LIFT_INSUFFICIENT`는 판정을 `BORDERLINE` 이하로
묶는다.

### 6.5 게이트가 아닌 강건성 점검

두 군의 score 분포를 보고하고, 같은 부트스트랩으로 score 매칭 비교를 함께 돌린다. 원시 차이와
매칭 차이가 어긋나면 **한계로 기록한다.** 마음에 드는 쪽을 고르는 데 쓰지 않는다. 게이트는
위에 선언한 원시 차이다.

## 7. 유니버스

### 7.1 Source of Truth는 아티팩트다

`universe.source_of_truth = IMMUTABLE_ARTIFACT`

**러너는 불변 아티팩트에서 심볼 집합을 읽고 모든 개수를 거기서 계산한다.** 이 계약에도,
러너에도, 러너가 읽는 어떤 문서에도 심볼 개수를 하드코딩하지 않는다. 사람이 적은 숫자를
믿지 않는다.

run identity에 들어가는 것: `universe_artifact`, `universe_sha256`,
`universe_symbol_count`(아티팩트를 읽어 계산한 값).

아티팩트 요건:

| 항목 | 내용 |
| --- | --- |
| 내용 | 실행 창의 정렬된 심볼 집합 + **심볼별 scope 세션 목록** |
| 구성 | scope 구간의 D에 대한 S(D)의 합집합. 각 S(D)는 **D 이전 정보만**으로 결정된다(D보다 이른 CS 스냅샷 + D-1까지의 일봉). 근거는 `b_universe.scope_membership`이 "rows < i, snapshots dated < D"만 읽는다는 것이다 |
| 불변성 | authoritative 실행이 참조한 아티팩트는 다시 편집하지 않는다. 정정은 새 sha256을 가진 새 아티팩트다 |

### 7.1.1 집합은 superset이다 (중요)

합집합은 **집합으로서는 미래를 본다.** 창 후반에 처음 scope에 들어온 심볼도 아티팩트의
구성원이다. 수집 계획기가 쓰는 구성과 같으며, 의도된 것이다.

**PIT 안전성은 집합이 아니라 세션별 재필터에서 나온다.**

```text
심볼이 세션 D에 거래 가능 <=> D가 그 심볼의 scope 세션 목록에 있다
                              (그 목록은 D-1 정보로 계산됐다)
```

- 러너는 (심볼, 세션)마다 `SymbolSession.scope`를 아티팩트의 세션별 멤버십에서 채운다.
  그러면 F0 6.1이 `ScopeDecision.eligible`이 아닌 후보를 `REJECTED`로 떨어뜨린다.
- **합집합을 통째로 모든 세션에 먹이면 생존편향 선택이 된다.** 준비 게이트가 이것을 검사한다:
  어떤 세션도 그 세션 멤버십에 없는 심볼을 받지 않아야 한다.
- 즉 **아티팩트에 있다는 것은 적격이라는 뜻이 아니다.** 그 세션 목록에 있다는 것이 적격이다.

### 7.1.2 이 아티팩트는 아직 없다

디스크에 4개월 심볼 집합이나 심볼별 범위를 나열한 파일은 **존재하지 않는다.**
`historical_v2._save`가 쓰기 전에 `plan["plans"]`를 버리기 때문에 요청 목록은 프로세스 메모리에만
있고, `b_fetch_universe_q1.json`은 2년짜리 문서다.

- 심볼 집합 자체는 결정적으로 재현 가능하다: `b_fetch_universe_q1.json`(digest `33c194f3...`)에서
  마지막 scope 세션이 scope 시작일 이후인 심볼들이다.
- **세션별 멤버십은 그 문서로 복원되지 않는다.** `b_universe`로 멤버십 행렬을 다시 만들어야 하며,
  이는 grouped daily를 읽는다.
- 따라서 이 아티팩트를 만들고 체크섬을 박는 것이 authoritative 실행의 선결 조건이고,
  없으면 `DATASET_NOT_READY`다.

### 7.2 이 아티팩트가 아닌 것

| 대상 | 왜 아닌가 |
| --- | --- |
| `b_fetch_universe_q1.json` (`B_FETCH_UNIVERSE_Q1`) | 2년 격자(2024-09-17~2026-09-16)의 **수집 계획** 유니버스다. 실행 유니버스가 아니며 E0 실행이 이것을 읽어서는 안 된다 |
| `MINUTE_UNIVERSE_V1` (A의 29종목) | 전 기간 data-eligibility로 고른 집합이라 B에는 룩어헤드다 |

### 7.3 3,883 vs 3,855

두 숫자는 **다른 것을 센다. 불일치가 아니다.**

| 숫자 | 뜻 | 근거 |
| --- | --- | --- |
| 3,883 | 창 안에 scope 세션이 하나라도 있는 **심볼** 수 | `minute_window_ranges` |
| 3,855 | 심볼 범위 안에서 아직 비어 있는 **연속 구간마다 한 건**인 요청 수 | `raw_fetch.plan_requests` |

요청은 심볼이 아니다. 완전히 채워진 심볼은 요청이 **0건**이고, 커버리지 중간에 구멍이 있는
심볼은 **2건 이상**이다.

```text
3,883 - 29 + 1 = 3,855
```

- **-29**: A의 research universe V2 29종목이 legacy 수집기 manifest와 2026-09-16 Common Raw
  원장으로 이미 완전히 덮여 있어 요청이 0건이다.
- **+1**: `SPCX`는 범위 중간에 구멍이 있다(legacy가 2026-08-06~2026-09-03만 덮음). 앞뒤 두
  구간으로 쪼개져 1건이 늘어난다.
- 검산: `29 x 104 + 21 = 3,037 = existing_symbol_sessions`. plan.json의 자체 수치와 정확히
  맞으므로, 창 안에 커버리지를 가진 다른 심볼은 없다는 것까지 증명된다.

이 숫자들은 2026-09-18 스냅샷이며 **계약에 정보용으로만 기록**한다. 어떤 코드도 읽지 않고
어떤 판정도 여기에 기대지 않는다. authoritative 실행은 7.1의 아티팩트에서만 개수를 얻는다.

**`CON`에 대한 정정:** `CON`은 계획 단계에서 걸러지지 않는다. 3,883에도 3,855에도 들어 있고,
fetch 시점에 `raw_fetch.reserved_path_name`이 건너뛴다. 즉 **계획에는 있고 저장소에 없다.**
실행 유니버스 아티팩트에 제외 사유와 함께 기록해, 개수가 조용히 모자란 것이 아니라 설명된
상태가 되게 한다.

## 8. 데이터 준비 게이트

authoritative 실행 전에 다음을 전부 만족해야 한다. 하나라도 미달이면 `DATASET_NOT_READY`로
**실행을 거부한다.** authoritative 모드에서 부분 실행은 금지다.

1. **이 계약이 다시 적은 모든 값(`signal_ttl_minutes`, 사이징 비율·한도, lift horizon)이
   실제 실행할 `StrategyBConfig`와 일치한다.** 러너는 이 숫자들의 사본을 갖지 않는다
2. 유니버스 아티팩트가 존재하고 파싱되며, sha256이 실행에 기록된 값과 일치
3. **모든 세션이 그 세션의 scope 멤버십에 있는 심볼만 받는다**(합집합이 통째로 들어가지 않는다)
4. 유니버스가 요구하는 모든 (심볼, 세션)이 데이터셋에 존재
5. 심볼별 RVOL 워밍업 세션이 전부 존재
6. 유니버스의 모든 심볼에 대한 split 레코드 존재
7. scope와 기업행위 플래그가 의존하는 일봉·grouped daily 존재
8. 데이터셋 schema 버전과 수집기 format이 계약이 기록한 값과 일치
9. 참조된 모든 원본 파일의 checksum이 원장 항목과 일치
10. 규칙 파일이 선언된 canonical checksum으로 해시됨
11. 이 계약 파일이 선언된 canonical checksum으로 해시됨

preflight는 실패한 검사와 최초 위반 항목들을 담은 기계가독 보고서를 쓴다. 실패가 "뭔가 없다"가
아니라 **무엇을 고쳐야 하는지**를 말하게 한다.

게이트를 통과하더라도 `app.dev.validate_historical_v2 minute --tier drive`의 완결률과 미수집
심볼 목록을 보고서에 싣고 결과와 함께 보고한다.

## 9. Run identity와 산출물

### 9.1 identity

들어가는 것: `strategy_id`, `strategy_version`, `contract_id`,
`contract_canonical_checksum`, `rules_canonical_checksum`, `initial_capital_usd`, `fx_model`,
`universe_artifact`, `universe_sha256`, `universe_symbol_count`, `dataset_identity`,
`dataset_digest`, `scope_start`, `scope_end`, `fill_scenario`, `cost_level`,
`commission_bps_per_side`, `execution_cost_bps_per_side`, `config_fingerprint`,
`engine_code_digest`, `bootstrap_seed`, `bootstrap_replicates`, `bootstrap_block_length`,
`run_mode`.

들어가지 않는 것: 실행 시각, 머신·사용자, git 작업트리 상태(provenance로 따로 기록).

비용과 체결 시나리오가 identity에 들어가므로, 비용이 다른 두 실행은 구조적으로 다른 run이 되고
실수로 비교될 수 없다.

### 9.2 재현성

같은 source commit, 계약 checksum, 유니버스 checksum, 데이터셋 digest, seed로 실행하면
**바이트 동일한 산출물**이 나와야 한다. 실행 시각·run id 같은 비결정 값은 digest 대상에서
빼고 provenance에만 쓴다.

### 9.3 산출물

`backtest/strategy_b/<run_id>/` 아래 append-only로 쓴다.

필수: `identity.json`, `run_manifest.json`, `trades.jsonl`, `candidates.jsonl`,
`sessions.jsonl`, `metrics.json`, `gate_result.json`
선택: `equity_curve.jsonl`, `daily_metrics.jsonl`, `lift.json`

`gate_result.json`의 상태는 `PASS`, `BORDERLINE`, `FAIL`, `INSUFFICIENT_SAMPLE`,
`DATASET_NOT_READY`, `ERROR`다. 5.3의 목록과 정확히 일치한다.

`candidates.jsonl`이 중요하다. 진입까지 못 간 후보의 탈락 사유 분포가 "규칙이 너무 빡빡한가"를
답하는 유일한 근거이고, 규칙을 고치자는 말을 꺼내기 전에 먼저 읽어야 하는 것이다.

## 10. 이 계약이 바꾸지 않는 것

전략 로직, 후보 FSM, `HOD_BREAKOUT` 탐지, 진입·청산 규칙, `StrategyBConfig`의 어떤 임계값,
그리고 Strategy A의 로직·baseline·UI·체결 의미.

**러너는 조율만 한다. 판정하지 않는다.** 러너가 판정처럼 보이는 일을 하고 있다면 그것은 결함이다.

## 11. 결과에 반드시 싣는 한계

1. `CON` 티커 부재. Windows 예약 장치명이라 저장소가 폴더를 만들지 못한다. 모멘텀과 무관한
   제약이지만 명시한다.
2. 스프레드 필터 없음, 측정 스프레드 없음. 모든 비용은 선언값이다.
3. `halt_inferred == UNKNOWN`은 적격성을 통과한다(F0 6.3). 실제로 정지였던 tape 위의 진입이
   섞여 있을 수 있다.
4. Massive 분봉 거래량은 같은 세션의 Kiwoom REST 분봉 대비 약 0.70배로 측정됐다. 여기서 맞춘
   RVOL 임계가 실시간에서 같은 뜻이라는 보장이 없다. 배포 전 재검증 대상이다.
5. 4개월 단일 구간, 단일 국면. 계절성·국면 일반화를 주장하지 않는다.
6. 프리마켓 가격이 두 소스 간 불일치한다. 진입은 정규장 한정이지만 `return_*`은
   `EXTENDED_DAY` scope라, 프리마켓 체결가가 정규장 수익률의 기준점이 될 수 있다.
7. 저장소에 없는 (심볼, 세션)은 유니버스에서 빠지며 그 목록을 결과에 싣는다.
8. A의 기존 baseline은 10,000달러에서 나온 것이라, A를 재실행하기 전까지 달러 비교는 불가하다.
9. 조기 마감일이 없는 창이라 EOD 클램프 규칙에 대한 증거를 이 실행은 제공하지 않는다.

## 12. 변경 정책

이 계약을 바꾸면 canonical checksum이 바뀌고 그 시점부터 다른 연구다. **결과를 본 뒤의 수정은
`b_e0_contract_v2.json`과 새 선언으로만 한다.** 결과를 보기 전의 수정(설계 오류 발견 등)은 같은
V1 안에서 허용되며, 사유와 이전 checksum을 `b_e0_contract_v1.sha256`에 남긴다(F0·D가 쓴 방식과
동일하다).

## 13. 이 문서가 처음 정한 결정들

| # | 결정 | 근거 |
| --- | --- | --- |
| 1 | A/B/C/D 공통 초기자본 7,428.92 USD, 독립 계좌 | 전략 간 자본 우위를 두지 않는다. 값은 페이퍼 계좌 상수에서 읽었다 |
| 2 | A baseline이 10,000인 사실을 충돌로 기록 | 숨기면 나중에 달러 비교가 조용히 틀린다 |
| 3 | 자본 불변성 도출을 계약에 명시 | 자본 변경이 판정 설계를 흔들지 않음을 보여야 변경이 안전하다 |
| 4 | 사이징 정본은 비율, 달러 값은 표시용 | 현재 equity 기반이라 달러 값은 첫 거래에서만 참이다 |
| 5 | 비용을 편도 all-in으로 표기(25/30/50) | 왕복·편도 혼동이 결론을 바꿀 수 있다 |
| 6 | 민감도에서 수수료 고정, 체결비용만 이동 | 모르는 값은 스프레드이지 수수료가 아니다 |
| 7 | 주 결과 = `NEXT_BAR_OPEN` | 보수성이 아니라 실시간 경로와의 일치. 런타임에 stop 주문 기능이 없다 |
| 8 | `SIGNAL_BAR`는 counterfactual로 재해석 | 같은 숫자를 다른 질문에 쓴다 |
| 9 | 체결 실패 = `EXPIRED`/`SIGNAL_TTL`, 계좌 거절 아님 | 기존 enum으로 충분하다. 계좌 거절 사유를 오염시키지 않는다 |
| 10 | 세션 블록 부트스트랩, block = 1 | 같은 날 거래는 독립이 아니다. 84세션에서 10일 블록은 너무 거칠다 |
| 11 | 판정 문턱 0.10R | 0으로 두면 측정 못 한 비용만큼 자동으로 틀린다 |
| 12 | 표본 게이트에 세션 수 조건 | 30건이 며칠에 몰린 것은 표본이 아니다 |
| 13 | 판정을 순위 목록으로 정의 | 배타적·빠짐없음을 구조로 보장하고 `BORDERLINE`을 사후 정의 불가로 만든다 |
| 14 | lift 처리군 = 체결이 아니라 신호 전체 | 계좌 한도는 셋업에 관한 사실이 아니다 |
| 15 | lift는 손익이 아니라 전방 수익률로 | 대조군에 1R이 없다. 없는 규칙을 지어내지 않는다 |
| 16 | 전방 수익률 결측을 두 종류로 분리 집계 | 조용한 tape가 0%로 위장하는 것을 막는다 |
| 17 | 유니버스 Source of Truth = 불변 아티팩트 | 사람이 적은 심볼 수를 믿지 않는다 |
| 18 | 데이터 준비 게이트, 부분 실행 금지 | authoritative 결과는 부분 입력에서 나올 수 없다 |
| 19 | 판정별 후속을 미리 고정 | 결과를 본 뒤에는 어떤 후속도 합리화된다 |
| 20 | 실행 원장 의무화 | 폐기된 실행이 보이지 않으면 골라 쓴 것과 구분되지 않는다 |
