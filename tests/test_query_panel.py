"""Экран "Просмотр агрегатных данных": фильтры (станция обязательна, период
опционален -> весь диапазон), переключение таблица/график, тип вывода,
показатели, запоминание выбора в Preferences, экспорт в Excel."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QElapsedTimer, QEventLoop, QStandardPaths, Qt, QThread, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton, QWidget

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.preferences import Preferences
from meteoregik.reports.parameters import PARAMETER_LABELS
from meteoregik.ui.query_panel import AggregateViewDialog, _ParametersDialog, suggest_export_filename
from meteoregik.ui.empty_state import NO_DATA_HINT

WMO_INDEX = 27704


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", text))
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", text))
    )
    return shown


def _row(period_type, start, end, stat_type, value, parameter="T"):
    return {
        "wmo_index": WMO_INDEX,
        "period_type": period_type,
        "period_start": start,
        "period_end": end,
        "parameter": parameter,
        "stat_type": stat_type,
        "value": value,
    }


def _make_engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(ru_regions.insert().values(code=40, region="Калужская область"))
        conn.execute(stations.insert().values(name="Мосальск", wmo_index=WMO_INDEX, country="ru", state="40"))
        conn.execute(
            computed_indicators.insert(),
            [
                _row("year", date(2020, 1, 1), date(2020, 12, 31), "avg", 5.0),
                _row("year", date(2020, 1, 1), date(2020, 12, 31), "min", -20.0),
                _row("year", date(2020, 1, 1), date(2020, 12, 31), "max", 25.0),
                _row("year", date(2021, 1, 1), date(2021, 12, 31), "avg", 6.0),
                _row("month", date(2020, 1, 1), date(2020, 1, 31), "avg", -6.13),
                _row("decade", date(2020, 1, 1), date(2020, 1, 10), "avg", -13.03),
                _row("day", date(2020, 1, 1), date(2020, 1, 1), "avg", -8.11),
            ],
        )
    return engine


def _dialog(tmp_path, prefs=None):
    _qapp()
    engine = _make_engine(tmp_path)
    return engine, AggregateViewDialog(engine, preferences=prefs or Preferences(None), run_async=False)


def _select_station(dialog):
    dialog.station_combo.select_wmo(WMO_INDEX)


def _rows(dialog) -> int:
    model = dialog.table.model()
    return 0 if model is None else model.rowCount()


def _cols(dialog) -> int:
    model = dialog.table.model()
    return 0 if model is None else model.columnCount()


def _text(dialog, row, col) -> str:
    model = dialog.table.model()
    return model.data(model.index(row, col))


# --- открытие экрана ---


def test_export_button_disabled_on_open(tmp_path):
    _, dialog = _dialog(tmp_path)
    assert not dialog.export_btn.isEnabled()


def test_station_combo_lists_only_stations_with_computed_indicators(tmp_path):
    _, dialog = _dialog(tmp_path)
    labels = [dialog.station_combo.itemText(i) for i in range(dialog.station_combo.count())]
    assert labels == ["Мосальск, Калужская область (27704)"]


def test_output_mode_radios_stay_enabled_for_chart_view(tmp_path):
    # Требование 2026-09-23, п.8: "Тип вывода" доступен и для вида "график"
    # (гранулярность точек графика тоже берётся из этого фильтра) — радиокнопки
    # не должны скрываться/блокироваться при переключении на "График".
    _, dialog = _dialog(tmp_path)
    dialog.chart_radio.setChecked(True)
    for radio in (dialog.detailed_radio, dialog.monthly_radio, dialog.yearly_radio):
        assert not radio.isHidden()
        assert radio.isEnabled()


def test_station_combo_is_empty_with_placeholder_on_open(tmp_path):
    # Требование 2026-09-23, п.7: фильтр "Метеостанция" всегда пуст при
    # открытии экрана, с плейсхолдером "Выберите метеостанцию".
    _, dialog = _dialog(tmp_path)
    assert dialog.station_combo.currentText() == ""
    assert dialog.station_combo.lineEdit().placeholderText() == "Выберите метеостанцию"
    with pytest.raises(ValueError):
        dialog.station_combo.selected_wmo()


def test_dialog_resizes_to_match_parent_window(tmp_path):
    # Требование 2026-09-23, п.2: модалка того же размера, что основное окно.
    _qapp()
    engine = _make_engine(tmp_path)
    parent = QWidget()
    parent.resize(1234, 876)
    dialog = AggregateViewDialog(engine, parent, preferences=Preferences(None), run_async=False)
    assert dialog.size() == parent.size()


def test_params_group_shows_default_selection_count(tmp_path):
    _, dialog = _dialog(tmp_path)
    assert dialog.params_label.text() == "Выбрано: 3 из 3"


# --- валидация фильтров ---


def test_apply_without_station_shows_warning(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.apply_btn.click()
    assert messages == [("warning", "Выберите метеостанцию из списка.")]
    assert not dialog.export_btn.isEnabled()


def test_apply_without_any_parameter_shows_warning(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog._selected_parameter_keys = set()

    dialog.apply_btn.click()

    assert messages == [("warning", "Выберите хотя бы один показатель.")]


def test_apply_with_only_one_period_date_shows_warning(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.date_from_edit.set_date(date(2020, 1, 1))

    dialog.apply_btn.click()

    assert len(messages) == 1 and messages[0][0] == "warning"


def test_apply_with_from_after_to_shows_warning(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.date_from_edit.set_date(date(2020, 6, 1))
    dialog.date_to_edit.set_date(date(2020, 1, 1))

    dialog.apply_btn.click()

    assert messages == [("warning", "Начало периода позже его конца.")]


# --- применение фильтров: режимы вывода ---


def test_apply_empty_period_uses_full_station_bounds_and_fills_table(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)

    dialog.apply_btn.click()

    assert messages == []
    assert _rows(dialog) > 0
    assert dialog.export_btn.isEnabled()
    assert dialog._stack.currentWidget() is dialog.table


def test_apply_detailed_mode_places_day_and_decade_values(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.apply_btn.click()

    # строка "t средняя" января 2020 должна содержать день 1 (-8.11)
    found = any(
        _text(dialog, row, 0) == "t средняя" and _text(dialog, row, 1) == "-8.11" for row in range(_rows(dialog))
    )
    assert found


def test_apply_monthly_mode_switches_table_layout(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.monthly_radio.setChecked(True)

    dialog.apply_btn.click()

    assert _cols(dialog) == 14  # показатель + 12 месяцев + "за год"


def test_apply_yearly_mode_switches_table_layout(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.yearly_radio.setChecked(True)
    dialog.date_from_edit.set_date(date(2020, 1, 1))
    dialog.date_to_edit.set_date(date(2021, 12, 31))

    dialog.apply_btn.click()

    assert _cols(dialog) == 3  # показатель + 2020 + 2021


def test_apply_chart_mode_plots_and_switches_stack(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.chart_radio.setChecked(True)

    dialog.apply_btn.click()

    assert dialog._stack.currentWidget() is dialog.chart_view
    assert len(dialog.chart_view._ax.get_lines()) == 3  # t средняя/Мин/макс по умолчанию


def test_chart_granularity_follows_output_mode(tmp_path):
    # Требование 2026-09-23, п.8: гранулярность графика = выбранный "Тип
    # вывода", даже когда вид — "график".
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.chart_radio.setChecked(True)
    dialog.yearly_radio.setChecked(True)
    dialog.date_from_edit.set_date(date(2020, 1, 1))
    dialog.date_to_edit.set_date(date(2021, 12, 31))

    dialog.apply_btn.click()

    line = dialog.chart_view._ax.get_lines()[0]
    assert len(line.get_xdata()) == 2  # 2020, 2021 — годовая гранулярность, не дневная


# --- показатели (модальный выбор, по аналогии с "Выбрать поля для отображения") ---


def test_parameters_dialog_initial_check_state_matches_selected():
    _qapp()
    dialog = _ParametersDialog(selected={("T", "avg"), ("T", "max")})
    assert set(dialog.selected_parameters()) == {("T", "avg"), ("T", "max")}


def test_parameters_dialog_select_all_button():
    _qapp()
    dialog = _ParametersDialog(selected=set())
    select_all_btn = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Выбрать все")
    select_all_btn.click()
    assert set(dialog.selected_parameters()) == set(PARAMETER_LABELS)


def test_parameters_dialog_clear_all_button():
    _qapp()
    dialog = _ParametersDialog(selected=set(PARAMETER_LABELS))
    clear_btn = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Снять все")
    clear_btn.click()
    assert dialog.selected_parameters() == []


def test_choose_parameters_updates_selection_and_label(tmp_path, monkeypatch):
    _, dialog = _dialog(tmp_path)

    def fake_exec(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if key == ("T", "avg") else Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_ParametersDialog, "exec", fake_exec)
    dialog.params_btn.click()

    assert dialog._selected_parameter_keys == {("T", "avg")}
    assert dialog.params_label.text() == "Выбрано: 1 из 3"


def test_choose_parameters_cancel_keeps_previous_selection(tmp_path, monkeypatch):
    _, dialog = _dialog(tmp_path)
    monkeypatch.setattr(_ParametersDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    dialog.params_btn.click()

    assert dialog.params_label.text() == "Выбрано: 3 из 3"


# --- запоминание фильтров ---


def test_preferences_are_restored_on_reopen_except_station(tmp_path):
    prefs = Preferences(tmp_path / "prefs.json")
    engine, dialog = _dialog(tmp_path, prefs)
    _select_station(dialog)
    dialog._selected_parameter_keys = {("T", "avg"), ("T", "max")}
    dialog.monthly_radio.setChecked(True)
    dialog.chart_radio.setChecked(True)

    dialog.apply_btn.click()

    reopened = AggregateViewDialog(engine, preferences=prefs, run_async=False)
    # станция — требование 2026-09-23, п.7: НЕ запоминается, поле пустое
    assert reopened.station_combo.currentText() == ""
    assert reopened._selected_parameter_keys == {("T", "avg"), ("T", "max")}
    assert reopened.params_label.text() == "Выбрано: 2 из 3"
    assert reopened.monthly_radio.isChecked()
    assert reopened.chart_radio.isChecked()


# --- экспорт в Excel ---


def test_export_without_chosen_path_does_nothing(tmp_path, monkeypatch, messages):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.apply_btn.click()

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    dialog.export_btn.click()

    assert ("info", "Файл сохранён.") not in messages


def test_export_writes_xlsx_file(tmp_path, monkeypatch, messages):
    import openpyxl

    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.apply_btn.click()

    out_path = tmp_path / "result.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))
    dialog.export_btn.click()

    assert out_path.exists()
    wb = openpyxl.load_workbook(out_path)
    assert wb.sheetnames == ["Данные", "График", "Данные для графика"]
    assert ("info", "Файл сохранён.") in messages


# --- когда какой фильтр применяется (корректировки 2026-09-25, п.7) ---


def test_output_mode_change_after_apply_rebuilds_table_without_apply(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.apply_btn.click()
    assert _cols(dialog) == 36

    dialog.monthly_radio.setChecked(True)

    assert _cols(dialog) == 14


def test_view_kind_change_after_apply_switches_immediately(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.apply_btn.click()

    dialog.chart_radio.setChecked(True)

    assert dialog._stack.currentWidget() is dialog.chart_view
    assert len(dialog.chart_view._ax.get_lines()) == 3


def test_display_options_before_first_apply_do_not_build_results(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)

    dialog.monthly_radio.setChecked(True)
    dialog.chart_radio.setChecked(True)

    assert _rows(dialog) == 0
    assert not dialog.export_btn.isEnabled()
    assert messages == []


def test_period_change_is_not_applied_until_apply_clicked(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.yearly_radio.setChecked(True)
    dialog.date_from_edit.set_date(date(2020, 1, 1))
    dialog.date_to_edit.set_date(date(2021, 12, 31))
    dialog.apply_btn.click()
    assert _cols(dialog) == 3  # показатель + 2020 + 2021

    # новый период без "Применить" — смена типа вывода берёт старый период
    dialog.date_to_edit.set_date(date(2020, 12, 31))
    dialog.monthly_radio.setChecked(True)
    dialog.yearly_radio.setChecked(True)

    assert _cols(dialog) == 3


def test_parameters_applied_on_done_without_apply(tmp_path, monkeypatch):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.yearly_radio.setChecked(True)
    dialog.apply_btn.click()
    assert _rows(dialog) == 4  # шапка + 3 показателя

    def fake_exec(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if key == ("T", "avg") else Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_ParametersDialog, "exec", fake_exec)
    dialog.params_btn.click()

    assert _rows(dialog) == 2  # шапка + "t средняя"


def test_parameters_dialog_done_with_empty_selection_stays_open():
    _qapp()
    dialog = _ParametersDialog(selected=set())
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not dialog.error_label.isHidden()


# --- раскладка (корректировки 2026-09-25, п.1) ---


def test_station_title_and_export_button_share_one_row(tmp_path):
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.apply_btn.click()
    dialog.show()
    QApplication.processEvents()

    label_center = dialog._station_label.geometry().center().y()
    button_center = dialog.export_btn.geometry().center().y()
    assert abs(label_center - button_center) <= 2
    dialog.close()


# --- корректировки 2026-09-25 (вторая партия) ---


def test_export_suggests_descriptive_filename_in_downloads(tmp_path, monkeypatch, messages):
    # п.4: при выгрузке предлагается понятное имя файла.
    _, dialog = _dialog(tmp_path)
    _select_station(dialog)
    dialog.monthly_radio.setChecked(True)
    dialog.date_from_edit.set_date(date(2020, 1, 1))
    dialog.date_to_edit.set_date(date(2021, 12, 31))
    dialog.apply_btn.click()

    proposed = []

    def fake_save(parent, caption, directory, *a, **k):
        proposed.append(directory)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_save)
    dialog.export_btn.click()

    assert Path(proposed[0]).name == "Мосальск (27704) — таблица, помесячно — 01.01.2020–31.12.2021.xlsx"
    downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    assert Path(proposed[0]).parent == Path(downloads)  # по умолчанию — "Загрузки"


def test_export_remembers_folder_for_next_time(tmp_path, monkeypatch, messages):
    prefs = Preferences(tmp_path / "prefs.json")
    _, dialog = _dialog(tmp_path, prefs)
    _select_station(dialog)
    dialog.apply_btn.click()
    out_dir = tmp_path / "выгрузки"
    out_dir.mkdir()

    proposed = []

    def fake_save(parent, caption, directory, *a, **k):
        proposed.append(directory)
        return str(out_dir / "result.xlsx"), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_save)
    dialog.export_btn.click()
    dialog.export_btn.click()

    assert Path(proposed[1]).parent == out_dir


@pytest.mark.parametrize(
    ("station", "view", "mode", "expected"),
    [
        ("Мосальск", "chart", "yearly", "Мосальск (27704) — график, по годам — 01.01.2020–31.12.2021.xlsx"),
        ('Ст. "Южная"/2', "table", "detailed", "Ст. _Южная__2 (27704) — таблица, подробно по дням — 01.01.2020–31.12.2021.xlsx"),
    ],
)
def test_suggest_export_filename(station, view, mode, expected):
    assert suggest_export_filename(station, 27704, view, mode, date(2020, 1, 1), date(2021, 12, 31)) == expected


def test_dialog_has_minimize_and_maximize_buttons(tmp_path):
    # п.3: кнопки "свернуть"/"развернуть" в заголовке окна.
    _, dialog = _dialog(tmp_path)
    flags = dialog.windowFlags()
    assert flags & Qt.WindowType.WindowMinimizeButtonHint
    assert flags & Qt.WindowType.WindowMaximizeButtonHint
    assert flags & Qt.WindowType.WindowCloseButtonHint


def test_minimizing_dialog_minimizes_owner_window(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    owner = QWidget()
    owner.show()
    dialog = AggregateViewDialog(engine, owner, preferences=Preferences(None), run_async=False)
    dialog.show()
    QApplication.processEvents()

    dialog.setWindowState(Qt.WindowState.WindowMinimized)
    QApplication.processEvents()

    assert owner.isMinimized()
    assert not dialog.isMinimized()
    dialog.close()
    owner.close()


def test_buttons_reserve_width_for_bold_focus_font(tmp_path):
    # п.2: в фокусе подпись "Экспорт в Excel" не должна обрезаться.
    _, dialog = _dialog(tmp_path)
    for button in (dialog.export_btn, dialog.apply_btn, dialog.params_btn):
        assert button.minimumWidth() >= button.sizeHint().width() + 20


def test_loader_is_shown_while_results_are_built(tmp_path, monkeypatch):
    # п.6: пока идёт выборка, поверх результата виден лоадер.
    import meteoregik.ui.query_panel as query_panel

    _, dialog = _dialog(tmp_path)
    dialog.show()
    _select_station(dialog)
    seen = []
    original = query_panel.build_results

    def spy(*args, **kwargs):
        seen.append(dialog.loading_overlay.isVisible())
        return original(*args, **kwargs)

    monkeypatch.setattr(query_panel, "build_results", spy)
    dialog.apply_btn.click()

    assert seen == [True]
    assert not dialog.loading_overlay.isVisible()
    assert "Загрузка данных" in dialog.loading_overlay.label.text()
    dialog.close()


def _wait_until(predicate, timeout_ms=5000):
    loop = QEventLoop()
    deadline = QElapsedTimer()
    deadline.start()
    while not predicate() and deadline.elapsed() < timeout_ms:
        QTimer.singleShot(10, loop.quit)
        loop.exec()
    return predicate()


def test_background_loading_delivers_last_request_in_gui_thread(tmp_path, monkeypatch):
    # Фоновый режим: результат приходит в GUI-поток; если пока грузилось,
    # переключили тип вывода — показывается результат последнего запроса.
    import meteoregik.ui.query_panel as query_panel

    _qapp()
    engine = _make_engine(tmp_path)
    dialog = AggregateViewDialog(engine, preferences=Preferences(None), run_async=True)
    _select_station(dialog)
    shown_in = []
    original_show = dialog._show_results

    def spy_show(*args, **kwargs):
        shown_in.append(QThread.currentThread() is QApplication.instance().thread())
        return original_show(*args, **kwargs)

    monkeypatch.setattr(dialog, "_show_results", spy_show)
    dialog.apply_btn.click()
    dialog.monthly_radio.setChecked(True)

    assert _wait_until(lambda: not dialog.loading_overlay.isVisible() and shown_in)
    assert shown_in == [True]  # первый (устаревший) результат отброшен
    assert _cols(dialog) == 14  # помесячно
    assert dialog.export_btn.isEnabled()
    QThreadPool.globalInstance().waitForDone()


def test_empty_database_shows_no_data_hint(tmp_path):
    _qapp()
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)

    dialog = AggregateViewDialog(engine, preferences=Preferences(None), run_async=False)

    assert dialog._stack.currentWidget() is dialog._no_data_hint
    assert dialog._no_data_hint.text() == NO_DATA_HINT


def test_database_with_data_opens_without_no_data_hint_and_shows_results(tmp_path):
    _engine, dialog = _dialog(tmp_path)
    assert dialog._stack.currentWidget() is not dialog._no_data_hint

    _select_station(dialog)
    dialog.apply_btn.click()

    assert dialog._stack.currentWidget() is dialog.table
