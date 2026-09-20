"""Списки станций для выпадающих списков интерфейса: "Название, Регион (индекс
ВМО)", например "Сухиничи, Калужская область (27707)".

Регион есть только у российских станций: `stations.state` при
`country='ru'` — код региона, совпадающий с `ru_regions.code` (у остальных
стран `state` — "<country><номер>", расшифровки нет, а cast такого текста в
число дал бы 0 и ложно совпал бы с кодом 0 — поэтому в условии join'а
обязателен `country = 'ru'`). Без региона подпись — "Название (индекс)".
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Table, and_, cast, func, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import ru_regions, stations


@dataclass(frozen=True)
class StationChoice:
    wmo_index: int
    name: str | None
    region: str | None = None

    @property
    def label(self) -> str:
        if not self.name:
            return str(self.wmo_index)
        title = f"{self.name}, {self.region}" if self.region else self.name
        return f"{title} ({self.wmo_index})"


_REGION_JOIN = and_(
    stations.c.country == "ru",
    ru_regions.c.code == cast(stations.c.state, Integer),
)


def _sorted(choices: list[StationChoice]) -> list[StationChoice]:
    return sorted(choices, key=lambda c: ((c.name or "").casefold(), c.wmo_index))


def load_directory_stations(engine: Engine) -> list[StationChoice]:
    """Все станции справочника (с индексом ВМО) — для добавления строки."""
    stmt = (
        select(stations.c.wmo_index, stations.c.name, ru_regions.c.region)
        .select_from(stations.outerjoin(ru_regions, _REGION_JOIN))
        .where(stations.c.wmo_index.is_not(None))
    )
    with engine.connect() as conn:
        return _sorted([StationChoice(*row) for row in conn.execute(stmt)])


def load_stations_in_table(engine: Engine, table: Table) -> list[StationChoice]:
    """Станции, реально присутствующие в таблице формата (rp5_readings/
    gg_readings) — для фильтра просмотра. Название — из справочника, а если
    станции там нет — кэшированное station_name из самой таблицы."""
    present = (
        select(table.c.wmo_index, func.max(table.c.station_name).label("cached_name"))
        .where(table.c.wmo_index.is_not(None))
        .group_by(table.c.wmo_index)
        .subquery()
    )
    stmt = select(
        present.c.wmo_index,
        func.coalesce(stations.c.name, present.c.cached_name),
        ru_regions.c.region,
    ).select_from(
        present.outerjoin(stations, stations.c.wmo_index == present.c.wmo_index).outerjoin(
            ru_regions, _REGION_JOIN
        )
    )
    with engine.connect() as conn:
        return _sorted([StationChoice(*row) for row in conn.execute(stmt)])
