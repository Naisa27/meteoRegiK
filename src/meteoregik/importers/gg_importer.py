"""Импортёр формата ГГ (среднесуточные показатели) -> gg_readings.

Источник: "Индекс ВМО, Год, Месяц, День, Tavg, Tmax, RRR, Tmin". В файле нет
названия станции — matching.require_stations проверяет, что все станции
файла есть в справочнике: если какой-то нет, импорт останавливается
(StationNotFoundError со всеми ненайденными индексами), ничего не
загружается и в справочник ничего не добавляется. gg_readings хранит
wmo_index напрямую (без FK на stations.id — см. plan.md, п.11 решений),
station_name берётся из stations.name по wmo_index и кэшируется для
читаемости.

Строка без единого значимого значения (все четыре — Tavg, Tmax, Tmin, RRR —
пусты) не загружается и не участвует ни в периоде отчёта, ни в создании
проверке станции (пустая строка с неизвестным индексом импорт не
останавливает); их количество попадает в отчёт (rows_without_data).
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import gg_readings
from meteoregik.importers.base import ImportFormatError, ImportReport, Importer
from meteoregik.stations.matching import require_stations

REQUIRED_COLUMNS = ("Индекс ВМО", "Год", "Месяц", "День")
# Где в файле искать индекс — для сообщения StationNotFoundError
INDEX_LOCATION = "в столбце «Индекс ВМО»"
VALUE_FIELDS = ("t_avg", "t_min", "t_max", "rrr_precipitation")


def _clean(value: object) -> object | None:
    return None if pd.isna(value) else value


def _check_format(df: pd.DataFrame, file_path: str) -> None:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ImportFormatError(
            f"«{file_path}» не похож на файл формата ГГ: в нём нет столбцов "
            f"{', '.join(missing)}. Похоже, выбран неверный формат для импорта "
            "этого файла — проверьте выбор rp5/ГГ."
        )


class GgImporter(Importer):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def import_file(self, file_path: str) -> ImportReport:
        df = pd.read_excel(file_path)
        _check_format(df, file_path)
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

        total_rows = len(parsed_rows)
        parsed_rows = [r for r in parsed_rows if any(r[field] is not None for field in VALUE_FIELDS)]
        rows_without_data = total_rows - len(parsed_rows)

        if not parsed_rows:
            return ImportReport(None, None, None, None, total_rows, 0, 0, rows_without_data)

        period_start = min(r["obs_date"] for r in parsed_rows)
        period_end = max(r["obs_date"] for r in parsed_rows)

        with self.engine.begin() as conn:
            station_names = require_stations(
                conn, {r["wmo_index"] for r in parsed_rows}, index_location=INDEX_LOCATION
            )

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
            total_rows=total_rows,
            imported_rows=len(new_rows),
            skipped_rows=len(parsed_rows) - len(new_rows),
            rows_without_data=rows_without_data,
        )
