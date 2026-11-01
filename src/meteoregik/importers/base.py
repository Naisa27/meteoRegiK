"""Общий интерфейс Importer и результат импорта (см. "Экраны UI", п.1 и
"Решения, согласованные с пользователем" в .docs/plan.md).

Для ГГ уже существующие ключи (wmo_index, obs_date) пропускаются. Для rp5
по ключам (wmo_index, obs_date, obs_time) сначала проверяется однозначность
файла, затем выбираются полные суточные наборы по правилам полноты и
подтверждению пользователя. Неизменившиеся наборы повторно не записываются.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time


class ImportFormatError(ValueError):
    """Файл не соответствует ожидаемому формату импорта (rp5/ГГ) — типично,
    когда пользователь выбрал неверный формат в диалоге импорта. Отдельный
    тип нужен, чтобы UI мог показать понятное сообщение вместо голого
    KeyError/ValueError при разборе структуры файла."""


class DuplicateKeyConflictError(ValueError):
    """Новые строки одного ключа противоречат друг другу: выбирать молча нельзя."""

    def __init__(
        self,
        wmo_index: int,
        obs_date: date,
        first_row: int,
        second_row: int,
        obs_time: time | None = None,
    ) -> None:
        self.wmo_index = wmo_index
        self.obs_date = obs_date
        self.obs_time = obs_time
        self.first_row = first_row
        self.second_row = second_row
        moment = obs_date.isoformat()
        if obs_time is not None:
            moment += f" {obs_time:%H:%M}"
        super().__init__(
            f"В файле разные значения для станции ВМО {wmo_index}, дата {moment}: "
            f"строки Excel {first_row} и {second_row}. Исправьте повторяющиеся записи. "
            "Файл не загружен."
        )


@dataclass(frozen=True)
class Rp5DayConflict:
    """Два полных набора сроков за сутки; решение относится к исходным данным."""

    wmo_index: int
    station_name: str
    obs_date: date
    old_times: tuple[time, ...]
    new_times: tuple[time, ...]
    old_stats: dict[tuple[str, str], float | None]
    new_stats: dict[tuple[str, str], float | None]


class Rp5DayChoiceRequiredError(ValueError):
    """Без явного выбора пользователя неоднозначная замена суток запрещена."""

    def __init__(self, conflict: Rp5DayConflict) -> None:
        self.conflict = conflict
        super().__init__(
            f"Для станции ВМО {conflict.wmo_index} за {conflict.obs_date.isoformat()} "
            f"нужен выбор набора сроков: было {len(conflict.old_times)}, "
            f"в новом файле {len(conflict.new_times)}. Файл не загружен."
        )


@dataclass(frozen=True)
class StationImportReport:
    """Границы файла нужны для восстановления, новых строк — для обычного пересчёта."""

    wmo_index: int
    station_name: str
    period_start: date
    period_end: date
    imported_rows: int
    skipped_rows: int
    new_period_start: date | None
    new_period_end: date | None
    duplicates_in_file: int = 0
    replaced_rows: int = 0


@dataclass(frozen=True)
class ImportReport:
    """Отчёт по итогам импорта — выводится пользователю на экране "Импорт".

    stations содержит все станции с измерениями в файле, в том числе когда
    новых строк нет. wmo_index и station_name оставлены для отчёта по одной
    станции; для многостанционного файла они None, пересчёт использует stations.

    rows_without_data — число строк файла, в которых нет ни одного значимого
    значения (не загружаются в БД); None — формат такой проверки не делает
    (rp5), строка в отчёте не выводится. Для ГГ: total_rows == imported_rows +
    skipped_rows + rows_without_data. skipped_rows включает известные ключи
    БД и одинаковые новые строки файла; duplicates_in_file — число последних."""

    wmo_index: int | None
    station_name: str | None
    period_start: date | None
    period_end: date | None
    total_rows: int
    imported_rows: int
    skipped_rows: int
    rows_without_data: int | None = None
    stations: tuple[StationImportReport, ...] = ()
    duplicates_in_file: int = 0
    replaced_rows: int = 0


class Importer:
    """Базовый интерфейс импортёра. Реализуется в rp5_importer.py / gg_importer.py."""

    def import_file(self, file_path: str) -> ImportReport:
        raise NotImplementedError
