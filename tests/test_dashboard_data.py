"""Данные дашборда: правило полноты месяца "3/5", нормы по полной истории,
отклонение за период по дням, рекорды только по полным месяцам/годам внутри
периода, тренд аномалий, выборка из computed_indicators."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import pytest

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators
from meteoregik.db.session import get_engine
from meteoregik.reports.dashboard_data import (
    DayTemps,
    build_dashboard,
    compute_dashboard,
    fetch_station_temperatures,
    linear_trend,
    month_is_complete,
    station_day_bounds,
)

WMO_INDEX = 27704


def _full_year(year: int, avg: float, *, skip: set[date] = frozenset()) -> dict[date, DayTemps]:
    """Год с одинаковой суточной средней avg и min/max = avg ∓ 5."""
    days = {}
    day = date(year, 1, 1)
    while day.year == year:
        if day not in skip:
            days[day] = DayTemps(avg, avg - 5, avg + 5)
        day += timedelta(days=1)
    return days


def _aggregates(days: dict[date, DayTemps]) -> tuple[dict[tuple[int, int], float], dict[int, float]]:
    """Месячные/годовые средние так же, как их считает
    calculations/temperature.py — среднее суточных средних за доступные дни."""
    by_month: dict[tuple[int, int], list[float]] = {}
    by_year: dict[int, list[float]] = {}
    for day, temps in days.items():
        by_month.setdefault((day.year, day.month), []).append(temps.avg)
        by_year.setdefault(day.year, []).append(temps.avg)
    return (
        {key: sum(v) / len(v) for key, v in by_month.items()},
        {key: sum(v) / len(v) for key, v in by_year.items()},
    )


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        (set(), True),
        ({1, 2, 3}, True),  # 3 подряд — ещё можно
        ({1, 2, 3, 4}, False),  # 4 подряд — нет
        ({1, 3, 5, 7, 9}, True),  # 5 всего, не подряд
        ({1, 3, 5, 7, 9, 11}, False),  # 6 всего
        ({29, 30, 31}, True),
    ],
)
def test_month_is_complete_follows_3_5_rule(missing, expected):
    present = set(range(1, 32)) - missing
    assert month_is_complete(present, 31) is expected


def test_linear_trend_needs_enough_years_and_finds_slope():
    assert linear_trend([(2000 + i, float(i)) for i in range(4)]) is None
    trend = linear_trend([(2000 + i, 0.1 * i) for i in range(10)])
    assert trend is not None
    assert trend.per_decade == pytest.approx(1.0)
    assert trend.at(2000) == pytest.approx(0.0)


def test_norm_anomalies_and_records_use_complete_years():
    days = {}
    for year, avg in ((2018, 4.0), (2019, 6.0), (2020, 8.0)):
        days |= _full_year(year, avg)
    # 2021 — только январь: неполный год, в норму и аномалии не попадает
    days |= {date(2021, 1, d): DayTemps(-20.0, -25.0, -15.0) for d in range(1, 32)}
    months, years = _aggregates(days)

    data = compute_dashboard(days, months, years, date(2018, 1, 1), date(2021, 12, 31))

    assert data.annual_norm is not None
    assert data.annual_norm.value == pytest.approx(6.0)
    assert (data.annual_norm.first_year, data.annual_norm.last_year, data.annual_norm.year_count) == (2018, 2020, 3)
    assert [(y.year, y.anomaly) for y in data.year_anomalies] == [(2018, -2.0), (2019, 0.0), (2020, 2.0)]
    assert data.warmest_year.year == 2020
    assert data.coldest_year.year == 2018  # не 2021 с его одним январём
    assert data.trend is None  # 3 года — меньше MIN_TREND_YEARS
    # январь 2021 полный — он и есть самый холодный месяц, с отклонением от нормы января (6.0)
    assert (data.coldest_month.year, data.coldest_month.month) == (2021, 1)
    assert data.coldest_month.anomaly == pytest.approx(-20.0 - (4 + 6 + 8 + -20) / 4)


def test_period_anomaly_is_against_calendar_month_norm():
    """Период из одного месяца сравнивается с нормой этого месяца, а не с
    годовой."""
    days = _full_year(2019, 0.0) | _full_year(2020, 2.0)
    for d in range(1, 32):
        days[date(2020, 7, d)] = DayTemps(21.0, 16.0, 26.0)
        days[date(2019, 7, d)] = DayTemps(19.0, 14.0, 24.0)
    months, years = _aggregates(days)

    data = compute_dashboard(days, months, years, date(2020, 7, 1), date(2020, 7, 31))

    assert data.period_mean == pytest.approx(21.0)
    assert data.period_anomaly == pytest.approx(1.0)  # норма июля = 20.0
    assert data.days_total == data.days_with_data == 31
    assert data.year_anomalies == []  # ни один год не лежит в периоде целиком
    assert list(data.month_cells) == [(2020, 7)]


def test_extremes_take_earliest_date_among_equal_values():
    days = _full_year(2020, 5.0)
    days[date(2020, 3, 1)] = DayTemps(5.0, -30.0, 10.0)
    days[date(2020, 7, 10)] = DayTemps(25.0, 15.0, 35.0)
    days[date(2020, 8, 2)] = DayTemps(25.0, 15.0, 35.0)
    months, years = _aggregates(days)

    data = compute_dashboard(days, months, years, date(2020, 1, 1), date(2020, 12, 31))

    assert (data.abs_max.value, data.abs_max.day) == (35.0, date(2020, 7, 10))
    assert (data.abs_min.value, data.abs_min.day) == (-30.0, date(2020, 3, 1))


def test_incomplete_month_is_shown_but_marked_and_not_a_record():
    gap = {date(2020, 2, d) for d in range(10, 20)}  # 10 дней пропуска
    days = _full_year(2020, 5.0, skip=gap)
    days |= {date(2020, 2, d): DayTemps(-40.0, -45.0, -35.0) for d in (1, 2)}
    months, years = _aggregates(days)

    data = compute_dashboard(days, months, years, date(2020, 1, 1), date(2020, 12, 31))

    february = data.month_cells[(2020, 2)]
    assert not february.complete
    assert (february.days_with_data, february.days_in_month) == (monthrange(2020, 2)[1] - 10, 29)
    assert data.coldest_month.month != 2
    assert data.year_anomalies == []  # год с неполным февралём — неполный


def test_empty_period():
    data = compute_dashboard({}, {}, {}, date(2020, 1, 1), date(2020, 12, 31))
    assert not data.has_data
    assert data.period_mean is None and data.abs_max is None and data.trend is None


def test_build_dashboard_reads_computed_indicators(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    rows = []
    for day in (date(2020, 1, 1), date(2020, 1, 2)):
        for stat, value in (("avg", 1.0), ("min", -3.0), ("max", 4.0)):
            rows.append(
                {"wmo_index": WMO_INDEX, "period_type": "day", "period_start": day, "period_end": day,
                 "parameter": "T", "stat_type": stat, "value": value}
            )
    rows.append(
        {"wmo_index": WMO_INDEX, "period_type": "month", "period_start": date(2020, 1, 1),
         "period_end": date(2020, 1, 31), "parameter": "T", "stat_type": "avg", "value": 1.0}
    )
    rows.append(  # другая станция — не попадает
        {"wmo_index": 1, "period_type": "day", "period_start": date(2020, 1, 3), "period_end": date(2020, 1, 3),
         "parameter": "T", "stat_type": "avg", "value": 9.0}
    )
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), rows)

    days, months, years = fetch_station_temperatures(engine, WMO_INDEX)
    assert days[date(2020, 1, 1)] == DayTemps(1.0, -3.0, 4.0)
    assert date(2020, 1, 3) not in days
    assert months == {(2020, 1): 1.0} and years == {}

    data = build_dashboard(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 31))
    assert data.days_with_data == 2
    assert not data.month_cells[(2020, 1)].complete


def test_station_day_bounds_ignore_year_rows(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    assert station_day_bounds(engine, WMO_INDEX) is None

    def row(period_type, start, end):
        return {"wmo_index": WMO_INDEX, "period_type": period_type, "period_start": start, "period_end": end,
                "parameter": "T", "stat_type": "avg", "value": 1.0}

    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), [
            row("day", date(2020, 7, 1), date(2020, 7, 1)),
            row("day", date(2021, 2, 28), date(2021, 2, 28)),
            row("year", date(2020, 1, 1), date(2020, 12, 31)),
            row("year", date(2021, 1, 1), date(2021, 12, 31)),
        ])
    assert station_day_bounds(engine, WMO_INDEX) == (date(2020, 7, 1), date(2021, 2, 28))
