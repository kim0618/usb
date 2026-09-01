# Backend Development

Run these commands from the project root on WSL2 Ubuntu or Ubuntu.

## Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` only when local overrides are needed. Never commit
`.env` or secrets.

## Run the API

```bash
PYTHONPATH=backend uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
```

## Test

```bash
python -m pytest
```

Tests use fixed dates and local temporary SQLite databases. They do not require a
broker, API key, or internet connection.

## Database migrations

```bash
alembic current
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

Alembic reads `DATABASE_URL` through the same application settings as the API.
No trading tables or initial schema migration are part of Stage 1.
