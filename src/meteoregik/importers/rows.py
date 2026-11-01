"""Общая проверка повторов файла до записи и отчёты по станциям без Qt."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, time
from typing import cast

from meteoregik.importers.base import DuplicateKeyConflictError, StationImportReport

ImportRow = dict[str, object]


def _stored_value(value: object, *, text: bool) -> object:
    """Сравниваем значения, которые попадут в SQLite, а не Excel-типы чисел."""
    if value is None:
        return None
    return str(value) if text else float(value)


def new_unique_rows(
    rows: list[ImportRow],
    existing_keys: set[tuple[object, ...]],
    key_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
    text_fields: frozenset[str] = frozenset(),
) -> tuple[list[ImportRow], dict[int, int]]:
    """Сначала проверяет весь набор: конфликт в конце не оставляет первые строки.

    Известные ключи БД пропускаются до сравнения значений: повторная заливка
    не меняет сохранённое и не вызывает новых вопросов о старых наблюдениях.
    _excel_row служит только диагностике, в БД он не передаётся.
    """
    unique: dict[tuple[object, ...], ImportRow] = {}
    duplicates: Counter[int] = Counter()
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        if key in existing_keys:
            continue
        if key in unique:
            first = unique[key]
            # Большие архивы обычно не содержат внутренних повторов: не создаём
            # вторую нормализованную копию всех их измерений ради редкого сравнения.
            stored = tuple(_stored_value(row.get(field), text=field in text_fields) for field in value_fields)
            first_stored = tuple(_stored_value(first.get(field), text=field in text_fields) for field in value_fields)
            if stored != first_stored:
                raise DuplicateKeyConflictError(
                    cast(int, row["wmo_index"]),
                    cast(date, row["obs_date"]),
                    cast(int, first["_excel_row"]),
                    cast(int, row["_excel_row"]),
                    cast(time | None, row.get("obs_time")),
                )
            duplicates[cast(int, row["wmo_index"])] += 1
            continue
        unique[key] = row
    return [
        {field: value for field, value in row.items() if field != "_excel_row"}
        for row in unique.values()
    ], dict(duplicates)


def station_reports(
    rows: list[ImportRow], new_rows: list[ImportRow], duplicates: dict[int, int]
) -> tuple[StationImportReport, ...]:
    """Не теряет станции повторного импорта: по их периоду можно восстановить расчёты."""
    grouped: dict[int, list[ImportRow]] = defaultdict(list)
    grouped_new: dict[int, list[ImportRow]] = defaultdict(list)
    for row in rows:
        grouped[cast(int, row["wmo_index"])].append(row)
    for row in new_rows:
        grouped_new[cast(int, row["wmo_index"])].append(row)
    result = []
    for wmo_index, station_rows in sorted(grouped.items()):
        days = [cast(date, row["obs_date"]) for row in station_rows]
        inserted = grouped_new[wmo_index]
        new_days = [cast(date, row["obs_date"]) for row in inserted]
        result.append(StationImportReport(
            wmo_index=wmo_index,
            station_name=cast(str, station_rows[0]["station_name"]),
            period_start=min(days),
            period_end=max(days),
            imported_rows=len(inserted),
            skipped_rows=len(station_rows) - len(inserted),
            new_period_start=min(new_days) if new_days else None,
            new_period_end=max(new_days) if new_days else None,
            duplicates_in_file=duplicates.get(wmo_index, 0),
        ))
    return tuple(result)
