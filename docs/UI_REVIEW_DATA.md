# Stage 9.7 — UI Review Data Seed

## 목적과 데이터 성격

UI Review Seed는 7개 Frontend 화면을 정보가 채워진 상태로 육안 검수하기 위한
development/test 전용 synthetic dataset이다. 실제 시장 데이터, 계좌 데이터 또는 전략
수익성 자료가 아니다. Quant 공식, Research/Evidence 규칙, 승인 제한, Strategy/Risk 규칙과
Backend API 계약은 변경하지 않는다. 외부 API와 GPT 호출도 없다.

같은 source revision과 설정에서 실행하면 Scanner, TOP8, GPT 순위와 Evidence, Human 결정,
주문·체결, Shadow A–E, Runtime 장애 예시가 동일하게 생성된다. audit ID를 공유하는 것이
아니라 사용자가 보는 business 결과를 재현한다.

## Local Data / Git 정책

Git에는 seed source, deterministic fixture, migration, 이 문서만 공유한다. SQLite DB와
`-wal`/`-shm`, `data/runtime` 아래 replay report 등 generated runtime output, logs는 공유하지
않는다. `data/runtime/.gitkeep`과 `logs/.gitkeep`만 디렉터리 유지를 위해 추적할 수 있다.

PC A와 PC B는 각자 다음 DB를 독립적으로 만든다.

```text
일반 개발: data/runtime/usb.sqlite3
UI Review: data/runtime/usb_ui_review.sqlite3
```

따라서 SQLite를 Git에 commit하지 않는다. 두 PC의 DB는 복제·동기화 관계가 아니며, 동일한
seed 명령으로 같은 UI 검수 상태를 재생성한다. 향후 실제 Kiwoom Paper/Live 운영 데이터의
Source of Truth는 단일 Naver Cloud Server DB가 되며, 그 구성은 Stage 9.7 범위가 아니다.

## 생성 및 실행

프로젝트 root에서 다음 순서로 실행한다. Seed 명령이 전용 DB에 기존 Alembic migration
`20260901_0008` head를 적용한 뒤 데이터를 만든다. migration을 별도 구현하거나 application
startup에 연결하지 않는다.

```bash
PYTHONPATH=backend .venv/bin/python -m app.dev.seed_ui
```

동일 전용 DB에 다시 실행하면 기존 marker를 감지하고 아무것도 변경하지 않은 채 현재 count를
출력한다. 완전히 다시 만들 때만 다음 명시적 명령을 사용한다.

```bash
PYTHONPATH=backend .venv/bin/python -m app.dev.seed_ui --reset
```

`--reset`은 SQLite 파일명이 `ui_review`를 포함하는 경우에만 허용된다. 기본 개발 DB
`usb.sqlite3`와 다른 이름의 DB는 거부한다. `APP_ENV`는 현재 Config 값인 `development` 또는
테스트의 `test`만 허용하고, `production`을 포함한 그 밖의 값에서는 migration과 DB 변경 전에
non-zero로 종료한다.

Seed DB로 Backend를 실행한다.

```bash
DATABASE_URL=sqlite:///data/runtime/usb_ui_review.sqlite3 PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

이 relative URL은 현재 Config가 project root 기준 absolute path로 해석하므로 PC마다 repository
경로가 달라도 같은 명령을 쓸 수 있다. Seed 완료 출력에는 확인용 absolute URL도 표시된다.

별도 terminal에서 Frontend를 실행한다.

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

브라우저에서 `http://localhost:3000`을 연다. Frontend는 기존 Backend API만 사용하며 현재 DB가
UI Review용인지 알지 못한다. seed awareness나 demo fallback은 Frontend에 없다.

## PC A / PC B workflow

각 PC에서 `git pull` 후 위의 seed, Backend, Frontend 명령을 각각 실행한다. 한 PC에서 만든
`usb_ui_review.sqlite3`, WAL/SHM, replay report 또는 log를 다른 PC로 commit/push하지 않는다.
Reset이 필요하면 각 PC가 자기 전용 DB에 `--reset`을 실행한다.

## 데이터 범위와 제약

- Scanner는 기존 SyntheticReplayDataset과 QuantScanner를 사용해 Candidate Pool 및 TOP8을 계산한다.
- Research는 Stage 4 JSON Schema와 import/evidence service를 통과하며 HumanDecisionService로 정확히
  두 종목만 승인한다.
- Orders/Fills는 synthetic Sim execution review record이며 BUY/SELL과 여러 실제 enum 상태를 포함한다.
- Shadow A–E는 기존 ReplaySmokeRunner의 Strategy/Risk/SimBroker 결과를 사용한다. 실제 수익성 의미는 없다.
- Runtime은 NORMAL이며 fixed heartbeat/data/execution/reconciliation timestamp와 resolved/unresolved
  WARNING/ERROR 예시가 있다. unresolved CRITICAL은 만들지 않는다.
- Active SimBroker는 process-local이므로 DB seed만으로 Position을 만들지 않는다. Trading의
  `availability`는 `NO_ACTIVE_SIM_BROKER`, positions는 empty인 기존 production architecture를 유지한다.

Seed는 FastAPI/Frontend startup, Alembic 자체, pytest startup, server restart 또는 deploy에서 자동
실행되지 않는다. API key, secret, 실제 계좌번호, 개인정보, broker credential도 포함하지 않는다.
