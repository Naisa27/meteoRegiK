"""Суммы осадков за периоды между датами перехода температуры
(reports/transition_precipitation.py) и их блок на вкладке "Осадки"
дашборда (ui/dashboard_period_charts.py)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from matplotlib import dates as mdates
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.calculations.transitions import (
    AUTUMN,
    METHOD_HMC,
    METHOD_KR,
    SPRING,
    STATUS_DATE,
    STATUS_NO_DATA,
    STATUS_NONE,
    StationTransitions,
    Transition,
)
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.transition_precipitation import (
    COLD,
    SUMMER,
    VEGETATION,
    WARM,
    all_periods,
    compute_transition_precipitation,
    kind_title,
    mean_sums,
    period_label,
)
from meteoregik.ui.dashboard_period_charts import (
    NO_PERIODS,
    NO_TRANSITIONS,
    VISIBLE_YEARS,
    NestedPeriodsChart,
    WarmColdChart,
    mean_text,
    period_text,
)
from meteoregik.ui.dashboard_precip_tab import METHOD_MODES, SERIES_KINDS
from meteoregik.ui.dashboard_view import DashboardDialog

STATION = 27707


def _daily(first: date, last: date, amount: float = 1.0) -> dict[date, float]:
    days = {}
    day = first
    while day <= last:
        days[day] = amount
        day += timedelta(days=1)
    return days


def _transitions(rows: dict[tuple[int, str, int, str], Transition]) -> StationTransitions:
    return StationTransitions(sorted({key[0] for key in rows}), rows)


def _dated(day: date) -> Transition:
    return Transition(STATUS_DATE, day)


@pytest.fixture
def two_years() -> StationTransitions:
    """Переходы через 0 °C в 2014–2015 годах; через 5 °C — только в 2015.
    У "Климата России" весна 2015 через 0 °C на 10 дней позже."""
    rows = {}
    for method in (METHOD_HMC, METHOD_KR):
        rows[(2014, SPRING, 0, method)] = _dated(date(2014, 3, 25))
        rows[(2014, AUTUMN, 0, method)] = _dated(date(2014, 11, 10))
        rows[(2015, SPRING, 0, method)] = _dated(date(2015, 3, 20) if method == METHOD_HMC else date(2015, 3, 30))
        rows[(2015, AUTUMN, 0, method)] = _dated(date(2015, 11, 5))
        rows[(2014, SPRING, 5, method)] = Transition(STATUS_NONE)
        rows[(2014, AUTUMN, 5, method)] = _dated(date(2014, 10, 1))
        rows[(2015, SPRING, 5, method)] = _dated(date(2015, 4, 15))
        rows[(2015, AUTUMN, 5, method)] = Transition(STATUS_NO_DATA)
    return _transitions(rows)


def _find(periods, kind, method, year):
    return next(p for p in periods if (p.kind, p.method, p.year) == (kind, method, year))


# --- периоды и суммы ---


def test_warm_period_runs_from_spring_date_to_the_day_before_autumn_date(two_years):
    periods, _ = all_periods(two_years, _daily(date(2014, 1, 1), date(2015, 12, 31)))

    warm = _find(periods, WARM, METHOD_HMC, 2015)
    assert (warm.start, warm.end) == (date(2015, 3, 20), date(2015, 11, 4))
    # длительность — как в "Таблице периодов": осенняя дата − весенняя
    assert warm.days == (date(2015, 11, 5) - date(2015, 3, 20)).days
    assert warm.value == warm.days  # по 1 мм в день
    assert warm.complete
    assert warm.label == "2015"


def test_cold_period_spans_two_years_and_joins_warm_periods_without_gaps(two_years):
    periods, _ = all_periods(two_years, _daily(date(2014, 1, 1), date(2015, 12, 31)))

    cold = _find(periods, COLD, METHOD_HMC, 2014)
    assert (cold.start, cold.end) == (date(2014, 11, 10), date(2015, 3, 19))
    assert cold.label == "2014/2015"
    assert period_label(COLD, 2014, short=True) == "2014/15"
    warm_2014 = _find(periods, WARM, METHOD_HMC, 2014)
    warm_2015 = _find(periods, WARM, METHOD_HMC, 2015)
    assert warm_2014.end + timedelta(days=1) == cold.start
    assert cold.end + timedelta(days=1) == warm_2015.start


def test_each_reading_has_its_own_bounds(two_years):
    periods, _ = all_periods(two_years, _daily(date(2014, 1, 1), date(2015, 12, 31)))

    assert _find(periods, COLD, METHOD_KR, 2014).end == date(2015, 3, 29)
    assert _find(periods, WARM, METHOD_KR, 2015).value == _find(periods, WARM, METHOD_HMC, 2015).value - 10


def test_missing_date_leaves_a_gap_with_its_reason(two_years):
    periods, gaps = all_periods(two_years, _daily(date(2014, 1, 1), date(2015, 12, 31)))

    assert not [p for p in periods if p.kind == VEGETATION]
    reasons = {(g.kind, g.year): g.reason for g in gaps if g.method == METHOD_HMC}
    assert reasons[(VEGETATION, 2014)] == "весенний переход через 5 °C — не было"
    assert reasons[(VEGETATION, 2015)] == "осенний переход через 5 °C — нет данных"
    # холодный период 2015/2016: весны 2016 в архиве нет
    assert reasons[(COLD, 2015)] == "весенний 2016 года переход через 0 °C — нет данных"


def test_days_without_precipitation_data_are_counted_not_zeroed(two_years):
    days = _daily(date(2014, 1, 1), date(2015, 12, 31), 2.0)
    for day in (date(2015, 6, 1), date(2015, 6, 2), date(2015, 6, 3)):
        del days[day]

    warm = _find(all_periods(two_years, days)[0], WARM, METHOD_HMC, 2015)

    assert warm.missing_days == 3
    assert warm.value == (warm.days - 3) * 2.0
    assert "без данных об осадках 3 дн." in period_text(warm)


def test_period_without_any_precipitation_data_has_no_value(two_years):
    warm = _find(all_periods(two_years, {})[0], WARM, METHOD_HMC, 2015)

    assert warm.value is None
    assert period_text(warm).startswith("нет данных об осадках")


@pytest.mark.parametrize(("date_from", "date_to", "expected"), [
    # июнь 2015 — внутри тёплого периода: он показан целиком
    (date(2015, 6, 1), date(2015, 6, 30), {(WARM, 2015)}),
    # январь 2015 — внутри холодного 2014/2015
    (date(2015, 1, 1), date(2015, 1, 31), {(COLD, 2014)}),
    # весь 2015 год задевает и холодный период 2014/2015, и 2015/2016 (без весны — пропуск)
    (date(2015, 1, 1), date(2015, 12, 31), {(COLD, 2014), (WARM, 2015)}),
])
def test_dashboard_period_is_extended_to_whole_periods(two_years, date_from, date_to, expected):
    data = compute_transition_precipitation(
        two_years, _daily(date(2014, 1, 1), date(2015, 12, 31)), date_from, date_to
    )

    assert {(p.kind, p.year) for p in data.periods if p.method == METHOD_HMC} == expected
    assert all(p.value == p.days for p in data.periods)  # сумма — за весь период, а не за его часть


def test_mean_uses_only_complete_periods_and_the_norm_base_rule():
    rows = {}
    for year in range(1985, 2021):
        rows[(year, SPRING, 15, METHOD_HMC)] = _dated(date(year, 6, 1))
        rows[(year, AUTUMN, 15, METHOD_HMC)] = _dated(date(year, 9, 1))
    days = _daily(date(1985, 1, 1), date(2020, 12, 31), 1.0)
    for year in range(1991, 2021):
        days[date(year, 7, 1)] = 1.0 + (year - 1990)  # у каждого года своя сумма
    del days[date(2000, 7, 15)]  # 2000 — неполный: в среднюю не входит

    means = mean_sums(all_periods(_transitions(rows), days)[0])

    mean = means[(SUMMER, METHOD_HMC)]
    assert mean.standard is True  # 29 полных лет 1991–2020 — не меньше 24
    assert mean.years == 29
    assert (mean.first_year, mean.last_year) == (1991, 2020)
    expected = [92 + (year - 1990) for year in range(1991, 2021) if year != 2000]
    assert mean.value == pytest.approx(sum(expected) / len(expected))
    assert "1991–2020, 29 лет" in mean_text(METHOD_HMC, mean)


def test_station_without_transitions():
    data = compute_transition_precipitation(_transitions({}), {}, date(2015, 1, 1), date(2015, 12, 31))

    assert not data.has_transitions
    assert data.periods == ()


def test_kind_title():
    assert kind_title(WARM) == "Тёплый период (выше 0 °C)"
    assert kind_title(COLD) == "Холодный период (ниже 0 °C)"


# --- блок на вкладке "Осадки" ---


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(tmp_path):
    """Станция с 2019–2021 годами гладкого годового хода температуры
    (переходы через все пороги раз весной и раз осенью) и осадками по 1 мм."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    rows = []
    day = date(2019, 1, 1)
    while day <= date(2021, 12, 31):
        phase = 2 * math.pi * (day.timetuple().tm_yday - 20) / 365.25
        for parameter, stat_type, value in (("T", "avg", round(5 - 15 * math.cos(phase), 2)), ("R", "sum", 1.0)):
            rows.append({"wmo_index": STATION, "period_type": "day", "period_start": day, "period_end": day,
                         "parameter": parameter, "stat_type": stat_type, "value": value})
        day += timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Сухиничи", wmo_index=STATION))
        conn.execute(computed_indicators.insert(), rows)
    return engine


