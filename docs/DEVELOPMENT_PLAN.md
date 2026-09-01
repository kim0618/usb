# USB — Development Plan

## 1. 개발 방식

개발은 한 번에 전체를 구현하지 않는다.

원칙:

```text
작은 Phase
→ 구현
→ 테스트
→ 보고서
→ 검수
→ 다음 Phase
```

Codex를 주 구현 에이전트로 사용하고 Claude Code는 주요 Gate에서 독립 리뷰용으로 사용한다.

---

## 2. 역할

### ChatGPT

- 제품/전략 설계
- 작업 프롬프트 작성
- 결과 검수
- 다음 Phase 판단

### Codex

- 주 구현
- 테스트
- 변경 보고서 작성

### Claude Code

- 독립 Architecture Review
- Risk / Production Review
- 복잡한 문제의 2차 검증

### User

- WSL 실행
- 결과 확인
- 보고서 전달
- Git 관리
- 최종 의사결정

---

## 3. 개발 환경

```text
Local:
Windows + WSL2 Ubuntu

Production:
Naver Cloud Ubuntu
```

실제 키움 연결은 마지막 통합 단계까지 하지 않는다.

그 전까지:

- Fake Market Data
- Replay Market Data
- SimBroker

로 전체 시스템을 구현한다.

---

# Stage 0 — Documentation / Skeleton

목표:

프로젝트의 설계를 코드보다 먼저 고정한다.

산출물:

- docs/V1_FINAL_SPEC.md
- docs/ARCHITECTURE.md
- docs/DEVELOPMENT_PLAN.md
- docs/V2_BACKLOG.md
- AGENTS.md
- README.md
- 기본 backend/frontend 디렉터리

Definition of Done:

- 문서가 프로젝트 내에 존재
- 에이전트가 문서를 먼저 읽도록 AGENTS 정의
- V1/V2 Scope 분리
- Git ignore 기본 구성

---

# Stage 1 — Foundation

구현:

- Python 3.12
- FastAPI
- Config
- Logging
- SQLite
- SQLAlchemy
- Alembic
- Pydantic
- 테스트 환경
- Market Calendar
- 공통 Exception
- Health endpoint

DoD:

- 앱 기동
- SQLite 연결
- Migration 동작
- 테스트 실행
- ET/KST 변환 테스트
- Market holiday/early close 테스트

---

# Stage 2 — Data Layer

구현:

- MarketDataProvider interface
- FakeMarketDataProvider
- ReplayMarketDataProvider
- Parquet read/write
- Market Recorder
- Scanner Snapshot model

DoD:

- Fake 1m/Daily 데이터 제공
- Parquet 저장/재생
- observed_at / available_at 유지
- Replay deterministic
- Recorder 테스트 통과

---

# Stage 3 — Quant Scanner

구현:

- Basic Filter
- RVOL
- Relative Strength
- Dollar Volume
- 20D Momentum
- Quant Score
- Candidate Pool
- TOP8
- Scanner Snapshot

DoD:

- Fake 100+ 종목 입력
- 동일 입력 = 동일 결과
- TOP8 정확히 8개
- 동점 deterministic
- 구성요소 저장
- Candidate Pool 전체 저장

---

# Stage 4 — GPT Research

구현:

- TOP8 Prompt Generator
- Stock Detail Prompt Generator
- GPT JSON Schema
- JSON Import
- Source 저장
- UNKNOWN 처리
- Evidence Confidence
- GPT Rank
- Human Decision

DoD:

- Prompt 자동 생성
- 유효 JSON Import
- 잘못된 JSON 검증 실패
- Source 없는 핵심 claim 감점
- Human APPROVE/REJECT 저장

GPT API 호출은 V1 Non-Goal이다.

---

# Stage 5 — Core Trading / Risk

구현:

- StrategyEngine
- RiskEngine
- OrderIntent
- 1R
- Structure Stop abstraction
- Position Sizing
- Base 80%
- Pyramid Reserve 20%
- Daily entry lock

DoD:

- Broker 의존성 없음
- 동일 input = 동일 output
- 손실 중 추가매수 거부
- 1 symbol/day/1 entry
- 1R position sizing 테스트

---

# Stage 6 — SimBroker / Shadow

구현:

- SimBroker
- Orders
- Partial fills model
- Costs
- Slippage
- Spread
- Execution delay model
- Shadow variants
- ambiguous_bar_count
- Replay runner

DoD:

- TOP8 Shadow 실행
- 5 Variant 병렬
- Control Variant 존재
- 다음 봉 체결
- 모호 봉 보수 처리
- Gross/Net R 계산
- 300 trades / 60 days evaluation guard

---

