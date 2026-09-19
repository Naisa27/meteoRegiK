"""Общий интерфейс Importer и результат импорта (см. "Экраны UI", п.1 и
"Решения, согласованные с пользователем" в .claude/docs/plan.md).

Дедупликация зависит от формата: (wmo_index, obs_date) для ГГ,
(wmo_index, obs_date, obs_time) для rp5 — строки с уже существующим ключом
пропускаются, вставляются только новые, без диалогов подтверждения.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class ImportFormatError(ValueError):
    """Файл не соответствует ожидаемому формату импорта (rp5/ГГ) — типично,
    когда пользователь выбрал неверный формат в диалоге импорта. Отдельный
    тип нужен, чтобы UI мог показать понятное сообщение вместо голого
    KeyError/ValueError при разборе структуры файла."""


@dataclass(frozen=True)
class ImportReport:
    """Отчёт по итогам импорта — выводится пользователю на экране "Импорт".

    wmo_index нужен вызывающему коду (UI), чтобы после успешного импорта
    запустить calculations/recompute.py для затронутого диапазона."""

    wmo_index: int | None
    station_name: str | None
    period_start: date | None
    period_end: date | None
    total_rows: int
    imported_rows: int
    skipped_rows: int


class Importer:
    """Базовый интерфейс импортёра. Реализуется в rp5_importer.py / gg_importer.py."""

    def import_file(self, file_path: str) -> ImportReport:
        raise NotImplementedError
