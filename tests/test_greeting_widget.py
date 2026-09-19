from PySide6.QtWidgets import QApplication, QPushButton

from meteoregik.ui.greeting_widget import GreetingWidget


def _qapp():
    return QApplication.instance() or QApplication([])


def test_greeting_widget_emits_import_requested_on_button_click():
    _qapp()
    widget = GreetingWidget()

    emitted = {"n": 0}
    widget.import_requested.connect(lambda: emitted.__setitem__("n", emitted["n"] + 1))

    button = widget.findChild(QPushButton)
    assert button is not None
    button.click()

    assert emitted["n"] == 1


def test_greeting_widget_labels_have_transparent_background():
    _qapp()
    widget = GreetingWidget()

    for label in (widget._date_label, widget._clock_label):
        assert "background: transparent" in label.styleSheet()


def test_greeting_widget_emits_data_browser_requested_on_button_click():
    _qapp()
    widget = GreetingWidget()

    emitted = {"n": 0}
    widget.data_browser_requested.connect(lambda: emitted.__setitem__("n", emitted["n"] + 1))

    buttons = widget.findChildren(QPushButton)
    assert len(buttons) == 2
    data_browser_btn = next(b for b in buttons if "Просмотр" in b.text())
    data_browser_btn.click()

    assert emitted["n"] == 1


def test_greeting_widget_buttons_use_different_accent_colors():
    _qapp()
    widget = GreetingWidget()

    buttons = widget.findChildren(QPushButton)
    import_btn = next(b for b in buttons if "Импорт" in b.text())
    data_browser_btn = next(b for b in buttons if "Просмотр" in b.text())

    assert import_btn.styleSheet() != data_browser_btn.styleSheet()
