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
        data_browser_btn = next(b for b in buttons if b.text() == "Просмотр исходных данных")
        data_browser_btn.click()

    mock_dialog_cls.assert_called_once_with(engine, window)
    mock_dialog_cls.return_value.exec.assert_called_once()


def test_aggregate_view_button_opens_aggregate_view_dialog():
    _qapp()
    engine = get_engine(":memory:")
    window = MainWindow(engine)

    with patch("meteoregik.ui.main_window.AggregateViewDialog") as mock_dialog_cls:
        buttons = window.centralWidget().findChildren(QPushButton)
        aggregate_view_btn = next(b for b in buttons if b.text() == "Просмотр агрегатных данных")
        aggregate_view_btn.click()

    mock_dialog_cls.assert_called_once_with(engine, window)
    mock_dialog_cls.return_value.exec.assert_called_once()


def test_center_on_screen_places_window_in_the_middle_of_available_area():
    _qapp()
    window = MainWindow(get_engine(":memory:"))
    window.resize(400, 300)  # заведомо помещается на любой экран
    window.move(0, 0)

    window.center_on_screen()

    available = window.screen().availableGeometry()
    center, target = window.frameGeometry().center(), available.center()
    assert abs(center.x() - target.x()) <= 2
    assert abs(center.y() - target.y()) <= 2


def test_center_on_screen_shrinks_and_keeps_window_inside_a_small_screen():
    _qapp()
    window = MainWindow(get_engine(":memory:"))
    available = window.screen().availableGeometry()
    window.resize(available.width() + 500, available.height() + 500)

    window.center_on_screen()

    assert window.width() <= available.width()
    assert window.height() <= available.height()
    assert window.x() >= available.left()
    assert window.y() >= available.top()


def test_no_button_has_focus_ring_at_startup():
    _qapp()
    window = MainWindow(get_engine(":memory:"))
    window.show()
    QApplication.processEvents()

    assert not isinstance(window.focusWidget(), QPushButton)
    window.close()



def test_station_directory_button_opens_station_directory_dialog():
    _qapp()
    engine = get_engine(":memory:")
    window = MainWindow(engine)

    with patch("meteoregik.ui.main_window.StationDirectoryDialog") as mock_dialog_cls:
        buttons = window.centralWidget().findChildren(QPushButton)
        directory_btn = next(b for b in buttons if b.text() == "Справочники")
        directory_btn.click()

    mock_dialog_cls.assert_called_once_with(engine, window)
    mock_dialog_cls.return_value.exec.assert_called_once()


def test_window_title_shows_application_version():
    """Номер версии в заголовке — тот же, что в pyproject.toml и в имени
    установщика: по нему пользователь называет свою версию."""
    import tomllib
    from pathlib import Path

    _qapp()
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    window = MainWindow(get_engine(":memory:"))

    assert window.windowTitle() == f"meteoRegiK {project['project']['version']}"
