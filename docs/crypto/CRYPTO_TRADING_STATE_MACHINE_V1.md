# US-B CRYPTO 운용 상태기계 V1

작성 2026-09-23. **구현하지 않는다. 상태와 전이 규칙만 고정한다.**

주식 쪽 `app/monitoring/kill_switch.py`는 재사용하지 않는다(`MinuteBar`, `Currency.USD`,
`RiskEngine`, `StrategyDecision`에 결합). 그러나 패턴 4개는 계승한다: 확인 게이트, **먼저 halt
그 다음 청산**, 실패 심볼을 삼키지 않고 보고, 결과를 구조체로 반환.

---

## 1. 상태

| 상태 | 신규 진입 | 기존 포지션 | 설명 |
|---|---|---|---|
| `MANUAL` | 사람만 | 사람이 관리 | 자동 진입이 없다. 사람의 지시로만 주문이 생긴다 |
| `AUTO` | 전략이 | 전략의 exit rule | 전략이 진입과 청산을 모두 관리한다 |
| `AUTO_STOPPING` | **차단** | **기존 exit rule 유지** | AUTO를 끈 직후의 배수 상태. 신규만 막고 보유분은 원래 규칙으로 끝낸다 |
| `EMERGENCY` | **차단** | **즉시 종료 처리** | 사고 상태. 보유분을 즉시 종료하고 신규를 막는다 |

**`AUTO_STOPPING`이 이 상태기계의 핵심이다.** AUTO를 끄는 것과 포지션을 닫는 것은 다른 사건이며,
이 둘을 한 동작으로 묶으면 전략이 계획한 exit를 강제로 깨뜨린다.

---

## 2. 전이 규칙

### 2.1 AUTO OFF (정상 정지)

```
AUTO  --(사용자 AUTO OFF)-->  AUTO_STOPPING
                                  |
                                  | 신규진입 차단
                                  | 보유 포지션은 기존 exit rule로 계속 관리
                                  v
                              FLAT 도달
                                  |
                                  v
                               MANUAL
```

| # | 규칙 |
|---|---|
| N1 | AUTO OFF는 즉시 `AUTO_STOPPING`으로 간다 |
| N2 | `AUTO_STOPPING`에서 **신규 진입은 차단된다** |
| N3 | `AUTO_STOPPING`에서 **보유 포지션은 기존 exit rule로 관리된다.** 강제 청산하지 않는다 |
| N4 | 포지션이 `FLAT`이 되면 자동으로 `MANUAL`로 전이한다 |
| N5 | `AUTO_STOPPING`에서 다시 AUTO ON을 하면 `AUTO`로 복귀한다 (진입 차단 해제) |
| N6 | `AUTO_STOPPING`은 **시간 제한이 없다.** exit rule이 만족될 때까지 유지된다 |
| N7 | 포지션이 없는 상태에서 AUTO OFF를 하면 `AUTO_STOPPING`을 지나 즉시 `MANUAL`이 된다 |

### 2.2 EMERGENCY

```
MANUAL | AUTO | AUTO_STOPPING  --(EMERGENCY)-->  EMERGENCY
                                                     |
                                                     | 1. 신규진입 차단 (먼저)
                                                     | 2. 보유 포지션 즉시 종료 처리
                                                     v
                                                 EMERGENCY 유지
                                                 (사람의 명시적 해제까지)
```

| # | 규칙 |
|---|---|
| E1 | **어느 상태에서든** EMERGENCY로 갈 수 있다 |
| E2 | **차단이 청산보다 먼저다.** 청산 중에 새 진입이 들어오는 경합을 없앤다 (주식 kill switch 패턴 계승) |
| E3 | 보유 포지션은 **즉시 종료 처리**한다. exit rule을 기다리지 않는다 |
| E4 | 종료에 실패한 포지션은 **삼키지 않고 보고한다.** `failed_positions`로 결과에 담는다 |
| E5 | EMERGENCY는 **자동 해제되지 않는다.** 사람의 명시적 지시로만 나간다 |
| E6 | EMERGENCY 해제 시 도착 상태는 **`MANUAL`이다.** AUTO로 직접 복귀하지 않는다 |
| E7 | EMERGENCY 진입 사유를 원장에 기록한다 |
| E8 | 확인 게이트를 둔다. 사람이 트리거하는 EMERGENCY는 확인을 요구할 수 있다 (주식 `kill_switch_requires_confirmation` 계승) |

