from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from meteoregik.db.session import get_engine
from meteoregik.ui.main_window import MainWindow
from meteoregik.ui.whats_new import WhatsNewDialog, whats_new_title
from meteoregik.version import app_version


def test_whats_new_button_opens_window_for_installed_version() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(get_engine(":memory:"))
    assert app_version() in window._greeting.whats_new_btn.text()
    with patch("meteoregik.ui.main_window.exec_window_modal") as show:
        window._greeting.whats_new_btn.click()
    dialog = show.call_args.args[0]
    assert isinstance(dialog, WhatsNewDialog)
    assert dialog.windowTitle() == whats_new_title()
    assert "Предварительные" in dialog.text.toPlainText()
    assert "rp5" in dialog.text.toPlainText()
    window.close()
