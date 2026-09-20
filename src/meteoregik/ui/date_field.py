"""Поле даты дд.мм.гггг с календарём-выпадашкой — общее для фильтра "Период" и
формы "Новая строка".

Вместо QDateEdit (у него нет пустого состояния, а календарь открывается только
по стрелке): обычное текстовое поле, в которое можно вписать дату руками, и
календарь, открывающийся при получении фокуса (клик, Tab) или по стрелке вниз.
В календаре — русские названия, недоступные для выбора будущие даты, кнопки
"Сегодня" и (если поле необязательное) "Очистить"; в самом поле для очистки
есть крестик.

Выбор будущей даты запрещён и в календаре (maximumDate = сегодня, на момент
открытия — приложение может работать через полночь), и при вписывании руками
(DateField.value() бросает DateFieldError).
"""

from __future__ import annotations

import re
from datetime import date

from PySide6.QtCore import QDate, QLocale, QPoint, QRegularExpression, Qt, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from meteoregik.ui import theme

DATE_FORMAT = "%d.%m.%Y"
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
# Причины фокуса, при которых календарь открывается сам. Остальные (возврат
# фокуса после закрытия календаря — PopupFocusReason, активация окна после
# модального сообщения — ActiveWindowFocusReason) — нет, иначе календарь
# открывался бы снова и снова.
_OPEN_ON_FOCUS = (
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


class DateFieldError(ValueError):
    """Текст в поле даты не является допустимой датой (сообщение — для
    пользователя)."""


def _to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


class _Calendar(QCalendarWidget):
    """Календарь, у которого дни после maximumDate() нарисованы бледно: Qt
    делает их недоступными для выбора, но при стилизации через QSS рисует их
    обычным цветом, и они выглядят выбираемыми."""

    def paintCell(self, painter, rect, cell_date: QDate) -> None:
        if cell_date > self.maximumDate():
            painter.save()
            painter.setPen(QColor(theme.CALENDAR_DISABLED_TEXT))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(cell_date.day()))
            painter.restore()
            return
        super().paintCell(painter, rect, cell_date)


class _CalendarPopup(QFrame):
    date_chosen = Signal(QDate)
    cleared = Signal()

    def __init__(self, parent, allow_empty: bool) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("calendarPopup")
        # клик мимо календаря только закрывает его и не "проваливается" в
        # поле под ним (иначе клик по самому полю тут же открывал бы календарь
        # заново и его нельзя было бы закрыть, чтобы печатать руками)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.setStyleSheet(theme.CALENDAR_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.calendar = _Calendar()
        self.calendar.setLocale(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setGridVisible(False)
        weekend = QTextCharFormat()
        weekend.setForeground(Qt.GlobalColor.darkRed)
        self.calendar.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, weekend)
        self.calendar.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, weekend)
        self.calendar.clicked.connect(self._choose)
        self.calendar.activated.connect(self._choose)
        layout.addWidget(self.calendar)

        footer = QHBoxLayout()
        today_btn = QPushButton("Сегодня")
        today_btn.setObjectName("secondary")
        today_btn.clicked.connect(lambda: self._choose(QDate.currentDate()))
        footer.addWidget(today_btn)
        footer.addStretch(1)
        if allow_empty:
            clear_btn = QPushButton("Очистить")
            clear_btn.setObjectName("secondary")
            clear_btn.clicked.connect(self._clear)
            footer.addWidget(clear_btn)
        layout.addLayout(footer)

    def prepare(self, current: date | None, today: date) -> None:
        self.calendar.setMaximumDate(_to_qdate(today))
        shown = _to_qdate(min(current, today) if current else today)
        self.calendar.setSelectedDate(shown)
        self.calendar.setCurrentPage(shown.year(), shown.month())

    def _choose(self, chosen: QDate) -> None:
        self.close()
        self.date_chosen.emit(chosen)

    def _clear(self) -> None:
        self.close()
        self.cleared.emit()


class DateField(QLineEdit):
    def __init__(self, allow_empty: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._allow_empty = allow_empty
        self.setPlaceholderText("дд.мм.гггг")
        self.setMaxLength(10)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9.]*"), self))
        self.setClearButtonEnabled(allow_empty)
        self.setFixedWidth(140)

        self._popup = _CalendarPopup(self, allow_empty)
        self._popup.date_chosen.connect(lambda chosen: self.set_date(chosen.toPython()))
        self._popup.cleared.connect(self.clear)

    def set_date(self, value: date | None) -> None:
        self.setText(value.strftime(DATE_FORMAT) if value else "")

    def value(self) -> date | None:
        """Введённая дата; None — поле пусто (если пустое допустимо).
        DateFieldError — формат неверен, дата не существует или она в будущем."""
        text = self.text().strip()
        if not text:
            if self._allow_empty:
                return None
            raise DateFieldError("Укажите дату.")
        match = _DATE_RE.match(text)
        try:
            parsed = date(int(match[3]), int(match[2]), int(match[1])) if match else None
        except ValueError:
            parsed = None
        if parsed is None:
            raise DateFieldError("Дата должна быть в формате дд.мм.гггг и существовать в календаре.")
        if parsed > date.today():
            raise DateFieldError("Нельзя выбрать дату позже текущей.")
        return parsed

    # --- календарь ---

    def open_calendar(self) -> None:
        if self._popup.isVisible():
            return
        try:
            current = self.value()
        except DateFieldError:
            current = None
        self._popup.prepare(current, date.today())
        self._popup.adjustSize()

        size = self._popup.sizeHint()
        screen = self.screen().availableGeometry()
        below = self.mapToGlobal(QPoint(0, self.height() + 2))
        x = min(below.x(), screen.right() - size.width())
        y = below.y()
        if y + size.height() > screen.bottom():  # не помещается снизу — над полем
            y = self.mapToGlobal(QPoint(0, 0)).y() - size.height() - 2
        self._popup.move(x, max(y, screen.top()))
        self._popup.show()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        if event.reason() in _OPEN_ON_FOCUS:
            self.open_calendar()

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.open_calendar()  # поле уже в фокусе, а календарь закрыт — открыть снова

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Down:
            self.open_calendar()
            return
        super().keyPressEvent(event)
