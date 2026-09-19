from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton

from meteoregik.db.session import get_engine
from meteoregik.ui.main_window import MainWindow


def _qapp():
    return QApplication.instance() or QApplication([])


def test_main_window_has_no_menu():
    _qapp()
    engine = get_engine(":memory:")
    window = MainWindow(engine)

    assert window.menuBar().actions() == []


def test_import_button_under_greeting_opens_import_dialog():
    _qapp()
    engine = get_engine(":memory:")
    window = MainWindow(engine)

    with patch("meteoregik.ui.main_window.ImportDialog") as mock_dialog_cls:
        button = window.centralWidget().findChild(QPushButton)
        assert button is not None
        button.click()

    mock_dialog_cls.assert_called_once_with(engine, window)
    mock_dialog_cls.return_value.exec.assert_called_once()


def test_data_browser_button_opens_data_browser_dialog():
    _qapp()
    engine = get_engine(":memory:")
    window = MainWindow(engine)

    with patch("meteoregik.ui.main_window.DataBrowserDialog") as mock_dialog_cls:
        buttons = window.centralWidget().findChildren(QPushButton)
        data_browser_btn = next(b for b in buttons if "Просмотр" in b.text())
        data_browser_btn.click()

    mock_dialog_cls.assert_called_once_with(engine, window)
    mock_dialog_cls.return_value.exec.assert_called_once()
