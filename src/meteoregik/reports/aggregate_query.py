"""Запросы к computed_indicators для экрана "Просмотр агрегатных данных" и
разбор плоского результата в year/month/decade/day срезы, из которых
reports/grid.py и reports/chart_data.py строят готовую раскладку.

Фильтр периода — по ПЕРЕСЕЧЕНИЮ [date_from, date_to] с [period_start,
period_end] хранимой строки, не по строгому вхождению: в computed_indicators
нет значений "за кусок месяца" (decade/month/year считаются только целиком —
см. calculations/temperature.py), поэтому decade/month/year, лишь частично
попадающие в выбранный диапазон, всё равно возвращаются целиком. У day
period_start == period_end, так что для дней пересечение равносильно
строгому вхождению — период не "протекает" за выбранные границы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.engine import Engine, Row

from meteoregik.db.schema import computed_indicators
from meteoregik.reports.parameters import ParamKey

Stats = dict[ParamKey, float | None]

PeriodType = str  # "day" | "decade" | "month" | "year"

DETAILED_PERIOD_TYPES: tuple[PeriodType, ...] = ("year", "month", "decade", "day")
MONTHLY_PERIOD_TYPES: tuple[PeriodType, ...] = ("year", "month")
YEARLY_PERIOD_TYPES: tuple[PeriodType, ...] = ("year",)


@dataclass(frozen=True)
class MonthSlice:
    """Данные одного месяца одного года: показатели самого месяца, трёх
    декад и отдельных дней (только реально встретившиеся в выборке)."""

    month_values: Stats = field(default_factory=dict)
    decade_values: dict[int, Stats] = field(default_factory=dict)  # 1..3
    day_values: dict[int, Stats] = field(default_factory=dict)  # 1..31


@dataclass(frozen=True)
class YearSlice:
    """Данные одного года: показатели самого года и (для режима "Подробно")
    вложенные месяцы — только реально встретившиеся в выборке."""

    year: int
    year_values: Stats = field(default_factory=dict)
    months: dict[int, MonthSlice] = field(default_factory=dict)


def _decade_of(day: date) -> int:
    """Номер декады (1..3) внутри месяца по дате её начала."""
    return (day.day - 1) // 10 + 1


def station_period_bounds(engine: Engine, wmo_index: int) -> tuple[date, date] | None:
    """Фактические границы данных станции в computed_indicators (min
    period_start .. max period_end) — используется, когда пользователь не
    указал период (означает "весь доступный диапазон"). None, если для
    станции вообще нет посчитанных агрегатов."""
    with engine.connect() as conn:
        row = conn.execute(
            select(
                func.min(computed_indicators.c.period_start),
                func.max(computed_indicators.c.period_end),
            ).where(computed_indicators.c.wmo_index == wmo_index)
        ).one()
    if row[0] is None or row[1] is None:
        return None
    return row[0], row[1]


def fetch_rows(
    engine: Engine,
    wmo_index: int,
    date_from: date,
    date_to: date,
    parameters: list[ParamKey],
    period_types: tuple[PeriodType, ...],
) -> Sequence[Row]:
    """Плоская выборка computed_indicators по станции/периоду/показателям/
    уровням агрегации — вход для group_detailed/group_monthly/group_yearly."""
    params = {p for p, _ in parameters}
    stats = {s for _, s in parameters}
    stmt = select(
        computed_indicators.c.period_type,
        computed_indicators.c.period_start,
        computed_indicators.c.period_end,
        computed_indicators.c.parameter,
        computed_indicators.c.stat_type,
        computed_indicators.c.value,
    ).where(
        computed_indicators.c.wmo_index == wmo_index,
        computed_indicators.c.parameter.in_(params),
        computed_indicators.c.stat_type.in_(stats),
        computed_indicators.c.period_type.in_(period_types),
        computed_indicators.c.period_start <= date_to,
        computed_indicators.c.period_end >= date_from,
    )
    with engine.connect() as conn:
        return conn.execute(stmt).all()


def group_detailed(rows: Sequence[Row]) -> list[YearSlice]:
    """Разбор для режима "Подробно" — годы со вложенными месяцами/декадами/
    днями, по возрастанию года."""
    years: dict[int, YearSlice] = {}

    def year_slice(year: int) -> YearSlice:
        return years.setdefault(year, YearSlice(year=year))

    def month_slice(year: int, month: int) -> MonthSlice:
        slice_ = year_slice(year)
        return slice_.months.setdefault(month, MonthSlice())

    for row in rows:
        key: ParamKey = (row.parameter, row.stat_type)
        if row.period_type == "year":
            year_slice(row.period_start.year).year_values[key] = row.value
        elif row.period_type == "month":
            month_slice(row.period_start.year, row.period_start.month).month_values[key] = row.value
        elif row.period_type == "decade":
            month = month_slice(row.period_start.year, row.period_start.month)
            month.decade_values.setdefault(_decade_of(row.period_start), {})[key] = row.value
        elif row.period_type == "day":
            month = month_slice(row.period_start.year, row.period_start.month)
            month.day_values.setdefault(row.period_start.day, {})[key] = row.value

    return [years[year] for year in sorted(years)]


def group_monthly(rows: Sequence[Row]) -> list[YearSlice]:
    """Разбор для режима "Помесячно" — годы с month_values по каждому
    месяцу, без decade/day (MonthSlice.decade_values/day_values остаются
    пустыми)."""
    years: dict[int, YearSlice] = {}

    def year_slice(year: int) -> YearSlice:
        return years.setdefault(year, YearSlice(year=year))

    for row in rows:
        key: ParamKey = (row.parameter, row.stat_type)
        if row.period_type == "year":
            year_slice(row.period_start.year).year_values[key] = row.value
        elif row.period_type == "month":
            slice_ = year_slice(row.period_start.year)
            month = slice_.months.setdefault(row.period_start.month, MonthSlice())
            month.month_values[key] = row.value

    return [years[year] for year in sorted(years)]


def group_yearly(rows: Sequence[Row]) -> dict[int, Stats]:
    """Разбор для режима "По годам" — просто год -> показатели года."""
    result: dict[int, Stats] = {}
    for row in rows:
        if row.period_type != "year":
            continue
        result.setdefault(row.period_start.year, {})[(row.parameter, row.stat_type)] = row.value
    return result
