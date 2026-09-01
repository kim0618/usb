# USB — V1 Final Specification

## 1. 목적

USB는 미국주식 단기 Catalyst Momentum 전략을 연구하고 모의매매하기 위한 개인용 Research & Trading System이다.

V1의 핵심 목적은 다음 두 가지다.

1. 실제 매매 시스템의 전체 라이프사이클을 구현하고 모의 환경에서 검증한다.
2. 매일 TOP 8 후보를 Shadow Trading하여 자체 연구 데이터셋을 축적한다.

V1은 수익률을 과도하게 최적화하는 단계가 아니다. 전략 가설을 검증하고, Live/Paper/Shadow 간 정합성을 확보하고, 향후 V2 판단에 사용할 데이터를 축적하는 것이 우선이다.

---

## 2. 핵심 운영 철학

USB의 의사결정 체계는 다음과 같다.

```text
Quant Bot
→ 숫자로 후보를 찾는다.

GPT
→ 회사/실적/뉴스/Catalyst/리스크를 분석한다.

Human
→ 최종 매매 승인 여부를 결정한다.

Market
→ Premarket / Opening에서 마지막 확인을 수행한다.

Trading Engine
→ 실제 진입, 손절, Trailing, 추가매수, Overnight, 청산을 수행한다.
```

중요 원칙:

- Human APPROVE는 즉시 BUY가 아니다.
- APPROVE는 해당 종목에 오늘 전략 실행을 허용한다는 의미다.
- 실제 진입은 Premarket 및 Opening 조건을 통과해야 한다.
- 실제 거래 대상은 하루 최대 0~2종목이다.
- TOP 8 전 종목은 Human 결정과 무관하게 Shadow Trading한다.
- 매매하지 않는 날도 정상이다.

---

## 3. 전체 라이프사이클

```text
미국장 종료
↓
Quant Scanner
↓
Candidate Pool
↓
TOP 8 선정
↓
GPT 분석용 프롬프트 생성
↓
GPT 웹검색 + TOP 8 재랭킹
↓
Human 0~2종목 승인
↓
Premarket Gate
↓
Opening Range 형성
↓
Entry Validation
↓
Risk / Position Sizing
↓
Paper / Shadow Entry
↓
Structure Stop
↓
ATR Trailing
↓
조건 충족 시 승자 추가매수
↓
장 마감 Overnight 평가
↓
조건부 Day 2
↓
최종 Exit
↓
모든 판단/성과/비용/장애 저장
```

---

## 4. Quant Scanner V0

### 기본 필터

V0 시작값:

- Price >= $5
- Market Cap >= $300M
- 20D Average Dollar Volume >= $20M

수치는 Shadow 데이터로 추후 검증한다.

### Quant Score V0

```text
RVOL                 35%
Relative Strength    30%
Dollar Volume        20%
20D Momentum         15%
```

Premarket Gap은 Quant Score에 포함하지 않는다. Premarket Gate 단계에서 별도로 사용한다.

스코어 구성 요소는 모두 저장한다.

### 출력

- Scanner Eligible Pool 전체 Snapshot 저장
- Quant Rank 전체 저장
- TOP 8 선정
- TOP 8은 반드시 deterministic하게 정렬
- 동점 처리 규칙도 deterministic하게 정의

---

## 5. GPT Research

### 입력

TOP 8 전체를 한 번에 하나의 프롬프트로 생성한다.

UI에 다음 버튼을 제공한다.

```text
[ TOP 8 GPT 분석 프롬프트 복사 ]
```

Stock Detail에서는 다음 버튼을 제공한다.

```text
[ GPT 심층분석 프롬프트 복사 ]
```

### GPT 분석 항목

각 종목마다 최소 다음을 조사한다.

- 회사가 무엇을 하는지
- 주요 사업 및 수익원
- 주요 고객
- 주요 공급사
- 주요 파트너
- 주요 경쟁사
- 연결 기업
- 연결 산업/테마
- 전일 상승의 직접적 Catalyst
- Catalyst의 지속성
- EPS
- Revenue
- Margin
- Guidance
- 최근 3~4개 분기 성장 추세
- 재무건전성
- 최근 뉴스
- 신규 악재
- 추가 Catalyst
- 과열 가능성
- 단기 Momentum 지속 가능성
- Stop 논리 후보
- Trailing Profile
- Overnight Suitability

### GPT 필수 규칙

