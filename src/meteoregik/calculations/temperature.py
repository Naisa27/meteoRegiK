"""Расчёт температурных показателей (среднесуточная/декадная/месячная/годовая
ср./мин./макс.) — см. раздел "Доменные правила" в .claude/docs/plan.md.

Декады: 1-10, 11-20, 21-последнее число месяца (третий интервал переменной
длины). Среднесуточная температура = сумма доступных сроков / число
доступных сроков (не жёстко на 8 — сроки могут отсутствовать).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.engine import Engine


def daily_stats(engine: Engine, station_id: int, day: date) -> dict[str, float]:
    """avg/min/max температуры за сутки по доступным срокам rp5 (или напрямую
    из gg_readings, если источник — ГГ)."""
    raise NotImplementedError


def decade_stats(engine: Engine, station_id: int, decade_start: date, decade_end: date) -> dict[str, float]:
    raise NotImplementedError


def month_stats(engine: Engine, station_id: int, year: int, month: int) -> dict[str, float]:
    raise NotImplementedError


def year_stats(engine: Engine, station_id: int, year: int) -> dict[str, float]:
    raise NotImplementedError
