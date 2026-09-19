"""Пересчёт computed_indicators при правке/добавлении/удалении сырых строк
через интерфейс — только затронутый диапазон (день -> декада -> месяц -> год)
для конкретной станции.

Декада/месяц/год пересчитываются целиком по своим границам (не только по
затронутой части [start, end]) — см. calculations/temperature.py.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Callable

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.temperature import (
    Stats,
    daily_stats,
    decade_bounds,
    decade_stats,
    month_stats,
    year_stats,
)
from meteoregik.db.schema import computed_indicators

PARAMETER = "T"


def _upsert(conn: Connection, station_id: int, period_type: str, period_start: date, period_end: date, stat_type: str, value: float | None) -> None:
    stmt = sqlite_insert(computed_indicators).values(
        station_id=station_id,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        parameter=PARAMETER,
        stat_type=stat_type,
        value=value,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            computed_indicators.c.station_id,
            computed_indicators.c.period_type,
            computed_indicators.c.period_start,
            computed_indicators.c.period_end,
            computed_indicators.c.parameter,
            computed_indicators.c.stat_type,
        ],
        set_={"value": value},
    )
    conn.execute(stmt)


def _store(engine: Engine, station_id: int, period_type: str, period_start: date, period_end: date, stats: Stats) -> None:
    with engine.begin() as conn:
        for stat_type in ("avg", "min", "max"):
            _upsert(conn, station_id, period_type, period_start, period_end, stat_type, stats[stat_type])


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
    station_id: int,
    start: date,
    end: date,
    progress_callback: Callable[[date], None] | None = None,
) -> None:
    """progress_callback(day), если передан, вызывается после пересчёта
    каждого дня диапазона — вызывающий код (UI) использует его, чтобы
    показать текущую дату и обновить полосу прогресса."""
    day = start
    while day <= end:
        _store(engine, station_id, "day", day, day, daily_stats(engine, station_id, day))
        if progress_callback is not None:
            progress_callback(day)
        day += timedelta(days=1)

    for decade_start, decade_end in _decades_in_range(start, end):
        stats = decade_stats(engine, station_id, decade_start, decade_end)
        _store(engine, station_id, "decade", decade_start, decade_end, stats)

    for year, month in _months_in_range(start, end):
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        stats = month_stats(engine, station_id, year, month)
        _store(engine, station_id, "month", month_start, month_end, stats)

    for year in range(start.year, end.year + 1):
        stats = year_stats(engine, station_id, year)
        _store(engine, station_id, "year", date(year, 1, 1), date(year, 12, 31), stats)
