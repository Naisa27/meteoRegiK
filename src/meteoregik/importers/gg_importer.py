"""Импортёр формата ГГ (среднесуточные показатели) -> gg_readings.

Источник: "Индекс ВМО, Год, Месяц, День, Tavg, Tmax, RRR, Tmin". В файле нет
названия станции — matching.find_or_create_station(wmo_index) гарантирует,
что запись в stations существует (создаёт новую станцию и запрашивает у
пользователя название, если не найдена); gg_readings хранит wmo_index
напрямую (без FK на stations.id — см. plan.md, п.11 решений), station_name
резолвится через stations.name по wmo_index и кэшируется для читаемости.

Запрос имени для новой станции — через опциональный callback name_resolver
(wmo_index -> name), который передаёт вызывающий код (UI: экран "Импорт").
Без callback'а и без существующей станции с таким wmo_index импорт падает с
понятной ошибкой, а не тихо пропускает строки.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine

from meteoregik.db.schema import gg_readings, stations
from meteoregik.importers.base import ImportReport, Importer
from meteoregik.stations.matching import find_or_create_station


def _clean(value: object) -> object | None:
    return None if pd.isna(value) else value


def _ensure_station(
    conn: Connection, wmo_index: int, name_resolver: Callable[[int], str] | None
) -> None:
    try:
        find_or_create_station(conn, wmo_index)
    except ValueError:
        if name_resolver is None:
            raise ValueError(
                f"Станция с wmo_index={wmo_index} не найдена в справочнике — "
                "нужно имя для создания новой записи (name_resolver не передан)"
            ) from None
        name = name_resolver(wmo_index)
        find_or_create_station(conn, wmo_index, name=name)


class GgImporter(Importer):
    def __init__(self, engine: Engine, name_resolver: Callable[[int], str] | None = None) -> None:
        self.engine = engine
        self.name_resolver = name_resolver

    def import_file(self, file_path: str) -> ImportReport:
        df = pd.read_excel(file_path)
        source_file = os.path.basename(file_path)

        parsed_rows = []
        for record in df.to_dict(orient="records"):
            wmo_index = int(record["Индекс ВМО"])
            obs_date = date(int(record["Год"]), int(record["Месяц"]), int(record["День"]))
            parsed_rows.append(
                {
                    "wmo_index": wmo_index,
                    "obs_date": obs_date,
                    "t_avg": _clean(record.get("Tavg")),
                    "t_min": _clean(record.get("Tmin")),
                    "t_max": _clean(record.get("Tmax")),
                    "rrr_precipitation": _clean(record.get("RRR")),
                }
            )

        if not parsed_rows:
            return ImportReport(None, None, None, None, 0, 0, 0)

        period_start = min(r["obs_date"] for r in parsed_rows)
        period_end = max(r["obs_date"] for r in parsed_rows)

        with self.engine.begin() as conn:
            station_names: dict[int, str] = {}
            for wmo_index in {r["wmo_index"] for r in parsed_rows}:
                _ensure_station(conn, wmo_index, self.name_resolver)
                station_names[wmo_index] = conn.execute(
                    select(stations.c.name).where(stations.c.wmo_index == wmo_index)
                ).scalar_one()

            rows_to_insert = [
                {
                    "wmo_index": r["wmo_index"],
                    "station_name": station_names[r["wmo_index"]],
                    "obs_date": r["obs_date"],
                    "t_avg": r["t_avg"],
                    "t_min": r["t_min"],
                    "t_max": r["t_max"],
                    "rrr_precipitation": r["rrr_precipitation"],
                    "source_file": source_file,
                }
                for r in parsed_rows
            ]

            touched_wmo_indexes = {r["wmo_index"] for r in parsed_rows}
            existing_keys = set(
                conn.execute(
                    select(gg_readings.c.wmo_index, gg_readings.c.obs_date).where(
                        gg_readings.c.wmo_index.in_(touched_wmo_indexes)
                    )
                ).all()
            )

            new_rows = [
                row
                for row in rows_to_insert
                if (row["wmo_index"], row["obs_date"]) not in existing_keys
            ]

            if new_rows:
                conn.execute(gg_readings.insert(), new_rows)

        primary_wmo_index = parsed_rows[0]["wmo_index"]
        return ImportReport(
            wmo_index=primary_wmo_index,
            station_name=station_names[primary_wmo_index],
            period_start=period_start,
            period_end=period_end,
            total_rows=len(parsed_rows),
            imported_rows=len(new_rows),
            skipped_rows=len(parsed_rows) - len(new_rows),
        )
