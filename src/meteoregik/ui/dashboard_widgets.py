"""Общие виджеты вкладок экрана "Дашборд": карточка-сводка со значком "?",
"вдавленная" черта-разделитель, переключатель вида в рамке. Вынесены из
ui/dashboard_view.py, когда появилась вторая вкладка — "Исходные данные"
(ui/dashboard_source_tab.py, корректировка 2026-10-10): обе вкладки
собираются из одних и тех же деталей. Стили — DASHBOARD_QSS в dashboard_view.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from datetime import date

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFocusEvent, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from meteoregik.ui import theme


# Значок "?" (HelpBadge) и подсказки — общие для дашборда и окон расчёта
HELP_QSS = f"""
QToolButton#helpBadge {{
    color: {theme.TEXT_SECONDARY};
    background: {theme.INPUT_BG};
    border: 1px solid {theme.TEXT_SECONDARY};
    border-radius: 10px;
    font-size: 12px;
    font-weight: 700;
    min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
    padding: 0;
}}
QToolButton#helpBadge:hover {{ background: {theme.HEADER_BG}; color: {theme.TEXT_PRIMARY}; }}
QToolButton#helpBadge:focus {{
    border: {theme.FOCUS_RING_WIDTH}px solid {theme.FOCUS_RING};
    color: {theme.TEXT_PRIMARY};
}}
QToolTip {{
    color: {theme.TEXT_PRIMARY};
    background: {theme.INPUT_BG};
    border: 1px solid {theme.BORDER};
    padding: 6px;
    font-size: 12px;
}}
"""


def format_day(day: date) -> str:
    return f"{day:%d.%m.%Y}"


def format_count(value: int) -> str:
    """Тысячи через неразрывный пробел — "3 652", как принято в русском тексте."""
    return f"{value:,}".replace(",", " ")


@dataclass(frozen=True)
class CardText:
    caption: str
    value: str
    note: str
    help: str = ""


def tooltip_html(text: str, width: int = 340) -> str:
    """Подсказка как rich text фиксированной ширины — такую Qt переносит по
    словам, а не тянет одной строкой во весь экран; абзацы — по пустой строке."""
    paragraphs = "".join(f"<p style='margin: 0 0 6px 0'>{p}</p>" for p in text.split("\n\n"))
    return f"<div style='width: {width}px'>{paragraphs}</div>"


class HelpBadge(QToolButton):
    """Значок "?" с подсказкой. Кроме наведения мыши подсказка показывается
    по фокусу с клавиатуры (Tab) и по щелчку/пробелу — подсказка только по
    наведению недоступна без мыши."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("helpBadge")
        self.setText("?")
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.clicked.connect(self.show_tip)

    def set_help(self, caption: str, text: str, width: int = 340) -> None:
        self.setToolTip(tooltip_html(text, width) if text else "")
        self.setAccessibleName(f"Как считается: {caption}")
        self.setAccessibleDescription(text)

    def show_tip(self) -> None:
        if self.toolTip():
            QToolTip.showText(self.mapToGlobal(QPoint(0, self.height() + 4)), self.toolTip(), self)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.show_tip()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        QToolTip.hideText()


class GrooveDivider(QWidget):
    """Вертикальная "вдавленная" черта между теплокартой и графиком: тёмная
    линия-тень и светлая линия-блик рядом (классический engraved-эффект),
    к концам плавно растворяется в фоне — не режет экран жёсткой чертой."""

    _SHADOW = QColor(theme.HEADER_BORDER).darker(135)
    _LIGHT = QColor("#FFFFFF")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(12)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:  # noqa: N802 — имя метода Qt
        painter = QPainter(self)
        x = self.width() // 2 - 1
        for offset, color in ((0, self._SHADOW), (1, self._LIGHT)):
            gradient = QLinearGradient(0, 0, 0, self.height())
            transparent = QColor(color)
            transparent.setAlpha(0)
            gradient.setColorAt(0.0, transparent)
            gradient.setColorAt(0.12, color)
            gradient.setColorAt(0.88, color)
            gradient.setColorAt(1.0, transparent)
            painter.fillRect(x + offset, 0, 1, self.height(), gradient)
        painter.end()


class StatCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(2)
        self.caption = QLabel()
        self.caption.setObjectName("statCaption")
        # Длинная подпись ("Средняя суточная температура за период") в узком
        # окне переносится, а не обрезается.
        self.caption.setWordWrap(True)
        self.help_badge = HelpBadge()
        self.value = QLabel()
        self.value.setObjectName("statValue")
        # Несколько месяцев-рекордсменов крупным шрифтом в одну строку
        # расталкивали бы колонки сетки — переносим, как подпись и примечание.
        self.value.setWordWrap(True)
        self.note = QLabel()
        self.note.setObjectName("statNote")
        self.note.setWordWrap(True)
        for label in (self.caption, self.value, self.note):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        caption_row = QHBoxLayout()
        caption_row.setSpacing(6)
        caption_row.addWidget(self.caption, stretch=1)
        caption_row.addWidget(self.help_badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(caption_row)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def set_text(self, text: CardText) -> None:
        self.caption.setText(text.caption)
        self.value.setText(text.value)
        self.note.setText(note_html(text.note))
        self.help_badge.set_help(text.caption, text.help)


# Межстрочный интервал примечания карточки из нескольких строк
NOTE_LINE_HEIGHT_PERCENT = 130


def note_html(note: str) -> str:
    """Примечание из нескольких строк ("rp5: …" / "ГГ: …") — с увеличенным
    межстрочным интервалом (корректировка 2026-10-10): обычным текстом
    QLabel ставит строки вплотную, и два периода под крупным значением
    выглядели склеенными. Однострочное примечание возвращается как есть."""
    lines = note.split("\n")
    if len(lines) < 2:
        return note
    style = f"margin: 0; line-height: {NOTE_LINE_HEIGHT_PERCENT}%"
    return "".join(f"<p style='{style}'>{escape(line)}</p>" for line in lines)


def scrollable(content: QWidget) -> QScrollArea:
    """Содержимое вкладки в области прокрутки (правка 2026-10-10): в не
    развёрнутом окне карточки, таблицы и графики вместе выше окна — раньше
    они раздвигали диалог за край экрана и уводили туда кнопку "Закрыть".
    Теперь окно остаётся своего размера, а не поместившееся прокручивается.
    Пока всё помещается, полосы прокрутки нет и содержимое растягивается
    на всю область, как без неё."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    # Имена — для DASHBOARD_QSS: без прозрачного фона область прокрутки
    # закрасила бы содержимое своим (белым) цветом вместо цвета окна
    area.viewport().setObjectName("scrollViewport")
    content.setObjectName("scrollContent")
    area.setWidget(content)
    return area


def mode_switch(labels: list[str], parent: QWidget) -> tuple[QFrame, QButtonGroup]:
    """Переключатель вида — радиокнопки в рамке (как у теплокарты первой
    вкладки). Первая кнопка выбрана; id кнопки в группе — её номер в labels."""
    frame = QFrame()
    frame.setObjectName("heatmapMode")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(10, 3, 10, 3)
    layout.setSpacing(12)
    group = QButtonGroup(parent)
    for index, label in enumerate(labels):
        radio = QRadioButton(label)
        radio.setChecked(index == 0)
        group.addButton(radio, index)
        layout.addWidget(radio)
    return frame, group
