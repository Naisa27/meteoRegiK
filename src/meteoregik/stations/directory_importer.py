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
файла — no-op). Станции/регионы, которых нет в файле, не удаляются (в
справочнике удаления нет вообще — решение пользователя 2026-09-25), а
примечания (stations.notes) импорт не трогает — их в файле нет.

Оба импортёра возвращают DirectoryImportReport (сколько добавлено и сколько
обновлено) — для отчёта на экране "Справочник станций". Файл не того вида
(нет нужных столбцов, пустой индекс/код/название) даёт DirectoryFormatError
с понятным текстом, а не KeyError/ValueError pandas. Весь файл проверяется
до записи в БД, так что неверный файл ничего не меняет.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from meteoregik.db.schema import ru_regions, stations

STATIONS_COLUMNS = ("wmo", "name", "country", "state", "lat", "lon")
REGIONS_COLUMNS = ("code", "region")
_STATION_FIELDS = ("name", "country", "state", "lat", "lon")


class DirectoryFormatError(ValueError):
    """Файл не похож на справочник станций/регионов."""


@dataclass(frozen=True)
class DirectoryImportReport:
    inserted: int = 0
    updated: int = 0


def _read_checked(file_path: str, required: tuple[str, ...], what: str) -> pd.DataFrame:
    df = pd.read_excel(file_path, sheet_name=0)
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise DirectoryFormatError(
            f"Файл не похож на {what}: на первом листе нет столбцов {', '.join(missing)}. "
            f"Нужны столбцы: {', '.join(required)}."
        )
    return df


def _required_int(value: object, column: str, excel_row: int) -> int:
    try:
        if value is None or pd.isna(value):
            raise ValueError
        return int(value)
    except (TypeError, ValueError):
        raise DirectoryFormatError(
            f"Строка {excel_row}: в столбце {column} должно быть целое число, а там «{value}»."
        ) from None


def _required_text(value: object, what: str, excel_row: int) -> str:
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()
    if not text:
        raise DirectoryFormatError(f"Строка {excel_row}: не указано {what}.")
    return text


def _clean_state(raw_state: object) -> str | None:
    if raw_state is None or (isinstance(raw_state, float) and pd.isna(raw_state)):
        return None
    text = str(raw_state).strip()
    return None if text == "" or text.lower() == "none" else text


def import_stations_directory(engine: Engine, file_path: str) -> DirectoryImportReport:
    """Заливает/обновляет stations из файла вида
    id/wmo/name/country/state/lat/lon (id не нужен и игнорируется)."""
    df = _read_checked(file_path, STATIONS_COLUMNS, "справочник станций")
    records = []
    for offset, row in enumerate(df.itertuples(index=False)):
        excel_row = offset + 2  # строка 1 — заголовки
        records.append(
            {
                "wmo_index": _required_int(row.wmo, "wmo", excel_row),
                "name": _required_text(row.name, "название станции", excel_row),
                "country": None if pd.isna(row.country) else str(row.country).strip(),
                "state": _clean_state(row.state),
                "lat": None if pd.isna(row.lat) else float(row.lat),
                "lon": None if pd.isna(row.lon) else float(row.lon),
            }
        )

    inserted = updated = 0
    with engine.begin() as conn:
        for record in records:
            existing = conn.execute(
                select(stations.c.id, *(stations.c[field] for field in _STATION_FIELDS)).where(
                    stations.c.wmo_index == record["wmo_index"]
                )
            ).mappings().one_or_none()

            if existing is None:
                conn.execute(stations.insert().values(**record))
                inserted += 1
            elif any(existing[field] != record[field] for field in _STATION_FIELDS):
                conn.execute(
                    stations.update()
                    .where(stations.c.id == existing["id"])
                    .values(
                        **{field: record[field] for field in _STATION_FIELDS},
                        updated_at=func.current_timestamp(),
                    )
                )
                updated += 1

    return DirectoryImportReport(inserted=inserted, updated=updated)


def import_ru_regions(engine: Engine, file_path: str) -> DirectoryImportReport:
    """Заливает/обновляет ru_regions из файла code/region."""
    df = _read_checked(file_path, REGIONS_COLUMNS, "справочник регионов")
    records = []
    for offset, row in enumerate(df.itertuples(index=False)):
        excel_row = offset + 2
        records.append(
            (_required_int(row.code, "code", excel_row), _required_text(row.region, "название региона", excel_row))
        )

    inserted = updated = 0
    with engine.begin() as conn:
        for code, region in records:
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
                updated += 1

    return DirectoryImportReport(inserted=inserted, updated=updated)
