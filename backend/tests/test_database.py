from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import create_db_engine


def test_sqlite_session_and_wal(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime" / "test.sqlite3"
    engine = create_db_engine(f"sqlite:///{database_path}")
    with Session(engine) as session:
        assert session.scalar(text("SELECT 1")) == 1
        assert session.scalar(text("PRAGMA journal_mode")) == "wal"
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
    engine.dispose()

