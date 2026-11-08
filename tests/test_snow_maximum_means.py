"""Многолетние максимумы: полные периоды, самостоятельные месяцы и сезоны."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from meteoregik.db.migrations import run_migrations
from meteoregik.db.session import get_engine
from meteoregik.db.schema import computed_indicators
from meteoregik.reports.snow_data import maximum_means, SnowData, SnowPeriod, SnowMaximumMean
from meteoregik.ui.dashboard_snow_tab import SnowTab


def test_means_use_season_maxima_and_full_months(tmp_path) -> None:
    engine = get_engine(tmp_path / "means.sqlite3")
    run_migrations(engine)
    first, last = date(2018, 7, 1), date(2020, 6, 30)
    rows = []
    day = first
    while day <= last:
        depth = {(2019, 1): 10, (2019, 2): 40, (2020, 1): 60, (2020, 2): 20}.get((day.year, day.month), 0)
        rows.append(dict(wmo_index=27606, parameter="SNOW_DEPTH", stat_type="max", period_type="day",
                         period_start=day, period_end=day, value=depth))
        day += timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), rows)
    months, season = maximum_means(engine, 27606, (first, last))
    assert months[0].value == 35
    assert months[1].value == 30
    assert months[5].value == 0
    assert season.value == 50  # среднее максимумов 40 и 60, не максимум месячных средних
    assert season.count == 2 and not season.standard
    months, season = maximum_means(engine, 27606, (date(2019, 1, 2), date(2019, 2, 28)))
    assert months[0] is None
    assert months[1].value == 40
    assert season is None
    engine.dispose()


def test_month_filter_is_independent_and_retained() -> None:
    app = QApplication.instance() or QApplication([])
    jan = SnowPeriod(date(2020, 1, 1), date(2020, 1, 31), date(2020, 1, 1), date(2020, 1, 31),
                     10, 25, (date(2020, 1, 2),), 0, 31)
    feb = SnowPeriod(date(2020, 2, 1), date(2020, 2, 29), date(2020, 2, 1), date(2020, 2, 29),
                     20, 40, (date(2020, 2, 2),), 0, 29)
    means = (SnowMaximumMean(20, 2010, 2020, 11, False),) + (None,) * 11
    tab = SnowTab()
    data = SnowData(jan, (jan, feb), maximum_month_means=means)
    tab.show_data("Станция", data)
    assert tab.maximum_month.currentIndex() == 1
    tab.maximum_month.setCurrentIndex(0)
    assert tab.maximum_chart.periods == (jan,)
    assert tab.maximum_chart.norms == (20,)
    assert tab.count_chart.periods == (jan, feb)
    tab.show_data("Станция", data)
    assert tab.maximum_month.currentIndex() == 0
    assert tab.heatmap._mode == "anomaly"
    tab.heatmap_modes.button(1).click()
    assert tab.heatmap._mode == "value"
    tab.close()
    assert app is not None


def test_standard_base_and_fallback_are_selected_per_month(tmp_path) -> None:
    engine = get_engine(tmp_path / "base.sqlite3")
    run_migrations(engine)
    first, last = date(1991, 1, 1), date(2021, 1, 31)
    rows = []
    day = first
    while day <= last:
        value = (100 if day.year == 2021 else 1) if day.month == 1 else 0
        rows.append(dict(wmo_index=27606, parameter="SNOW_DEPTH", stat_type="max", period_type="day",
                         period_start=day, period_end=day, value=value))
        day += timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), rows)
    monthly, _ = maximum_means(engine, 27606, (first, last))
    assert monthly[0].standard and monthly[0].count == 30
    assert monthly[0].value == 1
    monthly, _ = maximum_means(engine, 27606, (date(1998, 1, 1), last))
    assert not monthly[0].standard and monthly[0].count == 24
    assert monthly[0].value == pytest.approx(123 / 24)
    engine.dispose()
