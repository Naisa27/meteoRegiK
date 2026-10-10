"""Самопроверка собранного приложения (packaging/build.ps1).

Собранный exe нельзя проверить тестами: вопрос не в логике, а в том, всё ли
попало в сборку — шаблон базы, картинки, страница карты, браузерный движок
Qt. Если задана переменная окружения METEOREGIK_SMOKE_TEST (папка для
отчёта), приложение после запуска само открывает экран "Карта" — он тянет
за собой больше всего ресурсов, — ждёт, записывает в эту папку smoke.json и
снимки экранов и закрывается. Обычный запуск этот модуль не затрагивает.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

SMOKE_ENV = "METEOREGIK_SMOKE_TEST"
# Сколько ждать загрузки страницы карты и тайлов перед снимком
_MAP_WAIT_MS = 8000


def smoke_dir() -> Path | None:
    value = os.environ.get(SMOKE_ENV)
    return Path(value) if value else None


def schedule(app: QApplication, window, out_dir: Path) -> None:
    """Запланировать самопроверку: открыть карту, записать отчёт, выйти."""
    report: dict[str, object] = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "database": str(window.engine.url.database),
        "buttons": sorted(button.text() for button in window.findChildren(QPushButton)),
    }
    state: dict[str, object] = {}

    def finish(error: str | None = None) -> None:
        if error is not None:
            report["error"] = error
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.quit()

    def open_map() -> None:
        try:
            from meteoregik.ui.map_view import MapDialog

            window.grab().save(str(out_dir / "main_window.png"))
            dialog = MapDialog(window.engine, window)
            dialog.show()
            state["dialog"] = dialog
        except Exception as exc:  # noqa: BLE001 — любая ошибка и есть результат проверки
            finish(f"{type(exc).__name__}: {exc}")
            return
        QTimer.singleShot(_MAP_WAIT_MS, check_map)

    def check_map() -> None:
        try:
            dialog = state["dialog"]
            layer = dialog.current_layer()
            report["map_page_loaded"] = dialog.canvas._scripts.ready
            report["map_base"] = dialog._base
            report["directory_stations"] = len(dialog._data.stations) if dialog._data else None
            report["markers"] = len(layer.markers) if layer else None
            dialog.grab().save(str(out_dir / "map.png"))
        except Exception as exc:  # noqa: BLE001
            finish(f"{type(exc).__name__}: {exc}")
            return
        finish()

    out_dir.mkdir(parents=True, exist_ok=True)
    QTimer.singleShot(1500, open_map)
