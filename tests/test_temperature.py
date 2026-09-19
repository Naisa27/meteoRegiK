from datetime import date, time

from meteoregik.calculations.temperature import (
    daily_stats,
    decade_bounds,
    decade_stats,
    month_stats,
    year_stats,
)
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine


def _make_station(engine, wmo_index=27704, name="Мосальск"):
    with engine.begin() as conn:
        return conn.execute(
            stations.insert().values(name=name, wmo_index=wmo_index)
        ).inserted_primary_key[0]


def test_decade_bounds():
    assert decade_bounds(date(2020, 1, 5)) == (date(2020, 1, 1), date(2020, 1, 10))
    assert decade_bounds(date(2020, 1, 15)) == (date(2020, 1, 11), date(2020, 1, 20))
    assert decade_bounds(date(2020, 1, 25)) == (date(2020, 1, 21), date(2020, 1, 31))
    assert decade_bounds(date(2021, 2, 25)) == (date(2021, 2, 21), date(2021, 2, 28))  # не високосный
    assert decade_bounds(date(2020, 2, 25)) == (date(2020, 2, 21), date(2020, 2, 29))  # високосный


def test_daily_stats_from_rp5_averages_available_slots(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            rp5_readings.insert(),
            [
                {"wmo_index": 27704, "obs_date": date(2020, 1, 1), "obs_time": time(0, 0), "T_temperature": -2.0},
                {"wmo_index": 27704, "obs_date": date(2020, 1, 1), "obs_time": time(12, 0), "T_temperature": 4.0},
                {"wmo_index": 27704, "obs_date": date(2020, 1, 1), "obs_time": time(18, 0), "T_temperature": None},
            ],
        )

    stats = daily_stats(engine, 1, date(2020, 1, 1))
    assert stats == {"avg": 1.0, "min": -2.0, "max": 4.0}


def test_daily_stats_prefers_gg_over_rp5(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    station_id = _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            rp5_readings.insert().values(
                wmo_index=27704, obs_date=date(2020, 1, 1), obs_time=time(0, 0), T_temperature=100.0
            )
        )
        conn.execute(
            gg_readings.insert().values(
                station_id=station_id, obs_date=date(2020, 1, 1), t_avg=1.0, t_min=-1.0, t_max=3.0
            )
        )

    stats = daily_stats(engine, station_id, date(2020, 1, 1))
    assert stats == {"avg": 1.0, "min": -1.0, "max": 3.0}


def test_daily_stats_returns_none_when_no_data(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    stats = daily_stats(engine, 1, date(2020, 1, 1))
    assert stats == {"avg": None, "min": None, "max": None}


def test_decade_stats_averages_daily_stats_skipping_missing_days(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    station_id = _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"station_id": station_id, "obs_date": date(2020, 1, 1), "t_avg": 0.0, "t_min": -5.0, "t_max": 5.0},
                {"station_id": station_id, "obs_date": date(2020, 1, 2), "t_avg": 2.0, "t_min": -3.0, "t_max": 7.0},
                # 3 января данных нет вовсе — должен быть пропущен, не считаться нулём
            ],
        )

    stats = decade_stats(engine, station_id, date(2020, 1, 1), date(2020, 1, 10))
    assert stats == {"avg": 1.0, "min": -5.0, "max": 7.0}


def test_month_and_year_stats(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    station_id = _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"station_id": station_id, "obs_date": date(2020, 1, 1), "t_avg": -10.0, "t_min": -15.0, "t_max": -5.0},
                {"station_id": station_id, "obs_date": date(2020, 6, 15), "t_avg": 20.0, "t_min": 10.0, "t_max": 30.0},
            ],
        )

    jan_stats = month_stats(engine, station_id, 2020, 1)
    assert jan_stats == {"avg": -10.0, "min": -15.0, "max": -5.0}

    year_result = year_stats(engine, station_id, 2020)
    assert year_result == {"avg": 5.0, "min": -15.0, "max": 30.0}