### 2.3 전이 표

| from \ to | `MANUAL` | `AUTO` | `AUTO_STOPPING` | `EMERGENCY` |
|---|---|---|---|---|
| `MANUAL` | - | AUTO ON | 불가 | EMERGENCY |
| `AUTO` | 불가 (반드시 AUTO_STOPPING 경유) | - | AUTO OFF | EMERGENCY |
| `AUTO_STOPPING` | FLAT 도달 시 자동 | AUTO ON (복귀) | - | EMERGENCY |
| `EMERGENCY` | 사람의 명시적 해제 | **불가** | **불가** | - |

- **`AUTO` -> `MANUAL` 직접 전이는 없다.** 반드시 `AUTO_STOPPING`을 지난다. 포지션이 없어도 상태기계상
  경유한다(N7). 이렇게 두면 "AUTO를 껐는데 포지션이 남아 있다"는 상태가 표현 불가능해진다
- **`EMERGENCY` -> `AUTO` 직접 전이는 없다** (E6)

---

## 3. 불변식

| # | 불변식 |
|---|---|
| I1 | `AUTO_STOPPING`과 `EMERGENCY`에서 신규 진입 주문은 **하나도 생성되지 않는다** |
| I2 | `MANUAL`에서 전략이 자동으로 주문을 만들지 않는다 |
| I3 | 상태 전이는 전부 원장에 기록된다. 기록 없는 전이는 없다 |
| I4 | 현재 상태의 authority는 하나다. 여러 곳이 상태를 각자 들고 있지 않다 |
| I5 | 상태를 알 수 없으면 `UNKNOWN`으로 보고한다. 추측해서 채우지 않는다 (주식 authority 규칙 계승) |
| I6 | `EMERGENCY`에서 포지션이 남아 있는 것은 **정상 상태다** (종료 실패 가능). 남아 있음을 숨기지 않는다 |

---

## 4. 24/7 시장의 함의

주식 상태기계와 다른 점이다.

| # | 내용 |
|---|---|
| H1 | **장 마감이 없으므로 EOD 강제 청산 개념이 없다.** 주식 `end_of_day_lifecycle`에 대응하는 것이 crypto에 존재하지 않는다 |
| H2 | "당일 재진입 금지" 같은 규칙은 **"당일"이 정의되지 않으므로 그대로 쓸 수 없다.** 필요하면 UTC 일 기준으로 재정의하고 그 선택을 명시한다 |
| H3 | "overnight 최대 1종목" 규칙은 crypto에 의미가 없다. 항상 overnight이다 |
| H4 | **funding 정산 시각(UTC 00/08/16)이 유일한 주기적 사건이다.** 상태기계가 이 시각을 인지해야 한다 |
| H5 | `AUTO_STOPPING`이 funding 정산 시각을 넘어가면 funding이 계속 발생한다. 이것은 정상이며 비용으로 기록된다 |
| H6 | 운용 중단(프로세스 종료)과 `AUTO_STOPPING`은 다르다. 프로세스가 죽으면 exit rule이 동작하지 않는다. 메모리 기록: 세션 종료 시 nohup 수집기도 사망하고 lock이 잔존한다 |

---

## 5. 미해결

| # | 항목 | 상태 |
|---|---|---|
| Y1 | EMERGENCY의 "즉시 종료"를 PAPER에서 어떤 가격으로 체결할 것인가 (market taker 가정?) | **미정** |
| Y2 | 프로세스 재시작 시 상태 복원 규칙 | **미정** |
| Y3 | `AUTO_STOPPING` 중 exit rule이 영원히 만족되지 않는 경우의 처리 | **미정** |
| Y4 | EMERGENCY 트리거 조건 목록 (사람만인가, 자동 조건도 두는가) | **미정** |
| Y5 | 청산(liquidation)이 발생했을 때 상태기계가 어떻게 반응하는가 | **미정** |

Y5는 `CRYPTO_PAPER_EXECUTION_CONTRACT_V1.md` Q1(청산 시뮬레이션 필수)과 연결된다.
