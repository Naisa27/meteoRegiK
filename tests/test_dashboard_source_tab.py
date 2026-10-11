"""Вкладка "Исходные данные" дашборда: тексты карточек и подписей, таблицы
станций/файлов/пропусков, подробности по выбранной станции, графики."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.source_data import (
    DayGap,
    MonthFill,
    ObsGap,
    ParameterFill,
    SourceOverview,
    StationSourceDetail,
    StationSourceRow,
)
from meteoregik.ui.dashboard_source_charts import (
    format_share,
    month_fill_tooltip,
    obs_hour_tooltip,
    parameter_tooltip,
)
from meteoregik.ui.dashboard_source_tab import (
    GROUP_DAYS,
    GROUP_OBS,
    GroupedHeader,
    SourceDataTab,
    day_gaps_note,
    format_period,
    obs_gaps_note,
    plural,
    source_card_texts,
)
from meteoregik.ui.dashboard_widgets import note_html
from meteoregik.ui.dashboard_view import TAB_SOURCE, TAB_TEMPERATURE, DashboardDialog

MOSALSK = 27707
SUKHINICHI = 27704
HOURS = (0, 3, 6, 9, 12, 15, 18, 21)


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(tmp_path):
    """Мосальск — rp5: 1 января полный день, 2 января без сроков 15 и 18;
    расчётные дни 1, 2 и 5 января (3–4 пропущены). Сухиничи — только ГГ,
    три дня без пропусков."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    first = date(2020, 1, 1)
    rp5 = [
        {"wmo_index": MOSALSK, "station_name": "Мосальск", "obs_date": day, "obs_time": time(hour),
         "T_temperature": 5.0, "source_file": "mosalsk.xls"}
        for day, hours in ((first, HOURS), (first + timedelta(days=1), (0, 3, 6, 9, 12, 21)))
        for hour in hours
    ]
    gg_days = [date(1960, 5, day) for day in (1, 2, 3)]

    def day_rows(wmo_index: int, days: list[date]) -> list[dict]:
        return [
            {"wmo_index": wmo_index, "period_type": "day", "period_start": day, "period_end": day,
             "parameter": "T", "stat_type": "avg", "value": 1.0}
            for day in days
        ]

    with engine.begin() as conn:
        conn.execute(stations.insert(), [
            {"name": "Мосальск", "wmo_index": MOSALSK},
            {"name": "Сухиничи", "wmo_index": SUKHINICHI},
        ])
        conn.execute(rp5_readings.insert(), rp5)
        conn.execute(gg_readings.insert(), [
            {"wmo_index": SUKHINICHI, "station_name": "Сухиничи", "obs_date": day, "t_avg": 10.0,
             "source_file": "sukhinichi.xlsx"}
            for day in gg_days
        ])
        conn.execute(computed_indicators.insert(), [
            *day_rows(MOSALSK, [first, date(2020, 1, 2), date(2020, 1, 5)]),
            *day_rows(SUKHINICHI, gg_days),
        ])
    return engine


def _texts(table) -> list[list[str]]:
    model = table.model()
    return [
        [model.index(row, col).data() for col in range(model.columnCount())]
        for row in range(model.rowCount())
    ]


# --- тексты ---


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "день"), (2, "дня"), (5, "дней"), (11, "дней"), (21, "день"), (112, "дней"), (0, "дней")],
)
def test_plural(count, expected):
    assert plural(count, "день", "дня", "дней") == expected


def test_format_period():
    assert format_period((date(2005, 2, 1), date(2025, 10, 14))) == "01.02.2005–14.10.2025"
    assert format_period(None) == "—"


@pytest.mark.parametrize(
    ("share", "expected"),
    [(1.0, "100 %"), (0.0, "0 %"), (0.5, "50 %"), (30 / 31, "97 %"), (0.998, "99.8 %"), (0.9999, "99.9 %"),
     (0.001, "< 1 %")],
)
def test_format_share_never_rounds_incomplete_to_full(share, expected):
    assert format_share(share) == expected


def test_source_card_texts():
    overview = SourceOverview([
        StationSourceRow(1, "А", rp5_period=(date(2005, 2, 1), date(2025, 10, 14)),
                         computed_period=(date(2005, 2, 1), date(2005, 2, 10)), days_with_data=8,
                         obs_days=8, obs_present=61),
        StationSourceRow(2, "Б", rp5_period=(date(2014, 12, 1), date(2019, 12, 31)),
                         gg_period=(date(1959, 7, 1), date(2025, 2, 28)),
                         computed_period=(date(1959, 7, 1), date(1959, 7, 3)), days_with_data=3),
    ])
    stations_card, periods_card, gaps_card = source_card_texts(overview)

    assert stations_card.value == "2"
    assert stations_card.note == "rp5 — 2, ГГ — 1, в обоих форматах — 1"
    assert periods_card.value == "01.07.1959–10.02.2005"
    assert periods_card.note == "rp5: 01.02.2005–14.10.2025\nГГ: 01.07.1959–28.02.2025"
    assert gaps_card.value == "2 дня, 3 срока"
    assert gaps_card.note == "Пропущенные дни — у 1 станции, сроки — у 1 станции"
    assert all(card.help for card in (stations_card, periods_card, gaps_card))


