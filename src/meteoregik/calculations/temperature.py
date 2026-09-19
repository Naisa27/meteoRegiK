"""Расчёт температурных показателей (среднесуточная/декадная/месячная/годовая
ср./мин./макс.) — см. раздел "Доменные правила" в .claude/docs/plan.md.

Декады: 1-10, 11-20, 21-последнее число месяца (третий интервал переменной
длины). Среднесуточная температура = сумма доступных сроков / число
доступных сроков (не жёстко на 8 — сроки могут отсутствовать).

Декадные/месячные/годовые показатели строятся поверх daily_stats: avg —
среднее суточных средних за доступные дни периода, min/max — минимум/максимум
суточных min/max (стандартная климатологическая агрегация, каждый день
учитывается с равным весом независимо от числа доступных в нём сроков).
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import gg_readings, rp5_readings

Stats = dict[str, float | None]

_EMPTY_STATS: Stats = {"avg": None, "min": None, "max": None}
_ROUND_DIGITS = 2


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _ROUND_DIGITS)


def decade_bounds(day: date) -> tuple[date, date]:
    """Границы декады, содержащей day: 1-10, 11-20, 21-последнее число месяца."""
    if day.day <= 10:
        return date(day.year, day.month, 1), date(day.year, day.month, 10)
    if day.day <= 20:
        return date(day.year, day.month, 11), date(day.year, day.month, 20)
    last_day = monthrange(day.year, day.month)[1]
    return date(day.year, day.month, 21), date(day.year, day.month, last_day)


def daily_stats(engine: Engine, wmo_index: int, day: date) -> Stats:
    """avg/min/max температуры за сутки по доступным срокам rp5 (или напрямую
    из gg_readings, если источник — ГГ)."""
    with engine.connect() as conn:
        gg_row = conn.execute(
            select(gg_readings.c.t_avg, gg_readings.c.t_min, gg_readings.c.t_max).where(
                gg_readings.c.wmo_index == wmo_index, gg_readings.c.obs_date == day
            )
        ).mappings().first()
        if gg_row is not None:
            return {
                "avg": _round(gg_row["t_avg"]),
                "min": _round(gg_row["t_min"]),
                "max": _round(gg_row["t_max"]),
            }

        temps = conn.execute(
            select(rp5_readings.c.T_temperature).where(
                rp5_readings.c.wmo_index == wmo_index, rp5_readings.c.obs_date == day
            )
        ).scalars().all()

    values = [t for t in temps if t is not None]
    if not values:
        return dict(_EMPTY_STATS)
    return {
        "avg": _round(sum(values) / len(values)),
        "min": _round(min(values)),
        "max": _round(max(values)),
    }


def _aggregate_days(engine: Engine, wmo_index: int, start: date, end: date) -> Stats:
    avgs: list[float] = []
    mins: list[float] = []
    maxs: list[float] = []
    day = start
    while day <= end:
        stats = daily_stats(engine, wmo_index, day)
        if stats["avg"] is not None:
            avgs.append(stats["avg"])
        if stats["min"] is not None:
            mins.append(stats["min"])
        if stats["max"] is not None:
            maxs.append(stats["max"])
        day += timedelta(days=1)

    return {
        "avg": _round(sum(avgs) / len(avgs)) if avgs else None,
        "min": _round(min(mins)) if mins else None,
        "max": _round(max(maxs)) if maxs else None,
    }


def decade_stats(engine: Engine, wmo_index: int, decade_start: date, decade_end: date) -> Stats:
    return _aggregate_days(engine, wmo_index, decade_start, decade_end)


def month_stats(engine: Engine, wmo_index: int, year: int, month: int) -> Stats:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return _aggregate_days(engine, wmo_index, start, end)


def year_stats(engine: Engine, wmo_index: int, year: int) -> Stats:
    return _aggregate_days(engine, wmo_index, date(year, 1, 1), date(year, 12, 31))
