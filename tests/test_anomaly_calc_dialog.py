"""Окно "Отклонения от нормы": состояние станций и запуск расчёта."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from meteoregik.calculations.anomalies import CalcStatus, is_stale, mark_stale, recalculate_station
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, stations
from meteoregik.db.session import get_engine
from meteoregik.ui.anomaly_calc_dialog import AnomalyCalcDialog, norm_text, state_text, summary_text

FIRST = 27707
SECOND = 27704
PERIOD = (date(2020, 1, 1), date(2020, 12, 31))


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(tmp_path):
    """Две станции с одним полным 2020 годом каждая."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    rows = []
    for wmo_index, avg in ((FIRST, 2.0), (SECOND, -1.0)):
        day = date(2020, 1, 1)
        while day.year == 2020:
            rows.append({"wmo_index": wmo_index, "period_type": "day", "period_start": day, "period_end": day,
                         "parameter": "T", "stat_type": "avg", "value": avg})
            day += timedelta(days=1)
        for month in range(1, 13):
            start = date(2020, month, 1)
            end = date(2020 + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
            rows.append({"wmo_index": wmo_index, "period_type": "month", "period_start": start, "period_end": end,
                         "parameter": "T", "stat_type": "avg", "value": avg})
        rows.append({"wmo_index": wmo_index, "period_type": "year", "period_start": date(2020, 1, 1),
                     "period_end": date(2020, 12, 31), "parameter": "T", "stat_type": "avg", "value": avg})
    with engine.begin() as conn:
        conn.execute(stations.insert(), [
            {"name": "Сухиничи", "wmo_index": FIRST},
            {"name": "Мосальск", "wmo_index": SECOND},
        ])
        conn.execute(computed_indicators.insert(), rows)
    return engine


def _texts(table) -> list[list[str]]:
    model = table.model()
    return [
        [model.index(row, col).data() for col in range(model.columnCount())]
        for row in range(model.rowCount())
    ]


def test_state_and_norm_texts():
    stale = CalcStatus(1, PERIOD, None)
    standard = CalcStatus(1, PERIOD, datetime(2026, 10, 10, 14, 5), True, 1991, 2020, 30)
    archive = CalcStatus(1, PERIOD, datetime(2026, 10, 10, 14, 5), False, 2006, 2024, 16)
    no_years = CalcStatus(1, PERIOD, datetime(2026, 10, 10, 14, 5))

    assert state_text(stale) == "требуется расчёт"
    assert state_text(standard) == "рассчитано 10.10.2026 14:05"
    assert norm_text(stale) == "—"
    assert norm_text(standard) == "ВМО 1991–2020 (30 лет)"
    assert norm_text(archive) == "по архиву 2006–2024 (16 лет)"
    assert norm_text(no_years) == "нет полных лет — годовой нормы нет"


def test_summary_text():
    stale = CalcStatus(1, PERIOD, None)
    done = CalcStatus(2, PERIOD, datetime(2026, 10, 10))

    assert summary_text([]) == "Данные ещё не залиты — рассчитывать нечего."
    assert summary_text([stale, done]) == "Требуют расчёта: 1 из 2."
    assert summary_text([done, done]) == "Все станции рассчитаны (2)."


def test_dialog_lists_stations_with_their_state(engine):
    _qapp()
    recalculate_station(engine, FIRST)
    dialog = AnomalyCalcDialog(engine)

    rows = {row[0]: row for row in _texts(dialog.table)}
    assert rows["Мосальск (27704)"][1:] == ["01.01.2020–31.12.2020", "требуется расчёт", "—"]
    assert rows["Сухиничи (27707)"][2].startswith("рассчитано ")
    assert rows["Сухиничи (27707)"][3] == "по архиву 2020–2020 (1 год)"
    assert dialog._summary.text() == "Требуют расчёта: 1 из 2."
    assert dialog.calc_btn.isEnabled() and dialog.recalc_all_btn.isEnabled()


def test_calculate_processes_only_stale_stations(engine):
    _qapp()
    recalculate_station(engine, FIRST)
    dialog = AnomalyCalcDialog(engine)

    dialog.calc_btn.click()

    assert not is_stale(engine, SECOND)
    assert dialog._status.text() == "Рассчитано станций: 1."
    assert dialog._summary.text() == "Все станции рассчитаны (2)."
    assert not dialog.calc_btn.isEnabled()  # считать больше нечего
    assert dialog.recalc_all_btn.isEnabled() and dialog.close_btn.isEnabled()
    assert not dialog.progress_bar.isVisible()


def test_recalculate_all_includes_fresh_stations(engine):
    _qapp()
    recalculate_station(engine, FIRST)
    recalculate_station(engine, SECOND)
    dialog = AnomalyCalcDialog(engine)
    assert not dialog.calc_btn.isEnabled()

    dialog.recalc_all_btn.click()

    assert dialog._status.text() == "Рассчитано станций: 2."


def test_station_marked_stale_appears_after_reopen(engine):
    _qapp()
    recalculate_station(engine, FIRST)
    recalculate_station(engine, SECOND)
    with engine.begin() as conn:
        mark_stale(conn, FIRST)

    dialog = AnomalyCalcDialog(engine)

    assert dialog._summary.text() == "Требуют расчёта: 1 из 2."


def test_dialog_without_data(tmp_path):
    _qapp()
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)
    dialog = AnomalyCalcDialog(engine)

    assert dialog._summary.text() == "Данные ещё не залиты — рассчитывать нечего."
    assert not dialog.calc_btn.isEnabled() and not dialog.recalc_all_btn.isEnabled()
