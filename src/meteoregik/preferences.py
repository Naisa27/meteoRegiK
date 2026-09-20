"""Пользовательские настройки интерфейса (например, набор полей, выбранных
для показа в "Просмотре исходных данных" для каждого формата) — небольшой
JSON-файл рядом с рабочей БД, чтобы настройки переживали перезапуск
приложения и лежали там же, где данные (в frozen-режиме — рядом с exe).

Не QSettings: на Windows он пишет в реестр (нежелательно для приложения,
поставляемого одним exe), а INI-формат QSettings неоднозначно хранит пустые
списки и строки с запятыми.

Ошибки чтения/записи не пробрасываются — настройки не должны ронять экран:
при повреждённом файле или недоступной папке приложение работает с
настройками по умолчанию."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "meteoregik_settings.json"


class Preferences:
    def __init__(self, path: Path | None) -> None:
        """path=None — настройки живут только в памяти (in-memory БД, тесты)."""
        self._path = path
        self._data: dict[str, Any] = {}
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except (OSError, ValueError):
                pass

    @classmethod
    def next_to_database(cls, db_file: str | None) -> Preferences:
        """Настройки в папке файла БД (`engine.url.database`); для БД без файла
        (in-memory) — только в памяти."""
        if not db_file or db_file == ":memory:":
            return cls(None)
        return cls(Path(db_file).resolve().with_name(SETTINGS_FILENAME))

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        if self._path is None:
            return
        try:
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
