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
MainWindow сам решает, что делать. Кнопки — четырьмя рядами по разделам
(правка 2026-09-30; "Аналитика" — 2026-10-05), у каждого ряда подпись и своя
гамма из theme.py: "Работа с данными" (оттенки синего — импорт, просмотр
исходных и агрегатных данных), "Расчёт показателей" (зелёные, кнопок пока
нет), "Справочники" (тёмное золото, station_directory_requested) и
"Аналитика" (фиолетовый, дашборд — dashboard_requested)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt, Signal, SignalInstance
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from meteoregik.resources import DASHBOARD_ICON_PATH, LOGO_PATH
from meteoregik.ui import theme
from meteoregik.ui.buttons import reserve_focus_width, tinted_icon

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

# Сторона иконки в шапке и отступ шапки от краёв окна.
_LOGO_SIZE = 192
_HEADER_MARGIN = 24
# Минимальный зазор между иконкой и текстом шапки в самом узком окне.
_LOGO_GAP = 16


def _format_date_ru(now: datetime) -> str:
    weekday = _WEEKDAYS_RU[now.weekday()].capitalize()
    month = _MONTHS_RU_GENITIVE[now.month - 1]
    return f"{weekday}, {now.day} {month} {now.year} года"


class GreetingWidget(QWidget):
    import_requested = Signal()
    data_browser_requested = Signal()
    aggregate_view_requested = Signal()
    dashboard_requested = Signal()
    station_directory_requested = Signal()

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
        layout.setContentsMargins(0, _HEADER_MARGIN, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(6)

        # Шапка (правка 2026-10-10): иконка — в левом верхнем углу, приветствие
        # и подзаголовок — в одном ряду с ней. Иконка крупная (вдвое выше
        # блока заголовка) и стоит вне раскладки, поверх фона: в ряду она
        # растянула бы его на всю свою высоту и сдвинула вниз часы и кнопки.
        # Место под неё в ряду шапки зарезервировано отступами с обеих
        # сторон: левый не даёт тексту наехать на иконку в узком окне, а
        # правый той же ширины нужен, чтобы текст центрировался по всему
        # окну — как часы и кнопки ниже, — а не по остатку справа от иконки.
        icon = QLabel(self)
        icon.setStyleSheet("background: transparent;")
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        icon.setGeometry(_HEADER_MARGIN, _HEADER_MARGIN, _LOGO_SIZE, _LOGO_SIZE)
        pixmap = QPixmap(str(LOGO_PATH))
        if not pixmap.isNull():
            icon.setPixmap(
                pixmap.scaledToWidth(_LOGO_SIZE, Qt.TransformationMode.SmoothTransformation)
            )

        header = QHBoxLayout()
        header.setContentsMargins(_HEADER_MARGIN, 0, _HEADER_MARGIN, 0)
        header.setSpacing(0)
        header.addSpacing(_LOGO_SIZE + _LOGO_GAP)

        titles = QVBoxLayout()
        titles.setSpacing(6)

        headline = QLabel("Добро пожаловать в meteoRegiK")
        headline_font = QFont("Constantia", 21)
        headline.setFont(headline_font)
        headline.setStyleSheet(f"color: {_TEXT_PRIMARY}; background: transparent;")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titles.addWidget(headline)

        subtitle = QLabel(
            "Программа для работы с агрометеорологическими наблюдениями:\n"
            "импорт, расчёты и карта станций."
        )
        subtitle.setFont(QFont("Segoe UI", 11))
        subtitle.setStyleSheet(f"color: {_TEXT_SECONDARY}; background: transparent;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titles.addWidget(subtitle)

        header.addLayout(titles, 1)
        header.addSpacing(_LOGO_SIZE + _LOGO_GAP)
        layout.addLayout(header)

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

        # Четыре ряда кнопок по разделам работы (правка 2026-09-30, четвёртый
        # "Аналитика" — 2026-10-05): у каждого
        # ряда подпись и своя цветовая гамма (theme.*_ROW_COLORS), у кнопок
        # внутри ряда — разные оттенки этой гаммы. Раздел угадывается не
        # только по цвету, но и по подписи над рядом.
        data_row = self._button_row(
            layout, "Работа с данными", theme.DATA_ROW_TITLE,
            [
                ("Импорт данных", self.import_requested),
                ("Просмотр исходных данных", self.data_browser_requested),
                ("Просмотр агрегатных данных", self.aggregate_view_requested),
            ],
            theme.DATA_ROW_COLORS,
        )
        layout.addLayout(data_row)
        layout.addSpacing(18)

        # Расчётных экранов пока нет (в ТЗ не описаны) — ряд обозначен, чтобы
        # раскладка главного окна не менялась, когда они появятся.
        calc_row = self._button_row(layout, "Расчёт показателей", theme.CALC_ROW_TITLE, [], theme.CALC_ROW_COLORS)
        placeholder = QLabel("Раздел в разработке")
        placeholder.setFont(QFont("Segoe UI", 10))
        placeholder.setStyleSheet(f"color: {_TEXT_SECONDARY}; background: transparent; font-style: italic;")
        calc_row.addWidget(placeholder)
        layout.addLayout(calc_row)
        layout.addSpacing(18)

        directory_row = self._button_row(
            layout, "Работа со справочниками", theme.DIRECTORY_ROW_TITLE,
            [("Справочники", self.station_directory_requested)],
            theme.DIRECTORY_ROW_COLORS,
        )
        layout.addLayout(directory_row)
        layout.addSpacing(18)

        # Дашборд — отдельный четвёртый раздел (правка 2026-10-05): это
        # обзор уже посчитанных данных, а не работа с сырыми данными.
        analytics_row = self._button_row(
            layout, "Аналитика", theme.ANALYTICS_ROW_TITLE,
            [("Дашборд", self.dashboard_requested, tinted_icon(DASHBOARD_ICON_PATH, "#FFFFFF"))],
            theme.ANALYTICS_ROW_COLORS,
        )
        layout.addLayout(analytics_row)

        layout.addStretch(1)

    def _button_row(
        self,
        layout: QVBoxLayout,
        title: str,
        title_color: str,
        buttons: list[tuple[str, SignalInstance] | tuple[str, SignalInstance, QIcon]],
        colors: tuple[str, ...],
    ) -> QHBoxLayout:
        """Подпись раздела + горизонтальный ряд заливных кнопок; i-я кнопка
        получает i-й оттенок гаммы ряда. Подпись добавляется в layout сразу,
        ряд возвращается — вызывающий может дополнить его до вставки."""
        caption = QLabel(title.upper())
        caption_font = QFont("Segoe UI", 9)
        caption_font.setWeight(QFont.Weight.DemiBold)
        caption_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        caption.setFont(caption_font)
        caption.setStyleSheet(f"color: {title_color}; background: transparent;")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)

        row = QHBoxLayout()
        row.setSpacing(16)
        row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        for (text, signal, *icon), color in zip(buttons, colors):
            button = QPushButton(text)
            if icon:
                button.setIcon(icon[0])
            button.setStyleSheet(theme.accent_button_qss(color))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            reserve_focus_width(button)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
        return row

    def _tick(self) -> None:
        now = datetime.now()
        self._date_label.setText(_format_date_ru(now))
        self._clock_label.setText(now.strftime("%H:%M:%S"))
