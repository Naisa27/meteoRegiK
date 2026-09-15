"""Экран "Просмотр данных": таблица с пагинацией, фильтр по
станции/формату/периоду, inline-редактирование ячеек, добавление/удаление
строк -> триггерит calculations/recompute.py."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class DataBrowser(QWidget):
    pass
