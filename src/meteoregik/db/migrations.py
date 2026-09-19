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
"""

from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection, Engine

from meteoregik.db.schema import metadata, rp5_readings, schema_version

CURRENT_SCHEMA_VERSION = 2


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
            conn.execute(schema_version.update().values(version=CURRENT_SCHEMA_VERSION))


def _migrate_to_2(conn: Connection) -> None:
    has_rows = conn.execute(select(rp5_readings.c.id).limit(1)).first() is not None
    if has_rows:
        raise RuntimeError(
            "Миграция 2 пересоздаёт rp5_readings и ожидает пустую таблицу, "
            "но в ней уже есть данные — нужна миграция с сохранением данных"
        )
    rp5_readings.drop(conn, checkfirst=True)
    rp5_readings.create(conn)
