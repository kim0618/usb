# usb

USB — US Catalyst Momentum Research & Trading System

## Initial documents

- `AGENTS.md`
- `docs/V1_FINAL_SPEC.md`
- `docs/ARCHITECTURE.md`
- `docs/DEVELOPMENT_PLAN.md`
- `docs/V2_BACKLOG.md`

Development starts in WSL2 Ubuntu and is deployed to Naver Cloud Ubuntu.
Kiwoom integration is intentionally deferred until the final paper-integration stages.

## Setup after a fresh clone

Run every command from the project root on WSL2 Ubuntu or Ubuntu.

### Backend

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
alembic upgrade head
python -m pytest
```

`alembic upgrade head` creates `data/runtime/usb.sqlite3` from scratch. The database,
Parquet market data, and logs are intentionally untracked, so a fresh clone starts with an
empty database rather than another machine's state.

Copy `.env.example` to `.env` only when local overrides are needed. The defaults in
`backend/app/core/config.py` are sufficient without it.

### Frontend

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

The Backend must be running for the dashboard to load data. There is no mock-data
fallback.

### Run the API

```bash
PYTHONPATH=backend uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
```

Details live in `docs/BACKEND_DEVELOPMENT.md` and `docs/FRONTEND_V1.md`.
