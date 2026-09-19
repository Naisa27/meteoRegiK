"""Массовая заливка/обновление справочников stations и ru_regions из файлов
(метеостанции_с_координатами.xlsx, Регионы_РФ.xlsx) — один раз на этапе
создания проекта, не при сборке (см. plan.md, п.10 решений). Тем же
импортёром пользователь позже может обновить справочник через интерфейс
"Справочник станций".

Дедуп: stations — по wmo_index, ru_regions — по code. Собственный id из
исходного файла игнорируется — PK stations остаётся автоинкрементом.
Строка с уже существующим ключом не пропускается, а обновляется, если хотя
бы одно значение изменилось (в отличие от заливки сырых наблюдений rp5/ГГ) —
реимпорт справочника подтягивает изменившиеся координаты/название/регион, но
не трогает updated_at, если данные не изменились (повторная заливка того же
файла — no-op).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import ru_regions, stations


def _clean_state(raw_state: object) -> str | None:
    if raw_state is None or (isinstance(raw_state, float) and pd.isna(raw_state)):
        return None
    text = str(raw_state).strip()
    return None if text == "" or text.lower() == "none" else text


def import_stations_directory(engine: Engine, file_path: str) -> int:
    """Заливает/обновляет stations из файла вида
    id/wmo/name/country/state/lat/lon. Возвращает число вставленных строк."""
    df = pd.read_excel(file_path, sheet_name=0)

    inserted = 0
    with engine.begin() as conn:
        for row in df.itertuples(index=False):
            wmo_index = int(row.wmo)
            name = str(row.name).strip()
            country = None if pd.isna(row.country) else str(row.country).strip()
            state = _clean_state(row.state)
            lat = None if pd.isna(row.lat) else float(row.lat)
            lon = None if pd.isna(row.lon) else float(row.lon)

            existing = conn.execute(
                select(
                    stations.c.id,
                    stations.c.name,
                    stations.c.country,
                    stations.c.state,
                    stations.c.lat,
                    stations.c.lon,
                ).where(stations.c.wmo_index == wmo_index)
            ).mappings().one_or_none()

            if existing is None:
                conn.execute(
                    stations.insert().values(
                        name=name,
                        wmo_index=wmo_index,
                        country=country,
                        state=state,
                        lat=lat,
                        lon=lon,
                    )
                )
                inserted += 1
            elif (
                existing["name"] != name
                or existing["country"] != country
                or existing["state"] != state
                or existing["lat"] != lat
                or existing["lon"] != lon
            ):
                conn.execute(
                    stations.update()
                    .where(stations.c.id == existing["id"])
                    .values(
                        name=name,
                        country=country,
                        state=state,
                        lat=lat,
                        lon=lon,
                        updated_at=func.current_timestamp(),
                    )
                )

    return inserted


def import_ru_regions(engine: Engine, file_path: str) -> int:
    """Заливает/обновляет ru_regions из файла code/region. Возвращает число
    вставленных строк."""
    df = pd.read_excel(file_path, sheet_name=0)

    inserted = 0
    with engine.begin() as conn:
        for row in df.itertuples(index=False):
            code = int(row.code)
            region = str(row.region).strip()

            existing_region = conn.execute(
                select(ru_regions.c.region).where(ru_regions.c.code == code)
            ).scalar_one_or_none()

            if existing_region is None:
                conn.execute(ru_regions.insert().values(code=code, region=region))
                inserted += 1
            elif existing_region != region:
                conn.execute(
                    ru_regions.update()
                    .where(ru_regions.c.code == code)
                    .values(region=region, updated_at=func.current_timestamp())
                )

    return inserted
