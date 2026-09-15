"""Подключение к SQLite-файлу проекта, включение WAL-режима и внешних ключей."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

DEFAULT_DB_FILENAME = "meteoregik.sqlite3"


def default_db_path() -> Path:
    """Путь к БД по умолчанию — файл в корне проекта (см. plan.md: "SQLite,
    файл внутри проекта")."""
    # src/meteoregik/db/session.py -> src/meteoregik -> src -> <project root>
    project_root = Path(__file__).resolve().parents[3]
    return project_root / DEFAULT_DB_FILENAME


def get_engine(db_path: Path | None = None) -> Engine:
    path = db_path or default_db_path()
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
