"""Снег rp5: нулевые пропуски только внутри архива и производные показатели."""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from datetime import date, timedelta
from math import isfinite

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Connection

from meteoregik.calculations.temperature import decade_bounds
from meteoregik.db.schema import computed_indicators, rp5_readings

PARAMETERS = (("S", "sum"), ("SNOW_DEPTH", "max"), ("FROST_LOW_SNOW", "sum"))


def winter_bounds(day: date) -> tuple[date, date]:
    year = day.year if day.month >= 7 else day.year - 1
    return date(year, 7, 1), date(year + 1, 6, 30)


def archive_bounds(conn: Connection, wmo_index: int) -> tuple[date, date] | None:
    start, end = conn.execute(select(func.min(rp5_readings.c.obs_date),
                                     func.max(rp5_readings.c.obs_date)).where(
                                         rp5_readings.c.wmo_index == wmo_index)).one()
    return (start, end) if start is not None else None


def numeric_depth(value: str | float | None) -> float:
    """Словесные обозначения и некорректные числа считаются отсутствием снега."""
    try:
        result = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, result) if isfinite(result) else 0.0


def format_dates(days: Iterable[date]) -> str:
    """Все раздельные серии дат остаются отдельными интервалами."""
    runs: list[tuple[date, date]] = []
    for day in sorted(set(days)):
        if runs and day == runs[-1][1] + timedelta(days=1):
            runs[-1] = runs[-1][0], day
        else:
            runs.append((day, day))
    return ", ".join(start.strftime("%d.%m.%Y") if start == end else
                     f"{start:%d.%m.%Y}–{end:%d.%m.%Y}" for start, end in runs) or "—"


def period_bounds(day: date) -> tuple[tuple[str, date, date], ...]:
    return (("decade", *decade_bounds(day)),
            ("month", date(day.year, day.month, 1),
             date(day.year, day.month, monthrange(day.year, day.month)[1])),
            ("year", date(day.year, 1, 1), date(day.year, 12, 31)),
            ("winter_season", *winter_bounds(day)))


def rebuild_station(conn: Connection, wmo_index: int) -> int:
    """Перестроить только снег в транзакции вызывающего кода, сохраняя решения T/R.

    Полный диапазон нужен при расширении или сокращении архива: ранее пустые
    дни становятся нулевыми, а вышедшие за границу перестают быть наблюдениями.
    """
    table = computed_indicators
    conn.execute(delete(table).where(table.c.wmo_index == wmo_index,
                                    table.c.parameter.in_([key[0] for key in PARAMETERS])))
    bounds = archive_bounds(conn, wmo_index)
    if bounds is None:
        return 0
    depths: dict[date, float] = {}
    for day, value in conn.execute(select(rp5_readings.c.obs_date, rp5_readings.c.sss_snow_depth).where(
            rp5_readings.c.wmo_index == wmo_index)):
        depths[day] = max(depths.get(day, 0.0), numeric_depth(value))
    temperatures = dict(conn.execute(select(table.c.period_start, table.c.value).where(
        table.c.wmo_index == wmo_index, table.c.parameter == "T", table.c.stat_type == "avg",
        table.c.period_type == "day", table.c.value.is_not(None))).all())
    rows: list[dict[str, object]] = []
    aggregates: dict[tuple[str, date, date, str, str], float] = {}
    start, end = bounds
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        height = depths.get(day, 0.0)
        values = {("S", "sum"): float(height > 0), ("SNOW_DEPTH", "max"): height}
        if day in temperatures:
            values[("FROST_LOW_SNOW", "sum")] = float(temperatures[day] <= -20 and height <= 5)
        for (parameter, stat), value in values.items():
            rows.append(dict(wmo_index=wmo_index, parameter=parameter, stat_type=stat,
                             period_type="day", period_start=day, period_end=day, value=value))
            for kind, first, last in period_bounds(day):
                key = kind, first, last, parameter, stat
                previous = aggregates.get(key, 0.0)
                aggregates[key] = max(previous, value) if stat == "max" else previous + value
    rows.extend(dict(wmo_index=wmo_index, period_type=kind, period_start=first,
                     period_end=last, parameter=parameter, stat_type=stat, value=value)
                for (kind, first, last, parameter, stat), value in aggregates.items())
    for offset in range(0, len(rows), 1000):
        conn.execute(table.insert(), rows[offset:offset + 1000])
    return (end - start).days + 1
