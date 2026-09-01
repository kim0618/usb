# USB — Architecture

## 1. 목표

USB는 Strategy / Risk / Execution을 분리하고, Shadow/Paper/Live에서 동일한 Strategy Engine을 재사용하는 구조를 최우선 원칙으로 한다.

---

## 2. System Overview

```text
                 Market Data Provider
                         │
                         ▼
                   Quant Scanner
                         │
                         ▼
                 Candidate Pool
                         │
                         ▼
                      TOP 8
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
        GPT Research             Shadow Research
             │                       │
             ▼                       │
        Human Approval               │
             │                       │
             └───────────┬───────────┘
                         ▼
                  Strategy Engine
                         │
                         ▼
                    Risk Engine
                         │
                         ▼
                    Order Intent
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          SimBroker   PaperBroker  LiveBroker
```

---

## 3. 기술스택

### Backend

- Python 3.12
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic
- pandas
- numpy
- pyarrow
- httpx
- websockets
- APScheduler
- exchange-calendars 또는 pandas-market-calendars
- pytest / pytest-asyncio

### Frontend

- React
- TypeScript
- Vite
- Tailwind CSS
- TanStack Query
- React Router
- Recharts

### Storage

- SQLite: 운영/연구 메타데이터
- Parquet: 대량 Market Data

### Local

- Windows + WSL2 Ubuntu

### Production

- Naver Cloud Ubuntu
- systemd
- SQLite
- Parquet

Docker는 V1 필수가 아니다.

---

## 4. Backend Module Structure

```text
backend/
└─ app/
   ├─ api/
   ├─ core/
   ├─ models/
   ├─ repositories/
   ├─ services/
   ├─ market/
   ├─ scanner/
   ├─ research/
   ├─ strategy/
   ├─ risk/
   ├─ execution/
   ├─ broker/
   ├─ shadow/
   ├─ recorder/
   ├─ scheduler/
   └─ monitoring/
```

### api

Frontend 및 내부 관리 API.

### core

- config
- logging
- timezone
- exceptions
- constants

### models

SQLAlchemy ORM / Pydantic Schema.

### repositories

DB 접근을 캡슐화한다.

### market

MarketDataProvider interface 및 Replay/Fake/Kiwoom 구현체.

### scanner

Quant Filter / Score / Ranking / TOP8.

### research

GPT Prompt / JSON Import / Evidence Confidence.

### strategy

Entry / Exit / Overnight / Pyramiding 판단.

### risk

- 1R
- Position Sizing
- Exposure
- Gap Stress
- Daily Risk

### execution

Order Intent 및 Execution Adapter abstraction.

### broker

SimBroker / KiwoomPaperBroker / 향후 KiwoomLiveBroker.

### shadow

Shadow Variant orchestration.

### recorder

Market Data / Parquet / Scanner Snapshot 기록.

### scheduler

Market Calendar 기반 스케줄 실행.

### monitoring

Heartbeat / Safe Mode / Failure Events / Kill Switch.

---

## 5. 핵심 Interface

### MarketDataProvider

책임:

- Symbol metadata
- Daily OHLCV
- Minute bars
- Realtime quotes/trades
- Premarket/regular session data

V1 초기 구현:

- FakeMarketDataProvider
- ReplayMarketDataProvider

후반:

- KiwoomMarketDataProvider

---

### Broker

책임:

- place_order
- cancel_order
- get_orders
- get_positions
- get_balances
- get_fills

구현:

- SimBroker
- KiwoomPaperBroker
- KiwoomLiveBroker (최종 단계)

---

### StrategyEngine

Broker를 몰라야 한다.

입력:

- Market state
- Candidate metadata
- Research decision
- Position state

출력:

- ENTER
- HOLD
- ADD
- EXIT
- OVERNIGHT_HOLD
- NO_TRADE

---

### RiskEngine

Strategy 판단을 받아 실제 Order Intent를 생성한다.

책임:

- 1R
- Stop Distance
- Position Size
- Exposure Cap
- Pyramiding Reserve
- Overnight Stress
- Daily Risk
- Safe Mode Blocking

---

## 6. 동일 엔진 원칙

금지:

```text
live_strategy.py
shadow_strategy.py
```

허용:

```text
StrategyEngine
RiskEngine
ExecutionAdapter
```

동일 Market Event에 대해 동일 Strategy/Risk 함수를 호출한다.

---

## 7. Data Flow

### Scanner

```text
Market Data
→ Basic Filter
→ Score Components
→ Quant Score
→ Candidate Pool
→ Rank
→ TOP8
→ SQLite Snapshot
```

### GPT

```text
TOP8
→ Prompt
→ GPT Web Research
→ JSON
→ Import
→ Source Validation
→ Evidence Confidence
→ GPT Rank
```

### Human

```text
GPT Rank
→ APPROVE / REJECT
→ SQLite
```

### Trading

```text
APPROVED
→ Premarket Gate
→ Opening Gate
→ Strategy
→ Risk
→ Order Intent
→ Broker
```

### Shadow

```text
TOP8
→ 동일 Strategy
→ 동일 Risk
→ SimBroker
→ Variant Results
```

---

## 8. Database / File Boundary

SQLite:

- Scanner runs
- Candidates
- Scores
- GPT analysis
- Sources
- Human decisions
- Strategy states
- Orders
- Fills
- Positions
- Shadow results
- Paper results
- Costs
- Failures
- Execution metrics

Parquet:

- Daily bars
- Minute bars
- Premarket bars
- Replay datasets

---

## 9. Process Model

V1 권장 3 Process:

### api

FastAPI + Frontend API.

### worker

- Scanner
- Strategy
- Risk
- Shadow
- Paper orchestration

### recorder

Market data recording.

Production에서는 각각 systemd service로 관리할 수 있다.

---

## 10. Scheduling

내부 기준 timezone:

```text
America/New_York
```

KST cron 하드코딩 금지.

Market Calendar를 통해:

- market open
- market close
- early close
- holiday
- DST

결정.

---

## 11. Failure Architecture

```text
Component Failure
↓
Failure Manager
↓
Safe Mode
↓
New Entry Block
↓
Broker State Reconciliation
↓
Alert
```

Broker 상태가 Source of Truth다.

---

## 12. Security

V1 기본:

- API Key는 환경변수
- `.env` Git 제외
- Secret log 출력 금지
- Kill Switch는 인증 필요
- Production CORS 제한
- Web UI 외부 공개 최소화

---

## 13. 확장성

V1에서 Provider와 Broker를 interface로 분리하는 이유:

```text
Kiwoom → KIS
Kiwoom → IBKR
Stocks → Futures
```

변경 시 Strategy Engine을 수정하지 않기 위함이다.
