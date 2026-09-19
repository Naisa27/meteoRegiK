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

Миграция 4 — station_id (FK на автоинкрементный PK stations.id) в
gg_readings и computed_indicators переименован в wmo_index (без FK, тем же
паттерном, что и rp5_readings): FK-значение — внутренний PK, не имеющий
смысла при прямом просмотре сырых данных (например, у станции с
wmo_index=27704 в этом поле оказывалось произвольное число вроде 958) —
путаница обнаружена пользователем при проверке экрана "Импорт" 2026-09-19.
На момент миграции обе таблицы гарантированно пусты (задокументировано и
проверено вручную — единственные прогоны импортёров были в тестах/ручной
проверке на временных БД) — таблицы пересоздаются тем же паттерном.

Миграция 5 — добавлен computed_indicators.source_file (nullable): для
day-уровня хранит имя файла, давшего текущее значение; нужен
calculations/recompute.py для диалога разрешения конфликтов между
источниками с разными значениями на одну и ту же дату (rp5 vs ГГ), см.
правки пользователя от 2026-09-19. computed_indicators на момент миграции
пуста — пересоздаётся тем же паттерном.
"""

from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection, Engine

from meteoregik.db.schema import computed_indicators, gg_readings, metadata, rp5_readings, schema_version

CURRENT_SCHEMA_VERSION = 5


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
            if version < 4:
                _migrate_to_4(conn)
            if version < 5:
                _migrate_to_5(conn)
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


def _migrate_to_4(conn: Connection) -> None:
    _recreate_if_empty(conn, gg_readings, "Миграция 4")
    _recreate_if_empty(conn, computed_indicators, "Миграция 4")


def _migrate_to_5(conn: Connection) -> None:
    _recreate_if_empty(conn, computed_indicators, "Миграция 5")
