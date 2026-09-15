"""Экран "Импорт": выбор файла, явный выбор формата (rp5/ГГ), прогресс, отчёт
(ImportReport из importers/base.py) — станция, период, всего/импортировано/
пропущено строк."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog


class ImportDialog(QDialog):
    pass