- 반드시 최신 웹검색을 사용한다.
- 확인되지 않은 정보는 추측하지 않는다.
- 확인 불가 항목은 `UNKNOWN`으로 표시한다.
- 핵심 주장에는 출처를 붙인다.
- SEC / 공식 IR / 거래소 / 공식 발표를 최우선으로 한다.
- 사실과 해석을 구분한다.
- Quant Rank에 종속되지 않고 독립적으로 재평가한다.
- 최종적으로 GPT Rank 1~8을 다시 산출한다.

### GPT JSON 출력

최종 결과는 구조화된 JSON이어야 한다.

필수 필드 예시:

```json
{
  "ticker": "NVDA",
  "gpt_rank": 1,
  "overall_score": 91,
  "catalyst_score": 94,
  "fundamental_score": 86,
  "momentum_score": 90,
  "risk_score": 72,
  "catalyst_duration": "1_2_DAYS",
  "stop_profile": "NORMAL",
  "trailing_profile": "WIDE",
  "overnight_suitability": "HIGH",
  "unknown_fields": [],
  "sources": [
    {
      "claim": "guidance_raised",
      "url": "https://...",
      "type": "IR",
      "published_at": "..."
    }
  ]
}
```

함께 저장할 메타데이터:

- provider
- model
- prompt_version
- analysis_at

---

## 6. Evidence Confidence

GPT 자기신고 confidence를 그대로 사용하지 않는다.

봇이 Evidence Confidence를 기계적으로 산출한다.

평가 요소:

- 공식 IR 확인 여부
- SEC 확인 여부
- 공식 거래소/회사 발표 확인 여부
- 신뢰 금융언론 교차검증 여부
- Catalyst 출처 존재 여부
- EPS 확인 여부
- Guidance 확인 여부
- UNKNOWN 필드 수
- 출처 없는 주요 주장 수
- 출처 품질

핵심 Catalyst가 검증 불가능한 종목은 상위 등급을 제한한다.

---

## 7. Human Approval

Human은 GPT 결과와 Quant 데이터를 확인하고 최종 0~2개를 승인한다.

상태:

- APPROVE
- REJECT

승인 이유를 선택적으로 기록할 수 있다.

`APPROVE != BUY`

승인은 해당 종목에 당일 Strategy 실행을 허용하는 행위다.

---

## 8. Premarket Gate V0

초기 시작값:

- Gap: +2% ~ +15%
- Premarket Volume: 데이터 프로바이더 기준 Threshold 이상
- 신규 악재 없음
- Spread가 허용 가능한 수준

정확한 Volume Threshold는 실제 데이터 소스 확인 후 확정한다.

조건 미충족 시:

```text
NO TRADE
```

---

## 9. Opening Entry V0

모든 시간 계산 기준은 `America/New_York`이다.

```text
09:30~09:45 ET
Opening Range 생성
```

09:45 이후:

```text
Price > VWAP
AND
Price > 15m Opening Range High
```

동시 충족 시 Entry 후보가 된다.

Entry Deadline 기본값:

```text
10:30 ET
```

Deadline까지 진입 조건이 없으면 해당 종목은 `NO TRADE`.

---

## 10. 재진입

V1에서는:

```text
1 symbol / 1 trading day / 1 entry attempt
```

손절 또는 청산 후 같은 날 동일 종목 재진입은 금지한다.

재진입 전략은 V2 Backlog다.

---

## 11. Risk Unit (1R)

V1 시작값:

```text
1R = Account Equity × 0.5%
```

예:

```text
Account Equity = 1,000,000 KRW
1R = 5,000 KRW
```

Live 1,000,000 KRW 단계는 전략 성능 검증이 아닌 운영 검증용이다.

---

## 12. Position Sizing

순서:

```text
1. Structure Stop 계산
2. Entry와 Stop Distance 계산
3. Position Size = 1R / Stop Distance
4. Exposure Cap 적용
5. 주문 가능 수량 검증
6. 최종 Order Intent 생성
```

자금배정이 아니라 Risk가 포지션 크기를 결정한다.

---

## 13. Capital Allocation

V1 기본 구조:

```text
Base Capacity        최대 80%
Pyramiding Reserve   최대 20%
```

80%는 의무 투자금이 아니라 최대 운용 가능 상한이다.

좋은 조건이 없으면 100% Cash도 정상이다.

하루 실제 신규 종목 최대:

