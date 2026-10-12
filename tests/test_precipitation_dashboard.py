"""Вкладка "Осадки" дашборда: расчёт данных (reports/precipitation_data.py),
дни с пропущенным сроком, тексты карточек и сама вкладка в окне дашборда."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, time, timedelta

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.calculations import precipitation
from meteoregik.calculations.anomalies import StationValues
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.precipitation_data import (
    DryRun,
    PrecipitationData,
    compute_precipitation,
    longest_dry_runs,
    percent_of_norm,
    station_day_bounds,
)
from meteoregik.ui.dashboard_precip_charts import (
    HEATMAP_PERCENT,
    HEATMAP_SUM,
    heat_grid,
    month_tooltip,
    season_tooltip,
)
from meteoregik.ui.dashboard_precip_tab import annual_note, annual_title, card_texts
from meteoregik.ui.dashboard_view import TAB_PRECIPITATION, TAB_SOURCE, TAB_TEMPERATURE, DashboardDialog

STATION = 27707


def _values(days: dict[date, float]) -> StationValues:
    """Суммы месяцев, сезонов и лет — так же, как их считает пересчёт."""
    values = StationValues(days=dict(days))
    for day, amount in days.items():
        month = (day.year, day.month)
        values.months[month] = values.months.get(month, 0.0) + amount
        values.years[day.year] = values.years.get(day.year, 0.0) + amount
        season = precipitation.season_bounds(day)[0]
        values.seasons[season] = values.seasons.get(season, 0.0) + amount
    return values


def _daily(first: date, last: date, amount: float) -> dict[date, float]:
    days = {}
    day = first
    while day <= last:
        days[day] = amount
        day += timedelta(days=1)
    return days


@pytest.fixture(scope="module")
def three_years() -> StationValues:
    """2019 и 2020 — по 1 мм в день, 2021 — по 2 мм."""
    return _values({
        **_daily(date(2019, 1, 1), date(2020, 12, 31), 1.0),
        **_daily(date(2021, 1, 1), date(2021, 12, 31), 2.0),
    })


# --- расчёт ---


def test_period_sum_is_compared_with_norm_of_its_own_days(three_years):
    whole = compute_precipitation(three_years, date(2019, 1, 1), date(2021, 12, 31))
    july = compute_precipitation(three_years, date(2021, 7, 1), date(2021, 7, 31))

    assert whole.period_sum == 365 + 366 + 730
    assert whole.period_percent == pytest.approx(100)  # весь архив сравнивается сам с собой
    assert whole.norm_standard is False
    # июль 2021 — с нормой июля (31 + 31 + 62) / 3, а не с годовой
    assert july.period_sum == 62
    assert july.period_norm == pytest.approx(124 / 3)
    assert july.period_percent == pytest.approx(150)


def test_missing_days_do_not_lower_percent_of_norm(three_years):
    """Дни без данных не входят ни в сумму, ни в норму периода."""
    days = dict(three_years.days)
    for day in range(1, 11):
        del days[date(2021, 7, day)]

    july = compute_precipitation(_values(days), date(2021, 7, 1), date(2021, 7, 31), norms=None)

    assert july.days_with_data == 21 and july.days_total == 31
    assert july.period_sum == 42
    # норма июля — по полным июлям 2019 и 2020 (по 31 мм); в периоде 21 день с данными
    assert july.period_norm == pytest.approx(31 * 21 / 31)
    assert july.period_percent == pytest.approx(200)


def test_records_are_taken_from_complete_years_and_months_inside_period(three_years):
    data = compute_precipitation(three_years, date(2019, 1, 1), date(2021, 12, 31))

    assert [y.year for y in data.wettest_years] == [2021]
    assert [y.year for y in data.driest_years] == [2019]
    assert data.wettest_years[0].percent == pytest.approx(730 / ((365 + 366 + 730) / 3) * 100)
    # 31-дневных месяцев в 2021 семь — рекорд делят все
    assert [(m.year, m.month) for m in data.wettest_months] == [(2021, m) for m in (1, 3, 5, 7, 8, 10, 12)]
    assert [(m.year, m.month) for m in data.driest_months] == [(2019, 2)]
    assert [y.year for y in data.year_sums] == [2019, 2020, 2021]
    assert data.trend is None  # меньше пяти полных лет
    assert data.daily_max.value == 2.0 and len(data.daily_max.days) == 365


def test_year_cut_by_period_is_not_a_record(three_years):
    data = compute_precipitation(three_years, date(2019, 3, 1), date(2021, 12, 31))

    assert [y.year for y in data.year_sums] == [2020, 2021]
    assert (2019, 3) in data.month_cells and (2019, 2) not in data.month_cells
    assert data.years == [2019, 2020, 2021]


def test_wet_days_are_counted_from_one_millimetre(three_years):
    days = dict(three_years.days)
    for day in range(1, 32):
        days[date(2020, 1, day)] = 0.9  # следы осадков — не "день с осадками"

    data = compute_precipitation(_values(days), date(2019, 1, 1), date(2021, 12, 31))

    assert data.wet_days == 365 + (366 - 31) + 365
    assert data.wet_days_years == 3
    assert data.wet_days_per_year == pytest.approx((365 + 335 + 365) / 3)


def test_seasons_touching_period_with_winter_named_by_its_last_year(three_years):
    data = compute_precipitation(three_years, date(2020, 1, 1), date(2020, 12, 31))

    assert [(s.name, s.year) for s in data.seasons] == [
        ("зима", 2020), ("весна", 2020), ("лето", 2020), ("осень", 2020), ("зима", 2021),
    ]
    winter = data.seasons[0]
    assert winter.value == 31 + 31 + 29 and winter.complete
    assert data.seasons[-1].value == 31 + 62 + 56


def test_longest_dry_runs_break_on_rain_and_on_missing_day():
    days = {
        **_daily(date(2020, 1, 1), date(2020, 1, 5), 0.0),
        date(2020, 1, 6): 5.0,
        **_daily(date(2020, 1, 7), date(2020, 1, 9), 0.5),
        # 10 января данных нет — период обрывается
        **_daily(date(2020, 1, 11), date(2020, 1, 15), 0.0),
    }

    runs = longest_dry_runs(days, date(2020, 1, 1), date(2020, 1, 31))

    assert runs == (DryRun(date(2020, 1, 1), date(2020, 1, 5)), DryRun(date(2020, 1, 11), date(2020, 1, 15)))
    assert runs[0].days == 5
    assert longest_dry_runs(days, date(2020, 1, 3), date(2020, 1, 6)) == (DryRun(date(2020, 1, 3), date(2020, 1, 5)),)
    assert longest_dry_runs({date(2020, 1, 1): 3.0}, date(2020, 1, 1), date(2020, 1, 2)) == ()


def test_missed_terms_are_counted_only_for_days_of_period_with_sum(three_years):
    missed = {date(2020, 5, 1), date(2020, 5, 2), date(2019, 5, 1), date(2030, 1, 1)}

    data = compute_precipitation(three_years, date(2020, 1, 1), date(2020, 12, 31), missed_terms=missed)

    assert data.missed_term_days == 2


def test_percent_of_norm_without_norm_or_with_zero_norm():
    assert percent_of_norm(10.0, None) is None
    assert percent_of_norm(10.0, 0.0) is None
    assert percent_of_norm(30.0, 60.0) == 50.0


# --- дни с пропущенным сроком ---


def _rp5(day: date, hour: int, amount: str | None, period: float | None = 12.0) -> dict:
    return {"wmo_index": STATION, "station_name": "Сухиничи", "obs_date": day, "obs_time": time(hour),
            "RRR_precipitation": amount, "tR_precipitation_period": period, "source_file": "rp5.xls"}


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Сухиничи", wmo_index=STATION))
    return engine


def test_day_with_one_main_term_is_a_missed_term_not_a_missed_day(engine):
    first = date(2020, 6, 1)
    both, one, from_gg, daily = (first + timedelta(days=n) for n in range(4))
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert(), [
            _rp5(both, 9, "1.0"), _rp5(both, 21, "Осадков нет"),
            _rp5(both, 6, "0.5"),  # второй ряд сроков — не основной
            _rp5(one, 9, "2.0"), _rp5(one, 21, None),
            _rp5(from_gg, 21, "3.0"),
            _rp5(daily, 9, "4.0", period=24.0),  # суточная запись: срока основного ряда нет по устройству
        ])
        conn.execute(gg_readings.insert().values(
            wmo_index=STATION, obs_date=from_gg, rrr_precipitation=3.0, source_file="gg.xlsx"
        ))

        assert precipitation.days_with_missed_term(conn, STATION) == {one}


def test_station_without_twelve_hourly_records_has_no_missed_terms(engine):
    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(
            wmo_index=STATION, obs_date=date(2020, 6, 1), rrr_precipitation=3.0, source_file="gg.xlsx"
        ))

        assert precipitation.days_with_missed_term(conn, STATION) == set()


# --- тексты ---


def test_card_texts_for_full_data(three_years):
    data = compute_precipitation(three_years, date(2019, 1, 1), date(2021, 12, 31), missed_terms={date(2020, 5, 1)})

    cards = card_texts(data)

    assert [c.caption for c in cards] == [
        "Сумма осадков за период", "Самый влажный год", "Самый сухой год", "Самый влажный месяц",
        "Самый сухой месяц", "Суточный максимум", "Дней с осадками ≥ 1 мм в году",
        "Самый длинный период без осадков", "Полнота данных об осадках",
    ]
    assert cards[0].value == "1461.0 мм"
    assert cards[0].note == "100 % средней по архиву (0.0 мм; 1461.0 мм)"
    assert cards[1].value == "2021"
    assert cards[1].note == "730.0 мм, 150 % средней по архиву (+243.0 мм; 487.0 мм)"
    assert cards[3].value == "Январь 2021 и ещё 6 мес."
    assert cards[4].value == "Февраль 2019"
    # норма февраля — (28 + 29 + 56) / 3
    assert cards[4].note == "28.0 мм, 74 % средней февраля по архиву (−9.7 мм; 37.7 мм)"
    assert cards[5].value == "2.0 мм" and cards[5].note.endswith("и ещё 360 дн.")
    assert cards[6].value == "365 дн." and "за 3 полн. лет" in cards[6].note
    assert cards[7].value == "—" and cards[7].note == "нет дней без осадков в периоде"
    assert cards[8].value == "100.0 %"
    assert cards[8].note == (
        "1\xa0096 из 1\xa0096 дн., без пропущенных дней\n"  # тысячи — через неразрывный пробел
        "Пропущен срок наблюдения: 1 дн. (сумма может быть занижена)"
    )


def test_completeness_card_reports_missed_days_and_no_missed_terms(three_years):
    data = compute_precipitation(three_years, date(2018, 12, 22), date(2019, 1, 10))

    completeness = card_texts(data)[8]

    assert completeness.value == "50.0 %"
    assert completeness.note == "10 из 20 дн., пропущено 10 дн.\nПропущенных сроков наблюдения нет"


def test_card_texts_explain_missing_values_and_every_card_has_its_own_help():
    data = PrecipitationData(date(2020, 1, 1), date(2020, 1, 10), days_total=10, days_with_data=0)

    cards = card_texts(data)

    assert [c.value for c in cards[:8]] == ["—"] * 8
    assert cards[0].note == "нет суточных сумм в периоде"
    assert cards[1].note == "нет полных лет в периоде" and cards[3].note == "нет полных месяцев в периоде"
    helps = [c.help for c in cards]
    assert all(helps) and len(set(helps)) == len(helps)
    assert "Пропущенный срок" in helps[8] and "правило ВМО «3/5»" in helps[1]


def test_annual_note_names_archive_mean_and_trend_threshold(three_years):
    note = annual_note(compute_precipitation(three_years, date(2019, 1, 1), date(2021, 12, 31)))

    assert "средняя по архиву 487 мм в год за 3 полн. лет (2019–2021)" in note
    assert "Линия тренда — от 5 полных лет" in note


def test_chart_tooltips(three_years):
    days = dict(three_years.days)
    for day in range(1, 11):
        del days[date(2021, 7, day)]
    data = compute_precipitation(_values(days), date(2019, 1, 1), date(2021, 12, 31))

    assert month_tooltip("Март 2020", None) == "Март 2020: нет данных"
    assert month_tooltip("Июль 2021", data.month_cells[(2021, 7)]) == (
        "Июль 2021: 42.0 мм\n"
        "135 % нормы (+11.0 мм, норма 31.0 мм)\n"
        "Неполный месяц: данные за 21 из 31 дн."
    )
    summer = next(s for s in data.seasons if s.start == date(2021, 6, 1))
    assert season_tooltip(summer).splitlines()[0] == "Лето 2021: 164.0 мм"
    assert "Неполный сезон" in season_tooltip(summer)


# --- вкладка в окне дашборда ---


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", text)))
    return shown


def _add_indicators(engine, parameter: str, stat_type: str, values: StationValues) -> None:
    def row(period_type: str, start: date, end: date, value: float) -> dict:
        return {"wmo_index": STATION, "period_type": period_type, "period_start": start, "period_end": end,
                "parameter": parameter, "stat_type": stat_type, "value": value}

    rows = [row("day", day, day, value) for day, value in values.days.items()]
    for (year, month), value in values.months.items():
        start = date(year, month, 1)
        rows.append(row("month", start, date(year, month, monthrange(year, month)[1]), value))
    rows += [row("year", date(year, 1, 1), date(year, 12, 31), value) for year, value in values.years.items()]
    if parameter == "R":
        rows += [
            row("season", start, precipitation.season_bounds(start)[1], value)
            for start, value in values.seasons.items()
        ]
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), rows)


def test_dashboard_has_three_tabs_with_filter_shared_by_the_first_two(engine, three_years):
    _qapp()
    dialog = DashboardDialog(engine, run_async=False)
    dialog.show()

    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        TAB_TEMPERATURE, TAB_PRECIPITATION, TAB_SOURCE,
    ]
    dialog.tabs.setCurrentWidget(dialog.precipitation_tab)
    assert dialog._filter_card.isVisible()
    dialog.tabs.setCurrentWidget(dialog.source_tab)
    assert not dialog._filter_card.isVisible()  # "Исходные данные" — по всей базе
    dialog.close()


def test_precipitation_tab_shows_cards_and_charts(engine, three_years, messages):
    _qapp()
    _add_indicators(engine, "R", "sum", three_years)
    dialog = DashboardDialog(engine, run_async=False)
    tab = dialog.precipitation_tab
    dialog.station_combo.select_wmo(STATION)

    dialog.apply_btn.click()

    assert station_day_bounds(engine, STATION) == (date(2019, 1, 1), date(2021, 12, 31))
    assert tab._stack.currentWidget() is tab._content
    assert "01.01.2019–31.12.2021" in tab._station_label.text()
    assert tab.cards[0].value.text() == "1461.0 мм"
    assert tab.cards[1].value.text() == "2021"
    assert [tab.year_combo.itemText(i) for i in range(tab.year_combo.count())] == ["2019", "2020", "2021"]
    assert tab.course_chart.year == 2021
    assert [item.year for item in tab.annual_chart._years] == [2019, 2020, 2021]
    assert not tab.loading_overlay.isVisible()
    # у станции нет температуры: её вкладка объясняет это сама, без окна-сообщения
    assert messages == []
    assert "данных не найдено" in dialog._hint.text()

    tab.year_combo.setCurrentIndex(0)
    assert tab.course_chart.year == 2019
    assert tab.heatmap.mode == HEATMAP_PERCENT
    tab._heatmap_mode_group.button(1).setChecked(True)
    assert tab.heatmap.mode == HEATMAP_SUM
    assert tab._heatmap_title.text() == "Сумма осадков по месяцам"


def test_station_with_temperature_only_explains_empty_precipitation_tab(engine, three_years, messages):
    _qapp()
    _add_indicators(engine, "T", "avg", three_years)
    dialog = DashboardDialog(engine, run_async=False)
    dialog.station_combo.select_wmo(STATION)

    dialog.apply_btn.click()

    tab = dialog.precipitation_tab
    assert messages == []
    assert dialog._stack.currentWidget() is dialog._content
    assert tab._stack.currentWidget() is tab._hint
    assert "данных об осадках не найдено" in tab._hint.text()


# --- короткий период: декады вместо лет ---


def test_short_period_shows_decades_instead_of_years(three_years):
    data = compute_precipitation(three_years, date(2020, 3, 1), date(2021, 8, 31))

    assert data.short_period
    # третья декада 31-дневного месяца 2021 года — 11 дней по 2 мм
    assert [d.start for d in data.wettest_decades] == [date(2021, month, 21) for month in (1, 3, 5, 7, 8)]
    assert data.wettest_decades[0].value == 22 and data.wettest_decades[0].number == 3
    assert all(d.value == 10 for d in data.driest_decades)

    cards = card_texts(data)
    assert cards[1].caption == "Самая влажная декада"
    assert cards[1].value == "3-я декада января 2021 и ещё 4 дек."
    assert cards[1].note.startswith("22.0 мм, 150 % средней 3-й декады января по архиву")
    assert cards[2].caption == "Самая сухая декада"
    assert cards[6].caption == "Дней с осадками ≥ 1 мм за период"
    assert cards[6].value == "549 дн." and cards[6].note == "из 549 дн. с данными"
    assert len({c.help for c in cards}) == len(cards)
    assert annual_title(data) == "Месячная сумма осадков относительно нормы"
    assert "по месяцам" in annual_note(data)


def test_two_full_years_are_not_a_short_period(three_years):
    data = compute_precipitation(three_years, date(2019, 1, 1), date(2020, 12, 31))

    assert not data.short_period
    assert card_texts(data)[1].caption == "Самый влажный год"
    assert annual_title(data) == "Годовая сумма осадков относительно нормы"


def test_short_period_heatmap_is_months_by_decades_and_chart_is_by_months(engine, three_years, messages):
    _qapp()
    _add_indicators(engine, "R", "sum", three_years)
    dialog = DashboardDialog(engine, run_async=False)
    tab = dialog.precipitation_tab
    dialog.station_combo.select_wmo(STATION)
    dialog.date_from_edit.set_date(date(2020, 3, 1))
    dialog.date_to_edit.set_date(date(2021, 8, 31))

    dialog.apply_btn.click()

    grid = tab.heatmap._grid
    assert grid.col_labels == ["1-я дек.", "2-я дек.", "3-я дек."]
    assert len(grid.row_labels) == 18 and grid.row_labels[0] == "мар 2020"
    assert tab._heatmap_title.text() == "Осадки по декадам, % нормы"
    assert "неполная декада" in tab._heatmap_note.text()
    assert len(tab.annual_chart._months) == 18 and tab.annual_chart._years == []
    assert tab._annual_title.text() == "Месячная сумма осадков относительно нормы"
    assert heat_grid(compute_precipitation(three_years, date(2019, 1, 1), date(2021, 12, 31))).row_labels == [
        "2019", "2020", "2021",
    ]
