"""Экран "Просмотр исходных данных": пустая таблица при открытии, фильтр станции
по индексу ВМО, период (обе даты пусты или обе заполнены), выбор полей с
запоминанием по форматам, формат/выравнивание ячеек, доступность кнопок."""

from datetime import date, time, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from sqlalchemy import Date, Time

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import gg_readings, rp5_readings, ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.preferences import Preferences
from meteoregik.ui import data_browser
from meteoregik.ui.empty_state import NO_DATA_HINT
from meteoregik.ui.data_browser import (
    GG_SPEC,
    RP5_COLUMNS,
    RP5_SPEC,
    DataBrowserDialog,
    ReadingsTableModel,
    _parse_cell_value,
)

WMO_INDEX = 27704


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    """Перехват QMessageBox: (тип, текст) вместо модального окна."""
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", text))
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", text))
    )
    return shown


def _make_engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(ru_regions.insert().values(code=40, region="Калужская область"))
        conn.execute(stations.insert().values(name="Мосальск", wmo_index=WMO_INDEX, country="ru", state="40"))
        conn.execute(stations.insert().values(name="Другая", wmo_index=99999))
        conn.execute(
            rp5_readings.insert(),
            [
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0), "T_temperature": 1.0},
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 3, 1), "obs_time": time(9, 0), "T_temperature": 2.0},
                {"wmo_index": 99999, "obs_date": date(2020, 2, 1), "obs_time": time(12, 0), "T_temperature": 3.0},
            ],
        )
    return engine


def _dialog(tmp_path, prefs=None):
    _qapp()
    engine = _make_engine(tmp_path)
    return engine, DataBrowserDialog(engine, preferences=prefs or Preferences(None))


def _combo_items(dialog):
    combo = dialog.station_combo
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


# --- открытие экрана, кнопки под таблицей ---


def test_table_is_empty_on_open_and_bottom_buttons_disabled(tmp_path):
    _, dialog = _dialog(tmp_path)

    assert dialog._model.rowCount() == 0
    assert dialog._model.loaded is False
    assert not dialog.delete_btn.isEnabled()
    assert not dialog.prev_btn.isEnabled()
    assert not dialog.next_btn.isEnabled()
    assert dialog.add_btn.isEnabled()


def test_apply_loads_data_and_enables_delete(tmp_path, messages):
    _, dialog = _dialog(tmp_path)

    dialog.apply_btn.click()

    assert dialog._model.rowCount() == 3
    assert dialog.delete_btn.isEnabled()
    assert not dialog.prev_btn.isEnabled()  # единственная страница
    assert not dialog.next_btn.isEnabled()
    assert messages == []


