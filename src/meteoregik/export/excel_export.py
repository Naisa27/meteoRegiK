"""Заготовка под экспорт результатов будущих экранов расчёта показателей
через openpyxl. Экспорт "Просмотра исходных данных" не нужен (решение
пользователя 2026-10-01); экспорт агрегатов — export/aggregate_excel.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def export_to_excel(rows: list[dict[str, Any]], columns: list[str], out_path: Path) -> None:
    raise NotImplementedError
