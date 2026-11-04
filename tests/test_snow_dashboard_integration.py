"""Общий фильтр дашборда работает и для архива только со снегом."""

from __future__ import annotations

from datetime import date, time

from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.ui.dashboard_view import DashboardDialog, TAB_SNOW


def test_snow_only_archive_uses_common_filter_and_does_not_report_no_data(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    engine = get_engine(tmp_path / "snow.sqlite3")
    run_migrations(engine)
    day = date(2026, 2, 12)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(wmo_index=27707, name="Мосальск"))
        conn.execute(rp5_readings.insert().values(wmo_index=27707, obs_date=day,
                     obs_time=time(9), sss_snow_depth="12"))
    recompute_range(engine, 27707, day, day)
    messages: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(str(args[-1])))
    dialog = DashboardDialog(engine, run_async=False, wmo_index=27707, tab=TAB_SNOW)
    assert dialog._archive_bounds(27707) == (day, day)
    assert len(dialog.snow_tab.count_chart.periods) == 1
    assert dialog.snow_tab.cards[0].value.text() == "1"
    assert dialog.snow_tab.cards[2].value.text() == "нет данных"
    assert not messages
    dialog.close()
    engine.dispose()
    assert app is not None
