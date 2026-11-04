from __future__ import annotations

from datetime import date, time, timedelta
from time import perf_counter

import pytest

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, rp5_readings
from meteoregik.db.session import get_engine
from meteoregik.reports.aggregate_query import (
    DETAILED_PERIOD_TYPES, fetch_rows, group_detailed,
)
from meteoregik.reports.grid import build_detailed_grid
from meteoregik.reports.chart_data import chart_data_from_detailed, chart_data_from_monthly, chart_data_from_yearly
from meteoregik.reports.parameters import decimals_for, is_bar, label_for
from meteoregik.reports.snow_data import build_snow, format_maximum_dates

WMO = 27704


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "snow.sqlite3")
    run_migrations(engine)
    yield engine
    engine.dispose()


def _archive(engine, first: date, last: date) -> None:
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert(), [
            {"wmo_index": WMO, "obs_date": day, "obs_time": time(9)}
            for day in sorted({first, last})
        ])


def _value(engine, day: date, parameter: str, value: float,
           period_type: str = "day", end: date | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert().values(
            wmo_index=WMO, period_type=period_type, period_start=day,
            period_end=end or day, parameter=parameter,
            stat_type="max" if parameter == "SNOW_DEPTH" else "avg" if parameter == "T" else "sum",
            value=value,
        ))


def test_no_rp5_or_period_outside_archive_has_no_snow_data(engine) -> None:
    _value(engine, date(2025, 1, 1), "T", -25)
    assert not build_snow(engine, WMO, date(2025, 1, 1), date(2025, 12, 31)).has_data
    _archive(engine, date(2025, 2, 1), date(2025, 2, 3))
    assert not build_snow(engine, WMO, date(2024, 1, 1), date(2024, 12, 31)).has_data


def test_months_seasons_and_missing_temperature_are_explicit(engine) -> None:
    _archive(engine, date(2025, 6, 30), date(2025, 7, 2))
    for day, height, frost in [(date(2025, 6, 30), 5.0, 1),
                               (date(2025, 7, 1), 5.0, 0),
                               (date(2025, 7, 2), 0.0, None)]:
        _value(engine, day, "S", int(height > 0))
        _value(engine, day, "SNOW_DEPTH", height)
        if frost is not None:
            _value(engine, day, "FROST_LOW_SNOW", frost)
            _value(engine, day, "T", -20 if frost else 0)
    data = build_snow(engine, WMO, date(2025, 1, 1), date(2025, 12, 31))
    assert data.has_data and data.total is not None
    assert data.total.snow_days == 2
    assert data.total.max_depth == 5
    assert data.total.frost_days == 1
    assert data.total.temperature_days == 2
    assert data.total.available_days == 3 and data.total.partial
    assert [period.start for period in data.seasons] == [date(2024, 7, 1), date(2025, 7, 1)]
    assert len(data.months) == 2 and all(period.partial for period in data.months)
    clipped = build_snow(engine, WMO, date(2025, 7, 2), date(2025, 7, 2))
    assert clipped.total is not None
    assert clipped.total.frost_days is None
    assert clipped.total.maximum_dates == ()


@pytest.mark.parametrize(("days", "expected"), [
    ((), "—"),
    ((date(2026, 2, 12), date(2026, 2, 13), date(2026, 2, 15)),
     "12–13.02.2026, 15.02.2026"),
    ((date(2025, 12, 31), date(2026, 1, 1)), "31.12.2025–01.01.2026"),
])
def test_maximum_dates_merge_only_consecutive_days(days, expected) -> None:
    assert format_maximum_dates(days) == expected


