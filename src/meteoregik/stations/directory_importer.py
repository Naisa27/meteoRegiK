"""Массовая заливка/обновление справочников stations и ru_regions из файлов
(метеостанции_с_координатами.xlsx, Регионы_РФ.xlsx) — один раз на этапе
создания проекта, не при сборке (см. plan.md, п.10 решений). Тем же
импортёром пользователь позже может обновить справочник через интерфейс
"Справочник станций".

Дедуп: stations — по wmo_index, ru_regions — по code. Собственный id из
исходного файла игнорируется — PK stations остаётся автоинкрементом.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine


def import_stations_directory(engine: Engine, file_path: str) -> int:
    """Заливает/обновляет stations из файла вида
    id/wmo/name/country/state/lat/lon. Возвращает число вставленных строк."""
    raise NotImplementedError


def import_ru_regions(engine: Engine, file_path: str) -> int:
    """Заливает/обновляет ru_regions из файла code/region. Возвращает число
    вставленных строк."""
    raise NotImplementedError
