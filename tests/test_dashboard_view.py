"""Экран "Дашборд": тексты карточек, фильтры (станция обязательна, пустой
период = весь архив), построение карточек и графиков по "Применить"."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.dashboard_data import (
    DashboardData,
    DatedValue,
    MonthRecord,
    NormInfo,
    YearAnomaly,
    describe_anomaly,
    format_signed,
)
from meteoregik.ui.dashboard_view import DashboardDialog, anomaly_note, card_texts

WMO_INDEX = 27704


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", text)))
    return shown


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5.84, "+5.8"), (-3.21, "−3.2"), (0.04, "0.0"), (-0.04, "0.0")],
)
def test_format_signed(value, expected):
    assert format_signed(value) == expected


def test_describe_anomaly_in_words():
    assert describe_anomaly(0.63) == "на 0.6 °C выше нормы"
    assert describe_anomaly(-1.25, "нормы июля") == "на 1.2 °C ниже нормы июля"
    assert describe_anomaly(0.01) == "на уровне нормы"


def test_card_texts_for_full_data():
    data = DashboardData(
        date_from=date(2018, 1, 1),
        date_to=date(2020, 12, 31),
        days_total=1096,
        days_with_data=1090,
        period_mean=6.04,
        period_anomaly=0.6,
        period_norm=5.44,
        abs_max=DatedValue(35.2, (date(2019, 7, 12), date(2020, 8, 3))),
        abs_min=DatedValue(-31.0, (date(2018, 2, 1),)),
        warmest_year=YearAnomaly(2020, 7.4, 1.6),
        coldest_year=YearAnomaly(2018, 4.9, -0.9),
        warmest_month=MonthRecord(2019, 7, 21.3, 2.0),
        coldest_month=MonthRecord(2018, 2, -12.0, None),
    )
    texts = [(t.caption, t.value, t.note) for t in card_texts(data)]
    assert texts == [
        ("Средняя суточная температура за период", "+6.0 °C", "на 0.6 °C выше нормы 1991–2020 (+5.4 °C)"),
        ("Абсолютный максимум за период", "+35.2 °C", "12.07.2019, 03.08.2020"),
        ("Абсолютный минимум за период", "−31.0 °C", "01.02.2018"),
        ("Полнота данных", "99.5 %", "1 090 из 1 096 дн., пропущено 6"),
        ("Самый тёплый год", "2020", "+7.4 °C, на 1.6 °C выше нормы 1991–2020"),
        ("Самый холодный год", "2018", "+4.9 °C, на 0.9 °C ниже нормы 1991–2020"),
        ("Самый тёплый месяц", "Июль 2019", "+21.3 °C, на 2.0 °C выше нормы июля"),
        ("Самый холодный месяц", "Февраль 2018", "−12.0 °C"),
    ]


def test_card_texts_name_archive_mean_when_no_standard_norm():
    """Откат на весь архив — это не норма ВМО, подпись говорит "средней по архиву"."""
    data = DashboardData(
        date_from=date(2018, 1, 1), date_to=date(2020, 12, 31), days_total=1096, days_with_data=1096,
        period_mean=6.0, period_anomaly=0.01, abs_max=None, abs_min=None,
        warmest_year=YearAnomaly(2020, 7.4, 1.6), coldest_year=None,
        warmest_month=MonthRecord(2019, 7, 21.3, 2.0), coldest_month=None,
        annual_norm=NormInfo(5.8, 2018, 2020, 3, standard=False), norm_standard=False,
    )
    texts = card_texts(data)
    assert texts[0].note == "на уровне средней по архиву"
    assert texts[4].note == "+7.4 °C, на 1.6 °C выше средней по архиву"
    assert texts[6].note == "+21.3 °C, на 2.0 °C выше средней июля по архиву"
    assert anomaly_note(data).startswith("За 1991–2020 меньше 24 полных лет")


def test_extreme_card_shortens_long_list_of_dates():
    days = tuple(date(2020, 7, d) for d in range(1, 8))
    data = DashboardData(
        date_from=date(2020, 1, 1), date_to=date(2020, 12, 31), days_total=366, days_with_data=366,
        period_mean=None, period_anomaly=None, abs_max=DatedValue(30.0, days), abs_min=None,
        warmest_year=None, coldest_year=None, warmest_month=None, coldest_month=None,
    )
    assert card_texts(data)[1].note == "01.07.2020, 02.07.2020, 03.07.2020, 04.07.2020, 05.07.2020 и ещё 2 дн."


def test_card_texts_explain_missing_values():
    data = DashboardData(
        date_from=date(2020, 1, 1), date_to=date(2020, 1, 10), days_total=10, days_with_data=0,
        period_mean=None, period_anomaly=None, abs_max=None, abs_min=None,
        warmest_year=None, coldest_year=None, warmest_month=None, coldest_month=None,
    )
    texts = card_texts(data)
    assert all(t.value == "—" for i, t in enumerate(texts) if i != 3)
    assert texts[4].note == "нет полных лет в периоде"
    assert anomaly_note(data).startswith("Норма не рассчитана")


def _make_engine(tmp_path):
    """Станция с двумя полными годами (2019 — 0 °C, 2020 — 2 °C) в
    computed_indicators: суточные avg/min/max + месячные и годовые avg."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    rows = []

    def add(period_type, start, end, stat, value):
        rows.append({"wmo_index": WMO_INDEX, "period_type": period_type, "period_start": start,
                     "period_end": end, "parameter": "T", "stat_type": stat, "value": value})

    for year, avg in ((2019, 0.0), (2020, 2.0)):
        day = date(year, 1, 1)
        while day.year == year:
            add("day", day, day, "avg", avg)
            add("day", day, day, "min", avg - 5)
            add("day", day, day, "max", avg + 5)
            day += timedelta(days=1)
        for month in range(1, 13):
            start = date(year, month, 1)
            end = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
            add("month", start, end, "avg", avg)
        add("year", date(year, 1, 1), date(year, 12, 31), "avg", avg)

    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Мосальск", wmo_index=WMO_INDEX))
        conn.execute(computed_indicators.insert(), rows)
    return engine


