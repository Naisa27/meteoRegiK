"""Изменения текущей поставки, доступные с главного экрана."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from meteoregik.ui.buttons import reserve_focus_width
from meteoregik.ui.dialogs import enable_min_max_buttons
from meteoregik.version import app_version


def whats_new_title() -> str:
    """Номер берётся из метаданных той же установленной версии, что и заголовок приложения."""
    return f"Что нового в последней версии {app_version()}".strip()


CHANGES_HTML = """
<h2>Импорт и восстановление расчётов</h2>
<ul>
<li>При каждом импорте автоматически проверяются расчёты всех станций файла,
даже если новых строк нет. Пропуски восстанавливаются, незавершённый после
сбоя пересчёт продолжается при следующем импорте. Ручной флажок пересчёта убран.</li>
<li>При повторном импорте rp5 сравниваются полные суточные наборы наблюдений.
Более полный набор принимается автоматически; при спорных изменениях можно
сравнить сроки, температуру и осадки и выбрать старый или новый набор.
Новый набор заменяет день целиком, сохранение старого не добавляет сроки.</li>
<li>В отчёте отдельно показаны повторы внутри файла и строки без данных;
виден ход обработки всех станций. Периоды указаны в формате дд.мм.гггг.</li>
</ul>
<h2>Переходы температуры</h2>
<ul>
<li>Найденные при неполных данных даты сохраняются как предварительные
и участвуют в длительностях периодов, осадках и средних значениях.</li>
<li>Предварительные значения выделены янтарным фоном и знаком ≈ в таблицах
и Excel. Добавлена легенда; подсказки сообщают число пропущенных дней
и максимальный непрерывный пропуск.</li>
<li>Таблица состояния расчёта стала удобнее для чтения: длинные описания
переносятся, столбцам выделено больше места.</li>
</ul>
<h2>Справка и установка</h2>
<ul>
<li>Обновлён путеводитель по импорту и переходам температуры.
Рядом с ним доступно это окно изменений текущей версии.</li>
<li>Обновлён установщик и улучшена самопроверка сборки.</li>
</ul>
"""


class WhatsNewDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(whats_new_title())
        enable_min_max_buttons(self)
        self.resize(740, 620)
        layout = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setHtml(f"<h1>{whats_new_title()}</h1>{CHANGES_HTML}")
        layout.addWidget(self.text)
        close = QPushButton("Закрыть")
        close.setObjectName("secondary")
        reserve_focus_width(close)
        close.clicked.connect(self.accept)
        layout.addWidget(close)