```text
2
```

종목별 총 노출 상한은 Risk Engine과 계좌 조건에 의해 최종 결정한다.

---

## 14. Pyramiding

손실 중 추가매수는 금지한다.

```text
Averaging Down = Forbidden
```

승자 추가매수만 허용한다.

기본 조건:

- 현재 수익 중
- Momentum 유지
- VWAP 유지
- 새로운 가격 Confirmation
- 기존 Risk 감소
- Risk Budget 여유

V1에서는 추가매수 최대 1회.

추가매수 재원은 최대 20% Reserve.

---

## 15. Stop Strategy

고정 -2%, -3% 같은 Stop을 기본 규칙으로 사용하지 않는다.

GPT는 Stop의 성격과 논리를 제안한다.

예:

- Premarket Low
- Opening Range Low
- VWAP Failure
- ATR 기반 구조
- Previous Day Structure

실제 Stop Price는 장중 Market Data를 기반으로 Trading Engine이 계산한다.

---

## 16. Trailing Strategy V0

V1은 단순한 ATR 기반 Profile로 시작한다.

초기 예시:

```text
TIGHT   = 1.0 × ATR
NORMAL  = 1.5 × ATR
WIDE    = 2.0 × ATR
```

정확한 배수는 Shadow Variant 데이터로 검증한다.

수익이 커질수록 보호강도를 높일 수 있다.

단, V1 구현은 단순하고 재현 가능해야 한다.

---

## 17. Overnight

무조건 당일 청산하지 않는다.

마감 전 다음을 재평가한다.

- Momentum
- Closing Strength
- Catalyst 지속성
- Volume
- Risk
- 다음날 Event Risk

V1 규칙:

- Overnight 동시 최대 1종목
- 조건부 Overnight만 허용
- 최대 보유기간은 Day 2까지

### Gap Stress Rule

Overnight 전에 최소 다음 시나리오를 계산한다.

- -10% Gap
- -20% Gap
- -30% Gap

기본 V1 Rule:

```text
IF Stress(-20%) 예상 계좌 손실 > Account Equity × 5%
THEN
    Overnight Position 축소 또는 Overnight 거부
```

Stress Rule은 장중 Stop Risk와 별개다.

---

## 18. Day 2

Overnight 포지션은 Day 2에서 다시 검증한다.

- Premarket
- 신규 뉴스
- Gap
- Sector
- Market
- Opening Structure
- Momentum

최대 보유기간 종료 전 반드시 청산한다.

---

## 19. Shadow Trading

TOP 8 전 종목을 Human 승인 여부와 관계없이 Shadow Trading한다.

실제 Paper 대상:

```text
0~2 symbols
```

Shadow 대상:

```text
8 symbols
```

목적:

- Quant Rank 평가
- GPT Rank 평가
- Human 선택 평가
- Premarket Gate 평가
- Opening Gate 평가
- Stop/Trailing 비교
- Overnight 성능 비교
- Pyramiding 효과 비교

---

## 20. 동일 Engine 원칙

Live/Paper/Shadow 전략 로직을 분리하지 않는다.

```text
Market Data
↓
Strategy Engine
↓
Risk Engine
↓
Order Intent
     ├─ SimBroker
     ├─ PaperBroker
     └─ LiveBroker (V1 후반/실계좌 단계)
```

Execution Adapter만 다르다.

---

## 21. Shadow Variant

V1 연구용 고정 Variant 예시:

- A: Intraday + ATR 1.5x
- B: 2-Day + ATR 1.0x
- C: 2-Day + ATR 1.5x — Live/Paper Control
- D: 2-Day + ATR 2.0x
- E: Intraday + Structure Stop

Variant는 최소 다음 조건을 만족할 때까지 변경 금지:

```text
300 eligible trades
AND
60 trading days
```

모든 Variant는 `variant_version`을 기록한다.

판정은 최소:

- Net R 평균
- MDD
- Trade Count
- Ambiguous Bar Ratio

를 함께 본다.

---

## 22. Conservative Shadow Fill

Shadow는 낙관적으로 체결하지 않는다.

원칙:

- 신호봉에서 즉시 이상적 체결 금지
- 기본적으로 다음 봉 가격 기준 체결
- Spread / Slippage / Commission / FX Cost 반영
- 동일 봉에서 Stop과 Profit 조건이 모두 가능하면 보수적으로 불리한 결과 우선
- `ambiguous_bar_count` 기록

