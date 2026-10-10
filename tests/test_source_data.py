"""Данные вкладки "Исходные данные" дашборда: сводка по всей базе, пропуски
дней и сроков, полнота по месяцам, заполненность параметров, история загрузок."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.source_data import (
    DayGap,
    MonthFill,
    ObsGap,
    StationSourceRow,
    day_gaps,
    fetch_source_overview,
    fetch_station_detail,
    grid_hours,
    month_fill,
    obs_gaps,
    obs_grid_offset,
)

MOSALSK = 27707
SUKHINICHI = 27704
HOURS = grid_hours(0)


def _day_rows(wmo_index: int, days: list[date]) -> list[dict]:
    return [
        {"wmo_index": wmo_index, "period_type": "day", "period_start": day, "period_end": day,
         "parameter": "T", "stat_type": "avg", "value": 1.0}
        for day in days
    ]


def _rp5_row(wmo_index: int, day: date, hour: int, temperature: float | None = 5.0, **extra) -> dict:
    # Набор ключей одинаков у всех строк — иначе массовая вставка не соберётся
    return {"wmo_index": wmo_index, "station_name": "Мосальск", "obs_date": day, "obs_time": time(hour),
            "T_temperature": temperature, "source_file": "mosalsk.xls",
            "U_humidity": None, "RRR_precipitation": None, **extra}


@pytest.fixture
def engine(tmp_path):
    """Мосальск — rp5 за 1–3 января 2020 (полный день, день с 6 сроками из 8
    и пустой температурой в одном из них, день без единого срока с
    температурой) и расчётные дни 1, 2 и 5 января. Сухиничи — только ГГ."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    first = date(2020, 1, 1)
    rp5 = [_rp5_row(MOSALSK, first, hour, U_humidity=80.0, RRR_precipitation="") for hour in HOURS]
    second = first + timedelta(days=1)
    rp5 += [_rp5_row(MOSALSK, second, hour) for hour in (0, 3, 6, 9, 12, 21)]
    rp5.append(_rp5_row(MOSALSK, second, 15, temperature=None))  # строка есть, температуры нет
    rp5.append(_rp5_row(MOSALSK, first + timedelta(days=2), 0, temperature=None))
    gg = [
        {"wmo_index": SUKHINICHI, "station_name": "Сухиничи", "obs_date": date(1960, 5, day),
         "t_avg": 10.0, "source_file": "sukhinichi.xlsx"}
        for day in (1, 2, 3)
    ]
    with engine.begin() as conn:
        conn.execute(stations.insert(), [
            {"name": "Мосальск", "wmo_index": MOSALSK},
            {"name": "Сухиничи", "wmo_index": SUKHINICHI},
        ])
        conn.execute(rp5_readings.insert(), rp5)
        conn.execute(gg_readings.insert(), gg)
        conn.execute(computed_indicators.insert(), [
            *_day_rows(MOSALSK, [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 5)]),
            *_day_rows(SUKHINICHI, [date(1960, 5, 1), date(1960, 5, 2), date(1960, 5, 3)]),
        ])
    return engine


# --- расчёт без БД ---


def test_day_gaps_between_days_with_data():
    days = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 5), date(2020, 1, 7)]
    assert day_gaps(days) == [
        DayGap(date(2020, 1, 3), date(2020, 1, 4)),
        DayGap(date(2020, 1, 6), date(2020, 1, 6)),
    ]
    assert [gap.days for gap in day_gaps(days)] == [2, 1]
    assert day_gaps([]) == []


def test_month_fill_expects_only_days_inside_archive():
    # Архив с 30 января по 2 марта: в январе ожидаются 2 дня, в марте — тоже 2
    days = [date(2020, 1, 30), date(2020, 1, 31), date(2020, 2, 1), date(2020, 3, 2)]
    assert month_fill(days) == [
        MonthFill(2020, 1, 2, 2),
        MonthFill(2020, 2, 1, 29),
        MonthFill(2020, 3, 1, 2),
    ]
    assert month_fill(days)[0].share == 1.0


@pytest.mark.parametrize(
    ("present", "expected"),
    [({0: 100, 1: 3}, 0), ({1: 100, 0: 3}, 1), ({}, 0), ({0: 5, 2: 5}, 0)],
)
def test_obs_grid_offset_follows_the_data(present, expected):
    assert obs_grid_offset(present) == expected


def test_grid_hours_for_shifted_time_zone():
    assert grid_hours(1) == (1, 4, 7, 10, 13, 16, 19, 22)


def test_obs_gaps_join_across_midnight_but_not_across_empty_days():
    day1, day2, day5 = date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 5)
    present = [
        *((day1, hour) for hour in HOURS if hour != 21),
        *((day2, hour) for hour in HOURS if hour not in (0, 3)),
        *((day5, hour) for hour in HOURS if hour != 0),
    ]
    assert obs_gaps(present, HOURS) == [
        # 21:00 первого дня и 00:00, 03:00 второго — одна серия
        ObsGap(datetime(2020, 1, 1, 21), datetime(2020, 1, 2, 3), 3),
        # между 2 и 5 января сроков нет вовсе — это пропущенные дни, не сроки
        ObsGap(datetime(2020, 1, 5, 0), datetime(2020, 1, 5, 0), 1),
    ]


def test_station_row_averages():
    row = StationSourceRow(
        1, "Станция", computed_period=(date(2020, 1, 1), date(2020, 12, 31)),
        days_with_data=356, obs_days=10, obs_present=60,
    )
    assert row.days_total == 366
    assert row.days_missing == 10
    assert row.days_missing_per_year == pytest.approx(10 / (366 / 365.25))
    assert row.days_missing_per_month == pytest.approx(10 / (366 / (365.25 / 12)))
    assert row.obs_missing == 20
    assert row.obs_missing_per_day == 2.0


