"""Экран "Карта": данные (значения показателей по станциям за период и по
шагам времени), оформление маркеров (цвета, подписи, карточки, легенда) и
сам экран с подменой браузера на RecordingCanvas."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.map_data import (
    ANOMALY,
    MAXIMUM,
    MEAN,
    MINIMUM,
    NOTE_INCOMPLETE_YEAR,
    NOTE_NO_COMPLETE_YEARS,
    NOTE_NO_DATA,
    PRECIP_DAY_MAX,
    PRECIP_PERCENT,
    PRECIP_SUM,
    STEP_MONTH,
    STEP_YEAR,
    MapStation,
    StationValue,
    fetch_map_data,
    period_values,
    time_series,
)
from meteoregik.reports.map_render import (
    DIVERGING_STOPS,
    FILL_SCALE,
    FORMAT_COLORS,
    MAP_SUM_STOPS,
    MUTED_COLOR,
    PERCENT_SCALE_MIN_HALF,
    ColorScale,
    background_points,
    fill_layer,
    indicator_layer,
    indicator_scale,
    popup_html,
    station_name,
    stations_layer,
)
from meteoregik.reports.precipitation_data import PERCENT_STOPS, build_precipitation
from meteoregik.ui.dashboard_view import DashboardDialog
from meteoregik.ui.map_view import (
    BASE_OSM,
    BASE_OUTLINE,
    MODE_FILL,
    MODE_INDICATOR,
    TIME_YEARS,
    MapDialog,
    RecordingCanvas,
    ScriptQueue,
    base_status_text,
    missing_coordinates_text,
)

WARM = 27707  # 2019 — 0 °C, 2020 — +2 °C, оба года полные
COLD = 27704  # 2020 — −4 °C, с пропуском в январе (год неполный)
# Осадки: у тёплой 2 мм в день в 2019 году (730 мм) и 1 мм в 2020-м, кроме
# ливня 10 августа — 21 мм (386 мм за год); у холодной — 3 мм в день в 2020-м
RAINY_DAY = date(2020, 8, 10)
NO_COORDS = 11111


def _qapp():
    return QApplication.instance() or QApplication([])


def _year_rows(wmo_index: int, year: int, avg: float, skip: set[date] = frozenset()) -> list[dict]:
    rows = []

    def add(period_type: str, start: date, end: date, stat: str, value: float) -> None:
        rows.append({"wmo_index": wmo_index, "period_type": period_type, "period_start": start,
                     "period_end": end, "parameter": "T", "stat_type": stat, "value": value})

    day = date(year, 1, 1)
    while day.year == year:
        if day not in skip:
            add("day", day, day, "avg", avg)
            add("day", day, day, "min", avg - 5)
            add("day", day, day, "max", avg + 5 + (10 if (day.month, day.day) == (7, 15) else 0))
        day += timedelta(days=1)
    for month in range(1, 13):
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        add("month", start, end, "avg", avg)
        # как в рабочей базе: у месяца и года есть и свои min/max (calculations/recompute.py)
        add("month", start, end, "min", avg - 5)
        add("month", start, end, "max", avg + 5 + (10 if month == 7 else 0))
    add("year", date(year, 1, 1), date(year, 12, 31), "avg", avg)
    add("year", date(year, 1, 1), date(year, 12, 31), "min", avg - 5)
    add("year", date(year, 1, 1), date(year, 12, 31), "max", avg + 15)
    return rows


def _precip_rows(wmo_index: int, year: int, daily: float, skip: set[date] = frozenset()) -> list[dict]:
    """Суточные суммы осадков года и суммы его месяцев и самого года — как
    их сохраняет calculations/recompute.py."""
    rows = []
    months: dict[int, float] = {}
    day = date(year, 1, 1)
    while day.year == year:
        if day not in skip:
            value = 21.0 if (wmo_index, day) == (WARM, RAINY_DAY) else daily
            months[day.month] = months.get(day.month, 0.0) + value
            rows.append({"wmo_index": wmo_index, "period_type": "day", "period_start": day, "period_end": day,
                         "parameter": "R", "stat_type": "sum", "value": value})
        day += timedelta(days=1)
    for month, total in months.items():
        end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        rows.append({"wmo_index": wmo_index, "period_type": "month", "period_start": date(year, month, 1),
                     "period_end": end, "parameter": "R", "stat_type": "sum", "value": total})
    rows.append({"wmo_index": wmo_index, "period_type": "year", "period_start": date(year, 1, 1),
                 "period_end": date(year, 12, 31), "parameter": "R", "stat_type": "sum", "value": sum(months.values())})
    return rows


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    january_gap = {date(2020, 1, day) for day in range(5, 15)}
    with engine.begin() as conn:
        conn.execute(stations.insert(), [
            {"name": "Тёплая", "wmo_index": WARM, "lat": 54.1, "lon": 35.3},
            {"name": "Холодная", "wmo_index": COLD, "lat": 54.5, "lon": 34.9},
            {"name": "Без координат", "wmo_index": NO_COORDS, "lat": None, "lon": None},
            {"name": "Пустая", "wmo_index": 22222, "lat": 60.0, "lon": 40.0},
        ])
        conn.execute(computed_indicators.insert(), [
            *_year_rows(WARM, 2019, 0.0),
            *_year_rows(WARM, 2020, 2.0),
            *_year_rows(COLD, 2020, -4.0, skip=january_gap),
            *_year_rows(NO_COORDS, 2020, 1.0),
            *_precip_rows(WARM, 2019, 2.0),
            *_precip_rows(WARM, 2020, 1.0),
            *_precip_rows(COLD, 2020, 3.0, skip=january_gap),
        ])
        conn.execute(gg_readings.insert(), [
            {"wmo_index": wmo, "station_name": "x", "obs_date": date(2020, 1, 1), "t_avg": 1.0, "source_file": "f.xlsx"}
            for wmo in (WARM, COLD, NO_COORDS)
        ])
    return engine


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    return shown


# --- данные ---


def test_map_data_lists_directory_and_stations_with_data(engine):
    data = fetch_map_data(engine)

    assert [s.label for s in data.stations] == ["Пустая (22222)", "Тёплая (27707)", "Холодная (27704)"]
    assert set(data.sources) == {WARM, COLD, NO_COORDS}
    assert set(data.periods) == {WARM, COLD, NO_COORDS}
    assert [row.wmo_index for row in data.without_coordinates] == [NO_COORDS]


def test_period_values_for_whole_archive_and_given_period(engine):
    data = fetch_map_data(engine)

    whole = period_values(engine, data, MEAN)
    assert whole[WARM].value == pytest.approx(1.0, abs=0.01)
    assert whole[COLD].value == pytest.approx(-4.0)

    year = period_values(engine, data, MEAN, date(2019, 1, 1), date(2019, 12, 31))
    assert year[WARM].value == pytest.approx(0.0)
    assert year[COLD] == StationValue(None, NOTE_NO_DATA)  # у станции нет данных за 2019


def test_period_extremes_carry_their_dates(engine):
    data = fetch_map_data(engine)

    highest = period_values(engine, data, MAXIMUM)[WARM]
    assert highest.value == pytest.approx(17.0)  # 2 + 5 + 10 в июльский день 2020 года
    assert highest.note == "15.07.2020"
    lowest = period_values(engine, data, MINIMUM)[WARM]
    assert lowest.value == pytest.approx(-5.0)
    assert "и ещё" in lowest.note  # минимум повторяется весь 2019 год


def test_period_values_match_dashboard(engine):
    """Значение на карте — то же, что на карточке дашборда той же станции."""
    _qapp()
    data = fetch_map_data(engine)
    dialog = DashboardDialog(engine, run_async=False, wmo_index=WARM)

    assert dialog.cards[0].value.text() == "+1.0 °C"
    assert round(period_values(engine, data, MEAN)[WARM].value, 1) == 1.0


def test_year_series_shows_only_complete_years(engine):
    data = fetch_map_data(engine)
    steps = time_series(data, MEAN, STEP_YEAR)

    assert [step.label for step in steps] == ["2019", "2020"]
    assert steps[0].values[WARM] == StationValue(0.0)
    assert steps[0].values[COLD] == StationValue(None, NOTE_NO_DATA)
    assert steps[1].values[WARM] == StationValue(2.0)
    assert steps[1].values[COLD] == StationValue(None, NOTE_INCOMPLETE_YEAR)


def test_year_extremes_do_not_require_complete_year(engine):
    data = fetch_map_data(engine)
    steps = time_series(data, MINIMUM, STEP_YEAR)

    assert steps[1].values[COLD].value == pytest.approx(-9.0)


def test_month_series_is_continuous_and_skips_incomplete_months(engine):
    data = fetch_map_data(engine)
    steps = time_series(data, MEAN, STEP_MONTH)

    assert len(steps) == 24
    assert (steps[0].label, steps[-1].label) == ("Январь 2019", "Декабрь 2020")
    january_2020 = steps[12]
    assert january_2020.values[WARM].value == pytest.approx(2.0)
    assert january_2020.values[COLD].value is None  # в январе пропущено 10 дней
    assert steps[13].values[COLD].value == pytest.approx(-4.0)


def test_anomaly_series_uses_station_norm(engine):
    data = fetch_map_data(engine)
    steps = time_series(data, ANOMALY, STEP_YEAR)

    # Норма станции — средняя по её архиву (+1 °C): базового периода ВМО у неё нет
    assert steps[0].values[WARM].value == pytest.approx(-1.0)
    assert steps[1].values[WARM].value == pytest.approx(1.0)


# --- данные: осадки ---


def test_precipitation_sum_for_period_and_mean_annual_for_whole_archive(engine):
    data = fetch_map_data(engine)

    year = period_values(engine, data, PRECIP_SUM, date(2019, 1, 1), date(2019, 12, 31))
    assert year[WARM] == StationValue(730.0)
    assert year[COLD] == StationValue(None, NOTE_NO_DATA)

    # Без периода — не сумма за весь архив (1116 мм), а средняя годовая по полным годам
    whole = period_values(engine, data, PRECIP_SUM)
    assert whole[WARM].value == pytest.approx(558.0)
    assert whole[WARM].note == "в среднем за год, полных лет — 2"
    assert whole[COLD] == StationValue(None, NOTE_NO_COMPLETE_YEARS)  # в январе 2020 пропуск


def test_precipitation_percent_matches_dashboard(engine):
    """Процент нормы на карте — тот же, что на карточке вкладки "Осадки"."""
    data = fetch_map_data(engine)
    period = (date(2020, 3, 1), date(2020, 9, 15))

    expected = build_precipitation(engine, WARM, *period).period_percent
    assert expected is not None
    assert period_values(engine, data, PRECIP_PERCENT, *period)[WARM].value == pytest.approx(expected)


def test_precipitation_daily_maximum_carries_its_date(engine):
    data = fetch_map_data(engine)

    highest = period_values(engine, data, PRECIP_DAY_MAX)[WARM]
    assert (highest.value, highest.note) == (21.0, "10.08.2020")


def test_precipitation_year_series(engine):
    data = fetch_map_data(engine)

    sums = time_series(data, PRECIP_SUM, STEP_YEAR)
    assert [step.label for step in sums] == ["2019", "2020"]
    assert (sums[0].values[WARM].value, sums[1].values[WARM].value) == (730.0, 386.0)
    assert sums[1].values[COLD] == StationValue(None, NOTE_INCOMPLETE_YEAR)

    # Норма станции — средняя годовая сумма по её архиву, 558 мм
    percents = time_series(data, PRECIP_PERCENT, STEP_YEAR)
    assert percents[0].values[WARM].value == pytest.approx(730 / 558 * 100)
    assert percents[1].values[WARM].value == pytest.approx(386 / 558 * 100)

    # Суточный максимум полноты года не требует
    maxima = time_series(data, PRECIP_DAY_MAX, STEP_YEAR)
    assert (maxima[1].values[WARM].value, maxima[1].values[COLD].value) == (21.0, 3.0)


def test_precipitation_month_series(engine):
    data = fetch_map_data(engine)

    maxima = time_series(data, PRECIP_DAY_MAX, STEP_MONTH)
    assert (maxima[19].label, maxima[19].values[WARM].value) == ("Август 2020", 21.0)
    sums = time_series(data, PRECIP_SUM, STEP_MONTH)
    assert sums[12].values[COLD].value is None  # январь 2020 неполный
    assert sums[13].values[COLD].value == pytest.approx(87.0)  # февраль: 29 дней по 3 мм


def test_station_without_precipitation_stays_on_the_map_as_no_data(engine):
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), _year_rows(22222, 2020, 5.0))
    data = fetch_map_data(engine)

    assert period_values(engine, data, PRECIP_SUM)[22222] == StationValue(None, NOTE_NO_DATA)
    assert period_values(engine, data, PRECIP_DAY_MAX)[22222] == StationValue(None, NOTE_NO_DATA)
    assert time_series(data, PRECIP_SUM, STEP_YEAR)[1].values[22222] == StationValue(None, NOTE_NO_DATA)


def test_time_series_of_empty_database(tmp_path):
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)

    assert time_series(fetch_map_data(engine), MEAN, STEP_YEAR) == []


# --- оформление ---


def test_color_scale_interpolates_and_clamps():
    scale = ColorScale(("#000000", "#ffffff"), 0.0, 10.0)

    assert scale.color(0) == "#000000"
    assert scale.color(10) == "#ffffff"
    assert scale.color(5) == "#808080"
    assert scale.color(-3) == "#000000" and scale.color(99) == "#ffffff"
    assert ColorScale(("#000000", "#ffffff"), 4.0, 4.0).color(4.0) == "#808080"  # одна станция — середина


def test_anomaly_scale_is_symmetric_around_zero():
    scale = indicator_scale(ANOMALY, [-1.0, 3.0])

    assert (scale.low, scale.high) == (-3.0, 3.0)
    assert scale.color(0.0) == DIVERGING_STOPS[3]
    assert indicator_scale(ANOMALY, [0.01]).high == 0.5  # сотые доли не красятся как крайние


def test_temperature_scale_spans_shown_values():
    scale = indicator_scale(MEAN, [5.4, 5.9])

    assert (scale.low, scale.high) == (5.4, 5.9)
    assert scale.color(5.4) == DIVERGING_STOPS[0] and scale.color(5.9) == DIVERGING_STOPS[-1]


def test_precipitation_scales():
    sums = indicator_scale(PRECIP_SUM, [400.0, 700.0])
    assert (sums.low, sums.high) == (400.0, 700.0)
    assert sums.color(400.0) == MAP_SUM_STOPS[0] and sums.color(700.0) == MAP_SUM_STOPS[-1]

    percents = indicator_scale(PRECIP_PERCENT, [80.0, 130.0])
    assert (percents.low, percents.high) == (70.0, 130.0)  # симметрично вокруг 100 %
    assert percents.color(100.0) == PERCENT_STOPS[3]
    assert indicator_scale(PRECIP_PERCENT, [100.4]).high == 100 + PERCENT_SCALE_MIN_HALF
    capped = indicator_scale(PRECIP_PERCENT, [60.0, 540.0])
    assert (capped.low, capped.high) == (0.0, 200.0)  # выброс не сжимает шкалу


def test_station_name_from_label():
    assert station_name("Сухиничи, Калужская область (27707)") == "Сухиничи"
    assert station_name("Ташкент (38457)") == "Ташкент"


def test_stations_layer_colors_by_format(engine):
    data = fetch_map_data(engine)
    layer = stations_layer(data)
    by_wmo = {m["wmo"]: m for m in layer.markers}

    assert set(by_wmo) == {WARM, COLD}  # станции без координат на карте нет
    assert by_wmo[WARM]["color"] == FORMAT_COLORS[("ГГ",)]
    assert by_wmo[WARM]["label"] == "Тёплая"
    assert (by_wmo[WARM]["lat"], by_wmo[WARM]["lon"]) == (54.1, 35.3)
    assert "только ГГ" in layer.legend and "только rp5" not in layer.legend
    assert "app://dashboard/27707" in by_wmo[WARM]["popup"]


def test_indicator_layer_labels_values_and_mutes_missing(engine):
    data = fetch_map_data(engine)
    values = {WARM: StationValue(2.0), COLD: StationValue(None, NOTE_INCOMPLETE_YEAR), NO_COORDS: StationValue(1.0)}
    scale = indicator_scale(MEAN, [0.0, 2.0])
    layer = indicator_layer(data, MEAN, values, scale, "2020")
    by_wmo = {m["wmo"]: m for m in layer.markers}

    assert set(by_wmo) == {WARM, COLD}
    # Название станции остаётся в подписи, значение — второй строкой
    assert (by_wmo[WARM]["label"], by_wmo[WARM]["value"]) == ("Тёплая", "+2.0 °C")
    assert (by_wmo[WARM]["color"], by_wmo[WARM]["muted"]) == (scale.color(2.0), False)
    assert by_wmo[WARM]["tip"] == "Тёплая (27707): +2.0 °C"
    assert (by_wmo[COLD]["label"], by_wmo[COLD]["value"]) == ("Холодная", "")
    assert (by_wmo[COLD]["color"], by_wmo[COLD]["muted"]) == (MUTED_COLOR, True)
    assert "неполный год" in by_wmo[COLD]["tip"]
    assert "Средняя температура, °C" in layer.legend and "2020" in layer.legend
    assert "значения нет" in layer.legend


def test_precipitation_layer_writes_millimetres_and_percents(engine):
    data = fetch_map_data(engine)

    values = {WARM: StationValue(730.0)}
    sums = indicator_layer(data, PRECIP_SUM, values, indicator_scale(PRECIP_SUM, [386.0, 730.0]), "2019")
    assert (sums.markers[0]["label"], sums.markers[0]["value"]) == ("Тёплая", "730.0 мм")
    assert "Сумма осадков, мм" in sums.legend and "386.0 мм" in sums.legend

    values = {WARM: StationValue(130.8), COLD: StationValue(540.0)}
    percents = indicator_layer(data, PRECIP_PERCENT, values, indicator_scale(PRECIP_PERCENT, [130.8, 540.0]), "2019")
    by_wmo = {m["wmo"]: m for m in percents.markers}
    assert by_wmo[WARM]["value"] == "131 %"
    assert by_wmo[COLD]["color"] == PERCENT_STOPS[-1]  # выше предела шкалы — крайним цветом
    assert "Осадки, % нормы" in percents.legend and "Осадки, % нормы, %" not in percents.legend
    assert "≥ 200 %" in percents.legend and ">100<" in percents.legend


def test_fill_layer_sizes_by_archive_length(engine):
    data = fetch_map_data(engine)
    by_wmo = {m["wmo"]: m for m in fill_layer(data).markers}

    assert (by_wmo[WARM]["label"], by_wmo[WARM]["value"]) == ("Тёплая", "100 %")
    assert by_wmo[WARM]["color"] == FILL_SCALE.color(1.0)
    assert by_wmo[COLD]["value"] == "97 %"  # 356 из 366 дней
    assert by_wmo[WARM]["radius"] > by_wmo[COLD]["radius"]  # два года против одного


def test_popup_escapes_station_name_and_omits_link_without_data():
    station = MapStation(1, "<b>Станция</b> (1)", 50.0, 40.0)
    html = popup_html(station, None)

    assert "&lt;b&gt;Станция&lt;/b&gt;" in html
    assert "не залиты" in html
    assert "app://dashboard" not in html


def test_background_points(engine):
    assert [60.0, 40.0, "Пустая (22222)"] in background_points(fetch_map_data(engine))


# --- экран ---


def _dialog(engine) -> tuple[MapDialog, RecordingCanvas]:
    _qapp()
    canvas = RecordingCanvas()
    return MapDialog(engine, run_async=False, canvas=canvas), canvas


def test_map_opens_with_stations_and_background(engine):
    dialog, canvas = _dialog(engine)

    assert len(canvas.last("setBackground")[0]) == 3
    markers, fit = canvas.last("setMarkers")
    assert {m["wmo"] for m in markers} == {WARM, COLD}
    assert fit is True  # при открытии карта наводится на станции с данными
    assert "Залитые данные" in canvas.last("setLegend")[0]
    assert "Без координат (11111)" in dialog._note.text()
    assert dialog._indicator_row.isHidden()
    assert not dialog.loading_overlay.isVisible()


def test_indicator_mode_shows_values_without_refitting(engine):
    dialog, canvas = _dialog(engine)

    dialog.mode.button(MODE_INDICATOR).setChecked(True)

    markers, fit = canvas.last("setMarkers")
    assert fit is False
    assert {m["wmo"]: (m["label"], m["value"]) for m in markers} == {
        WARM: ("Тёплая", "+1.0 °C"), COLD: ("Холодная", "−4.0 °C"),
    }
    assert "за весь архив каждой станции" in canvas.last("setLegend")[0]
    assert not dialog._indicator_row.isHidden() and dialog._slider_box.isHidden()


def test_period_filter_applies_to_all_stations(engine, messages):
    dialog, canvas = _dialog(engine)
    dialog.mode.button(MODE_INDICATOR).setChecked(True)

    dialog.date_from_edit.set_date(date(2019, 1, 1))
    dialog.apply_btn.click()
    assert messages == [
        "Период задаётся двумя датами: заполните обе или очистите обе (тогда берётся весь архив каждой станции)."
    ]

    dialog.date_to_edit.set_date(date(2019, 12, 31))
    dialog.apply_btn.click()
    labels = {m["wmo"]: m["value"] for m in canvas.last("setMarkers")[0]}
    assert labels == {WARM: "0.0 °C", COLD: ""}
    assert "01.01.2019–31.12.2019" in canvas.last("setLegend")[0]


def test_time_slider_steps_through_years_with_one_scale(engine):
    dialog, canvas = _dialog(engine)
    dialog.mode.button(MODE_INDICATOR).setChecked(True)

    dialog.time_combo.setCurrentIndex(dialog.time_combo.findData(TIME_YEARS))

    assert dialog._period_box.isHidden() and not dialog._slider_box.isHidden()
    assert (dialog.time_slider.maximum(), dialog.time_slider.value()) == (1, 1)  # последний год
    assert dialog._step_label.text() == "2020"
    last_year = {m["wmo"]: m for m in canvas.last("setMarkers")[0]}

    dialog.time_slider.setValue(0)
    assert dialog._step_label.text() == "2019"
    first_year = {m["wmo"]: m for m in canvas.last("setMarkers")[0]}
    assert (first_year[WARM]["value"], last_year[WARM]["value"]) == ("0.0 °C", "+2.0 °C")
    # шкала общая на все годы: 0 °C — холодный край, +2 °C — тёплый
    assert first_year[WARM]["color"] == DIVERGING_STOPS[0]
    assert last_year[WARM]["color"] == DIVERGING_STOPS[-1]


def test_precipitation_indicators_on_the_map(engine):
    dialog, canvas = _dialog(engine)
    dialog.mode.button(MODE_INDICATOR).setChecked(True)

    dialog.indicator_combo.setCurrentIndex(dialog.indicator_combo.findData(PRECIP_SUM))
    by_wmo = {m["wmo"]: m for m in canvas.last("setMarkers")[0]}
    assert (by_wmo[WARM]["label"], by_wmo[WARM]["value"]) == ("Тёплая", "558.0 мм")
    assert (by_wmo[COLD]["label"], by_wmo[COLD]["value"], by_wmo[COLD]["muted"]) == ("Холодная", "", True)
    legend = canvas.last("setLegend")[0]
    assert "Сумма осадков, мм" in legend and "в среднем за год" in legend

    dialog.time_combo.setCurrentIndex(dialog.time_combo.findData(TIME_YEARS))
    dialog.indicator_combo.setCurrentIndex(dialog.indicator_combo.findData(PRECIP_PERCENT))
    assert dialog._step_label.text() == "2020"
    by_wmo = {m["wmo"]: m for m in canvas.last("setMarkers")[0]}
    assert by_wmo[WARM]["value"] == "69 %"  # 386 из 558 мм
    assert "Осадки, % нормы" in canvas.last("setLegend")[0]


def test_indicator_list_separates_temperature_from_precipitation(engine):
    dialog, _ = _dialog(engine)
    combo = dialog.indicator_combo

    keys = [combo.itemData(index) for index in range(combo.count())]
    assert keys == [MEAN, ANOMALY, MAXIMUM, MINIMUM, None, PRECIP_SUM, PRECIP_PERCENT, PRECIP_DAY_MAX]


def test_play_walks_to_the_last_step_and_stops(engine):
    dialog, _ = _dialog(engine)
    dialog.mode.button(MODE_INDICATOR).setChecked(True)
    dialog.time_combo.setCurrentIndex(dialog.time_combo.findData(TIME_YEARS))

    dialog.play_btn.setChecked(True)  # с конца — начинает сначала
    assert dialog.time_slider.value() == 0 and dialog._play_timer.isActive()
    assert dialog.play_btn.text() == "Остановить"

    dialog._play_step()
    assert dialog.time_slider.value() == 1
    dialog._play_step()
    assert not dialog.play_btn.isChecked() and not dialog._play_timer.isActive()


def test_fill_mode_and_view_toggles(engine):
    dialog, canvas = _dialog(engine)

    dialog.mode.button(MODE_FILL).setChecked(True)
    assert "Дней с данными" in canvas.last("setLegend")[0]

    dialog.labels_check.setChecked(False)
    dialog.background_check.setChecked(False)
    dialog.outline_check.setChecked(True)
    assert canvas.last("setLabelsVisible") == (False,)
    assert canvas.last("showBackground") == (False,)
    assert canvas.last("setOutlineOnly") == (True,)
    assert dialog._status.text() == "Подложка: контурная карта"


def test_base_status_follows_the_page(engine):
    dialog, canvas = _dialog(engine)

    canvas.base_changed.emit(BASE_OSM)
    assert dialog._status.text() == "Подложка: OpenStreetMap"
    canvas.base_changed.emit(BASE_OUTLINE)
    assert "нет связи с OpenStreetMap" in dialog._status.text()
    assert base_status_text(BASE_OSM, True) == "Подложка: контурная карта"


def test_station_card_opens_dashboard_for_that_station(engine, monkeypatch):
    dialog, canvas = _dialog(engine)
    opened = []
    monkeypatch.setattr(
        "meteoregik.ui.map_view.DashboardDialog",
        lambda engine, parent, wmo_index: opened.append(wmo_index) or type("D", (), {"exec": lambda self: 0})(),
    )

    canvas.dashboard_requested.emit(WARM)

    assert opened == [WARM]


def test_map_of_empty_database_shows_only_directory(tmp_path):
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Пустая", wmo_index=1, lat=60.0, lon=40.0))
    dialog, canvas = _dialog(engine)

    assert canvas.last("setMarkers") == ([], True)
    assert "Данные ещё не залиты" in dialog._note.text()
    assert missing_coordinates_text(dialog._data) == ""


def test_dashboard_can_open_on_given_station(engine):
    _qapp()
    dialog = DashboardDialog(engine, run_async=False, wmo_index=WARM)

    assert dialog._stack.currentWidget() is dialog._content
    assert dialog._station_label.text().startswith("Тёплая (27707)")


def test_slider_ends_at_last_date_of_stations_on_the_map(engine):
    """Станция без координат на карту не попадает — её более поздние данные
    не должны удлинять ползунок до дат, где на карте пусто."""
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), _year_rows(NO_COORDS, 2026, 3.0))
    data = fetch_map_data(engine)

    assert [step.label for step in time_series(data, MEAN, STEP_YEAR)] == ["2019", "2020"]
    assert time_series(data, MEAN, STEP_MONTH)[-1].label == "Декабрь 2020"
    assert NO_COORDS not in period_values(engine, data, MEAN)

    dialog, _ = _dialog(engine)
    dialog.mode.button(MODE_INDICATOR).setChecked(True)
    dialog.time_combo.setCurrentIndex(dialog.time_combo.findData(TIME_YEARS))
    assert dialog._step_label.text() == "2020"


def test_script_queue_waits_for_the_page_then_runs_at_once():
    ran: list[str] = []
    queue = ScriptQueue(ran.append)

    queue.push("a")
    queue.push("b")
    assert ran == []

    queue.page_loaded(True)
    assert ran == ["a", "b"]
    queue.push("c")
    assert ran == ["a", "b", "c"]


def test_script_queue_survives_rejected_link_navigation():
    """Qt сообщает loadFinished(False) на каждую отклонённую ссылку (переход
    в дашборд из карточки станции) — карта после этого должна обновляться."""
    ran: list[str] = []
    queue = ScriptQueue(ran.append)
    queue.page_loaded(True)

    queue.page_loaded(False)
    queue.push("after link")

    assert queue.ready
    assert ran == ["after link"]


def test_script_queue_keeps_calls_if_first_load_failed():
    ran: list[str] = []
    queue = ScriptQueue(ran.append)
    queue.push("a")

    queue.page_loaded(False)
    assert ran == [] and not queue.ready
    queue.page_loaded(True)
    assert ran == ["a"]
