"""Импортёр формата rp5 (срочные наблюдения) -> rp5_readings.

Из первой строки шапки (между последними "#"-строками) регэкспом парсится и
название станции, и WMO_ID:
    "Метеостанция (?P<name>.+?), Россия, WMO_ID=(?P<wmo>\\d+),"
Далее matching.require_stations проверяет, что станция с таким индексом есть
в справочнике: если нет — импорт останавливается (StationNotFoundError), в
справочник ничего не добавляется. Название из шапки сверяется с названием
станции в справочнике (matching.confirm_station_name): если они разные,
загрузку по индексу подтверждает пользователь — иначе опечатка в индексе
записала бы данные чужой станции. Сама rp5_readings хранит wmo_index и
station_name напрямую, без FK station_id (см. plan.md, п.11 решений);
station_name — название из справочника: после подтверждённого расхождения
название из файла относилось бы уже не к той станции, которой принадлежат
данные.

Столбцы сопоставляются по имени заголовка (RP5_COLUMN_MAP), не по позиции —
состав и порядок столбцов зависит от конкретной выгрузки с rp5.ru (см.
plan.md, "Формат rp5"). Первый столбец файла ("Местное время в <Станция>")
всегда дата+время — определяется позиционно, т.к. его название зависит от
имени станции.

Одинаковые записи одного срока загружаются один раз, противоречивые внутри
файла отклоняют весь файл. Для уже сохранённых суток выбирается полный
набор: больше сроков с температурой — новый автоматически, остальные
различия требуют явного выбора пользователя. Смешивания двух наборов нет.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.engine import Engine

from meteoregik.db.schema import rp5_readings
from meteoregik.calculations.pending import mark_pending, mark_range
from meteoregik.importers.base import ImportFormatError, ImportReport, Importer, StationImportReport
from meteoregik.importers.rows import new_unique_rows
from meteoregik.importers.rp5_days import DayResolver, plan_days
from meteoregik.stations.matching import StationNameMismatch, confirm_station_name, require_stations

# Где в файле искать индекс — для сообщения StationNotFoundError
INDEX_LOCATION = "в первой строке файла (WMO_ID=…)"
# Где искать и индекс, и название — для сообщения StationNameMismatchError
HEADER_LOCATION = "в первой строке файла"

RP5_HEADER_RE = re.compile(r"Метеостанция\s+(?P<name>.+?),\s*Россия,\s*WMO_ID=(?P<wmo>\d+),")

# rp5.ru: заголовок столбца в файле -> колонка в rp5_readings (см.
# db/schema.py: RP5_PARAMETER_COLUMNS).
RP5_COLUMN_MAP = {
    "T": "T_temperature",
    "Po": "Po_pressure_station",
    "P": "P_pressure_sealevel",
    "Pa": "Pa_pressure_tendency",
    "U": "U_humidity",
    "DD": "DD_wind_direction",
    "Ff": "Ff_wind_speed",
    "ff10": "ff10_wind_gust",
    "ff3": "ff3_wind_gust",
    "N": "N_cloudiness_total",
    "WW": "WW_weather_present",
    "W1": "W1_weather_past1",
    "W2": "W2_weather_past2",
    "Tn": "Tn_temperature_min",
    "Tx": "Tx_temperature_max",
    "Cl": "Cl_clouds_low",
    "Nh": "Nh_cloud_amount",
    "H": "H_cloud_base_height",
    "Cm": "Cm_clouds_mid",
    "Ch": "Ch_clouds_high",
    "VV": "VV_visibility",
    "Td": "Td_dewpoint",
    "RRR": "RRR_precipitation",
    "tR": "tR_precipitation_period",
    "E": "E_soil_state",
    "Tg": "Tg_soil_temp_min",
    "E'": "E2_soil_state_snow",
    "sss": "sss_snow_depth",
}


def _find_header_row(file_path: str) -> int:
    """Строка заголовков — сразу после последней строки, начинающейся с '#'."""
    preview = pd.read_excel(file_path, header=None, nrows=20)
    last_hash_row = -1
    for i, value in enumerate(preview.iloc[:, 0]):
        if isinstance(value, str) and value.strip().startswith("#"):
            last_hash_row = i
    if last_hash_row < 0:
        raise ImportFormatError(
            f"«{file_path}» не похож на файл формата rp5: в нём нет строк шапки, "
            "начинающихся с '#'. Похоже, выбран неверный формат для импорта "
            "этого файла — проверьте выбор rp5/ГГ."
        )
    return last_hash_row + 1


def _parse_station_header(file_path: str) -> tuple[str, int]:
    first_line = pd.read_excel(file_path, header=None, nrows=1).iat[0, 0]
    match = RP5_HEADER_RE.search(str(first_line))
    if not match:
        raise ImportFormatError(
            f"«{file_path}» не похож на файл формата rp5: не удалось найти в шапке "
            "название станции и WMO_ID. Похоже, выбран неверный формат для импорта "
            "этого файла — проверьте выбор rp5/ГГ."
        )
    return match.group("name"), int(match.group("wmo"))


def _clean(value: object) -> object | None:
    return None if pd.isna(value) else value


class Rp5Importer(Importer):
    def __init__(
        self, engine: Engine, confirm_name_mismatch: Callable[[StationNameMismatch], bool] | None = None,
        confirm_day_replacement: DayResolver | None = None,
    ) -> None:
        """confirm_name_mismatch — вопрос пользователю, загружать ли файл по
        индексу, если станция в справочнике названа иначе, чем в файле
        (True — загружать). Без него такое расхождение останавливает импорт."""
        self.engine = engine
        self._confirm_name_mismatch = confirm_name_mismatch
        self._confirm_day_replacement = confirm_day_replacement

    def import_file(self, file_path: str) -> ImportReport:
        name_in_file, wmo_index = _parse_station_header(file_path)
        # До чтения строк: файл за много лет читается долго, а без станции он не нужен
        with self.engine.connect() as conn:
            directory_names = require_stations(
                conn, [wmo_index], index_location=INDEX_LOCATION, name_from_file=name_in_file
            )
        confirm_station_name(
            name_in_file,
            directory_names[wmo_index],
            wmo_index,
            index_location=HEADER_LOCATION,
            confirm=self._confirm_name_mismatch,
        )
        station_name = directory_names[wmo_index] or name_in_file
        header_row = _find_header_row(file_path)
        df = pd.read_excel(file_path, header=header_row)

        datetime_col = df.columns[0]
        source_file = os.path.basename(file_path)

        parsed_rows = []
        for excel_row, record in enumerate(df.to_dict(orient="records"), start=header_row + 2):
            dt = datetime.strptime(str(record[datetime_col]).strip(), "%d.%m.%Y %H:%M")
            row = {
                "wmo_index": wmo_index,
                "station_name": station_name,
                "obs_date": dt.date(),
                "obs_time": dt.time(),
                "source_file": source_file,
                "_excel_row": excel_row,
            }
            for source_col, target_col in RP5_COLUMN_MAP.items():
                if source_col in record:
                    row[target_col] = _clean(record[source_col])
            parsed_rows.append(row)

        if not parsed_rows:
            return ImportReport(wmo_index, station_name, None, None, 0, 0, 0)

        period_start = min(r["obs_date"] for r in parsed_rows)
        period_end = max(r["obs_date"] for r in parsed_rows)

        with self.engine.begin() as conn:
            # Проверяем и повторы известных ключей: суточный набор может
            # заменить старый, поэтому кандидат должен быть однозначным.
            unique_rows, duplicates = new_unique_rows(
                parsed_rows,
                set(),
                ("wmo_index", "obs_date", "obs_time"),
                tuple(RP5_COLUMN_MAP.values()),
                frozenset(column.name for column in rp5_readings.c if column.type.python_type is str),
            )
            text_fields = frozenset(column.name for column in rp5_readings.c if column.type.python_type is str)
            plan = plan_days(conn, wmo_index, station_name, unique_rows, tuple(RP5_COLUMN_MAP.values()),
                             text_fields, self._confirm_day_replacement)
            for day in plan.replace_dates:
                conn.execute(delete(rp5_readings).where(
                    rp5_readings.c.wmo_index == wmo_index, rp5_readings.c.obs_date == day,
                ))
            if plan.rows:
                conn.execute(rp5_readings.insert(), plan.rows)
            if plan.old_main != plan.new_main and plan.archive_start is not None and plan.archive_end is not None:
                # Смена основного осадочного ряда влияет и на неизменённые дни.
                # Их ручные дневные решения сохраняет обычный conflict_resolver.
                mark_range(conn, wmo_index, plan.archive_start, plan.archive_end, auto_accept=False)
            mark_pending(conn, wmo_index, plan.accepted_dates, auto_accept=True)

        new_period_start = min(plan.accepted_dates) if plan.accepted_dates else None
        new_period_end = max(plan.accepted_dates) if plan.accepted_dates else None
        station_report = StationImportReport(
            wmo_index=wmo_index, station_name=station_name, period_start=period_start, period_end=period_end,
            imported_rows=len(plan.rows), skipped_rows=len(parsed_rows) - len(plan.rows),
            new_period_start=new_period_start, new_period_end=new_period_end,
            duplicates_in_file=sum(duplicates.values()), replaced_rows=plan.replaced_rows,
        )

        return ImportReport(
            wmo_index=wmo_index,
            station_name=station_name,
            period_start=period_start,
            period_end=period_end,
            total_rows=len(parsed_rows),
            imported_rows=len(plan.rows),
            skipped_rows=len(parsed_rows) - len(plan.rows),
            stations=(station_report,),
            duplicates_in_file=sum(duplicates.values()),
            replaced_rows=plan.replaced_rows,
        )
