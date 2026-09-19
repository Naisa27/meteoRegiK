"""Пересчёт computed_indicators при правке/добавлении/удалении сырых строк
через интерфейс — только затронутый диапазон (день -> декада -> месяц -> год)
для конкретной станции.

Декада/месяц/год пересчитываются целиком по своим границам (не только по
затронутой части [start, end]) — см. calculations/temperature.py. В отличие
от дней (которые всегда считаются заново из rp5_readings/gg_readings),
декада/месяц/год строятся поверх уже сохранённых в computed_indicators
значений дней (_aggregate_from_stored) — так выбор пользователя при
конфликте на уровне дня корректно поднимается вверх по иерархии периодов.

Разрешение конфликтов: если пересчитанное значение отличается от уже
сохранённого в computed_indicators (например, для одной и той же даты
среднесуточная rp5-агрегация не совпадает со значением из файла ГГ),
вызывающему коду (UI) передаётся ValueConflict через опциональный callback
conflict_resolver — тем же паттерном, что и name_resolver в gg_importer.py.
Без conflict_resolver сохраняется прежнее поведение: новое значение всегда
побеждает.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.temperature import Stats, daily_stats, decade_bounds
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings

PARAMETER = "T"
_EPSILON = 1e-6

ConflictChoice = Literal["old", "new"]


@dataclass(frozen=True)
class ValueConflict:
    """Расхождение между уже сохранённым и свежерассчитанным значением —
    передаётся в conflict_resolver для выбора пользователем."""

    wmo_index: int
    period_type: str  # 'day' | 'decade' | 'month' | 'year'
    period_start: date
    period_end: date
    stat_type: str  # 'avg' | 'min' | 'max'
    old_value: float
    old_source_file: str | None
    new_value: float
    new_source_file: str | None


ConflictResolver = Callable[[ValueConflict], ConflictChoice]


def _source_file_for_day(engine: Engine, wmo_index: int, day: date) -> str | None:
    """Источник значения за день — ГГ в приоритете над rp5, тем же порядком,
    что и daily_stats."""
    with engine.connect() as conn:
        gg_source = conn.execute(
            select(gg_readings.c.source_file).where(
                gg_readings.c.wmo_index == wmo_index, gg_readings.c.obs_date == day
            )
        ).scalar_one_or_none()
        if gg_source is not None:
            return gg_source
        return conn.execute(
            select(rp5_readings.c.source_file)
            .where(rp5_readings.c.wmo_index == wmo_index, rp5_readings.c.obs_date == day)
            .limit(1)
        ).scalar_one_or_none()


def _existing_stats(
    conn: Connection, wmo_index: int, period_type: str, period_start: date, period_end: date
) -> dict[str, dict[str, object]]:
    rows = conn.execute(
        select(
            computed_indicators.c.stat_type,
            computed_indicators.c.value,
            computed_indicators.c.source_file,
        ).where(
            computed_indicators.c.wmo_index == wmo_index,
            computed_indicators.c.period_type == period_type,
            computed_indicators.c.period_start == period_start,
            computed_indicators.c.period_end == period_end,
            computed_indicators.c.parameter == PARAMETER,
        )
    ).all()
    return {row.stat_type: {"value": row.value, "source_file": row.source_file} for row in rows}


def _write(
    conn: Connection,
    wmo_index: int,
    period_type: str,
    period_start: date,
    period_end: date,
    stat_type: str,
    value: float | None,
    source_file: str | None,
) -> None:
    stmt = sqlite_insert(computed_indicators).values(
        wmo_index=wmo_index,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        parameter=PARAMETER,
        stat_type=stat_type,
        value=value,
        source_file=source_file,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            computed_indicators.c.wmo_index,
            computed_indicators.c.period_type,
            computed_indicators.c.period_start,
            computed_indicators.c.period_end,
            computed_indicators.c.parameter,
            computed_indicators.c.stat_type,
        ],
        set_={"value": value, "source_file": source_file},
    )
    conn.execute(stmt)


def _resolve_and_store(
    engine: Engine,
    wmo_index: int,
    period_type: str,
    period_start: date,
    period_end: date,
    stats: Stats,
    new_source_file: str | None,
    conflict_resolver: ConflictResolver | None,
) -> None:
    with engine.begin() as conn:
        existing = _existing_stats(conn, wmo_index, period_type, period_start, period_end)

        for stat_type in ("avg", "min", "max"):
            new_value = stats[stat_type]
            old = existing.get(stat_type)
            old_value = old["value"] if old else None

            if new_value is None:
                if old is None:
                    continue  # нечего сохранять и нечего сравнивать
                continue  # новых данных для пересчёта нет — сохранённое не трогаем

            if old is None or old_value is None or abs(old_value - new_value) <= _EPSILON:
                _write(conn, wmo_index, period_type, period_start, period_end, stat_type, new_value, new_source_file)
                continue

            choice: ConflictChoice = "new"
            if conflict_resolver is not None:
                conflict = ValueConflict(
                    wmo_index=wmo_index,
                    period_type=period_type,
                    period_start=period_start,
                    period_end=period_end,
                    stat_type=stat_type,
                    old_value=old_value,
                    old_source_file=old["source_file"],
                    new_value=new_value,
                    new_source_file=new_source_file,
                )
                choice = conflict_resolver(conflict)

            if choice == "new":
                _write(conn, wmo_index, period_type, period_start, period_end, stat_type, new_value, new_source_file)
            # choice == "old" — сохранённое значение оставляем как есть


def _aggregate_from_stored(engine: Engine, wmo_index: int, start: date, end: date) -> Stats:
    """avg/min/max за период поверх уже сохранённых в computed_indicators
    дневных значений (а не пересчётом заново из сырых данных) — чтобы выбор
    пользователя при конфликте на уровне дня учитывался в декаде/месяце/году."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(computed_indicators.c.stat_type, computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.period_start >= start,
                computed_indicators.c.period_start <= end,
                computed_indicators.c.parameter == PARAMETER,
            )
        ).all()

    avgs = [row.value for row in rows if row.stat_type == "avg" and row.value is not None]
    mins = [row.value for row in rows if row.stat_type == "min" and row.value is not None]
    maxs = [row.value for row in rows if row.stat_type == "max" and row.value is not None]
    return {
        "avg": round(sum(avgs) / len(avgs), 2) if avgs else None,
        "min": min(mins) if mins else None,
        "max": max(maxs) if maxs else None,
    }


