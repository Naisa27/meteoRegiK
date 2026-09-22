from __future__ import annotations

from datetime import date

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, stations
from meteoregik.db.session import get_engine
from meteoregik.reports.aggregate_query import (
    DETAILED_PERIOD_TYPES,
    MONTHLY_PERIOD_TYPES,
    YEARLY_PERIOD_TYPES,
    fetch_rows,
    group_detailed,
    group_monthly,
    group_yearly,
    station_period_bounds,
)

WMO_INDEX = 27704
PARAMETERS = [("T", "avg"), ("T", "min"), ("T", "max")]


def _make_engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Мосальск", wmo_index=WMO_INDEX))
    return engine


def _insert(engine, rows):
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), rows)


def _row(period_type, start, end, stat_type, value, parameter="T"):
    return {
        "wmo_index": WMO_INDEX,
        "period_type": period_type,
        "period_start": start,
        "period_end": end,
        "parameter": parameter,
        "stat_type": stat_type,
        "value": value,
    }


def test_station_period_bounds_none_when_no_data(tmp_path):
    engine = _make_engine(tmp_path)
    assert station_period_bounds(engine, WMO_INDEX) is None


def test_station_period_bounds_min_max(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("day", date(2020, 1, 1), date(2020, 1, 1), "avg", 1.0),
            _row("year", date(2021, 1, 1), date(2021, 12, 31), "avg", 2.0),
        ],
    )
    assert station_period_bounds(engine, WMO_INDEX) == (date(2020, 1, 1), date(2021, 12, 31))


def test_group_detailed_builds_nested_year_month_decade_day(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("year", date(2020, 1, 1), date(2020, 12, 31), "avg", 5.0),
            _row("month", date(2020, 1, 1), date(2020, 1, 31), "avg", -6.0),
            _row("decade", date(2020, 1, 1), date(2020, 1, 10), "avg", -8.0),
            _row("day", date(2020, 1, 1), date(2020, 1, 1), "avg", -8.1),
            _row("day", date(2020, 1, 2), date(2020, 1, 2), "avg", -7.9),
        ],
    )
    rows = fetch_rows(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 31), PARAMETERS, DETAILED_PERIOD_TYPES)
    years = group_detailed(rows)

    assert [y.year for y in years] == [2020]
    year_slice = years[0]
    assert year_slice.year_values[("T", "avg")] == 5.0
    month = year_slice.months[1]
    assert month.month_values[("T", "avg")] == -6.0
    assert month.decade_values[1][("T", "avg")] == -8.0
    assert month.day_values[1][("T", "avg")] == -8.1
    assert month.day_values[2][("T", "avg")] == -7.9
    # только реально встретившиеся месяцы/декады/дни — остальные ключи отсутствуют
    assert 2 not in year_slice.months
    assert 2 not in month.decade_values


def test_group_detailed_skips_months_outside_requested_range(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("month", date(2020, 1, 1), date(2020, 1, 31), "avg", 1.0),
            _row("month", date(2020, 2, 1), date(2020, 2, 29), "avg", 2.0),
        ],
    )
    rows = fetch_rows(engine, WMO_INDEX, date(2020, 1, 15), date(2020, 1, 20), PARAMETERS, MONTHLY_PERIOD_TYPES)
    years = group_monthly(rows)
    assert years[0].months.keys() == {1}


def test_group_detailed_handles_february_without_29_31(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("day", date(2021, 2, 27), date(2021, 2, 27), "avg", 1.0),
            _row("day", date(2021, 2, 28), date(2021, 2, 28), "avg", 2.0),
        ],
    )
    rows = fetch_rows(engine, WMO_INDEX, date(2021, 2, 1), date(2021, 2, 28), PARAMETERS, DETAILED_PERIOD_TYPES)
    years = group_detailed(rows)
    month = years[0].months[2]
    assert set(month.day_values) == {27, 28}
    assert 29 not in month.day_values


def test_group_monthly_builds_year_and_month_values_only(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("year", date(2020, 1, 1), date(2020, 12, 31), "avg", 6.83),
            _row("month", date(2020, 1, 1), date(2020, 1, 31), "avg", -6.13),
        ],
    )
    rows = fetch_rows(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 12, 31), PARAMETERS, MONTHLY_PERIOD_TYPES)
    years = group_monthly(rows)
    assert years[0].year_values[("T", "avg")] == 6.83
    assert years[0].months[1].month_values[("T", "avg")] == -6.13
    assert years[0].months[1].decade_values == {}
    assert years[0].months[1].day_values == {}


def test_group_yearly_builds_year_to_stats_mapping(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("year", date(2020, 1, 1), date(2020, 12, 31), "avg", 5.9),
            _row("year", date(2021, 1, 1), date(2021, 12, 31), "avg", 7.3),
        ],
    )
    rows = fetch_rows(engine, WMO_INDEX, date(2020, 1, 1), date(2021, 12, 31), PARAMETERS, YEARLY_PERIOD_TYPES)
    result = group_yearly(rows)
    assert result == {2020: {("T", "avg"): 5.9}, 2021: {("T", "avg"): 7.3}}


def test_fetch_rows_filters_by_parameters_and_stat_types(tmp_path):
    engine = _make_engine(tmp_path)
    _insert(
        engine,
        [
            _row("year", date(2020, 1, 1), date(2020, 12, 31), "avg", 1.0),
            _row("year", date(2020, 1, 1), date(2020, 12, 31), "min", 2.0, parameter="RRR"),
        ],
    )
    rows = fetch_rows(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 12, 31), [("T", "avg")], YEARLY_PERIOD_TYPES)
    assert len(rows) == 1
    assert rows[0].parameter == "T"