def test_gap_notes():
    days = [DayGap(date(2020, 1, 3), date(2020, 1, 4)), DayGap(date(2020, 2, 1), date(2020, 2, 1))]
    assert day_gaps_note(days) == "2 периода, всего 3 дня. Самый длинный — 2 дня (с 03.01.2020)."
    assert day_gaps_note([]) == "Пропущенных дней нет."

    obs = [ObsGap(datetime(2020, 1, 2, 15), datetime(2020, 1, 2, 18), 2)]
    detail = StationSourceDetail(1, obs_gaps=obs, obs_days=2)
    assert obs_gaps_note(obs, detail) == "1 период, всего 2 срока. Самый длинный — 2 срока (с 02.01.2020 15:00)."
    assert obs_gaps_note([], StationSourceDetail(1, obs_days=2)) == "Пропущенных сроков нет."
    assert "rp5 по станции нет" in obs_gaps_note([], StationSourceDetail(1))


def test_chart_tooltips():
    assert month_fill_tooltip(2014, 3, MonthFill(2014, 3, 28, 31)) == "Март 2014: данные за 28 из 31 дн. (90 %)"
    assert month_fill_tooltip(2014, 3, None) == "Март 2014: вне архива станции"
    # тысячи — через неразрывный пробел (format_count)
    assert obs_hour_tooltip(3, 7300, 7340, True) == (
        "Срок 03:00: температура есть в 7\xa0300 из 7\xa0340 дн. (99 %)"
    )
    assert obs_hour_tooltip(13, 2, 7340, False) == "13:00 — вне сетки сроков: 2 набл."
    assert parameter_tooltip(ParameterFill("U_humidity", 50, 200)) == (
        "U — Относительная влажность, %\nЗаполнено 50 из 200 строк (25 %)"
    )


# --- вкладка ---


