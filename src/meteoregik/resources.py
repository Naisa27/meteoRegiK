"""Пути к статическим ресурсам приложения (иконки, логотип, сиды справочников)."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

APP_ICON_PATH = STATIC_DIR / "app_icon.ico"
LOGO_PATH = STATIC_DIR / "barometr.jpg"

# CSV-дампы справочников stations/ru_regions — результат разовой заливки из
# метеостанции_с_координатами.xlsx / Регионы_РФ.xlsx на этапе создания
# проекта (см. plan.md, "Открытые вопросы для доработки"). Бандлятся с
# приложением и накатываются db/seed.py в пустые таблицы при первом запуске —
# без повторного парсинга исходных xlsx.
SEED_DIR = Path(__file__).resolve().parent / "seed_data"

STATIONS_SEED_PATH = SEED_DIR / "stations.csv"
RU_REGIONS_SEED_PATH = SEED_DIR / "ru_regions.csv"