# Stage 7 — Strategy Lifecycle

구현:

- Premarket Gate
- Opening Range 15m
- VWAP
- OR High Breakout
- 10:30 ET Deadline
- Stop
- ATR Trailing
- Pyramiding 1회
- Overnight
- Gap Stress
- Day2
- Exit

DoD:

- 승인 종목만 Paper candidate
- Shadow는 TOP8 전부
- NO TRADE 정상 처리
- Overnight max 1
- Stress(-20%) > 5% 자동 축소/거부
- Day2 최대 보유 종료

---

# Stage 8 — Failure Manager

구현:

- Heartbeat
- Safe Mode
- Kill Switch
- Reconciliation interface
- Failure events
- Process restart handling
- Market data timeout
- Broker timeout abstraction

DoD:

- 시세 장애 시 신규진입 중지
- SAFE MODE 기록
- Kill Switch 동작
- FakeBroker mismatch 재현
- 장애 로그 저장

---

# Stage 9 — Frontend

우선순위:

1. Candidates
2. Stock Detail
3. GPT Prompt / JSON Import
4. Approval
5. Trades
6. Shadow
7. Dashboard
8. Settings

기술:

- React
- TypeScript
- Vite
- Tailwind
- TanStack Query
- React Router
- Recharts

UX 원칙:

- 깔끔한 Dark UI
- HTS처럼 복잡하지 않음
- 오늘 해야 할 행동이 명확
- Quant Rank / GPT Rank 변화가 한눈에 보임
- NO TRADE도 정상 상태로 표현

---

# Stage 10 — Kiwoom Spike

이 단계에서 처음 키움 계좌/API 준비.

본 프로젝트에 바로 붙이지 않는다.

별도 Spike:

```text
spikes/kiwoom/
```

실측 항목:

- 인증
- Token
- 미국주식 시세
- 분봉
- Historical range
- WebSocket
- Premarket
- 모의 BUY
- 모의 SELL
- 잔고
- 미체결
- 체결
- Partial Fill
- Order Type
- Fractional
- Rate Limit
- Stop/Stop-Limit
- Token Renewal

Spike 성공 후에만 본체 Adapter 구현.

---

# Stage 11 — Kiwoom Adapter

구현:

- KiwoomMarketDataProvider
- KiwoomPaperBroker

Strategy/Risk Engine 수정 금지.

DoD:

- 기존 interface 완전 준수
- Fake/Replay와 동일 테스트 contract
- Paper order/fill 반영
- Broker state reconciliation

---

# Stage 12 — Paper Trading

실제 미국장에서:

```text
Kiwoom Paper
+
Shadow Control
```

동시 운용.

측정:

- Entry Gap bps
- Exit Gap bps
- Latency
- Slippage
- Rejections
- Partial fills
- Broker failures

---

# Stage 13 — Production Hardening

실제 장애 테스트:

- Process kill
- Restart
- Network interruption
- Market data timeout
- Broker timeout
- Token expiry
- Partial fill
- Order reject
- Position mismatch
- Early close
- DST boundary

---

# Stage 14 — Small Live

마지막 단계.

목적:

수익 검증이 아니라 운영 검증.

초기 자본 예시:

```text
1,000,000 KRW
```

검증:

- 실제 주문
- 실제 비용
- 실제 spread
- 실제 slippage
- 실제 reconciliation
- 실제 overnight
- actual execution gap

---

## 4. Claude Code Review Gates

### Gate 1

Stage 1~3 완료 후:

- Architecture
- Data layer
- Scanner
- coupling
- test coverage

리뷰.

### Gate 2

Stage 4~7 완료 후:

- Strategy/Risk
- Shadow/Control
- Data leakage
- Look-ahead
- Variant contamination

리뷰.

### Gate 3

Paper 직전:

- Failure Mode
- Broker abstraction
- Production readiness
- Secret handling
- restart consistency

리뷰.

---

## 5. 작업 프롬프트 규칙

각 Codex 작업은 반드시 하나의 명확한 완료 단위를 가진다.

금지:

```text
전체 봇을 구현해라.
```

권장:

```text
Stage 2 MarketDataProvider interface 및 Fake Provider만 구현해라.
```

모든 작업에는 Definition of Done을 포함한다.

---

## 6. Codex 완료 보고서

각 작업 완료 시 반드시 아래 형식으로 보고한다.

1. 변경 파일
2. 구현 내용
3. 설계 판단
4. 실행 테스트
5. 테스트 결과
6. 발견 문제
7. 미구현 사항
8. 다음 단계 주의점
9. Git status

Commit / Push는 사용자가 명시적으로 요청하지 않으면 하지 않는다.