@pytest.fixture
def no_messages(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
    return shown


def _dashboard(engine, opened: list[str] | None = None) -> DashboardDialog:
    _qapp()
    dialog = DashboardDialog(engine, run_async=False, open_guide=opened.append if opened is not None else None)
    dialog.station_combo.select_wmo(STATION)
    dialog.apply_btn.click()
    return dialog


def _event(chart, x: float) -> SimpleNamespace:
    return SimpleNamespace(inaxes=chart._ax, xdata=x)


def test_dashboard_computes_transitions_and_draws_the_three_charts(engine, no_messages):
    dialog = _dashboard(engine)
    tab = dialog.precipitation_tab
    periods = tab.warm_cold_chart._data

    assert periods is not None and periods.has_transitions
    assert {p.year for p in periods.series(WARM, METHOD_HMC)} == {2019, 2020, 2021}
    assert {p.year for p in periods.series(COLD, METHOD_HMC)} == {2019, 2020}
    assert tab.series_chart._years == [2019, 2020, 2021]
    assert tab._series_title.text() == "Сумма осадков за тёплый период по годам"

    # подсказки по наведению называют оба прочтения
    middle = periods.series(WARM, METHOD_HMC)[1].middle
    text = tab.warm_cold_chart.tooltip_at(_event(tab.warm_cold_chart, mdates.date2num(middle)))
    assert "Гидрометцентр: тёплый период 2020" in text
    assert "«Климат России»: тёплый период 2020" in text
    nested = tab.nested_chart.tooltip_at(_event(tab.nested_chart, mdates.date2num(middle)))
    assert nested.splitlines()[0] == "2020"
    assert "летний период —" in nested
    series = tab.series_chart.tooltip_at(_event(tab.series_chart, 2020))
    assert series.splitlines()[0] == "Тёплый период (выше 0 °C), 2020"
    assert "Средняя многолетняя (Гидрометцентр)" in series
    assert no_messages == []


def test_switches_change_readings_and_period(engine, no_messages):
    dialog = _dashboard(engine)
    tab = dialog.precipitation_tab
    charts = (tab.warm_cold_chart, tab.nested_chart, tab.series_chart)

    tab._methods_group.button(2).setChecked(True)  # только "Климат России"
    assert all(chart.methods == METHOD_MODES[2] == (METHOD_KR,) for chart in charts)

    tab._kinds_group.button(SERIES_KINDS.index(COLD)).setChecked(True)
    assert tab.series_chart.kind == COLD
    assert tab._series_title.text() == "Сумма осадков за холодный период по зимам"
    tooltip = tab.series_chart.tooltip_at(_event(tab.series_chart, 2019))
    assert tooltip.splitlines()[0] == "Холодный период (ниже 0 °C), 2019/2020"
    assert "Гидрометцентр" not in tooltip


def test_short_dashboard_period_draws_bars_and_extends_to_whole_periods(engine, no_messages):
    _qapp()
    dialog = DashboardDialog(engine, run_async=False)
    dialog.station_combo.select_wmo(STATION)
    dialog.date_from_edit.set_date(date(2020, 6, 1))
    dialog.date_to_edit.set_date(date(2020, 8, 31))
    dialog.apply_btn.click()
    tab = dialog.precipitation_tab

    assert tab.series_chart._bars
    assert "столбики" in tab._series_note.text()
    warm = tab.series_chart._data.series(WARM, METHOD_HMC)
    assert [p.year for p in warm] == [2020]
    assert warm[0].start < date(2020, 6, 1)  # период показан целиком


def test_charts_explain_why_they_are_empty(engine, no_messages):
    dialog = _dashboard(engine)
    tab = dialog.precipitation_tab

    tab.series_chart.plot(None)
    assert tab.series_chart.figure.axes[0].texts[0].get_text() == NO_TRANSITIONS
    empty = compute_transition_precipitation(
        _transitions({(2019, SPRING, 0, METHOD_HMC): Transition(STATUS_NONE)}), {}, date(2019, 1, 1), date(2019, 12, 31)
    )
    tab.warm_cold_chart.plot(empty)
    assert tab.warm_cold_chart.figure.axes[0].texts[0].get_text() == NO_PERIODS


def test_guide_link_opens_the_dashboard_section(engine, no_messages):
    opened: list[str] = []
    dialog = _dashboard(engine, opened)
    tab = dialog.precipitation_tab

    tab._open_guide("dashboard")  # то же, что делает ссылка "Подробнее в путеводителе"
    assert opened == ["dashboard"]
    assert "Средняя многолетняя сумма" in tab.periods_help.accessibleDescription()


# --- прокрутка первых двух графиков ---


@pytest.fixture
def fifteen_years():
    """Переходы через 0 и 5 °C в 2006–2020 годах, осадки по 1 мм в день."""
    rows = {}
    for year in range(2006, 2021):
        for method in (METHOD_HMC, METHOD_KR):
            rows[(year, SPRING, 0, method)] = _dated(date(year, 3, 20))
            rows[(year, AUTUMN, 0, method)] = _dated(date(year, 11, 10))
            rows[(year, SPRING, 5, method)] = _dated(date(year, 4, 15))
            rows[(year, AUTUMN, 5, method)] = _dated(date(year, 10, 5))
    return compute_transition_precipitation(
        _transitions(rows), _daily(date(2006, 1, 1), date(2020, 12, 31)), date(2006, 1, 1), date(2020, 12, 31)
    )


@pytest.mark.parametrize("chart_class", [WarmColdChart, NestedPeriodsChart])
def test_long_archive_shows_a_five_year_window_with_a_scrollbar(fifteen_years, chart_class):
    _qapp()
    chart = chart_class()
    chart.plot(fifteen_years)

    assert not chart.scroll_row.isHidden()
    low, high = chart._ax.get_xlim()
    assert high - low == pytest.approx(VISIBLE_YEARS * 365.25, abs=1)
    # по умолчанию — последние годы
    assert chart.scrollbar.value() == chart.scrollbar.maximum()
    assert chart.window_label.text() == "Показаны 2015–2020 из 2006–2020"  # окно — 5 лет до ноября 2020

    chart.scrollbar.setValue(0)
    assert chart._ax.get_xlim()[0] == pytest.approx(mdates.date2num(chart._first))
    assert chart.window_label.text().startswith("Показаны 2006–")

    chart.set_methods((METHOD_KR,))  # переключение прочтений прокрутку не сбрасывает
    assert chart.scrollbar.value() == 0


def test_shift_wheel_scrolls_the_chart(fifteen_years):
    _qapp()
    chart = WarmColdChart()
    chart.plot(fifteen_years)
    start = chart.scrollbar.value()
    wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ShiftModifier, Qt.ScrollPhase.NoScrollPhase, False)

    assert chart.eventFilter(chart.canvas, wheel)
    assert chart.scrollbar.value() < start


def test_short_archive_has_no_scrollbar(engine, no_messages):
    dialog = _dashboard(engine)
    tab = dialog.precipitation_tab

    assert tab.warm_cold_chart.scroll_row.isHidden()  # 2019–2021 — короче окна
    assert tab.nested_chart.scroll_row.isHidden()