def test_tab_loads_only_when_asked(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    assert tab.stations_model.rows == []

    tab.ensure_loaded()

    assert tab._stack.currentWidget() is tab._content
    assert [card.value.text() for card in tab.cards] == ["2", "01.05.1960–05.01.2020", "2 дня, 2 срока"]
    assert not tab.loading_overlay.isVisible()


def test_stations_table_rows(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    assert _texts(tab.stations_table) == [
        ["Мосальск (27707)", "rp5", "01.01.2020–02.01.2020", "—", "01.01.2020–05.01.2020",
         "2", "146.1 / 12.2", "2", "1.0"],
        ["Сухиничи (27704)", "ГГ", "—", "01.05.1960–03.05.1960", "01.05.1960–03.05.1960",
         "0", "0.0 / 0.0", "—", "—"],
    ]


def test_stations_table_sorts_by_number_not_text(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    tab.stations_table.sortByColumn(5, Qt.SortOrder.AscendingOrder)  # пропущено дней
    assert [row[0] for row in _texts(tab.stations_table)] == ["Сухиничи (27704)", "Мосальск (27707)"]
    tab.stations_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # период rp5: "—" в конце
    assert [row[0] for row in _texts(tab.stations_table)] == ["Мосальск (27707)", "Сухиничи (27704)"]


def test_first_station_details_shown_after_load(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    assert tab._station_label.text() == "Мосальск (27707)"
    assert _texts(tab.day_gaps_table) == [["03.01.2020", "04.01.2020", "2"]]
    assert _texts(tab.obs_gaps_table) == [["02.01.2020 15:00", "02.01.2020 18:00", "2"]]
    assert tab._gaps_note.text() == "1 период, всего 2 дня. Самый длинный — 2 дня (с 03.01.2020)."
    assert tab.fill_calendar._years == [2020]


def test_selecting_station_switches_details(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    tab.stations_table.selectRow(1)

    assert tab._station_label.text() == "Сухиничи (27704)"
    assert _texts(tab.day_gaps_table) == []
    assert tab._gaps_note.text() == "Пропущенных дней нет."
    tab.gaps_mode.button(1).setChecked(True)
    assert tab._gap_tables.currentWidget() is tab.obs_gaps_table
    assert "rp5 по станции нет" in tab._gaps_note.text()


def test_chart_switch_plots_selected_chart(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()
    assert tab.hours_chart._hours == []  # невидимый график не строится заранее

    tab.chart_mode.button(1).setChecked(True)
    assert tab._charts.currentWidget() is tab.hours_chart
    assert tab.hours_chart._hours == list(HOURS)
    assert tab._chart_title.text() == "Наблюдения по срокам"

    tab.chart_mode.button(2).setChecked(True)
    assert tab._charts.currentWidget() is tab.parameters_chart
    assert tab.parameters_chart._parameters[0].column == "T_temperature"

    tab.stations_table.selectRow(1)  # у станции только ГГ — параметров rp5 нет
    assert tab.parameters_chart._parameters == []


def test_files_view_lists_loaded_files_and_selects_station(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    tab.table_mode.button(1).setChecked(True)
    assert tab._tables.currentWidget() is tab.files_table
    rows = {row[0]: row for row in _texts(tab.files_table)}
    assert rows["mosalsk.xls"][1:5] == ["rp5", "Мосальск (27707)", "14", "01.01.2020–02.01.2020"]
    assert rows["sukhinichi.xlsx"][1:5] == ["ГГ", "Сухиничи (27704)", "3", "01.05.1960–03.05.1960"]

    row = next(i for i, texts in enumerate(_texts(tab.files_table)) if texts[0] == "sukhinichi.xlsx")
    tab.files_table.selectRow(row)
    assert tab._station_label.text() == "Сухиничи (27704)"


def test_refresh_picks_up_new_data_and_keeps_station(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()
    tab.stations_table.selectRow(1)

    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(
            wmo_index=SUKHINICHI, station_name="Сухиничи", obs_date=date(1960, 5, 4), source_file="more.xlsx"
        ))
    tab.refresh_btn.click()

    assert tab.files_model.rowCount() == 3
    assert tab._station_label.text() == "Сухиничи (27704)"
    assert _texts(tab.stations_table)[1][3] == "01.05.1960–04.05.1960"


def test_empty_database_shows_hint(tmp_path):
    _qapp()
    engine = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(engine)
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    assert tab._stack.currentWidget() is tab._hint
    assert "Импорт данных" in tab._hint.text()


# --- диалог ---


def test_dashboard_has_two_tabs_and_loads_source_tab_on_open(engine):
    _qapp()
    dialog = DashboardDialog(engine, run_async=False)

    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [TAB_TEMPERATURE, TAB_SOURCE]
    assert dialog.tabs.currentIndex() == 0
    assert dialog.source_tab.stations_model.rows == []  # вторая вкладка ещё не открывалась

    dialog.tabs.setCurrentWidget(dialog.source_tab)

    assert len(dialog.source_tab.stations_model.rows) == 2


def test_tabs_scroll_instead_of_growing_the_window(engine):
    """В небольшом окне содержимое вкладок прокручивается, а не раздвигает
    диалог за край экрана вместе с кнопкой "Закрыть"."""
    _qapp()
    dialog = DashboardDialog(engine, run_async=False)
    dialog.tabs.setCurrentWidget(dialog.source_tab)

    assert dialog.minimumSizeHint().height() < 450
    # Прокручивается страница результатов; подсказка — вне области прокрутки,
    # иначе она встаёт по центру высокой страницы и уезжает за край окна
    for tab in (dialog, dialog.source_tab):
        assert isinstance(tab._content, QScrollArea) and tab._content.widgetResizable()
        assert not isinstance(tab._hint.parentWidget().parentWidget(), QScrollArea)


def test_mouse_wheel_over_chart_is_left_to_the_scroll_area(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()
    point = QPointF(10, 10)
    wheel = QWheelEvent(point, point, QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)

    QApplication.sendEvent(tab.fill_calendar.canvas, wheel)

    assert not wheel.isAccepted()


def test_multiline_card_note_gets_wider_line_spacing():
    assert note_html("Одна строка") == "Одна строка"
    html = note_html("rp5: <a>\nГГ: b")
    assert html.count("line-height: 130%") == 2
    assert "rp5: &lt;a&gt;" in html  # текст примечания не разбирается как разметка


def test_periods_card_shows_both_formats_on_separate_lines(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()

    note = tab.cards[1].note.text()
    assert "rp5: 01.01.2020–02.01.2020</p>" in note
    assert "ГГ: 01.05.1960–03.05.1960</p>" in note


def test_stations_table_header_has_two_levels(engine):
    _qapp()
    tab = SourceDataTab(engine, run_async=False)
    tab.ensure_loaded()
    header = tab.stations_table.horizontalHeader()
    model = tab.stations_table.model()
    titles = [model.headerData(col, Qt.Orientation.Horizontal) for col in range(model.columnCount())]

    assert isinstance(header, GroupedHeader)
    assert titles[5:] == ["всего", "среднее\nв год/в месяц", "всего", "среднее\nв день"]
    assert [header._groups[col] for col in range(5, 9)] == [GROUP_DAYS, GROUP_DAYS, GROUP_OBS, GROUP_OBS]
    assert header.group_span(5) == header.group_span(6) == (5, 6)
    assert header.group_span(7) == header.group_span(8) == (7, 8)
    assert header.group_span(0) is None
    # Верхний уровень + две строки нижнего выше обычного заголовка
    assert header.sizeHint().height() > tab.files_table.horizontalHeader().sizeHint().height()
    assert header.isSortIndicatorShown() and header.sectionsClickable()
