"""Выпадающий список станций с поиском по вводимым буквам — общий для фильтра
"Метеостанция" и формы "Новая строка".

Идентификатор станции — индекс ВМО (число, хранится в `itemData`), а не текст
подписи: по нему строится выборка/запись. Подпись — "Название, Регион (индекс)",
ищется по любой её части (и по названию, и по региону, и по индексу).

Режим с `all_text` (фильтр): первый пункт "Все метеостанции", а пустое поле
равноценно ему — очистить фильтр можно крестиком в поле или стерев текст.
Без `all_text` (форма добавления) станция обязательна.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter

from meteoregik.stations.choices import StationChoice

ALL_STATIONS_TEXT = "Все метеостанции"


class StationCombo(QComboBox):
    def __init__(self, all_text: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._all_text = all_text
        self._names: dict[int, str | None] = {}

        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumWidth(320)
        # QCompleter с popup-режимом: по умолчанию у editable QComboBox
        # inline-автодополнение, а нужен выпадающий отфильтрованный список.
        completer = QCompleter(self.model(), self)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setCompleter(completer)

        line_edit = self.lineEdit()
        line_edit.setPlaceholderText(all_text or "Выберите метеостанцию")
        if all_text is not None:
            line_edit.setClearButtonEnabled(True)

    def set_choices(self, choices: list[StationChoice]) -> None:
        """Заменяет список, сохраняя выбранную станцию, если она осталась."""
        try:
            previous = self.selected_wmo()
        except ValueError:
            previous = None

        self.clear()
        self._names = {c.wmo_index: c.name for c in choices}
        if self._all_text is not None:
            self.addItem(self._all_text, None)
        for choice in choices:
            self.addItem(choice.label, choice.wmo_index)
        self.select_wmo(previous)

    def select_wmo(self, wmo_index: int | None) -> None:
        """None — "все станции" (или пусто, если режима "все" нет)."""
        index = self.findData(wmo_index) if wmo_index is not None else -1
        if index < 0:
            index = 0 if self._all_text is not None else -1
        self.setCurrentIndex(index)
        if index < 0:
            self.setCurrentText("")

    def selected_wmo(self) -> int | None:
        """Индекс ВМО выбранной станции; None — "все станции" (только в режиме
        с all_text). ValueError — поле пусто (в режиме без all_text) или в нём
        текст, которого нет в списке."""
        text = self.currentText().strip()
        if not text:
            if self._all_text is not None:
                return None
            raise ValueError("станция не выбрана")
        index = self.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            raise ValueError(f"станции «{text}» нет в списке")
        return self.itemData(index)

    def name_of(self, wmo_index: int) -> str | None:
        return self._names.get(wmo_index)