---

## 23. 비용 모델

반드시 Gross와 Net을 분리한다.

필수 항목:

- gross_pnl
- commission
- spread_cost
- slippage
- fx_cost
- net_pnl
- gross_r
- net_r

전략의 공식 성적표는 `Net R`을 기준으로 한다.

세금은 거래별 즉시 차감하지 않고 연간 세후 성과 리포트에서 별도로 추정한다.

---

## 24. Execution Gap

Shadow Control Variant와 Paper/Live의 실행 품질을 bps로 비교한다.

```text
Entry Gap (bps)
= (Paper/Live Entry - Shadow Entry) / Shadow Entry × 10,000

Exit Gap (bps)
= 동일 방식
```

추가 저장:

- order_submit_at
- broker_ack_at
- fill_at
- execution_latency_ms
- unfilled_reason

---

## 25. Failure Mode Spec

### Source of Truth

Broker State가 Local SQLite보다 우선한다.

재기동 시:

```text
1. Broker 잔고 조회
2. 미체결 주문 조회
3. 체결 내역 조회
4. SQLite와 비교
5. 불일치 시 SAFE MODE
6. 알림
```

### Heartbeat

모니터링 대상:

- Market Data
- Broker
- Strategy Engine
- Scheduler
- Recorder

### Kill Switch

한 번의 명령으로:

```text
전량 청산
+
신규 진입 중지
```

### Safe Mode

Market Data/Broker 이상 시:

- 신규 진입 금지
- 기존 포지션 상태 확인
- Broker 정상일 경우 필요한 Emergency Action 수행
- 무조건적인 blind market sell은 금지

### Partial Fill

부분체결 시 실제 Fill 수량을 기준으로 Stop / Position / Pyramiding 상태를 재계산한다.

### Failure Events

최소 다음을 기록한다.

- MARKET_DATA_TIMEOUT
- BROKER_TIMEOUT
- ORDER_REJECTED
- PARTIAL_FILL
- TOKEN_REFRESH_FAILED
- PROCESS_RESTART
- POSITION_MISMATCH
- CALENDAR_ERROR
- SAFE_MODE_ENTERED
- KILL_SWITCH_EXECUTED

---

## 26. Market Calendar

KST 하드코딩 금지.

모든 내부 시장 시간 기준:

```text
America/New_York
```

거래 캘린더 기반으로:

- Holiday
- DST
- Early Close
- Regular Open/Close

를 계산한다.

UI에만 한국시간을 변환해서 표시한다.

---

## 27. Point-in-Time 원칙

Research 데이터는 당시 이용 가능했던 정보만 사용한다.

모든 핵심 데이터에 가능한 경우:

- observed_at
- available_at
- source_published_at

을 저장한다.

미래 수정값이나 사후 정정 데이터를 과거 Shadow 판단에 사용하지 않는다.

---

## 28. Live 1,000,000 KRW의 역할

초기 소액 Live는 전략 수익성 평가용이 아니다.

목적:

- 주문 정상 작동
- 잔고 정합성
- Stop 동작
- Trailing 동작
- Overnight 동작
- 서버 무인 운영
- 장애 복구
- 실제 Slippage
- 실제 Spread
- 실제 비용
- Execution Gap 측정

전략 성능 평가는 Shadow + Historical Replay + Paper를 우선한다.

---

## 29. V1 Non-Goals

다음은 V1에서 구현하지 않는다.

- GPT API 완전자동 호출
- ML Ranking
- 재진입 전략
- 다단계 Pyramiding
- 3~5일 이상 장기보유 전략
- 초단위 Microstructure Exit
- Multi Broker 실전운용
- Crypto/Futures 통합
- PostgreSQL
- 대규모 사용자 기능

해당 항목은 V2/V3 Backlog에서 관리한다.

---

## 30. V1 완료 기준

V1은 다음이 모두 가능해야 한다.

- Quant Scanner → TOP 8
- GPT Prompt 생성
- GPT JSON Import
- Human Approval
- Premarket / Opening Strategy
- Risk / Position Sizing
- SimBroker Shadow
- PaperBroker Adapter
- Stop / Trailing / Pyramiding / Overnight
- SQLite 기록
- Parquet Market Data
- Shadow Variant
- Replay
- Failure Manager
- UI
- Paper Trading
- Shadow/Paper Execution Gap 비교
