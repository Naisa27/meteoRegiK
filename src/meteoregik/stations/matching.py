"""Единый поиск/создание станции для rp5 и ГГ — по wmo_index, не по названию
(см. plan.md, п.8 решений)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Connection

from meteoregik.db.schema import stations


def find_or_create_station(conn: Connection, wmo_index: int, name: str | None = None) -> int:
    """Возвращает stations.id для данного wmo_index. Если станции нет в
    справочнике — создаёт новую запись (name обязателен для rp5; для ГГ, если
    name не передан, вызывающий код должен запросить его у пользователя перед
    вызовом)."""
    existing_id = conn.execute(
        select(stations.c.id).where(stations.c.wmo_index == wmo_index)
    ).scalar_one_or_none()
    if existing_id is not None:
        return existing_id

    if not name:
        raise ValueError(
            f"Станция с wmo_index={wmo_index} не найдена в справочнике, "
            "а name не передан — нужно запросить название у пользователя"
        )

    result = conn.execute(stations.insert().values(name=name, wmo_index=wmo_index))
    return result.inserted_primary_key[0]
