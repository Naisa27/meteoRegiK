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

Миграция 6 — три новые таблицы сохранённого расчёта отклонений от нормы
(station_norms, period_completeness, anomaly_state; calculations/anomalies.py).
Существующие таблицы не меняются — в отличие от прежних миграций, рабочая
база к этому моменту уже с данными, пересоздавать нечего и нельзя. Строк
состояния ни у одной станции нет, поэтому все станции считаются требующими
расчёта и досчитываются при первом открытии дашборда или карты.

Миграция 7 — данные, а не схема: появился второй параметр, сумма осадков
(computed_indicators, parameter='R'; calculations/precipitation.py). По уже
залитым станциям суммы досчитываются из сырых данных, иначе осадки появились
бы только у станций, залитых после обновления программы. Станции помечаются
требующими расчёта отклонений. Таблицы не меняются. Сам пересчёт выполняет
миграция 8 (см. ниже) — она идёт следом и считает те же суммы заново.

Миграция 8 — нормы и отклонения осадков, сезоны. Новая таблица norm_bases
(база нормы по ключу "станция, параметр" — у температуры и осадков она
разная); сведения о базе нормы уходят туда из anomaly_state, и та
пересоздаётся без этих столбцов. Обе таблицы — производные данные расчёта
отклонений: после миграции все станции требуют расчёта и досчитываются при
открытии дашборда, карты или по кнопке. Суммы осадков пересчитываются ещё
раз — к ним добавились сезонные (period_type='season').

Обновление у пользователя (решение 2026-10-11). Миграции с пересчётом
данных перечислены в DATA_UPDATES: что именно досчитывается, словами для
пользователя. Если такие ждут применения и в базе есть данные
(pending_data_updates), main.py запускает миграции не молча, а через окно
ui/upgrade_dialog.py — с копией базы перед началом (backup_database) и ходом
расчёта по станциям (параметр progress). Все миграции идут одной
транзакцией: при сбое база остаётся прежней, попытка повторится при
следующем запуске.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations import precipitation
from meteoregik.db.schema import (
    anomaly_state,
    computed_indicators,
    gg_readings,
    metadata,
    norm_bases,
    period_completeness,
    rp5_readings,
    schema_version,
    station_norms,
)

CURRENT_SCHEMA_VERSION = 8

# Миграции, которые досчитывают показатели по уже залитым данным: номер
# версии -> что считается (продолжение фразы "Идёт расчёт новых показателей:").
# Текст видит пользователь в окне обновления.
DATA_UPDATES: dict[int, str] = {
    7: "суммы осадков за сутки, декаду, месяц и год",
    8: "суммы осадков по сезонам (зима, весна, лето, осень)",
}
BACKUP_SUFFIX = "_до_обновления"

# Ход расчёта по станциям: номер станции, всего станций, индекс ВМО
StationProgress = Callable[[int, int, int], None]


def _stored_version(engine: Engine) -> int | None:
    """Версия схемы базы; None — база ещё не создана (таблицы версии нет)."""
    if not inspect(engine).has_table("schema_version"):
        return None
    with engine.connect() as conn:
        return conn.execute(select(schema_version.c.version)).scalar_one()


def pending_data_updates(engine: Engine) -> list[str]:
    """Что предстоит досчитать при обновлении этой базы — описания из
    DATA_UPDATES. Пусто, если база актуальна, только что создана или в ней
    нет залитых данных: считать нечего, окно обновления не нужно."""
    version = _stored_version(engine)
    if version is None or version >= CURRENT_SCHEMA_VERSION:
        return []
    with engine.connect() as conn:
        if not precipitation.stations_with_readings(conn):
            return []
    return [text for number, text in sorted(DATA_UPDATES.items()) if number > version]


def backup_path(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.stem}{BACKUP_SUFFIX}{db_path.suffix}")


def backup_database(db_path: Path) -> Path:
    """Копия базы перед обновлением — рядом с ней, одна (прежняя копия
    заменяется). Через механизм резервного копирования SQLite, а не
    копированием файла: часть данных может лежать в файле журнала (-wal)."""
    target = backup_path(db_path)
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def run_migrations(engine: Engine, progress: StationProgress | None = None) -> None:
    """progress — ход миграций, досчитывающих данные (см. DATA_UPDATES)."""
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
            if version < 6:
                _migrate_to_6(conn)
            if version < 7:
                _migrate_to_7(conn, progress)
            if version < 8:
                _migrate_to_8(conn, progress)
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


def _migrate_to_6(conn: Connection) -> None:
    metadata.create_all(conn, tables=[station_norms, period_completeness, anomaly_state], checkfirst=True)


def _rebuild_precipitation(conn: Connection, progress: StationProgress | None) -> None:
    """Пересчитать суммы осадков всех станций с сырыми данными."""
    wmo_indexes = precipitation.stations_with_readings(conn)
    for number, wmo_index in enumerate(wmo_indexes, start=1):
        if progress is not None:
            progress(number, len(wmo_indexes), wmo_index)
        precipitation.rebuild_station(conn, wmo_index)


def _migrate_to_8(conn: Connection, progress: StationProgress | None = None) -> None:
    # Строки состояния со старыми столбцами не нужны: расчёт устарел у всех
    conn.exec_driver_sql("DROP TABLE IF EXISTS anomaly_state")
    metadata.create_all(conn, tables=[anomaly_state, norm_bases], checkfirst=True)
    _rebuild_precipitation(conn, progress)


def _migrate_to_7(conn: Connection, progress: StationProgress | None = None) -> None:
    """Суммы осадков по залитым данным. Сам пересчёт делает миграция 8: она
    всегда идёт следом и пересчитывает те же суммы уже вместе с сезонными —
    считать дважды незачем (версия 7 отдельно пользователям не выпускалась)."""
