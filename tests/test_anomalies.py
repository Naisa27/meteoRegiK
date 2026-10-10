"""Сохранённый расчёт отклонений от нормы: что записывается, когда станция
помечается требующей расчёта и когда досчитывается."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import func, inspect, select, text

from meteoregik.calculations.anomalies import (
    RULE_VERSION,
    STAT_ANOMALY,
    calculation_status,
    compute_station_norms,
    ensure_fresh,
    fetch_station_temperatures,
    is_stale,
    load_station_norms,
    recalculate_station,
)
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import CURRENT_SCHEMA_VERSION, run_migrations
from meteoregik.db.schema import (
    anomaly_state,
    computed_indicators,
    period_completeness,
    rp5_readings,
    schema_version,
    station_norms,
)
from meteoregik.db.session import get_engine
from meteoregik.reports.dashboard_data import build_dashboard, compute_dashboard

STATION = 27707
OTHER = 27704


def _year_rows(wmo_index: int, year: int, avg: float, skip: frozenset[date] = frozenset()) -> list[dict]:
    """Год суточных значений (avg одинаковый весь год) + месячные и годовая средние."""
    rows = []

    def add(period_type: str, start: date, end: date, stat: str, value: float) -> None:
        rows.append({"wmo_index": wmo_index, "period_type": period_type, "period_start": start,
                     "period_end": end, "parameter": "T", "stat_type": stat, "value": value})

    day = date(year, 1, 1)
    while day.year == year:
        if day not in skip:
            add("day", day, day, "avg", avg)
        day += timedelta(days=1)
    for month in range(1, 13):
        start = date(year, month, 1)
        add("month", start, date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1), "avg", avg)
    add("year", date(year, 1, 1), date(year, 12, 31), "avg", avg)
    return rows


@pytest.fixture
def engine(tmp_path):
    """Станция с полными 2019 (0 °C) и 2020 (+2 °C) годами и 2021 годом
    (+4 °C) с десятидневной дырой в январе — год и январь неполные."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    gap = frozenset(date(2021, 1, day) for day in range(5, 15))
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), [
            *_year_rows(STATION, 2019, 0.0),
            *_year_rows(STATION, 2020, 2.0),
            *_year_rows(STATION, 2021, 4.0, skip=gap),
        ])
    return engine


def _count(engine, table, *where) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar_one()


def _anomaly(engine, period_type: str, start: date) -> float | None:
    with engine.connect() as conn:
        return conn.execute(
            select(computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == STATION,
                computed_indicators.c.period_type == period_type,
                computed_indicators.c.period_start == start,
                computed_indicators.c.stat_type == STAT_ANOMALY,
            )
        ).scalar_one_or_none()


def test_new_station_requires_calculation(engine):
    (status,) = calculation_status(engine)

    assert status.wmo_index == STATION
    assert status.stale and status.calculated_at is None
    assert status.period == (date(2019, 1, 1), date(2021, 12, 31))
    assert is_stale(engine, STATION)
    assert load_station_norms(engine, STATION) is None


def test_recalculation_stores_norms_anomalies_and_completeness(engine):
    norms = recalculate_station(engine, STATION)

    # Нормы — по полным 2019 и 2020 годам (базы ВМО у станции нет): +1 °C
    assert norms.annual.value == pytest.approx(1.0)
    assert (norms.annual.first_year, norms.annual.last_year, norms.annual.year_count) == (2019, 2020, 2)
    assert not norms.standard
    # Июль полон во все три года, январь — только в первые два
    assert norms.months[7] == pytest.approx(2.0)
    assert norms.months[1] == pytest.approx(1.0)
    assert _count(engine, station_norms, station_norms.c.period_type == "month") == 12
    assert _count(engine, station_norms, station_norms.c.period_type == "year") == 1
    assert _count(engine, station_norms, station_norms.c.period_type == "decade") == 36

    assert _anomaly(engine, "year", date(2019, 1, 1)) == pytest.approx(-1.0)
    assert _anomaly(engine, "year", date(2020, 1, 1)) == pytest.approx(1.0)
    assert _anomaly(engine, "month", date(2021, 7, 1)) == pytest.approx(2.0)
    assert _anomaly(engine, "decade", date(2021, 7, 11)) == pytest.approx(2.0)
    # Отклонение неполного периода тоже хранится — признак полноты лежит рядом
    assert _anomaly(engine, "year", date(2021, 1, 1)) == pytest.approx(3.0)
    assert _anomaly(engine, "month", date(2021, 1, 1)) == pytest.approx(3.0)

    with engine.connect() as conn:
        flags = {
            (kind, start): (days, complete)
            for kind, start, days, complete in conn.execute(select(
                period_completeness.c.period_type, period_completeness.c.period_start,
                period_completeness.c.days_with_data, period_completeness.c.complete,
            ))
        }
    assert flags[("year", date(2020, 1, 1))] == (366, True)
    assert flags[("year", date(2021, 1, 1))] == (355, False)
    assert flags[("month", date(2021, 1, 1))] == (21, False)
    assert flags[("month", date(2021, 2, 1))] == (28, True)


def test_recalculation_records_state(engine):
    recalculate_station(engine, STATION)
    (status,) = calculation_status(engine)

    assert not status.stale and status.calculated_at is not None
    assert (status.norm_standard, status.norm_first_year, status.norm_last_year, status.norm_years) == (
        False, 2019, 2020, 2,
    )
    assert not is_stale(engine, STATION)
    with engine.connect() as conn:
        assert conn.execute(select(anomaly_state.c.rule_version)).scalar_one() == RULE_VERSION


