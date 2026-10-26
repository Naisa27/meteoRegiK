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
from meteoregik.ui.transitions_dialog import TAB_PERIODS, TAB_STATE, TAB_TRANSITIONS, TransitionsDialog, no_data_html
from test_transitions import STATION, _annual_cycle


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(tmp_path):
    """2019–2021 годы гладкого годового хода, в июле 2021 — 6 пропусков:
    весной 2021 "нет данных" только у 15 °C (июнь, за ним июль), осенью 2021
    — у всех порогов (июль — начало окна)."""
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
    assert [row.label for row in table.rows] == ["2019", "2020", "2021", view.MEAN_DATE_LABEL]
    row_2021 = table.rows[2]
    assert [cell.kind for cell in row_2021.cells] == [view.KIND_VALUE] * 3 + [view.KIND_NO_DATA] * 5
    # У обоих прочтений "нет данных" — слово один раз
    assert row_2021.cells[3].text == view.NO_DATA_TEXT
    assert "июль 2021 (25 дн. из 31)" in row_2021.cells[3].tip
    hmc, kr = (data.get(2019, tr.SPRING, 0, method).day for method in tr.METHODS)
    assert table.rows[0].cells[0].text == f"{hmc:%d.%m} / {kr:%d.%m}"
    assert "Гидрометцентр" in table.rows[0].cells[0].tip
    mean = table.rows[-1]
    assert mean.mean and "по архиву станции, 2019–2021: 3 года" in mean.cells[0].tip
    assert view.SEPARATOR in mean.cells[0].text


def test_cell_shows_both_readings_through_a_slash():
    data = tr.StationTransitions([2014], {
        (2014, tr.SPRING, 10, tr.METHOD_HMC): tr.Transition(tr.STATUS_DATE, date(2014, 4, 19)),
        (2014, tr.SPRING, 10, tr.METHOD_KR): tr.Transition(tr.STATUS_NO_DATA),
        (2014, tr.SPRING, 15, tr.METHOD_HMC): tr.Transition(tr.STATUS_NONE),
        (2014, tr.SPRING, 15, tr.METHOD_KR): tr.Transition(tr.STATUS_NONE),
    })

    cells = view.transitions_table(data, {}).rows[0].cells

    assert (cells[2].text, cells[2].kind) == ("19.04 / нет данных", view.KIND_NO_DATA)
    assert "Гидрометцентр: 19.04; «Климат России»: нет данных" in cells[2].tip
    assert (cells[3].text, cells[3].kind) == (view.NONE_TEXT, view.KIND_NONE)


def test_autumn_date_in_the_next_year_is_written_with_the_year():
    data = tr.StationTransitions([2020], {
        (2020, tr.AUTUMN, 0, method): tr.Transition(tr.STATUS_DATE, date(2021, 1, 4)) for method in tr.METHODS
    })

    table = view.transitions_table(data, {})

    assert table.rows[0].cells[7].text == "04.01.2021 / 04.01.2021"


def test_periods_table_explains_missing_duration():
    same = {
        (2020, tr.SPRING, 0): tr.Transition(tr.STATUS_NONE),
        (2020, tr.AUTUMN, 0): tr.Transition(tr.STATUS_DATE, date(2020, 11, 20)),
        (2020, tr.SPRING, 5): tr.Transition(tr.STATUS_DATE, date(2020, 4, 10)),
        (2020, tr.AUTUMN, 5): tr.Transition(tr.STATUS_DATE, date(2020, 10, 10)),
    }
    transitions = {(*key, method): value for key, value in same.items() for method in tr.METHODS}
    transitions[(2020, tr.AUTUMN, 5, tr.METHOD_KR)] = tr.Transition(tr.STATUS_DATE, date(2020, 10, 20))
    data = tr.StationTransitions([2020], transitions)

    table = view.periods_table(data, {})

    cells = table.rows[0].cells
    assert (cells[0].text, cells[0].kind) == (view.NO_VALUE, view.KIND_NONE)
    assert "весеннего перехода через 0 °C не было" in cells[0].tip
    assert cells[1].text == "183 / 193"
    assert cells[2].kind == view.KIND_NO_DATA  # 10 °C не посчитан вовсе
    assert table.rows[-1].label == view.MEAN_DURATION_LABEL


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
    assert model.is_mean(3) and model.is_no_data(2, 4) and not model.is_no_data(2, 1)
    assert "<div" in model.data(model.index(2, 4), Qt.ItemDataRole.ToolTipRole)  # подсказка с переносом
    assert dialog.periods_model.rowCount() == 4
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [TAB_TRANSITIONS, TAB_PERIODS, TAB_STATE]


def test_state_tab_lists_stations_and_what_was_recalculated(qapp, engine):
    dialog = TransitionsDialog(engine, run_async=False)

    [row] = dialog.state_model.rows
    assert row.state.recalculated == "весна и осень 2019, весна и осень 2020, весна и осень 2021"
    assert [s.year for s in row.state.no_data_seasons[tr.SPRING]] == [2021]
    assert no_data_html(row.state.no_data_seasons[tr.AUTUMN]) == (
        "<b>2021</b>: июль (25 дн. из 31), январь – февраль 2022: нет данных"
    )
    assert "пересчитаны сезоны 1 из них" in dialog.summary.text()

    again = TransitionsDialog(engine, run_async=False)
    assert "Все даты уже были посчитаны" in again.summary.text()


@pytest.mark.parametrize(("months", "empty", "text"), [
    ([(2015, 7, 11, 31), (2015, 8, 0, 31), (2015, 9, 0, 30), (2016, 1, 0, 31), (2016, 2, 0, 29)], False,
     "2015: июль (11 дн. из 31), август – сентябрь: нет данных, январь – февраль 2016: нет данных"),
    ([(2015, 12, 0, 31), (2016, 1, 0, 31)], False, "2015: декабрь – январь 2016: нет данных"),
    ([(2016, m, 0, 30) for m in range(1, 8)], True, "2016: нет данных за весь сезон"),
])
def test_no_data_html_groups_empty_months_and_bolds_years(months, empty, text):
    year, _, rest = text.partition(": ")
    assert no_data_html([tr.NoDataSeason(months[0][0], months, empty)]) == f"<b>{year}</b>: {rest}"


def test_no_data_html_without_seasons_is_a_dash():
    assert no_data_html([]) == "—"


def test_years_with_dates_column_is_centered(qapp, engine):
    dialog = TransitionsDialog(engine, run_async=False)

    index = dialog.state_table.model().index(0, 3)
    assert index.data(Qt.ItemDataRole.TextAlignmentRole) == int(Qt.AlignmentFlag.AlignCenter)


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
    assert sheet["A2"].value == "В ячейках: Гидрометцентр / «Климат России»"
    assert (sheet["B3"].value, sheet["F3"].value, sheet["B4"].value) == ("Весна", "Осень", "0 °C")
    assert sheet["A5"].value == 2019
    hmc, kr = (data.get(2019, tr.SPRING, 0, method).day for method in tr.METHODS)
    assert sheet["B5"].value == f"{hmc:%d.%m} / {kr:%d.%m}"
    assert sheet["E7"].value == view.NO_DATA_TEXT and sheet["E7"].comment is not None
    assert sheet["A8"].value == view.MEAN_DATE_LABEL
    periods = workbook[SHEET_PERIODS]
    assert periods["B5"].value == " / ".join(str(data.duration(2019, 0, m)) for m in tr.METHODS)
