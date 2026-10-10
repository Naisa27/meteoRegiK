"""Подключение к SQLite-файлу проекта, включение WAL-режима и внешних ключей."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

DEFAULT_DB_FILENAME = "meteoregik.sqlite3"
# Папка данных пользователя внутри "Документов" у установленного приложения
DATA_DIR_NAME = "meteoRegiK"
# Переменная окружения с папкой данных — вместо папки по умолчанию. Нужна
# проверке сборки (packaging/, meteoregik/smoke.py), чтобы не трогать
# настоящую базу пользователя; пригодится и тому, кто держит базу на другом диске.
DATA_DIR_ENV = "METEOREGIK_DATA_DIR"


def documents_dir() -> Path:
    """Папка "Документы" текущего пользователя. Спрашиваем у Windows, а не
    собираем как ~/Documents: папку часто переносят (OneDrive, другой диск),
    и тогда ~/Documents либо нет, либо это не то место, куда смотрит
    пользователь."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Guid(ctypes.Structure):
            _fields_ = [
                ("data1", wintypes.DWORD),
                ("data2", wintypes.WORD),
                ("data3", wintypes.WORD),
                ("data4", ctypes.c_ubyte * 8),
            ]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = _Guid(
            0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)
        )
        buffer = ctypes.c_wchar_p()
        try:
            result = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(buffer)
            )
            if result == 0 and buffer.value:
                return Path(buffer.value)
        except OSError:
            pass
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buffer)
    return Path.home() / "Documents"


def local_data_dir() -> Path:
    """Запасная папка данных — в профиле пользователя, вне "Документов"
    (см. installed_data_dir)."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / DATA_DIR_NAME / "data"


def _is_writable(folder: Path) -> bool:
    """Можно ли создать папку и файл в ней. Проверяется записью, а не
    правами доступа: "Контролируемый доступ к папкам" Защитника Windows
    права не меняет, а запись в "Документы" неизвестной программе запрещает
    (ошибкой "файл не найден")."""
    probe = folder / ".write_test"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def installed_data_dir() -> Path:
    """Папка данных установленного приложения: "Документы\\meteoRegiK", а
    если писать туда нельзя — папка в профиле пользователя (local_data_dir).

    Запасной вариант нужен из-за "Контролируемого доступа к папкам" Защитника
    Windows: при включённой защите неподписанная программа не может писать в
    "Документы", и приложение не запускалось вовсе (найдено при первой
    установке, 2026-10-10).

    Уже существующая база важнее правила: где она лежит, там и остаётся —
    иначе после включения или отключения защиты приложение открыло бы новую
    пустую базу, а данные пользователя "пропали" бы. Заодно при существующей
    базе запись не пробуется, и Защитник не показывает уведомление о
    заблокированной попытке при каждом запуске."""
    documents = documents_dir() / DATA_DIR_NAME
    local = local_data_dir()
    if (documents / DEFAULT_DB_FILENAME).exists():
        return documents
    if (local / DEFAULT_DB_FILENAME).exists():
        return local
    return documents if _is_writable(documents) else local


def default_db_path() -> Path:
    """Путь к рабочей БД по умолчанию.

    Установленное приложение (собранный PyInstaller-exe, `sys.frozen`) —
    "Документы\\meteoRegiK" пользователя (решение пользователя 2026-10-10).
    Не рядом с exe, как было до появления установщика: программа ставится в
    папку, куда обычным запуском писать нельзя или не стоит, а база — это
    данные пользователя: она должна переживать обновление и удаление
    программы и легко находиться для копирования. Если в "Документы" писать
    нельзя — запасная папка, см. installed_data_dir. `__file__` тоже не
    годится — в frozen-режиме он указывает внутрь папки программы.

    В dev-режиме — файл в корне проекта (см. plan.md: "SQLite, файл внутри
    проекта"). Переменная METEOREGIK_DATA_DIR перекрывает оба варианта."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        base_dir = Path(override)
    elif getattr(sys, "frozen", False):
        base_dir = installed_data_dir()
    else:
        # src/meteoregik/db/session.py -> src/meteoregik -> src -> <project root>
        base_dir = Path(__file__).resolve().parents[3]
    return base_dir / DEFAULT_DB_FILENAME


def get_engine(db_path: Path | None = None) -> Engine:
    path = db_path or default_db_path()
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
