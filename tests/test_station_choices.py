"""Список станций: подпись "Название, Регион (индекс)", StationCombo,
русские кнопки "Да"/"Нет"."""

from datetime import date, time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import rp5_readings, ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.stations.choices import StationChoice, load_directory_stations, load_stations_in_table
from meteoregik.ui.dialogs import ask_yes_no
from meteoregik.ui.station_combo import ALL_STATIONS_TEXT, StationCombo


def _qapp():
    return QApplication.instance() or QApplication([])


def _engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(ru_regions.insert(), [{"code": 0, "region": "Россия"}, {"code": 40, "region": "Калужская область"}])
        conn.execute(
            stations.insert(),
            [
                {"name": "Сухиничи", "wmo_index": 27707, "country": "ru", "state": "40"},
                {"name": "Астана", "wmo_index": 35188, "country": "kz", "state": "kz12"},  # не РФ — региона нет
                {"name": "Без региона", "wmo_index": 11111, "country": "ru", "state": None},
                {"name": "Неизвестный регион", "wmo_index": 22222, "country": "ru", "state": "88"},
            ],
        )
        conn.execute(
            rp5_readings.insert(),
            [
                {"wmo_index": 27707, "station_name": "кэш", "obs_date": date(2020, 1, 1), "obs_time": time(0, 0)},
                {"wmo_index": 27707, "station_name": "кэш", "obs_date": date(2020, 1, 1), "obs_time": time(3, 0)},
                {"wmo_index": 99999, "station_name": "Только в файле", "obs_date": date(2020, 1, 1), "obs_time": time(0, 0)},
                {"wmo_index": None, "station_name": "Без индекса", "obs_date": date(2020, 1, 1), "obs_time": time(0, 0)},
            ],
        )
    return engine


def test_label_with_region_without_region_and_without_name():
    assert StationChoice(27707, "Сухиничи", "Калужская область").label == "Сухиничи, Калужская область (27707)"
    assert StationChoice(11111, "Без региона").label == "Без региона (11111)"
    assert StationChoice(11111, None).label == "11111"


def test_directory_stations_get_region_only_for_russian_stations_with_known_code(tmp_path):
    labels = [c.label for c in load_directory_stations(_engine(tmp_path))]

    assert labels == [
        "Астана (35188)",  # kz: cast('kz12') дал бы 0 = "Россия", но country != 'ru'
        "Без региона (11111)",
        "Неизвестный регион (22222)",  # кода 88 нет в ru_regions
        "Сухиничи, Калужская область (27707)",
    ]


def test_stations_in_table_use_directory_name_falling_back_to_cached_and_skip_null_index(tmp_path):
    labels = [c.label for c in load_stations_in_table(_engine(tmp_path), rp5_readings)]

    # имя из справочника приоритетнее кэшированного; станции нет в справочнике —
    # берётся кэшированное; дубли сроков схлопнуты, строка без индекса пропущена
    assert labels == ["Сухиничи, Калужская область (27707)", "Только в файле (99999)"]


def test_combo_all_mode_empty_text_means_all_and_is_case_insensitive():
    _qapp()
    combo = StationCombo(all_text=ALL_STATIONS_TEXT)
    combo.set_choices([StationChoice(27707, "Сухиничи", "Калужская область"), StationChoice(1, "Абв")])

    assert combo.selected_wmo() is None  # по умолчанию "Все метеостанции"
    combo.setCurrentText("сухиничи, калужская область (27707)")
    assert combo.selected_wmo() == 27707
    combo.lineEdit().clear()
    assert combo.selected_wmo() is None
    combo.setCurrentText("Сухиничи")  # частичный текст — не станция из списка
    with pytest.raises(ValueError):
        combo.selected_wmo()


def test_combo_required_mode_rejects_empty_and_unknown_text():
    _qapp()
    combo = StationCombo()
    combo.set_choices([StationChoice(27707, "Сухиничи")])

    with pytest.raises(ValueError):
        combo.selected_wmo()  # ничего не выбрано
    assert combo.findData(None) == -1  # пункта "Все" нет
    combo.select_wmo(27707)
    assert combo.selected_wmo() == 27707
    assert combo.name_of(27707) == "Сухиничи"
    combo.setCurrentText("Другая")
    with pytest.raises(ValueError):
        combo.selected_wmo()


def test_combo_set_choices_keeps_selection_when_station_still_exists():
    _qapp()
    combo = StationCombo(all_text=ALL_STATIONS_TEXT)
    combo.set_choices([StationChoice(1, "А"), StationChoice(2, "Б")])
    combo.select_wmo(2)

    combo.set_choices([StationChoice(1, "А"), StationChoice(2, "Б"), StationChoice(3, "В")])
    assert combo.selected_wmo() == 2

    combo.set_choices([StationChoice(1, "А")])  # выбранной станции больше нет -> "Все"
    assert combo.selected_wmo() is None
    assert combo.currentText() == ALL_STATIONS_TEXT


@pytest.mark.parametrize(("click", "expected"), [("Да", True), ("Нет", False)])
def test_ask_yes_no_has_russian_buttons_and_returns_choice(monkeypatch, click, expected):
    _qapp()
    seen = {}

    def fake_exec(self):
        seen["texts"] = [b.text() for b in self.buttons()]
        seen["default"] = self.defaultButton().text()
        next(b for b in self.buttons() if b.text() == click).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    assert ask_yes_no(None, "Удаление строки", "Удалить?") is expected
    assert seen["texts"] == ["Да", "Нет"]
    assert seen["default"] == "Нет"  # необратимое действие — по умолчанию отказ
