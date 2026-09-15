"""Простые пронумерованные миграции + таблица schema_version.

Миграция 1 — начальное создание схемы из db/schema.py. Новые миграции
добавляются как номера выше CURRENT_SCHEMA_VERSION и применяются по порядку,
каждая проверяет текущую версию, записанную в schema_version, и поднимает её
на единицу после применения.
"""

from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import metadata, schema_version

CURRENT_SCHEMA_VERSION = 1


def run_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table("schema_version"):
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(schema_version.insert().values(version=CURRENT_SCHEMA_VERSION))
        return

    with engine.begin() as conn:
        version = conn.execute(select(schema_version.c.version)).scalar_one()

    if version < CURRENT_SCHEMA_VERSION:
        # Место для будущих пронумерованных миграций (version -> version + 1).
        raise NotImplementedError(
            f"Миграция с версии {version} до {CURRENT_SCHEMA_VERSION} ещё не реализована"
        )
