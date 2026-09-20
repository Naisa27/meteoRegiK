"""Поле даты с календарём (ui/date_field.py): формат дд.мм.гггг, пустое
значение, запрет будущих дат, открытие календаря при получении фокуса."""

from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate, QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QPushButton

from meteoregik.ui.date_field import DateField, DateFieldError


def _qapp():
    return QApplication.instance() or QApplication([])


def _focus_in(field: DateField, reason: Qt.FocusReason) -> None:
    QApplication.sendEvent(field, QFocusEvent(QEvent.Type.FocusIn, reason))


def _popup_buttons(field: DateField) -> dict[str, QPushButton]:
    return {b.text(): b for b in field._popup.findChildren(QPushButton)}


def test_value_parses_ddmmyyyy_and_formats_back():
    _qapp()
    field = DateField()

    field.setText("05.01.2020")
    assert field.value() == date(2020, 1, 5)
    field.setText("5.1.2020")
    assert field.value() == date(2020, 1, 5)

    field.set_date(date(2021, 12, 31))
    assert field.text() == "31.12.2021"
    field.set_date(None)
    assert field.text() == ""


def test_empty_is_allowed_only_when_field_is_optional():
    _qapp()
    optional, required = DateField(allow_empty=True), DateField(allow_empty=False)

    assert optional.value() is None
    assert optional.isClearButtonEnabled()
    with pytest.raises(DateFieldError):
        required.value()
    assert not required.isClearButtonEnabled()


@pytest.mark.parametrize("text", ["31.02.2020", "2020-01-05", "01.01.20", "aa.bb.cccc", "1.1"])
def test_malformed_or_nonexistent_dates_are_rejected(text):
    _qapp()
    field = DateField()
    field.setText(text)

    with pytest.raises(DateFieldError, match="дд.мм.гггг"):
        field.value()


def test_future_date_is_rejected_but_today_is_fine():
    _qapp()
    field = DateField()

    field.set_date(date.today())
    assert field.value() == date.today()
    field.set_date(date.today() + timedelta(days=1))
    with pytest.raises(DateFieldError, match="позже текущей"):
        field.value()


def test_validator_blocks_letters_and_limits_length():
    _qapp()
    field = DateField()
    validator = field.validator()

    assert validator.validate("01.02.2020", 0)[0] == validator.State.Acceptable
    assert validator.validate("01-02", 0)[0] == validator.State.Invalid
    assert field.maxLength() == 10


def test_calendar_forbids_future_dates_and_starts_on_current_value():
    _qapp()
    field = DateField()
    field.set_date(date(2020, 3, 15))

    field.open_calendar()
    calendar = field._popup.calendar
    try:
        assert field._popup.isVisible()
        assert calendar.maximumDate() == QDate.currentDate()  # будущее недоступно
        assert calendar.selectedDate() == QDate(2020, 3, 15)
        assert calendar.locale().language() == calendar.locale().Language.Russian
    finally:
        field._popup.close()


def test_calendar_starts_on_today_when_field_is_empty_or_invalid():
    _qapp()
    field = DateField()
    field.setText("32.13.")

    field.open_calendar()
    try:
        assert field._popup.calendar.selectedDate() == QDate.currentDate()
    finally:
        field._popup.close()


def test_choosing_a_day_fills_field_and_closes_calendar():
    _qapp()
    field = DateField()
    field.open_calendar()

    field._popup.calendar.clicked.emit(QDate(2020, 6, 7))

    assert field.text() == "07.06.2020"
    assert not field._popup.isVisible()


def test_today_and_clear_buttons():
    _qapp()
    field = DateField()
    field.set_date(date(2020, 1, 1))
    field.open_calendar()
    assert set(_popup_buttons(field)) == {"Сегодня", "Очистить"}

    _popup_buttons(field)["Сегодня"].click()
    assert field.value() == date.today()

    field.open_calendar()
    _popup_buttons(field)["Очистить"].click()
    assert field.text() == ""
    assert not field._popup.isVisible()


def test_required_field_has_no_clear_button_in_calendar():
    _qapp()
    field = DateField(allow_empty=False)

    assert list(_popup_buttons(field)) == ["Сегодня"]


@pytest.mark.parametrize(
    ("reason", "opens"),
    [
        (Qt.FocusReason.MouseFocusReason, True),
        (Qt.FocusReason.TabFocusReason, True),
        (Qt.FocusReason.BacktabFocusReason, True),
        # возврат фокуса после закрытия календаря / активации окна — не открывать заново
        (Qt.FocusReason.PopupFocusReason, False),
        (Qt.FocusReason.ActiveWindowFocusReason, False),
    ],
)
def test_calendar_opens_on_focus_only_for_user_focus_reasons(reason, opens):
    _qapp()
    field = DateField()
    try:
        _focus_in(field, reason)
        assert field._popup.isVisible() is opens
    finally:
        field._popup.close()
