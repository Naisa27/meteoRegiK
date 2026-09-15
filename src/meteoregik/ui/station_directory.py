"""Экран "Справочник станций": (а) таб правки stations — фильтры (страна,
название, индекс ВМО, регион РФ) + пагинируемая редактируемая таблица
(паттерн data_browser.py) с добавлением новой станции; (б) таб правки
ru_regions — простая редактируемая таблица code/region без фильтров."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class StationDirectory(QWidget):
    pass
