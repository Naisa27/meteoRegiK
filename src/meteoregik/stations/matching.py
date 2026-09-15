"""Единый поиск/создание станции для rp5 и ГГ — по wmo_index, не по названию
(см. plan.md, п.8 решений)."""

from __future__ import annotations

from sqlalchemy.engine import Connection


def find_or_create_station(conn: Connection, wmo_index: int, name: str | None = None) -> int:
    """Возвращает stations.id для данного wmo_index. Если станции нет в
    справочнике — создаёт новую запись (name обязателен для rp5; для ГГ, если
    name не передан, вызывающий код должен запросить его у пользователя перед
    вызовом)."""
    raise NotImplementedError
