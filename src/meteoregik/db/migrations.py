"""Простые пронумерованные миграции + таблица schema_version.

Миграция 1 — начальное создание схемы из db/schema.py. Новые миграции
добавляются как номера выше CURRENT_SCHEMA_VERSION и применяются по порядку,
каждая проверяет текущую версию, записанную в schema_version, и поднимает её
на единицу после применения.

Миграция 2 — финализация набора столбцов rp5_readings по факту разбора
реального файла rp5.ru при написании rp5_importer.py (добавлен ff3, RRR/VV/
sss переведены из Float в Text — см. db/schema.py). До этой миграции
rp5_readings нигде не заполнялась (rp5_importer.py ещё не был реализован),
поэтому миграция просто пересоздаёт таблицу по актуальному db/schema.py, а
не хранит замороженный DDL — с проверкой, что таблица действительно пуста.
Для будущих миграций, затрагивающих таблицы с данными, использовать явный
DDL, а не текущую metadata (иначе миграция "поплывёт" вместе с дальнейшими
изменениями schema.py).

Миграция 3 — добавлен UniqueConstraint на computed_indicators
(station_id, period_type, period_start, period_end, parameter, stat_type),
нужный calculations/recompute.py для атомарного upsert (INSERT ... ON
CONFLICT). До этой миграции computed_indicators нигде не заполнялась —
таблица пересоздаётся тем же паттерном, что и в миграции 2.
"""

from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection, Engine

from meteoregik.db.schema import computed_indicators, metadata, rp5_readings, schema_version

CURRENT_SCHEMA_VERSION = 3


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
        with engine.begin() as conn:
            if version < 2:
                _migrate_to_2(conn)
            if version < 3:
                _migrate_to_3(conn)
            conn.execute(schema_version.update().values(version=CURRENT_SCHEMA_VERSION))


def _recreate_if_empty(conn: Connection, table, migration_label: str) -> None:
    has_rows = conn.execute(select(table.c.id).limit(1)).first() is not None
    if has_rows:
        raise RuntimeError(
            f"{migration_label} пересоздаёт {table.name} и ожидает пустую таблицу, "
            "но в ней уже есть данные — нужна миграция с сохранением данных"
        )
    table.drop(conn, checkfirst=True)
    table.create(conn)


def _migrate_to_2(conn: Connection) -> None:
    _recreate_if_empty(conn, rp5_readings, "Миграция 2")


def _migrate_to_3(conn: Connection) -> None:
    _recreate_if_empty(conn, computed_indicators, "Миграция 3")
