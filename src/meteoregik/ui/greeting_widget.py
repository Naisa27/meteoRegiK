"""Приветственный экран главного окна: заголовок, дата/день недели, живые
часы, кнопка запуска импорта. Занимает место прежней заглушки-плейсхолдера в
MainWindow, теперь во всю площадь окна (верхняя шапка с "meteoRegiK" и меню
"Файл" убраны — единственный вход в импорт теперь эта кнопка) — содержимое
выровнено к верхней части, а не по центру всего окна.

Оформление — не веб-стиль, а спокойный "исследовательский" тон: засечный
шрифт для заголовка (Constantia), нейтральные serif/sans пары, палитра в
духе пасмурного неба и урожая (не дефолтные крем+терракота), иконка
static/meteo_1.png (прозрачный фон) как единственный акцент вместо крупного
hero-изображения. У всех подписей явно прозрачный фон — иначе Qt-стиль
подсвечивает QLabel своим цветом, который отличается от фона виджета.

Виджет не знает про ImportDialog/DataBrowserDialog/AggregateViewDialog/engine
напрямую (чисто декоративный экран) — при клике на кнопки испускает
import_requested / data_browser_requested / aggregate_view_requested,
MainWindow сам решает, что делать. Три кнопки на одной строке под часами —
"Импорт данных" (акцент — урожайное золото), "Просмотр исходных данных"
(второй акцент — пасмурно-небесный синий) и "Просмотр агрегатных данных"
(третий акцент — травяной зелёный), все три пары цветов из theme.py."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from meteoregik.resources import LOGO_PATH
from meteoregik.ui import theme

_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
_MONTHS_RU_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

_BG = theme.BG
_TEXT_PRIMARY = theme.TEXT_PRIMARY
_TEXT_SECONDARY = theme.TEXT_SECONDARY
_ACCENT = theme.ACCENT


def _format_date_ru(now: datetime) -> str:
    weekday = _WEEKDAYS_RU[now.weekday()].capitalize()
    month = _MONTHS_RU_GENITIVE[now.month - 1]
    return f"{weekday}, {now.day} {month} {now.year} года"


class GreetingWidget(QWidget):
    import_requested = Signal()
    data_browser_requested = Signal()
    aggregate_view_requested = Signal()

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
        layout.setContentsMargins(0, 48, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(6)

        icon = QLabel()
        icon.setStyleSheet("background: transparent;")
        pixmap = QPixmap(str(LOGO_PATH))
        if not pixmap.isNull():
            icon.setPixmap(
                pixmap.scaledToWidth(96, Qt.TransformationMode.SmoothTransformation)
            )
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        layout.addSpacing(12)

        headline = QLabel("Добро пожаловать в meteoRegiK")
        headline_font = QFont("Constantia", 21)
        headline.setFont(headline_font)
        headline.setStyleSheet(f"color: {_TEXT_PRIMARY}; background: transparent;")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(headline)

        subtitle = QLabel(
            "Программа для работы с агрометеорологическими наблюдениями:\n"
            "импорт, расчёты и карта станций."
        )
        subtitle.setFont(QFont("Segoe UI", 11))
        subtitle.setStyleSheet(f"color: {_TEXT_SECONDARY}; background: transparent;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(28)

        self._date_label = QLabel()
        self._date_label.setFont(QFont("Segoe UI", 12))
        self._date_label.setStyleSheet(f"color: {_TEXT_SECONDARY}; background: transparent;")
        self._date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._date_label)

        self._clock_label = QLabel()
        clock_font = QFont("Segoe UI", 30)
        clock_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
        self._clock_label.setFont(clock_font)
        self._clock_label.setStyleSheet(f"color: {_ACCENT}; background: transparent;")
        self._clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._clock_label)

        layout.addSpacing(32)

        button_row = QHBoxLayout()
        button_row.setSpacing(16)

        import_btn = self._accent_button("Импорт данных", theme.PRIMARY_BUTTON_QSS)
        import_btn.clicked.connect(self.import_requested.emit)
        button_row.addWidget(import_btn)

        data_browser_btn = self._accent_button("Просмотр исходных данных", theme.SKY_BUTTON_QSS)
        data_browser_btn.clicked.connect(self.data_browser_requested.emit)
        button_row.addWidget(data_browser_btn)

        aggregate_view_btn = self._accent_button("Просмотр агрегатных данных", theme.GRASS_BUTTON_QSS)
        aggregate_view_btn.clicked.connect(self.aggregate_view_requested.emit)
        button_row.addWidget(aggregate_view_btn)

        layout.addLayout(button_row)
        layout.setAlignment(button_row, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

    def _accent_button(self, text: str, qss: str) -> QPushButton:
        """Кнопка с заливным акцентом (см. theme.py::_filled_button_qss).

        `:focus` в BUTTON_QSS утяжеляет шрифт 600 -> 700 (видимый фокус не
        только цветом), а на реальных Windows-шрифтах (Segoe UI) вес 700
        рендерится на несколько пикселей шире того же текста. sizeHint()
        кнопки при этом всегда считается под базовый вес 600 — стилевой
        каскад Qt переопределяет фактический QFont виджета при полировке
        стиля, так что явно выставленный программно жирный QFont эффекта не
        даёт (проверено: `button.font().weight()` после `setFont(Bold)`
        всё равно возвращает 600). А увеличение padding в QSS тоже не
        помогает: sizeHint = ширина_текста(600) + 2*padding, то есть область
        под текст всегда точно равна ширине текста веса 600, каким бы ни был
        padding — лишнего места изнутри не появляется. Поэтому запас — явный
        minimumWidth сверх sizeHint; у самой длинной подписи ("Просмотр
        агрегатных данных") без него в фокусе обрезались первая и последняя
        буквы (правка 2026-09-23)."""
        button = QPushButton(text)
        button.setStyleSheet(qss)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(button.sizeHint().width() + 20)
        return button

    def _tick(self) -> None:
        now = datetime.now()
        self._date_label.setText(_format_date_ru(now))
        self._clock_label.setText(now.strftime("%H:%M:%S"))
