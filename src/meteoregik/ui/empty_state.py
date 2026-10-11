"""Подсказка на месте результатов экрана, когда показывать нечего: что
случилось и что делать дальше, вместо пустой таблицы или пустых карточек.

Текст о пустой базе — один на все экраны (корректировка 2026-10-11): его
показывают обе вкладки дашборда и оба "Просмотра". Без него в только что
установленной программе экран выглядит сломанным — список станций пуст, а
"Применить" лишь просит выбрать станцию, которой неоткуда взяться.

Стиль подписи — theme.DIALOG_QSS, QLabel#emptyHint.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

NO_DATA_HINT = (
    "В базе пока нет залитых данных.\n"
    "Загрузите файлы rp5 или ГГ через «Импорт данных» на главном экране."
)


def hint_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("emptyHint")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    return label
