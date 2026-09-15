"""Экспорт текущей выборки (просмотр данных или результат запроса агрегатов)
с выбранными столбцами через openpyxl."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def export_to_excel(rows: list[dict[str, Any]], columns: list[str], out_path: Path) -> None:
    raise NotImplementedError
