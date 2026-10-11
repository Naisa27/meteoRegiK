"""Путеводитель по приложению: текст (meteoregik/guide.py), окно
(ui/guide_window.py), кнопка на главном экране и то, что открытый экран не
блокирует окно путеводителя."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from meteoregik import guide
from meteoregik.db.session import get_engine
from meteoregik.ui.dialogs import exec_window_modal
from meteoregik.ui.greeting_widget import GreetingWidget
from meteoregik.ui.guide_window import GuideWindow
from meteoregik.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


# --- текст ---


def test_section_keys_and_titles_are_unique():
    assert len({s.key for s in guide.SECTIONS}) == len(guide.SECTIONS)
    assert len({s.title for s in guide.SECTIONS}) == len(guide.SECTIONS)


def test_every_main_screen_button_has_a_section_with_the_same_title(qapp):
    """Новая кнопка на главном экране без раздела в путеводителе — тест падает."""
    widget = GreetingWidget()
    screens = {b.text() for b in widget.findChildren(QPushButton)} - {guide.GUIDE_TITLE}

    assert screens <= {s.title for s in guide.SECTIONS}


def test_section_groups_repeat_main_screen_captions(qapp):
    widget = GreetingWidget()
    captions = {label.text() for label in widget.findChildren(QLabel)}

    for group in {s.group for s in guide.SECTIONS if s.group is not None}:
        assert group.upper() in captions


def test_links_between_sections_lead_to_existing_sections():
    for section in guide.SECTIONS:
        for key in re.findall(rf'href="{guide.LINK_SCHEME}:([^"]+)"', section.body):
            assert guide.find_section(key) is not None, f"{section.key}: ссылка на «{key}»"


def test_section_html_starts_with_title_and_has_no_unfilled_marks():
    for section in guide.SECTIONS:
        html = guide.section_html(section)
        assert html.startswith(f"<h1>{section.title}</h1>")
        assert "{{" not in html


def test_data_section_names_the_real_folder_when_it_is_known():
    section = guide.find_section("data")
    folder = Path("C:/Users/tester/Documents/meteoRegiK")

    assert str(folder) in guide.section_html(section, folder)
    assert "%LOCALAPPDATA%" not in guide.section_html(section, folder)
    assert "%LOCALAPPDATA%" in guide.section_html(section)


# --- окно ---


def _nav_keys(window: GuideWindow) -> list[str]:
    keys = (window.nav.item(row).data(Qt.ItemDataRole.UserRole) for row in range(window.nav.count()))
    return [key for key in keys if key is not None]


def test_guide_window_lists_all_sections_and_opens_on_the_first(qapp):
    window = GuideWindow()

    assert _nav_keys(window) == [s.key for s in guide.SECTIONS]
    assert window.current_key() == guide.SECTIONS[0].key
    assert guide.SECTIONS[0].title in window.text.toPlainText()


def test_group_captions_in_contents_cannot_be_selected(qapp):
    window = GuideWindow()
    captions = [
        window.nav.item(row) for row in range(window.nav.count())
        if window.nav.item(row).data(Qt.ItemDataRole.UserRole) is None
    ]

    assert guide.GROUP_DATA.upper() in {item.text() for item in captions}
    assert all(not item.flags() & Qt.ItemFlag.ItemIsSelectable for item in captions)


def test_show_section_switches_text_and_ignores_unknown_key(qapp):
    window = GuideWindow()

    window.show_section("map")
    assert window.current_key() == "map"
    assert "Проиграть" in window.text.toPlainText()

    window.show_section("нет такого")
    assert window.current_key() == "map"


def test_link_in_text_opens_another_section(qapp):
    window = GuideWindow()

    window.text.anchorClicked.emit(QUrl(f"{guide.LINK_SCHEME}:anomalies"))

    assert window.current_key() == "anomalies"


def test_on_top_checkbox_sets_window_flag_and_keeps_window_visible(qapp):
    window = GuideWindow()
    window.show()

    window.on_top_check.setChecked(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.isVisible()

    window.on_top_check.setChecked(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.close()


# --- главный экран ---


def test_guide_button_emits_signal(qapp):
    widget = GreetingWidget()
    emitted = []
    widget.guide_requested.connect(lambda: emitted.append(1))

    next(b for b in widget.findChildren(QPushButton) if b.text() == guide.GUIDE_TITLE).click()

    assert emitted == [1]


def test_guide_button_opens_one_parentless_window_and_main_window_closes_it(qapp):
    window = MainWindow(get_engine(":memory:"))
    window.show()

    window._greeting.guide_btn.click()
    opened = window._guide
    window._greeting.guide_btn.click()

    assert opened is not None and opened is window._guide
    assert opened.isVisible()
    # Без родителя: иначе экран, модальный для главного окна, блокировал бы и путеводитель
    assert opened.parent() is None

    window.close()
    assert not opened.isVisible()


def test_main_window_passes_database_folder_to_guide(qapp, tmp_path):
    window = MainWindow(get_engine(tmp_path / "meteoregik.sqlite3"))

    assert window._data_dir() == tmp_path.resolve()
    assert MainWindow(get_engine(":memory:"))._data_dir() is None


def test_screens_open_modal_to_main_window_only(qapp):
    window = MainWindow(get_engine(":memory:"))

    with patch("meteoregik.ui.main_window.ImportDialog") as dialog_cls:
        window._open_import_dialog()

    dialog_cls.return_value.setWindowModality.assert_called_once_with(Qt.WindowModality.WindowModal)
    dialog_cls.return_value.exec.assert_called_once()


def test_exec_window_modal_keeps_window_modality_while_dialog_is_open(qapp):
    """exec() не должен превращать окно в модальное для всего приложения."""
    from PySide6.QtCore import QTimer

    dialog = QDialog()
    seen = []

    def probe() -> None:
        seen.append(dialog.windowModality())
        dialog.accept()

    QTimer.singleShot(0, probe)
    exec_window_modal(dialog)

    assert seen == [Qt.WindowModality.WindowModal]