def test_dashboard_requires_station(tmp_path, messages):
    _qapp()
    dialog = DashboardDialog(_make_engine(tmp_path), run_async=False)
    dialog.apply_btn.click()
    assert messages == [("warning", "Выберите метеостанцию из списка.")]


def test_dashboard_builds_cards_and_charts_for_whole_archive(tmp_path, messages):
    _qapp()
    dialog = DashboardDialog(_make_engine(tmp_path), run_async=False)
    dialog.station_combo.select_wmo(WMO_INDEX)
    assert dialog._stack.currentIndex() == 0  # до "Применить" — подсказка

    dialog.apply_btn.click()

    assert messages == []
    assert dialog._stack.currentWidget() is dialog._content
    assert "01.01.2019–31.12.2020" in dialog._station_label.text()
    assert dialog.cards[0].value.text() == "+1.0 °C"
    assert dialog.cards[4].value.text() == "2020"
    assert dialog.cards[5].value.text() == "2019"
    assert "2 полн. лет (2019–2020)" in dialog._anomaly_note.text()
    assert dialog.heatmap._years == [2019, 2020]
    assert [a.year for a in dialog.anomaly_chart._anomalies] == [2019, 2020]
    assert not dialog.loading_overlay.isVisible()


def test_dashboard_reports_empty_period(tmp_path, messages):
    _qapp()
    dialog = DashboardDialog(_make_engine(tmp_path), run_async=False)
    dialog.station_combo.select_wmo(WMO_INDEX)
    dialog.date_from_edit.set_date(date(2010, 1, 1))
    dialog.date_to_edit.set_date(date(2010, 12, 31))

    dialog.apply_btn.click()

    assert messages == [("info", "За выбранный период данных не найдено.")]
    assert dialog._stack.currentIndex() == 0


def test_every_card_has_its_own_help_text():
    data = DashboardData(
        date_from=date(2020, 1, 1), date_to=date(2020, 1, 10), days_total=10, days_with_data=0,
        period_mean=None, period_anomaly=None, abs_max=None, abs_min=None,
        warmest_year=None, coldest_year=None, warmest_month=None, coldest_month=None,
    )
    helps = [t.help for t in card_texts(data)]
    # Подсказка есть и у карточки без значения — описывает расчёт, не результат.
    assert all(helps)
    assert len(set(helps)) == len(helps)
    assert "правило ВМО «3/5»" in helps[4]
    assert "с нормой своего месяца" in helps[0] and "1991–2020" in helps[0]


def test_help_badge_shows_calculation_tooltip(tmp_path, messages):
    _qapp()
    dialog = DashboardDialog(_make_engine(tmp_path), run_async=False)
    dialog.station_combo.select_wmo(WMO_INDEX)
    dialog.apply_btn.click()

    badge = dialog.cards[1].help_badge
    assert badge.text() == "?"
    assert "Самая высокая температура за выбранный период" in badge.toolTip()
    assert badge.accessibleName() == "Как считается: Абсолютный максимум за период"
