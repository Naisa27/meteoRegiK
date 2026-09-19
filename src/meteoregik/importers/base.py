"""Общий интерфейс Importer и результат импорта (см. "Экраны UI", п.1 и
"Решения, согласованные с пользователем" в .claude/docs/plan.md).

Дедупликация зависит от формата: (station_id, obs_date) для ГГ,
(wmo_index, obs_date, obs_time) для rp5 — строки с уже существующим ключом
пропускаются, вставляются только новые, без диалогов подтверждения.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ImportReport:
    """Отчёт по итогам импорта — выводится пользователю на экране "Импорт".

    station_id нужен вызывающему коду (UI), чтобы после успешного импорта
    запустить calculations/recompute.py для затронутого диапазона."""

    station_id: int | None
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
