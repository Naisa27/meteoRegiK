"""Экран "Справочник станций": фильтры, правка ячеек, добавление станций и
регионов, обновление из файла."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QMessageBox, QStyleOptionViewItem

import meteoregik.ui.station_directory as station_directory
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import gg_readings, ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.ui.station_directory import (
    AddRegionDialog,
    AddStationDialog,
    StationDirectoryDialog,
    StationsDelegate,
)


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(ru_regions.insert(), [{"code": 40, "region": "Калужская область"}, {"code": 32, "region": "Брянская область"}])
        conn.execute(
            stations.insert(),
            [
                {"name": "Сухиничи", "wmo_index": 27707, "country": "ru", "state": "40", "lat": 54.1, "lon": 35.35},
                {"name": "Мосальск", "wmo_index": 27704, "country": "ru", "state": "40", "lat": 54.5, "lon": 34.98},
                {"name": "Брянск", "wmo_index": 26898, "country": "ru", "state": "32", "lat": 53.25, "lon": 34.32},
                {"name": "Минск", "wmo_index": 26850, "country": "by", "state": "by01", "lat": 53.9, "lon": 27.5},
                {"name": "Новая", "wmo_index": 99001, "country": "ru", "state": None, "lat": None, "lon": None},
            ],
        )
        conn.execute(gg_readings.insert().values(wmo_index=27704, obs_date=date(2020, 1, 1), t_avg=1.0))
    return engine


@pytest.fixture
def dialog(engine):
    _qapp()
    return StationDirectoryDialog(engine, run_async=False)


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    return shown


def _names(dialog) -> list[str]:
    proxy = dialog.stations_proxy
    return [proxy.data(proxy.index(row, 1)) for row in range(proxy.rowCount())]


def _proxy_index(dialog, wmo_index: int, column: int):
    proxy = dialog.stations_proxy
    for row in range(proxy.rowCount()):
        if proxy.data(proxy.index(row, 0)) == str(wmo_index):
            return proxy.index(row, column)
    raise AssertionError(f"станции {wmo_index} нет в таблице")


# --- открытие и фильтры ---


def test_opens_with_all_stations_sorted_by_name(dialog):
    assert _names(dialog) == ["Брянск", "Минск", "Мосальск", "Новая", "Сухиничи"]
    assert dialog.count_label.text() == "Показано 5 из 5"


def test_country_and_region_shown_by_name(dialog):
    proxy = dialog.stations_proxy
    assert proxy.data(_proxy_index(dialog, 27704, 2)) == "Россия"
    assert proxy.data(_proxy_index(dialog, 27704, 3)) == "Калужская область"
    assert proxy.data(_proxy_index(dialog, 26850, 3)) == "by01"  # не РФ — регион как есть


def test_name_filter_is_case_insensitive_substring(dialog):
    dialog.name_filter.setText("СК")
    assert _names(dialog) == ["Брянск", "Минск", "Мосальск"]
    assert dialog.count_label.text() == "Показано 3 из 5"


def test_wmo_filter_matches_digits_of_index(dialog):
    dialog.wmo_filter.setText("2770")
    assert _names(dialog) == ["Мосальск", "Сухиничи"]


def test_country_filter_and_region_filter(dialog):
    dialog.country_filter.setCurrentIndex(dialog.country_filter.findData("by"))
    assert _names(dialog) == ["Минск"]
    assert not dialog.region_filter.isEnabled()  # регион РФ — только для России

    dialog.country_filter.setCurrentIndex(0)
    dialog.region_filter.setCurrentIndex(dialog.region_filter.findData(40))
    assert _names(dialog) == ["Мосальск", "Сухиничи"]
    assert dialog.country_filter.currentData() == "ru"  # выбор региона подставляет Россию


def test_missing_coordinates_filter_with_count(dialog):
    assert dialog.missing_coordinates_check.text() == "Только без координат (1)"
    dialog.missing_coordinates_check.setChecked(True)
    assert _names(dialog) == ["Новая"]


def test_reset_filters(dialog):
    dialog.name_filter.setText("xyz")
    dialog.missing_coordinates_check.setChecked(True)
    dialog.reset_filters_btn.click()
    assert dialog.name_filter.text() == ""
    assert not dialog.missing_coordinates_check.isChecked()
    assert len(_names(dialog)) == 5


# --- правка ячеек ---


def test_edit_name_saves_and_reports(dialog, engine):
    index = _proxy_index(dialog, 27707, 1)
    assert dialog.stations_proxy.setData(index, "Сухиничи-Главная", Qt.ItemDataRole.EditRole)
    assert "Сохранено" in dialog.stations_status.text()
    assert dialog.stations_proxy.data(_proxy_index(dialog, 27707, 7)) != ""  # "Изменено" заполнилось
    with engine.connect() as conn:
        assert conn.execute(stations.select().where(stations.c.wmo_index == 27707)).mappings().one()["name"] == "Сухиничи-Главная"


def test_invalid_edit_shows_error_and_keeps_value(dialog):
    index = _proxy_index(dialog, 27707, 4)
    assert not dialog.stations_proxy.setData(index, "95", Qt.ItemDataRole.EditRole)
    assert dialog.stations_status.text().startswith("Широта должна быть от")
    assert dialog.stations_proxy.data(index) == "54.1"


def test_wmo_index_locked_only_for_station_with_data(dialog):
    locked = _proxy_index(dialog, 27704, 0)
    free = _proxy_index(dialog, 99001, 0)
    assert not (dialog.stations_proxy.flags(locked) & Qt.ItemFlag.ItemIsEditable)
    assert "уже загружены данные" in dialog.stations_proxy.data(locked, Qt.ItemDataRole.ToolTipRole)
    assert dialog.stations_proxy.flags(free) & Qt.ItemFlag.ItemIsEditable
    assert not (dialog.stations_proxy.flags(_proxy_index(dialog, 99001, 7)) & Qt.ItemFlag.ItemIsEditable)


def test_region_editor_is_list_for_russia_and_text_elsewhere(dialog):
    delegate = StationsDelegate(dialog.stations_table)
    option = QStyleOptionViewItem()
    ru_editor = delegate.createEditor(dialog.stations_table, option, _proxy_index(dialog, 99001, 3))
    assert isinstance(ru_editor, QComboBox)
    assert ru_editor.findData("40") > 0

    ru_editor.setCurrentIndex(ru_editor.findData("32"))
    delegate.setModelData(ru_editor, dialog.stations_proxy, _proxy_index(dialog, 99001, 3))
    assert dialog.stations_proxy.data(_proxy_index(dialog, 99001, 3)) == "Брянская область"

    foreign_editor = delegate.createEditor(dialog.stations_table, option, _proxy_index(dialog, 26850, 3))
    assert not isinstance(foreign_editor, QComboBox)


def test_country_editor_resets_region(dialog):
    delegate = StationsDelegate(dialog.stations_table)
    editor = delegate.createEditor(dialog.stations_table, QStyleOptionViewItem(), _proxy_index(dialog, 27707, 2))
    editor.setCurrentIndex(editor.findData("by"))
    delegate.setModelData(editor, dialog.stations_proxy, _proxy_index(dialog, 27707, 2))
    assert dialog.stations_proxy.data(_proxy_index(dialog, 27707, 2)) == "Беларусь"
    assert dialog.stations_proxy.data(_proxy_index(dialog, 27707, 3)) == ""


# --- добавление ---


def test_add_station_dialog_rejects_duplicate_and_keeps_input(engine):
    _qapp()
    add = AddStationDialog(engine, {40: "Калужская область"})
    add.wmo_edit.setText("27704")
    add.name_edit.setText("Дубль")
    add._try_accept()
    assert add.result() != QDialog.DialogCode.Accepted
    assert "уже занят станцией «Мосальск»" in add.error_label.text()
    assert add.name_edit.text() == "Дубль"


def test_add_station_dialog_region_field_follows_country(engine):
    _qapp()
    add = AddStationDialog(engine, {40: "Калужская область"})
    assert add.region_stack.currentWidget() is add.region_combo
    add.country_combo.setCurrentIndex(add.country_combo.findData("kz"))
    assert add.region_stack.currentWidget() is add.region_edit


def test_add_station_selects_new_row_even_if_filtered_out(dialog, monkeypatch):
    def fake_exec(self):
        self.wmo_edit.setText("27705")
        self.name_edit.setText("Юхнов")
        self.region_combo.setCurrentIndex(self.region_combo.findData("40"))
        self._try_accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddStationDialog, "exec", fake_exec)
    dialog.name_filter.setText("Брянск")
    dialog.add_station_btn.click()

    assert dialog.name_filter.text() == ""  # фильтр мешал увидеть новую станцию — сброшен
    selected = dialog.stations_table.selectionModel().selectedRows()
    assert dialog.stations_proxy.data(selected[0].siblingAtColumn(1)) == "Юхнов"
    assert "добавлена" in dialog.stations_status.text()


def test_regions_tab_rename_and_add(dialog, monkeypatch):
    model = dialog.regions_model
    code_index, region_index = model.index(1, 0), model.index(1, 1)  # 40 — Калужская
    assert not (model.flags(code_index) & Qt.ItemFlag.ItemIsEditable)
    assert model.setData(region_index, "Калужская обл.", Qt.ItemDataRole.EditRole)
    assert "Сохранено" in dialog.regions_status.text()
    # новое название сразу видно и в списке регионов на вкладке станций
    dialog._reload_all()
    assert dialog.region_filter.findText("Калужская обл. (40)") > 0

    def fake_exec(self):
        self.code_edit.setText("77")
        self.region_edit.setText("Москва")
        self._try_accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddRegionDialog, "exec", fake_exec)
    dialog.add_region_btn.click()
    assert [r.code for r in model.records] == [32, 40, 77]


# --- обновление из файла ---


def _stations_file(tmp_path, rows):
    path = tmp_path / "stations.xlsx"
    pd.DataFrame(rows, columns=["id", "wmo", "name", "country", "state", "lat", "lon"]).to_excel(path, index=False)
    return str(path)


def test_import_stations_from_file_reports_and_reloads(dialog, tmp_path, monkeypatch):
    path = _stations_file(tmp_path, [(1, 27704, "Мосальск", "ru", 40, 54.6, 35.0), (2, 27705, "Юхнов", "ru", 40, 54.7, 35.2)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (path, ""))
    monkeypatch.setattr(station_directory, "ask_yes_no", lambda *a, **k: True)

    dialog.import_stations_btn.click()

    assert dialog.stations_status.text() == "Файл «stations.xlsx»: добавлено 1, обновлено 1."
    assert "Юхнов" in _names(dialog)
    assert not dialog.loading_overlay.isVisible()


def test_import_wrong_file_warns_and_changes_nothing(dialog, tmp_path, monkeypatch, messages):
    path = tmp_path / "regions.xlsx"
    pd.DataFrame([(1, "Адыгея")], columns=["code", "region"]).to_excel(path, index=False)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(station_directory, "ask_yes_no", lambda *a, **k: True)

    dialog.import_stations_btn.click()

    assert len(messages) == 1 and "нет столбцов" in messages[0] and "Справочник не изменён" in messages[0]
    assert len(_names(dialog)) == 5


def test_import_cancelled_at_confirmation(dialog, tmp_path, monkeypatch):
    path = _stations_file(tmp_path, [(1, 27705, "Юхнов", "ru", 40, 54.7, 35.2)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (path, ""))
    monkeypatch.setattr(station_directory, "ask_yes_no", lambda *a, **k: False)

    dialog.import_stations_btn.click()

    assert "Юхнов" not in _names(dialog)