def test_registry_and_daily_presence_in_shared_grid(engine) -> None:
    assert label_for(("S", "sum")) == "дни со снежным покровом"
    assert decimals_for(("S", "sum")) == 0
    assert decimals_for(("FROST_LOW_SNOW", "sum")) == 0
    assert is_bar(("S", "sum"))
    _archive(engine, date(2025, 1, 2), date(2025, 1, 3))
    _value(engine, date(2025, 1, 2), "S", 1)
    _value(engine, date(2025, 1, 3), "S", 0)
    _value(engine, date(2025, 1, 1), "S", 1, "month", date(2025, 1, 31))
    _value(engine, date(2025, 1, 1), "FROST_LOW_SNOW", 1, "month", date(2025, 1, 31))
    _value(engine, date(2025, 1, 2), "T", -20)
    _value(engine, date(2025, 1, 2), "FROST_LOW_SNOW", 1)
    rows = fetch_rows(engine, WMO, date(2025, 1, 1), date(2025, 1, 31),
                      [("S", "sum"), ("FROST_LOW_SNOW", "sum")], DETAILED_PERIOD_TYPES)
    years = group_detailed(rows)
    assert "Доступная часть периода" in years[0].months[1].month_values.notes[("S", "sum")]
    assert "1 из 2 дней" in years[0].months[1].month_values.notes[("FROST_LOW_SNOW", "sum")]
    grid = build_detailed_grid(years, [("S", "sum")])
    january = next(row for row in grid.rows if row.cells[0].value == "январь")
    assert january.cells[2].value == "есть"
    assert january.cells[3].value == "нет"
    assert january.cells[-1].value == 1
    assert "Доступная часть периода" in january.cells[-1].note
    monthly_chart = chart_data_from_monthly(years, [("S", "sum")])
    assert "Доступная часть периода" in monthly_chart.notes[("S", "sum")][0]
    assert monthly_chart.x_labels == ["01.2025"]
    detailed_chart = chart_data_from_detailed(years, [("S", "sum")])
    assert len(detailed_chart.notes[("S", "sum")]) == len(detailed_chart.x_labels)
    year_stats = years[0].months[1].month_values
    yearly_chart = chart_data_from_yearly({2025: year_stats}, [2025, 2026], [("S", "sum")])
    assert "Доступная часть периода" in yearly_chart.notes[("S", "sum")][0]
    assert yearly_chart.notes[("S", "sum")][1] == ""


@pytest.mark.parametrize("cached", [(), ("S",), ("SNOW_DEPTH",), ("S", "SNOW_DEPTH")])
def test_missing_or_partial_daily_cache_does_not_turn_into_zero(engine, cached) -> None:
    first, last = date(2025, 1, 1), date(2025, 1, 2)
    _archive(engine, first, last)
    for parameter in cached:
        _value(engine, first, parameter, 0)
    with pytest.raises(RuntimeError, match="Повторно импортируйте"):
        build_snow(engine, WMO, first, last)


def test_long_archive_reports_and_frost_notes_have_linear_cost(engine) -> None:
    """Тридцать лет суточных строк ловят повторный полный обход на каждую дату."""
    first, last = date(1991, 1, 1), date(2020, 12, 31)
    _archive(engine, first, last)
    count = (last - first).days + 1
    rows = []
    for index in range(count):
        day = first + timedelta(days=index)
        for parameter, stat, value in (("S", "sum", 1), ("SNOW_DEPTH", "max", 5),
                                       ("FROST_LOW_SNOW", "sum", 1), ("T", "avg", -20)):
            rows.append(dict(wmo_index=WMO, period_type="day", period_start=day,
                             period_end=day, parameter=parameter, stat_type=stat, value=value))
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), rows)
    started = perf_counter()
    data = build_snow(engine, WMO, first, last)
    frost_rows = fetch_rows(engine, WMO, first, last, [("FROST_LOW_SNOW", "sum")], ("day",))
    elapsed = perf_counter() - started
    assert data.total is not None and data.total.snow_days == count
    assert data.total.frost_days == count
    assert len(data.months) == 360 and len(frost_rows) == count
    assert all("не оценены" not in row.note for row in frost_rows)
    assert elapsed < 5, f"Сводка 30-летнего архива заняла {elapsed:.2f} с"


def test_frost_aggregate_rejects_missing_daily_cache_for_known_temperature(engine) -> None:
    first, last = date(2025, 1, 1), date(2025, 1, 2)
    _archive(engine, first, last)
    _value(engine, first, "T", -20)
    _value(engine, first, "FROST_LOW_SNOW", 0, "month", date(2025, 1, 31))
    with pytest.raises(RuntimeError, match="Расчёт сильных морозов"):
        fetch_rows(engine, WMO, first, last, [("FROST_LOW_SNOW", "sum")], ("month",))


@pytest.mark.parametrize("cached_frost_days", [0, 1])
def test_dashboard_rejects_missing_or_partial_frost_cache(engine, cached_frost_days) -> None:
    first, last = date(2025, 1, 1), date(2025, 1, 2)
    _archive(engine, first, last)
    for index, day in enumerate((first, last)):
        _value(engine, day, "S", 0)
        _value(engine, day, "SNOW_DEPTH", 0)
        _value(engine, day, "T", -20)
        if index < cached_frost_days:
            _value(engine, day, "FROST_LOW_SNOW", 1)
    with pytest.raises(RuntimeError, match="Повторно импортируйте"):
        build_snow(engine, WMO, first, last)