def test_station_row_without_data_has_no_averages():
    row = StationSourceRow(1, "Станция")
    assert row.formats == ()
    assert row.days_missing == 0
    assert row.days_missing_per_year is None
    assert row.obs_missing_per_day is None


# --- выборка ---


def test_overview_counts_stations_and_periods(engine):
    overview = fetch_source_overview(engine)

    assert [row.label for row in overview.stations] == ["Мосальск (27707)", "Сухиничи (27704)"]
    assert overview.stations_with("rp5") == 1
    assert overview.stations_with("ГГ") == 1
    assert overview.stations_with("rp5", "ГГ") == 0
    assert overview.computed_period == (date(1960, 5, 1), date(2020, 1, 5))
    assert overview.rp5_period == (date(2020, 1, 1), date(2020, 1, 3))
    assert overview.gg_period == (date(1960, 5, 1), date(1960, 5, 3))


def test_overview_counts_missing_days_and_observations(engine):
    mosalsk, sukhinichi = fetch_source_overview(engine).stations

    assert mosalsk.formats == ("rp5",)
    assert mosalsk.computed_period == (date(2020, 1, 1), date(2020, 1, 5))
    assert (mosalsk.days_total, mosalsk.days_missing) == (5, 2)
    # 3 января в расчёт не идёт: единственная его строка — без температуры
    assert mosalsk.obs_days == 2
    # 2 января: нет сроков 15 (пустая температура) и 18 (нет строки)
    assert mosalsk.obs_missing == 2
    assert mosalsk.obs_missing_per_day == 1.0

    assert sukhinichi.formats == ("ГГ",)
    assert sukhinichi.days_missing == 0
    assert sukhinichi.obs_missing == 0

    overview = fetch_source_overview(engine)
    assert (overview.days_missing, overview.obs_missing) == (2, 2)
    assert (overview.stations_with_day_gaps, overview.stations_with_obs_gaps) == (1, 1)


def test_overview_matches_station_detail(engine):
    row = fetch_source_overview(engine).stations[0]
    detail = fetch_station_detail(engine, MOSALSK)

    assert sum(gap.days for gap in detail.day_gaps) == row.days_missing
    assert sum(gap.count for gap in detail.obs_gaps) == row.obs_missing
    assert detail.obs_days == row.obs_days


def test_station_detail_lists_gap_periods(engine):
    detail = fetch_station_detail(engine, MOSALSK)

    assert detail.day_gaps == [DayGap(date(2020, 1, 3), date(2020, 1, 4))]
    assert detail.obs_gaps == [ObsGap(datetime(2020, 1, 2, 15), datetime(2020, 1, 2, 18), 2)]
    assert detail.months == [MonthFill(2020, 1, 3, 5)]
    assert detail.obs_hours == HOURS
    assert detail.hour_counts == {0: 2, 3: 2, 6: 2, 9: 2, 12: 2, 15: 1, 18: 1, 21: 2}


def test_station_detail_parameter_fill(engine):
    detail = fetch_station_detail(engine, MOSALSK)
    fill = {p.column: p for p in detail.parameters}

    assert detail.rp5_rows == 16
    assert (fill["T_temperature"].filled, fill["T_temperature"].total) == (14, 16)
    assert fill["U_humidity"].share == 0.5
    assert fill["RRR_precipitation"].filled == 0  # пустая строка — не значение
    assert fill["Td_dewpoint"].filled == 0


def test_station_detail_for_gg_only_station(engine):
    detail = fetch_station_detail(engine, SUKHINICHI)

    assert detail.day_gaps == [] and detail.obs_gaps == []
    assert detail.parameters == [] and detail.rp5_rows == 0
    assert detail.hour_counts == {}
    assert detail.months == [MonthFill(1960, 5, 3, 3)]


def test_observation_grid_shifts_with_station_time_zone(tmp_path):
    """Станция в поясе UTC+4: сроки 1, 4, …, 22 — это полные сутки, а не
    восемь пропусков относительно 0, 3, …, 21."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    day = date(2020, 1, 1)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert(), [_rp5_row(1, day, hour) for hour in grid_hours(1) if hour != 22])
        conn.execute(rp5_readings.insert(), [{**_rp5_row(1, day, 13), "obs_time": time(13, 30)}])

    (row,) = fetch_source_overview(engine).stations
    detail = fetch_station_detail(engine, 1)

    assert (row.obs_days, row.obs_missing) == (1, 1)
    assert detail.obs_hours == grid_hours(1)
    assert detail.obs_gaps == [ObsGap(datetime(2020, 1, 1, 22), datetime(2020, 1, 1, 22), 1)]
    assert row.label == "Мосальск (1)"  # станции нет в справочнике — имя из самих данных


def test_loaded_files_history(engine):
    files = {f.source_file: f for f in fetch_source_overview(engine).files}

    rp5_file = files["mosalsk.xls"]
    assert (rp5_file.format, rp5_file.rows, rp5_file.station_label) == ("rp5", 16, "Мосальск (27707)")
    assert rp5_file.period == (date(2020, 1, 1), date(2020, 1, 3))
    assert rp5_file.loaded_at is not None
    assert abs(rp5_file.loaded_at - datetime.now()) < timedelta(minutes=5)  # местное время, не UTC

    gg_file = files["sukhinichi.xlsx"]
    assert (gg_file.format, gg_file.rows) == ("ГГ", 3)


def test_overview_of_empty_database(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    overview = fetch_source_overview(engine)

    assert not overview.has_data
    assert overview.computed_period is None
    assert overview.days_missing == 0
