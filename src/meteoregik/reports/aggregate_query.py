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
from bisect import bisect_left, bisect_right
from datetime import date
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.engine import Engine, Row

from meteoregik.db.schema import computed_indicators, rp5_readings
from meteoregik.reports.parameters import ParamKey

class Stats(dict[ParamKey, float | None]):
    """Числа и пояснения полноты, общие для экранной и Excel-раскладки."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: dict[ParamKey, str] = {}


@dataclass(frozen=True)
class AggregateRow:
    period_type: str
    period_start: date
    period_end: date
    parameter: str
    stat_type: str
    value: float | None
    note: str = ""

PeriodType = str  # "day" | "decade" | "month" | "year"

DETAILED_PERIOD_TYPES: tuple[PeriodType, ...] = ("year", "month", "decade", "day")
MONTHLY_PERIOD_TYPES: tuple[PeriodType, ...] = ("year", "month")
YEARLY_PERIOD_TYPES: tuple[PeriodType, ...] = ("year",)


@dataclass(frozen=True)
class MonthSlice:
    """Данные одного месяца одного года: показатели самого месяца, трёх
    декад и отдельных дней (только реально встретившиеся в выборке)."""

    month_values: Stats = field(default_factory=Stats)
    decade_values: dict[int, Stats] = field(default_factory=dict)  # 1..3
    day_values: dict[int, Stats] = field(default_factory=dict)  # 1..31


@dataclass(frozen=True)
class YearSlice:
    """Данные одного года: показатели самого года и (для режима "Подробно")
    вложенные месяцы — только реально встретившиеся в выборке."""

    year: int
    year_values: Stats = field(default_factory=Stats)
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
            ).where(
                computed_indicators.c.wmo_index == wmo_index,
                # Без сезонов: зима начинается 1 декабря прошлого года, и
                # архив с января 2015 "начинался" бы с декабря 2014
                computed_indicators.c.period_type.in_(DETAILED_PERIOD_TYPES),
            )
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
) -> Sequence[AggregateRow]:
    """Плоская выборка computed_indicators по станции/периоду/показателям/
    уровням агрегации — вход для group_detailed/group_monthly/group_yearly."""
    from meteoregik.calculations.freshness import ensure_computed_fresh
    ensure_computed_fresh(engine, [wmo_index])
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
        rows = conn.execute(stmt).all()
        if not params.intersection(("S", "SNOW_DEPTH", "FROST_LOW_SNOW")):
            return [AggregateRow(*row) for row in rows]
        first, last = conn.execute(
            select(func.min(rp5_readings.c.obs_date), func.max(rp5_readings.c.obs_date))
            .where(rp5_readings.c.wmo_index == wmo_index)
        ).one()
        temperature_days = sorted(conn.execute(
            select(computed_indicators.c.period_start).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.parameter == "T",
                computed_indicators.c.stat_type == "avg",
                computed_indicators.c.value.is_not(None),
            )
        ).scalars())
        frost_days = sorted(conn.execute(
            select(computed_indicators.c.period_start).where(
                computed_indicators.c.wmo_index == wmo_index,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.parameter == "FROST_LOW_SNOW",
                computed_indicators.c.stat_type == "sum",
                computed_indicators.c.value.is_not(None),
            )
        ).scalars())
    result: list[AggregateRow] = []
    for row in rows:
        notes: list[str] = []
        if row.parameter in ("S", "SNOW_DEPTH", "FROST_LOW_SNOW") and first is not None and last is not None:
            available_start, available_end = max(first, row.period_start), min(last, row.period_end)
            if available_start > row.period_start or available_end < row.period_end:
                notes.append(f"Доступная часть периода: {available_start:%d.%m.%Y}–{available_end:%d.%m.%Y}.")
            if row.parameter == "FROST_LOW_SNOW":
                temperature_left = bisect_left(temperature_days, available_start)
                temperature_right = bisect_right(temperature_days, available_end)
                frost_left = bisect_left(frost_days, available_start)
                frost_right = bisect_right(frost_days, available_end)
                if (temperature_days[temperature_left:temperature_right]
                        != frost_days[frost_left:frost_right]):
                    raise RuntimeError(
                        "Расчёт сильных морозов при малоснежье отсутствует или неполон. "
                        "Повторно импортируйте файл этой станции для восстановления расчётов."
                    )
                count = frost_right - frost_left
                expected = (available_end - available_start).days + 1
                if count < expected:
                    notes.append(f"Температура известна за {count} из {expected} дней; остальные дни не оценены.")
        result.append(AggregateRow(*row, note=" ".join(notes)))
    return result


def _store(stats: Stats, row: AggregateRow | Row) -> None:
    key = (row.parameter, row.stat_type)
    stats[key] = row.value
    note = getattr(row, "note", "")
    if note:
        stats.notes[key] = note


def group_detailed(rows: Sequence[AggregateRow | Row]) -> list[YearSlice]:
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
            _store(year_slice(row.period_start.year).year_values, row)
        elif row.period_type == "month":
            _store(month_slice(row.period_start.year, row.period_start.month).month_values, row)
        elif row.period_type == "decade":
            month = month_slice(row.period_start.year, row.period_start.month)
            _store(month.decade_values.setdefault(_decade_of(row.period_start), Stats()), row)
        elif row.period_type == "day":
            month = month_slice(row.period_start.year, row.period_start.month)
            _store(month.day_values.setdefault(row.period_start.day, Stats()), row)

    return [years[year] for year in sorted(years)]


def group_monthly(rows: Sequence[AggregateRow | Row]) -> list[YearSlice]:
    """Разбор для режима "Помесячно" — годы с month_values по каждому
    месяцу, без decade/day (MonthSlice.decade_values/day_values остаются
    пустыми)."""
    years: dict[int, YearSlice] = {}

    def year_slice(year: int) -> YearSlice:
        return years.setdefault(year, YearSlice(year=year))

    for row in rows:
        key: ParamKey = (row.parameter, row.stat_type)
        if row.period_type == "year":
            _store(year_slice(row.period_start.year).year_values, row)
        elif row.period_type == "month":
            slice_ = year_slice(row.period_start.year)
            month = slice_.months.setdefault(row.period_start.month, MonthSlice())
            _store(month.month_values, row)

    return [years[year] for year in sorted(years)]


def group_yearly(rows: Sequence[AggregateRow | Row]) -> dict[int, Stats]:
    """Разбор для режима "По годам" — просто год -> показатели года."""
    result: dict[int, Stats] = {}
    for row in rows:
        if row.period_type != "year":
            continue
        _store(result.setdefault(row.period_start.year, Stats()), row)
    return result
