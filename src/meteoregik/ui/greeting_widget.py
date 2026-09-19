"""Приветственный экран главного окна: заголовок, дата/день недели, живые
часы. Занимает место прежней заглушки-плейсхолдера в MainWindow.

Оформление — не веб-стиль, а спокойный "исследовательский" тон: засечный
шрифт для заголовка (Constantia), нейтральные serif/sans пары, палитра в
духе пасмурного неба и урожая (не дефолтные крем+терракота), тонкая
барометр-иконка (static/barometr.jpg) как единственный акцент вместо
крупного hero-изображения."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from meteoregik.resources import LOGO_PATH

_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
_MONTHS_RU_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

_BG = "#F6F8FA"
_TEXT_PRIMARY = "#26333A"
_TEXT_SECONDARY = "#5B6B74"
_ACCENT = "#B8862E"


def _format_date_ru(now: datetime) -> str:
    weekday = _WEEKDAYS_RU[now.weekday()].capitalize()
    month = _MONTHS_RU_GENITIVE[now.month - 1]
    return f"{weekday}, {now.day} {month} {now.year} года"


class GreetingWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        icon = QLabel()
        pixmap = QPixmap(str(LOGO_PATH))
        if not pixmap.isNull():
            icon.setPixmap(
                pixmap.scaledToWidth(64, Qt.TransformationMode.SmoothTransformation)
            )
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        layout.addSpacing(12)

        headline = QLabel("Добро пожаловать в meteoRegiK")
        headline_font = QFont("Constantia", 21)
        headline.setFont(headline_font)
        headline.setStyleSheet(f"color: {_TEXT_PRIMARY};")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(headline)

        subtitle = QLabel(
            "Программа для работы с агрометеорологическими наблюдениями:\n"
            "импорт, расчёты и карта станций."
        )
        subtitle.setFont(QFont("Segoe UI", 11))
        subtitle.setStyleSheet(f"color: {_TEXT_SECONDARY};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(28)

        self._date_label = QLabel()
        self._date_label.setFont(QFont("Segoe UI", 12))
        self._date_label.setStyleSheet(f"color: {_TEXT_SECONDARY};")
        self._date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._date_label)

        self._clock_label = QLabel()
        clock_font = QFont("Segoe UI", 30)
        clock_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
        self._clock_label.setFont(clock_font)
        self._clock_label.setStyleSheet(f"color: {_ACCENT};")
        self._clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._clock_label)

    def _tick(self) -> None:
        now = datetime.now()
        self._date_label.setText(_format_date_ru(now))
        self._clock_label.setText(now.strftime("%H:%M:%S"))
