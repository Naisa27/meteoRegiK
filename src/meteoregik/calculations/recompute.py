"""Пересчёт computed_indicators при правке/добавлении/удалении сырых строк
через интерфейс — только затронутый диапазон (день -> декада -> месяц -> год,
у осадков ещё сезон) для конкретной станции.

Параметров два (корректировка 2026-10-11): температура ("T": avg/min/max) и
осадки ("R": sum, calculations/precipitation.py). Порядок пересчёта у них
общий, различается только то, как считается день и как дни складываются в
период, — это описано в _STAT_TYPES и _PERIOD_STATS, остальной код параметра
не знает.

Декада/месяц/год пересчитываются целиком по своим границам (не только по
затронутой части [start, end]) — см. calculations/temperature.py. В отличие
от дней (которые всегда считаются заново из rp5_readings/gg_readings),
декада/месяц/год строятся поверх уже сохранённых в computed_indicators
значений дней (_stored_days) — так выбор пользователя при
конфликте на уровне дня корректно поднимается вверх по иерархии периодов.

Разрешение конфликтов: если пересчитанное значение отличается от уже
сохранённого в computed_indicators (например, для одной и той же даты
среднесуточная rp5-агрегация не совпадает со значением из файла ГГ),
вызывающему коду (UI) передаётся ValueConflict через опциональный callback
conflict_resolver.
Без conflict_resolver сохраняется прежнее поведение: новое значение всегда
побеждает.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from meteoregik.calculations.anomalies import mark_stale
from meteoregik.calculations import precipitation
from meteoregik.calculations.temperature import Stats, daily_stats, decade_bounds
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings

PARAMETER_TEMPERATURE = "T"
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
    stat_type: str  # 'avg' | 'min' | 'max' | 'sum'
    old_value: float
    old_source_file: str | None
    new_value: float
    new_source_file: str | None
    parameter: str = PARAMETER_TEMPERATURE  # 'T' | 'R'


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
    conn: Connection, wmo_index: int, parameter: str, period_type: str, period_start: date, period_end: date
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
            computed_indicators.c.parameter == parameter,
            # Отклонение от нормы (stat_type='anomaly') лежит в той же
            # таблице, но пересчёту не принадлежит — calculations/anomalies.py
            computed_indicators.c.stat_type.in_(_STAT_TYPES[parameter]),
        )
    ).all()
    return {row.stat_type: {"value": row.value, "source_file": row.source_file} for row in rows}


def _write(
    conn: Connection,
    wmo_index: int,
    parameter: str,
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
        parameter=parameter,
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


def _delete_stat(
    conn: Connection,
    wmo_index: int,
    parameter: str,
    period_type: str,
    period_start: date,
    period_end: date,
    stat_type: str,
) -> None:
    conn.execute(
        delete(computed_indicators).where(
            computed_indicators.c.wmo_index == wmo_index,
            computed_indicators.c.period_type == period_type,
            computed_indicators.c.period_start == period_start,
            computed_indicators.c.period_end == period_end,
            computed_indicators.c.parameter == parameter,
            computed_indicators.c.stat_type == stat_type,
        )
    )


def _resolve_and_store(
    engine: Engine,
    wmo_index: int,
    parameter: str,
    period_type: str,
    period_start: date,
    period_end: date,
    stats: Stats,
    new_source_file: str | None,
    conflict_resolver: ConflictResolver | None,
) -> None:
    with engine.begin() as conn:
        existing = _existing_stats(conn, wmo_index, parameter, period_type, period_start, period_end)

        def write(stat_type: str, value: float) -> None:
            _write(
                conn, wmo_index, parameter, period_type, period_start, period_end, stat_type, value, new_source_file
            )

        for stat_type in _STAT_TYPES[parameter]:
            new_value = stats[stat_type]
            old = existing.get(stat_type)
            old_value = old["value"] if old else None

            if new_value is None:
                if old is not None:
                    # computed_indicators — производный кэш, а не хранилище
                    # истины: если по станции/периоду больше нет вообще
                    # никаких сырых данных (ни ГГ, ни rp5 — daily_stats уже
                    # проверил оба источника), сохранённое значение стало
                    # осиротевшим и должно исчезнуть вместе с данными
                    # (например, пользователь удалил единственную запись за
                    # день через "Просмотр данных").
                    _delete_stat(conn, wmo_index, parameter, period_type, period_start, period_end, stat_type)
                continue

            if old is None or old_value is None or abs(old_value - new_value) <= _EPSILON:
                write(stat_type, new_value)
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
                    parameter=parameter,
                )
                choice = conflict_resolver(conflict)

            if choice == "new":
                write(stat_type, new_value)
            # choice == "old" — сохранённое значение оставляем как есть


def _stored_days(
    engine: Engine, wmo_index: int, parameter: str, start: date, end: date
) -> dict[str, list[float]]:
    """Сохранённые суточные значения периода по видам статистики. Период
    строится поверх них, а не пересчётом из сырых данных, — чтобы выбор
    пользователя при конфликте на уровне дня учитывался в декаде/месяце/году."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(computed_indicators.c.stat_type, computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.period_start >= start,
                computed_indicators.c.period_start <= end,
                computed_indicators.c.parameter == parameter,
                computed_indicators.c.value.is_not(None),
            )
        ).all()
    values: dict[str, list[float]] = {}
    for stat_type, value in rows:
        values.setdefault(stat_type, []).append(value)
    return values