def _decades_in_range(start: date, end: date) -> list[tuple[date, date]]:
    decades = []
    day = start
    while day <= end:
        bounds = decade_bounds(day)
        decades.append(bounds)
        day = bounds[1] + timedelta(days=1)
    return decades


def _months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


def recompute_range(
    engine: Engine,
    wmo_index: int,
    start: date,
    end: date,
    progress_callback: Callable[[date], None] | None = None,
    conflict_resolver: ConflictResolver | None = None,
) -> None:
    """progress_callback(day), если передан, вызывается после пересчёта
    каждого дня диапазона. conflict_resolver(ValueConflict), если передан,
    вызывается при расхождении пересчитанного значения с уже сохранённым —
    должен вернуть "old" или "new"; без него побеждает новое значение."""
    day = start
    while day <= end:
        stats = daily_stats(engine, wmo_index, day)
        source_file = None
        if any(v is not None for v in stats.values()):
            source_file = _source_file_for_day(engine, wmo_index, day)
        _resolve_and_store(engine, wmo_index, "day", day, day, stats, source_file, conflict_resolver)
        if progress_callback is not None:
            progress_callback(day)
        day += timedelta(days=1)

    for decade_start, decade_end in _decades_in_range(start, end):
        stats = _aggregate_from_stored(engine, wmo_index, decade_start, decade_end)
        _resolve_and_store(engine, wmo_index, "decade", decade_start, decade_end, stats, None, conflict_resolver)

    for year, month in _months_in_range(start, end):
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        stats = _aggregate_from_stored(engine, wmo_index, month_start, month_end)
        _resolve_and_store(engine, wmo_index, "month", month_start, month_end, stats, None, conflict_resolver)

    for year in range(start.year, end.year + 1):
        year_start, year_end = date(year, 1, 1), date(year, 12, 31)
        stats = _aggregate_from_stored(engine, wmo_index, year_start, year_end)
        _resolve_and_store(engine, wmo_index, "year", year_start, year_end, stats, None, conflict_resolver)
