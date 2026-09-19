"""Подключение к SQLite-файлу проекта, включение WAL-режима и внешних ключей."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

DEFAULT_DB_FILENAME = "meteoregik.sqlite3"


def default_db_path() -> Path:
    """Путь к рабочей БД по умолчанию. В собранном PyInstaller-exe (`sys.frozen`)
    — файл рядом с exe (`sys.executable`), чтобы обновление приложения (замена
    exe в той же папке) не теряло БД: `__file__` в frozen-режиме указывает во
    временную папку распаковки (`sys._MEIPASS`), которая создаётся заново при
    каждом запуске и удаляется при закрытии, а не в папку рядом с exe. В
    dev-режиме — файл в корне проекта (см. plan.md: "SQLite, файл внутри
    проекта")."""
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        # src/meteoregik/db/session.py -> src/meteoregik -> src -> <project root>
        base_dir = Path(__file__).resolve().parents[3]
    return base_dir / DEFAULT_DB_FILENAME


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
