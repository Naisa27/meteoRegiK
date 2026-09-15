"""Пересчёт computed_indicators при правке/добавлении/удалении сырых строк
через интерфейс — только затронутый диапазон (день -> декада -> месяц -> год)
для конкретной станции."""

from __future__ import annotations

from datetime import date

from sqlalchemy.engine import Engine


def recompute_range(engine: Engine, station_id: int, start: date, end: date) -> None:
    raise NotImplementedError
