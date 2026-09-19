"""Копирование бандлящегося sqlite-шаблона (resources.SEED_DB_PATH) в рабочий
файл БД при первом запуске. Шаблон — результат разовой заливки справочников
stations/ru_regions из исходных xlsx на этапе создания проекта (см. plan.md);
здесь никакие xlsx не парсятся, только собственный sqlite-файл проекта.

Идемпотентно: если рабочий файл БД уже существует (обычный повторный запуск
или обновление exe в той же папке — у пользователя уже есть свои данные),
копирование пропускается, чтобы не затереть их.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from meteoregik.resources import SEED_DB_PATH


def ensure_seed_database(db_path: Path) -> None:
    """Копирует sqlite-шаблон в db_path, если там ещё нет файла."""
    if db_path.exists():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SEED_DB_PATH, db_path)
