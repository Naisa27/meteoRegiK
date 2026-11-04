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
<p>Изменения относительно версии 0.3.2.</p>
<h2>Снежный покров</h2>
<ul>
<li>Новая вкладка дашборда: дни со снегом, максимальная высота со всеми
датами и сильные морозы при малоснежье. Тепловая карта показывает дни со
снегом и отклонения от нормы; графики прокручиваются по всему архиву.</li>
<li>Таблицы по месяцам и зимним сезонам, а также экспорт Excel находятся
в отдельном окне «Снежный покров» раздела «Расчёт показателей».</li>
<li>Показатели автоматически рассчитываются по уже загруженным данным rp5
при обновлении базы и пересчитываются при изменении исходных данных.</li>
<li>Снег добавлен в просмотр агрегатных данных. Неполные периоды и дни
без температуры сопровождаются пояснениями.</li>
<li>В агрегатных таблицах закреплена годовая сводка, снежные и морозные
дни выделены голубым. Общее пояснение вынесено над результатами.</li>
</ul>
<h2>Обновление базы и справка</h2>
<ul>
<li>Обновление базы с пересчётом начинается только после успешного создания
резервной копии. Если копию сохранить не удалось, база остаётся неизменной.</li>
<li>В путеводитель добавлено описание снежных показателей и принятых
допущений расчёта.</li>
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
