"""Окно "Переходы температуры": расчёт при открытии, таблицы станции
(раскладка reports/transitions_view.py), вкладка состояния и выгрузка в Excel."""

from __future__ import annotations

from datetime import date

import openpyxl
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from meteoregik.calculations import transitions as tr
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators
from meteoregik.db.session import get_engine
from meteoregik.export.transitions_excel import SHEET_PERIODS, SHEET_TRANSITIONS, export_transitions
from meteoregik.reports import transitions_view as view
from meteoregik.ui.empty_state import NO_DATA_HINT
from meteoregik.ui.transitions_dialog import TAB_PERIODS, TAB_STATE, TAB_TRANSITIONS, TransitionsDialog
from test_transitions import STATION, _annual_cycle


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(tmp_path):
    """2019–2021 годы гладкого годового хода, в июле 2021 — 6 пропусков:
    весна и осень 2021 — "нет данных"."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    gap = frozenset(date(2021, 7, day) for day in (1, 5, 9, 13, 17, 21))
    days = _annual_cycle(date(2019, 1, 1), date(2021, 12, 31), skip=gap)
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), [
            {"wmo_index": STATION, "period_type": "day", "period_start": day, "period_end": day,
             "parameter": "T", "stat_type": "avg", "value": value}
            for day, value in days.items()
        ])
    return engine


def _texts(table: view.Table) -> list[list[str]]:
    return [[row.label, *(cell.text for cell in row.cells)] for row in table.rows]


# --- раскладка ---


def test_transitions_table_layout(engine):
    tr.ensure_fresh(engine)
    data = tr.load_station_transitions(engine, STATION)

    table = view.transitions_table(data, tr.fetch_daily_means(engine, STATION))

    assert [c.title for c in table.columns] == ["0 °C", "5 °C", "10 °C", "15 °C", "15 °C", "10 °C", "5 °C", "0 °C"]
    assert [c.group for c in table.columns] == [view.GROUP_SPRING] * 4 + [view.GROUP_AUTUMN] * 4
    assert [row.label for row in table.rows] == ["2019", "2020", "2021", view.MEAN_LABEL]
    row_2021 = table.rows[2]
    assert {cell.kind for cell in row_2021.cells} == {view.KIND_NO_DATA}
    assert "июль 2021 (25 дн. из 31)" in row_2021.cells[0].tip
    spring_0 = data.get(2019, tr.SPRING, 0).day
    assert table.rows[0].cells[0].text == f"{spring_0:%d.%m}"
    assert table.rows[-1].mean and "по архиву станции, 2019–2020: 2 года" in table.rows[-1].cells[0].tip


def test_autumn_date_in_the_next_year_is_written_with_the_year():
    data = tr.StationTransitions([2020], {(2020, tr.AUTUMN, 0): tr.Transition(tr.STATUS_DATE, date(2021, 1, 4))})

    table = view.transitions_table(data, {})

    assert table.rows[0].cells[7].text == "04.01.2021"


def test_periods_table_explains_missing_duration():
    data = tr.StationTransitions([2020], {
        (2020, tr.SPRING, 0): tr.Transition(tr.STATUS_NONE),
        (2020, tr.AUTUMN, 0): tr.Transition(tr.STATUS_DATE, date(2020, 11, 20)),
        (2020, tr.SPRING, 5): tr.Transition(tr.STATUS_DATE, date(2020, 4, 10)),
        (2020, tr.AUTUMN, 5): tr.Transition(tr.STATUS_DATE, date(2020, 10, 10)),
    })

    table = view.periods_table(data, {})

    cells = table.rows[0].cells
    assert (cells[0].text, cells[0].kind) == (view.NO_VALUE, view.KIND_NONE)
    assert "весеннего перехода через 0 °C не было" in cells[0].tip
    assert cells[1].text == "183"
    assert cells[2].kind == view.KIND_NO_DATA  # 10 °C не посчитан вовсе


# --- окно ---


def test_dialog_calculates_on_open_and_shows_station(qapp, engine):
    dialog = TransitionsDialog(engine, run_async=False)

    assert tr.stale_stations(engine) == []
    assert dialog.station_combo.isEnabled()
    assert not dialog.export_btn.isEnabled()

    dialog.station_combo.select_wmo(STATION)

    assert dialog.export_btn.isEnabled()
    model = dialog.transitions_model
    assert model.rowCount() == 4 and model.columnCount() == 9
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Год"
    assert model.is_mean(3) and model.is_no_data(2, 1)
    assert dialog.periods_model.rowCount() == 4
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [TAB_TRANSITIONS, TAB_PERIODS, TAB_STATE]


def test_state_tab_lists_stations_and_what_was_recalculated(qapp, engine):
    dialog = TransitionsDialog(engine, run_async=False)

    [row] = dialog.state_model.rows
    assert row.state.recalculated == "весна и осень 2019, весна и осень 2020, весна и осень 2021"
    assert row.state.no_data_seasons == {tr.SPRING: 1, tr.AUTUMN: 1}
    assert "пересчитаны сезоны 1 из них" in dialog.summary.text()

    again = TransitionsDialog(engine, run_async=False)
    assert "Все даты уже были посчитаны" in again.summary.text()


def test_empty_database_shows_hint(qapp, tmp_path):
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)

    dialog = TransitionsDialog(engine, run_async=False)

    assert dialog._hints[0].text() == NO_DATA_HINT
    assert not dialog.station_combo.isEnabled()


# --- Excel ---


def test_export_writes_both_tables(engine, tmp_path):
    tr.ensure_fresh(engine)
    data = tr.load_station_transitions(engine, STATION)
    days = tr.fetch_daily_means(engine, STATION)
    out = tmp_path / "out.xlsx"

    export_transitions(out, "Станция (27707)", view.transitions_table(data, days), view.periods_table(data, days))

    workbook = openpyxl.load_workbook(out)
    assert workbook.sheetnames == [SHEET_TRANSITIONS, SHEET_PERIODS]
    sheet = workbook[SHEET_TRANSITIONS]
    assert sheet["A1"].value == "Станция (27707)"
    assert (sheet["B2"].value, sheet["F2"].value, sheet["B3"].value) == ("Весна", "Осень", "0 °C")
    assert sheet["A4"].value == 2019
    assert sheet["B4"].value.date() == data.get(2019, tr.SPRING, 0).day
    assert sheet["B6"].value == view.NO_DATA_TEXT and sheet["B6"].comment is not None
    assert sheet["A7"].value == view.MEAN_LABEL
    periods = workbook[SHEET_PERIODS]
    assert periods["B4"].value == data.duration(2019, 0)
