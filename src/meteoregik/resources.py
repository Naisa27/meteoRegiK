"""Пути к бандлящимся с приложением ресурсам (иконки, логотип, sqlite-шаблон
справочников).

В собранном PyInstaller-exe (`--onefile`) такие ресурсы должны резолвиться
относительно временной папки распаковки (`sys._MEIPASS`), а не через
`Path(__file__)` — иначе в frozen-режиме путь окажется неверным (см.
db/session.py про ту же проблему для рабочей БД). Будущий
packaging/meteoregik.spec должен объявлять `datas` для `static/` и
`seed_data/` с сохранением этой же относительной структуры
(`meteoregik/static`, `meteoregik/seed_data`), чтобы `_bundled_base_dir()`
находил их верно.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bundled_base_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(meipass) / "meteoregik"
    return Path(__file__).resolve().parent


_BASE_DIR = _bundled_base_dir()

STATIC_DIR = _BASE_DIR / "static"
APP_ICON_PATH = STATIC_DIR / "app_icon.ico"
LOGO_PATH = STATIC_DIR / "meteo_1.png"

# sqlite-шаблон с уже залитыми справочниками stations/ru_regions — результат
# разовой заливки из метеостанции_с_координатами.xlsx / Регионы_РФ.xlsx на
# этапе создания проекта (см. plan.md). db/seed.py копирует этот файл в
# рабочую БД при первом запуске, если рабочего файла ещё нет.
SEED_DIR = _BASE_DIR / "seed_data"
SEED_DB_PATH = SEED_DIR / "seed.sqlite3"