def test_apply_with_no_matching_data_shows_message(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.station_combo.select_wmo(99999)
    dialog.date_from_edit.set_date(date(2020, 1, 1))
    dialog.date_to_edit.set_date(date(2020, 1, 1))  # у 99999 в этот день данных нет

    dialog.apply_btn.click()

    assert dialog._model.rowCount() == 0
    assert messages == [("info", "По указанным фильтрам данных не найдено.")]
    assert not dialog.delete_btn.isEnabled()


# --- фильтр "Метеостанция" ---


def test_station_combo_lists_name_region_and_index_only_for_stations_in_table(tmp_path):
    engine, dialog = _dialog(tmp_path)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Только ГГ", wmo_index=11111))
        conn.execute(gg_readings.insert().values(wmo_index=11111, obs_date=date(2020, 1, 1), t_avg=1.0))

    dialog._reload_station_choices()
    assert _combo_items(dialog) == [
        ("Все метеостанции", None),
        ("Другая (99999)", 99999),  # без региона — только название и индекс
        (f"Мосальск, Калужская область ({WMO_INDEX})", WMO_INDEX),
    ]

    dialog.gg_radio.click()
    assert _combo_items(dialog) == [("Все метеостанции", None), ("Только ГГ (11111)", 11111)]
    assert dialog._model.rowCount() == 0  # смена формата тоже не подгружает данные


def test_station_filter_uses_wmo_index(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.station_combo.select_wmo(WMO_INDEX)

    dialog.apply_btn.click()

    assert dialog._model.wmo_filter == WMO_INDEX
    assert isinstance(dialog._model.wmo_filter, int)
    assert dialog._model.rowCount() == 2


def test_station_filter_can_be_cleared_meaning_all_stations(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.station_combo.select_wmo(WMO_INDEX)
    assert dialog.station_combo.lineEdit().isClearButtonEnabled()

    dialog.station_combo.lineEdit().clear()  # как крестик в поле
    dialog.apply_btn.click()

    assert dialog._model.wmo_filter is None
    assert dialog._model.rowCount() == 3


def test_station_text_not_in_list_is_rejected(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.station_combo.setCurrentText("Несуществующая")

    dialog.apply_btn.click()

    assert messages == [("warning", "Выберите метеостанцию из списка или очистите поле.")]
    assert dialog._model.loaded is False


def test_station_combo_completer_filters_by_any_part_of_text(tmp_path):
    _, dialog = _dialog(tmp_path)
    completer = dialog.station_combo.completer()

    assert completer.filterMode() == Qt.MatchFlag.MatchContains
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive


# --- фильтр "Период" ---


def test_period_fields_are_empty_by_default_and_mean_whole_period(tmp_path, messages):
    _, dialog = _dialog(tmp_path)

    assert dialog.date_from_edit.text() == ""
    assert dialog.date_to_edit.text() == ""
    dialog.apply_btn.click()

    assert (dialog._model.date_from, dialog._model.date_to) == (None, None)
    assert dialog._model.rowCount() == 3
    assert messages == []


def test_period_with_both_dates_filters(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.date_from_edit.set_date(date(2020, 2, 1))
    dialog.date_to_edit.set_date(date(2020, 3, 1))

    dialog.apply_btn.click()

    assert dialog._model.rowCount() == 2
    assert messages == []


@pytest.mark.parametrize("filled", ["from", "to"])
def test_period_with_only_one_date_is_rejected(tmp_path, messages, filled):
    _, dialog = _dialog(tmp_path)
    field = dialog.date_from_edit if filled == "from" else dialog.date_to_edit
    field.set_date(date(2020, 2, 1))

    dialog.apply_btn.click()

    assert messages and messages[0][0] == "warning"
    assert "обе" in messages[0][1]
    assert dialog._model.loaded is False


def test_reversed_period_is_rejected(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.date_from_edit.set_date(date(2020, 3, 1))
    dialog.date_to_edit.set_date(date(2020, 1, 1))

    dialog.apply_btn.click()

    assert messages == [("warning", "Начало периода позже его конца.")]
    assert dialog._model.loaded is False


def test_future_and_malformed_period_dates_are_rejected(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    dialog.date_from_edit.set_date(date(2020, 1, 1))
    dialog.date_to_edit.set_date(date.today() + timedelta(days=1))

    dialog.apply_btn.click()
    assert messages[-1][0] == "warning" and "«по»" in messages[-1][1] and "позже текущей" in messages[-1][1]

    dialog.date_to_edit.setText("31.02.2020")
    dialog.apply_btn.click()
    assert "«по»" in messages[-1][1] and "дд.мм.гггг" in messages[-1][1]
    assert dialog._model.loaded is False


# --- отображение ячеек ---


def test_cell_display_and_alignment(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            rp5_readings.insert().values(
                wmo_index=1, station_name="Ст", obs_date=date(2020, 1, 5), obs_time=time(9, 0),
                T_temperature=-3.5, RRR_precipitation="Осадков нет", VV_visibility="10.0",
            )
        )
    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.set_filters(1, None, None)
    names = [c[0] for c in RP5_COLUMNS]

    def cell(name, role=Qt.ItemDataRole.DisplayRole):
        return model.data(model.index(0, names.index(name)), role)

    assert cell("obs_date") == "05.01.2020"
    assert cell("obs_time") == "09"  # только часы
    assert cell("obs_time", Qt.ItemDataRole.EditRole) == "09:00"  # минуты при правке не теряются
    right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    for numeric in ("wmo_index", "obs_date", "obs_time", "T_temperature", "VV_visibility"):
        assert cell(numeric, Qt.ItemDataRole.TextAlignmentRole) == right, numeric
    for textual in ("station_name", "RRR_precipitation", "source_file"):
        assert cell(textual, Qt.ItemDataRole.TextAlignmentRole) is None, textual


def test_obs_time_column_is_titled_srok_nablyudeniya(tmp_path):
    _, dialog = _dialog(tmp_path)
    dialog.apply_btn.click()

    model = dialog._model
    headers = [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())]
    assert "Срок наблюдения" in headers
    assert "Время" not in headers


def test_date_and_time_cell_parsing_accepts_displayed_format():
    assert _parse_cell_value(Date(), "05.01.2020") == date(2020, 1, 5)
    assert _parse_cell_value(Date(), "2020-01-05") == date(2020, 1, 5)
    assert _parse_cell_value(Time(), "9") == time(9, 0)
    assert _parse_cell_value(Time(), "09:30") == time(9, 30)
    with pytest.raises(ValueError):
        _parse_cell_value(Time(), "25")


# --- выбор полей ---


def test_selected_fields_limit_columns_and_are_remembered_per_format(tmp_path, messages):
    prefs_path = tmp_path / "prefs.json"
    engine, dialog = _dialog(tmp_path, Preferences(prefs_path))

    # по умолчанию (ничего не выбирали) — все поля
    assert dialog._selected_fields == [n for n, _, _ in RP5_SPEC.optional_fields()]

    dialog._selected_fields = ["T_temperature"]
    dialog._preferences.set("data_browser.rp5_fields", dialog._selected_fields)
    dialog.apply_btn.click()

    model = dialog._model
    headers = [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())]
    assert headers == ["WMO", "Станция", "Дата", "Срок наблюдения", "T"]

    # у ГГ свой набор (по умолчанию все), rp5 не затронут
    dialog.gg_radio.click()
    assert dialog._selected_fields == ["t_avg", "t_min", "t_max", "rrr_precipitation", "source_file"]
    dialog.rp5_radio.click()
    assert dialog._selected_fields == ["T_temperature"]

    # после "перезапуска" (новый диалог, тот же файл настроек) выбор сохранился
    reopened = DataBrowserDialog(engine, preferences=Preferences(prefs_path))
    assert reopened._selected_fields == ["T_temperature"]


def test_choose_fields_dialog_saves_selection(tmp_path, monkeypatch):
    prefs_path = tmp_path / "prefs.json"
    _, dialog = _dialog(tmp_path, Preferences(prefs_path))
    received = {}

    class _FakeFieldsDialog:
        def __init__(self, fields, selected, parent=None, fixed_headers=()):
            received["fixed_headers"] = fixed_headers

        def setWindowModality(self, modality):  # формы открываются через exec_window_modal
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_columns(self):
            return ["Tx_temperature_max", "T_temperature"]

    monkeypatch.setattr(data_browser, "_FieldsDialog", _FakeFieldsDialog)
    dialog.fields_btn.click()

    assert received["fixed_headers"] == ("WMO", "Станция", "Дата", "Срок наблюдения")
    assert dialog._selected_fields == ["Tx_temperature_max", "T_temperature"]
    assert Preferences(prefs_path).get("data_browser.rp5_fields") == ["Tx_temperature_max", "T_temperature"]
    assert dialog.fields_label.text() == f"Выбрано полей: 2 из {len(RP5_SPEC.optional_fields())}"


def test_fields_dialog_lists_name_with_description_and_reports_checked(tmp_path):
    _qapp()
    fields_dialog = data_browser._FieldsDialog(
        RP5_SPEC.optional_fields(), {"T_temperature", "U_humidity"}, fixed_headers=("WMO", "Срок наблюдения")
    )

    first = fields_dialog.list_widget.item(0)
    assert first.text() == "T — Температура воздуха, °C"
    assert fields_dialog.selected_columns() == ["T_temperature", "U_humidity"]

    fields_dialog._set_all(Qt.CheckState.Unchecked)
    assert fields_dialog.selected_columns() == []


def test_fixed_columns_are_not_in_fields_filter():
    rp5_fields = [name for name, _, _ in RP5_SPEC.optional_fields()]
    gg_fields = [name for name, _, _ in GG_SPEC.optional_fields()]
    for fixed in ("wmo_index", "station_name", "obs_date", "obs_time"):
        assert fixed not in rp5_fields
        assert fixed not in gg_fields
    assert all(description for _, _, description in RP5_SPEC.optional_fields())
    assert all(description for _, _, description in GG_SPEC.optional_fields())


# --- оформление ---


def test_button_names_and_styles(tmp_path):
    _, dialog = _dialog(tmp_path)

    assert dialog.apply_btn.text() == "Применить"
    assert dialog.fields_btn.text() == "Выбрать поля для отображения"
    assert dialog.fields_btn.objectName() == "skySoft"  # светло-голубая
    assert dialog.add_btn.objectName() == "add"
    assert dialog.delete_btn.objectName() == "danger"
    assert dialog.windowTitle() == "Просмотр исходных данных"
    # шапка таблицы выделена цветом
    assert "QHeaderView::section" in dialog.styleSheet()
    assert "QPushButton#skySoft:pressed" in dialog.styleSheet()


def test_period_fields_are_grouped_and_use_calendar_fields(tmp_path):
    from meteoregik.ui.date_field import DateField

    _, dialog = _dialog(tmp_path)

    assert isinstance(dialog.date_from_edit, DateField)
    assert dialog.date_from_edit.parentWidget() is dialog.date_to_edit.parentWidget()
    assert dialog.date_from_edit.parentWidget().objectName() == "group"


# --- удаление ---


def test_delete_asks_confirmation_with_russian_yes_no(tmp_path, messages, monkeypatch):
    _, dialog = _dialog(tmp_path)
    dialog.apply_btn.click()
    dialog.table.selectRow(0)
    asked = []
    monkeypatch.setattr(data_browser, "ask_yes_no", lambda parent, title, text: asked.append(title) or False)

    dialog.delete_btn.click()

    assert asked == ["Удаление строки"]
    assert dialog._model.total_count == 3  # ответили "нет" — ничего не удалено

    monkeypatch.setattr(data_browser, "ask_yes_no", lambda parent, title, text: True)
    dialog.delete_btn.click()
    assert dialog._model.total_count == 2


# --- добавление строки ---


def _add_dialog(dialog, is_rp5=True, preselected=None):
    return data_browser._AddRowDialog(
        is_rp5, data_browser.load_directory_stations(dialog.engine), preselected, dialog
    )


def test_add_dialog_preselects_station_from_filter_and_lists_directory(tmp_path, monkeypatch):
    engine, dialog = _dialog(tmp_path)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Нет данных", wmo_index=12345))  # в rp5-таблице её нет
    dialog.station_combo.select_wmo(WMO_INDEX)
    created = {}

    class _Spy(data_browser._AddRowDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created["dialog"] = self

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(data_browser, "_AddRowDialog", _Spy)
    dialog.add_btn.click()

    add = created["dialog"]
    assert add.station_combo.selected_wmo() == WMO_INDEX  # то же значение, что в фильтре
    assert add.station_combo.findData(12345) >= 0  # строку можно добавить и станции без данных
    assert add.station_combo.findData(None) == -1  # пункта "Все метеостанции" нет


def test_add_dialog_saves_station_name_and_manual_entry_source(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog, preselected=WMO_INDEX)
    add.date_edit.set_date(date(2020, 5, 5))
    add.hour_edit.setText("15")

    add._try_accept()

    assert add.result_values == {
        "wmo_index": WMO_INDEX,
        "station_name": "Мосальск",
        "obs_date": date(2020, 5, 5),
        "source_file": "ручной ввод",
        "obs_time": time(15, 0),
    }
    assert messages == []


def test_add_dialog_gg_has_no_hour_field(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog, is_rp5=False, preselected=WMO_INDEX)
    add.date_edit.set_date(date(2020, 5, 5))

    add._try_accept()

    assert not hasattr(add, "hour_edit")
    assert "obs_time" not in add.result_values
    assert add.result_values["station_name"] == "Мосальск"
    assert add.result_values["source_file"] == "ручной ввод"


def test_add_dialog_rejects_station_not_in_directory(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog)
    add.station_combo.setCurrentText("Выдуманная станция")
    add.hour_edit.setText("3")

    add._try_accept()

    assert add.result_values is None
    assert messages == [("warning", data_browser.STATION_NOT_FOUND_MESSAGE)]
    assert "не существует в текущем справочнике метеостанций" in messages[0][1]
    assert "внесите новую станцию в справочник (кнопка «Справочники» на главном экране)" in messages[0][1]


def test_add_dialog_requires_station_hour_and_valid_date(tmp_path, messages):
    _, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog)  # станция не выбрана
    add.hour_edit.setText("3")
    add._try_accept()
    assert messages[-1] == ("warning", "Выберите метеостанцию.")

    add.station_combo.select_wmo(WMO_INDEX)
    add.hour_edit.setText("")
    add._try_accept()
    assert "срок наблюдения" in messages[-1][1]

    add.hour_edit.setText("3")
    add.date_edit.set_date(date.today() + timedelta(days=1))
    add._try_accept()
    assert "позже текущей" in messages[-1][1]
    assert add.result_values is None


def test_add_dialog_hour_field_accepts_only_hours(tmp_path):
    _, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog)
    validator = add.hour_edit.validator()

    assert validator.validate("23", 0)[0] == validator.State.Acceptable
    assert validator.validate("24", 0)[0] == validator.State.Invalid
    assert validator.validate("-1", 0)[0] == validator.State.Invalid
    assert validator.validate("ab", 0)[0] == validator.State.Invalid
    assert add.hour_edit.maxLength() == 2


def test_add_row_before_apply_does_not_load_table(tmp_path, messages, monkeypatch):
    _, dialog = _dialog(tmp_path)

    class _FakeAddDialog:
        result_values = {
            "wmo_index": 55555, "station_name": "Новая", "obs_date": date(2021, 1, 1),
            "obs_time": time(0, 0), "source_file": "ручной ввод",
        }

        def __init__(self, *args, **kwargs):
            pass

        def setWindowModality(self, modality):  # формы открываются через exec_window_modal
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(data_browser, "_AddRowDialog", _FakeAddDialog)
    dialog.add_btn.click()

    assert dialog._model.rowCount() == 0
    assert messages and messages[0][0] == "info"
    assert dialog.station_combo.findData(55555) >= 0  # новая станция появилась в списке


@pytest.mark.parametrize("hour", [0, 3, 6, 9, 12, 15, 18, 21])
def test_add_dialog_accepts_only_three_hourly_observation_terms(tmp_path, messages, hour):
    _, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog, preselected=WMO_INDEX)
    add.hour_edit.setText(str(hour))

    add._try_accept()

    assert add.result_values["obs_time"] == time(hour, 0)
    assert messages == []


@pytest.mark.parametrize("hour", [1, 2, 4, 5, 7, 8, 10, 11, 13, 14, 16, 17, 19, 20, 22, 23])
def test_add_dialog_rejects_other_hours_with_message_and_saves_nothing(tmp_path, messages, hour):
    engine, dialog = _dialog(tmp_path)
    add = _add_dialog(dialog, preselected=WMO_INDEX)
    add.hour_edit.setText(str(hour))

    add._try_accept()

    assert add.result_values is None
    assert len(messages) == 1 and messages[0][0] == "warning"
    assert f"Недопустимый срок наблюдения: {hour}" in messages[0][1]
    assert "0, 3, 6, 9, 12, 15, 18, 21" in messages[0][1]
    assert "не сохранены" in messages[0][1]



def test_window_has_minimize_and_maximize_buttons(tmp_path):
    _, dialog = _dialog(tmp_path)
    flags = dialog.windowFlags()
    assert flags & Qt.WindowType.WindowMinMaxButtonsHint == Qt.WindowType.WindowMinMaxButtonsHint
    assert flags & Qt.WindowType.WindowCloseButtonHint


# --- подсказка "в базе пока нет данных" ---


def _empty_engine(tmp_path):
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Мосальск", wmo_index=WMO_INDEX))
    return engine


def test_empty_database_shows_no_data_hint_instead_of_table(tmp_path):
    _qapp()
    dialog = DataBrowserDialog(_empty_engine(tmp_path), preferences=Preferences(None))

    assert dialog._stack.currentWidget() is dialog._no_data_hint
    assert dialog._no_data_hint.text() == NO_DATA_HINT
    assert dialog.add_btn.isEnabled()  # первую строку можно добавить и вручную


def test_no_data_hint_is_not_shown_when_only_the_selected_format_is_empty(tmp_path):
    """В базе есть только ГГ, а открыт rp5: база не пуста — подсказка была бы неправдой."""
    _qapp()
    engine = _empty_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(wmo_index=WMO_INDEX, obs_date=date(2020, 1, 1), t_avg=1.0))

    dialog = DataBrowserDialog(engine, preferences=Preferences(None))

    assert dialog.rp5_radio.isChecked()
    assert dialog._stack.currentWidget() is dialog.table


def test_database_with_data_shows_table(tmp_path):
    _engine, dialog = _dialog(tmp_path)

    assert dialog._stack.currentWidget() is dialog.table


def test_no_data_hint_disappears_after_first_row_is_added(tmp_path, messages, monkeypatch):
    _qapp()
    engine = _empty_engine(tmp_path)
    dialog = DataBrowserDialog(engine, preferences=Preferences(None))

    class _FakeAddDialog:
        def __init__(self, *args, **kwargs):
            self.result_values = {
                "wmo_index": WMO_INDEX, "station_name": "Мосальск", "obs_date": date(2020, 1, 1),
                "obs_time": time(9, 0), "source_file": "ручной ввод",
            }

        def setWindowModality(self, modality):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(data_browser, "_AddRowDialog", _FakeAddDialog)

    dialog.add_btn.click()

    assert dialog._stack.currentWidget() is dialog.table
