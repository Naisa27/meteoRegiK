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