def _temperature_period(days: dict[str, list[float]]) -> Stats:
    avgs, mins, maxs = days.get("avg", []), days.get("min", []), days.get("max", [])
    return {
        "avg": round(sum(avgs) / len(avgs), 2) if avgs else None,
        "min": min(mins) if mins else None,
        "max": max(maxs) if maxs else None,
    }


def _precipitation_period(days: dict[str, list[float]]) -> Stats:
    return {precipitation.STAT_SUM: precipitation.aggregate(days.get(precipitation.STAT_SUM, []))}


# Виды статистики параметра и то, как суточные значения складываются в период
_STAT_TYPES: dict[str, tuple[str, ...]] = {
    PARAMETER_TEMPERATURE: ("avg", "min", "max"),
    precipitation.PARAMETER: (precipitation.STAT_SUM,),
}
_PERIOD_STATS: dict[str, Callable[[dict[str, list[float]]], Stats]] = {
    PARAMETER_TEMPERATURE: _temperature_period,
    precipitation.PARAMETER: _precipitation_period,
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
    должен вернуть "old" или "new"; без него побеждает новое значение.

    Нормы и отклонения станции (calculations/anomalies.py) здесь не
    пересчитываются — станция только помечается требующей расчёта. Пометка —
    первым делом: если пересчёт оборвётся на середине, станция всё равно не
    останется со старыми отклонениями при новых суточных значениях."""
    with engine.begin() as conn:
        mark_stale(conn, wmo_index)
        # Основной ряд сроков осадков — один на станцию и на весь пересчёт
        main_series = precipitation.main_series(conn, wmo_index)
    day = start
    while day <= end:
        stats = daily_stats(engine, wmo_index, day)
        source_file = None
        if any(v is not None for v in stats.values()):
            source_file = _source_file_for_day(engine, wmo_index, day)
        day_sum, sum_source = precipitation.daily_sum(engine, wmo_index, day, main_series)
        # У каждого параметра свой источник: осадки дня могут быть из rp5 при
        # температуре из ГГ (в ГГ осадки за этот день пусты)
        for parameter, values, source in (
            (PARAMETER_TEMPERATURE, stats, source_file),
            (precipitation.PARAMETER, {precipitation.STAT_SUM: day_sum}, sum_source),
        ):
            _resolve_and_store(engine, wmo_index, parameter, "day", day, day, values, source, conflict_resolver)
        if progress_callback is not None:
            progress_callback(day)
        day += timedelta(days=1)

    periods: list[tuple[str, date, date]] = [
        ("decade", decade_start, decade_end) for decade_start, decade_end in _decades_in_range(start, end)
    ]
    periods += [
        ("month", date(year, month, 1), date(year, month, monthrange(year, month)[1]))
        for year, month in _months_in_range(start, end)
    ]
    periods += [("year", date(year, 1, 1), date(year, 12, 31)) for year in range(start.year, end.year + 1)]
    seasons = [
        (precipitation.PERIOD_SEASON, season_start, season_end)
        for season_start, season_end in precipitation.seasons_in_range(start, end)
    ]
    for period_type, period_start, period_end in periods + seasons:
        for parameter, period_stats in _PERIOD_STATS.items():
            if period_type == precipitation.PERIOD_SEASON and parameter != precipitation.PARAMETER:
                continue  # сезонные значения есть только у осадков
            stats = period_stats(_stored_days(engine, wmo_index, parameter, period_start, period_end))
            _resolve_and_store(
                engine, wmo_index, parameter, period_type, period_start, period_end, stats, None, conflict_resolver
            )