def test_stored_norms_equal_computed_on_the_fly(engine):
    recalculate_station(engine, STATION)

    assert load_station_norms(engine, STATION) == compute_station_norms(*fetch_station_temperatures(engine, STATION))


def test_recalculation_replaces_previous_result(engine):
    recalculate_station(engine, STATION)
    before = _count(engine, computed_indicators, computed_indicators.c.stat_type == STAT_ANOMALY)
    with engine.begin() as conn:  # 2019 год стал теплее: 0 -> +1 °C
        conn.execute(
            computed_indicators.update()
            .where(computed_indicators.c.period_start < date(2020, 1, 1))
            .values(value=1.0)
        )

    recalculate_station(engine, STATION)

    assert _count(engine, computed_indicators, computed_indicators.c.stat_type == STAT_ANOMALY) == before
    assert _count(engine, station_norms) == 49
    assert _anomaly(engine, "year", date(2020, 1, 1)) == pytest.approx(0.5)  # норма сдвинулась на +1.5


def test_anomaly_rows_do_not_leak_into_source_values(engine):
    """Строки отклонений лежат в той же таблице, что и средние: чтение
    исходных значений не должно принять их за среднюю месяца или года."""
    before = fetch_station_temperatures(engine, STATION)
    recalculate_station(engine, STATION)

    assert fetch_station_temperatures(engine, STATION) == before


def test_recompute_marks_station_stale_without_recalculating(engine):
    recalculate_station(engine, STATION)
    day = date(2020, 3, 1)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(
            wmo_index=STATION, station_name="Станция", obs_date=day, obs_time=time(12), T_temperature=30.0,
        ))

    recompute_range(engine, STATION, day, day)

    assert is_stale(engine, STATION)
    assert load_station_norms(engine, STATION) is None
    # сохранённые отклонения ещё прежние — при заливке ничего не считается
    assert _anomaly(engine, "year", date(2020, 1, 1)) == pytest.approx(1.0)


def test_ensure_fresh_calculates_only_stale_stations(engine):
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), _year_rows(OTHER, 2020, -3.0))
    recalculate_station(engine, STATION)
    calls = []

    done = ensure_fresh(engine, progress=lambda number, total, wmo: calls.append((number, total, wmo)))

    assert done == [OTHER]
    assert calls == [(1, 1, OTHER)]
    assert ensure_fresh(engine) == []
    assert sorted(ensure_fresh(engine, force=True)) == [OTHER, STATION]


def test_ensure_fresh_for_given_stations_only(engine):
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), _year_rows(OTHER, 2020, -3.0))

    assert ensure_fresh(engine, [STATION]) == [STATION]
    assert is_stale(engine, OTHER)


def test_stored_results_of_station_without_data_are_removed(engine):
    recalculate_station(engine, STATION)
    with engine.begin() as conn:
        conn.execute(computed_indicators.delete().where(computed_indicators.c.stat_type != STAT_ANOMALY))
        conn.execute(anomaly_state.delete())  # как после recompute_range по удалённым строкам

    assert ensure_fresh(engine) == []

    assert _count(engine, station_norms) == 0
    assert _count(engine, period_completeness) == 0
    assert _count(engine, computed_indicators) == 0
    assert calculation_status(engine) == []


def test_outdated_rule_version_requires_recalculation(engine):
    recalculate_station(engine, STATION)
    with engine.begin() as conn:
        conn.execute(anomaly_state.update().values(rule_version=RULE_VERSION - 1))

    assert is_stale(engine, STATION)
    assert calculation_status(engine)[0].stale
    assert ensure_fresh(engine) == [STATION]


def test_dashboard_calculates_on_demand_and_uses_stored_norms(engine):
    days, months, years = fetch_station_temperatures(engine, STATION)
    on_the_fly = compute_dashboard(days, months, years, date(2019, 1, 1), date(2021, 12, 31))

    stored = build_dashboard(engine, STATION, date(2019, 1, 1), date(2021, 12, 31))

    assert not is_stale(engine, STATION)  # дашборд сам досчитал станцию
    assert stored == on_the_fly


def test_dashboard_recalculates_after_data_change(engine):
    build_dashboard(engine, STATION, date(2019, 1, 1), date(2021, 12, 31))
    day = date(2020, 3, 1)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(
            wmo_index=STATION, station_name="Станция", obs_date=day, obs_time=time(12), T_temperature=30.0,
        ))
    recompute_range(engine, STATION, day, day)

    data = build_dashboard(engine, STATION, date(2019, 1, 1), date(2021, 12, 31))

    assert not is_stale(engine, STATION)
    assert data.abs_max.value == 30.0
    days, months, years = fetch_station_temperatures(engine, STATION)
    assert data == compute_dashboard(days, months, years, date(2019, 1, 1), date(2021, 12, 31))


def test_migration_adds_tables_to_database_with_data(engine):
    """База версии 5 с уже залитыми данными: миграция 6 только добавляет
    таблицы, данные остаются, все станции требуют расчёта."""
    with engine.begin() as conn:
        for table in ("station_norms", "period_completeness", "anomaly_state"):
            conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(schema_version.update().values(version=5))
    rows_before = _count(engine, computed_indicators)

    run_migrations(engine)

    assert {"station_norms", "period_completeness", "anomaly_state"} <= set(inspect(engine).get_table_names())
    assert _count(engine, computed_indicators) == rows_before
    with engine.connect() as conn:
        assert conn.execute(select(schema_version.c.version)).scalar_one() == CURRENT_SCHEMA_VERSION
    assert [status.stale for status in calculation_status(engine)] == [True]
