from datetime import date

from sqlalchemy import select

from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, stations
from meteoregik.db.session import get_engine


def _make_station(engine, wmo_index=27704, name="Мосальск"):
    with engine.begin() as conn:
        return conn.execute(
            stations.insert().values(name=name, wmo_index=wmo_index)
        ).inserted_primary_key[0]


def _get(engine, station_id, period_type, period_start, period_end, stat_type):
    with engine.connect() as conn:
        return conn.execute(
            select(computed_indicators.c.value).where(
                computed_indicators.c.station_id == station_id,
                computed_indicators.c.period_type == period_type,
                computed_indicators.c.period_start == period_start,
                computed_indicators.c.period_end == period_end,
                computed_indicators.c.parameter == "T",
                computed_indicators.c.stat_type == stat_type,
            )
        ).scalar_one_or_none()


def test_recompute_range_stores_day_decade_month_year_within_single_period(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    station_id = _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"station_id": station_id, "obs_date": date(2020, 1, 1), "t_avg": 0.0, "t_min": -5.0, "t_max": 5.0},
                {"station_id": station_id, "obs_date": date(2020, 1, 2), "t_avg": 2.0, "t_min": -3.0, "t_max": 7.0},
            ],
        )

    recompute_range(engine, station_id, date(2020, 1, 1), date(2020, 1, 2))

    assert _get(engine, station_id, "day", date(2020, 1, 1), date(2020, 1, 1), "avg") == 0.0
    assert _get(engine, station_id, "day", date(2020, 1, 2), date(2020, 1, 2), "avg") == 2.0
    # декада целиком (1-10), даже если запрошенный диапазон был только 1-2 января
    assert _get(engine, station_id, "decade", date(2020, 1, 1), date(2020, 1, 10), "avg") == 1.0
    assert _get(engine, station_id, "month", date(2020, 1, 1), date(2020, 1, 31), "avg") == 1.0
    assert _get(engine, station_id, "year", date(2020, 1, 1), date(2020, 12, 31), "avg") == 1.0


def test_recompute_range_spans_decade_month_year_boundaries(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    station_id = _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"station_id": station_id, "obs_date": date(2019, 12, 30), "t_avg": -10.0, "t_min": -10.0, "t_max": -10.0},
                {"station_id": station_id, "obs_date": date(2020, 1, 2), "t_avg": 10.0, "t_min": 10.0, "t_max": 10.0},
            ],
        )

    recompute_range(engine, station_id, date(2019, 12, 30), date(2020, 1, 2))

    # затронуты 2 разные декады (21-31 декабря 2019 и 1-10 января 2020),
    # 2 месяца, 2 года — каждый пересчитан по своим полным границам
    assert _get(engine, station_id, "decade", date(2019, 12, 21), date(2019, 12, 31), "avg") == -10.0
    assert _get(engine, station_id, "decade", date(2020, 1, 1), date(2020, 1, 10), "avg") == 10.0
    assert _get(engine, station_id, "month", date(2019, 12, 1), date(2019, 12, 31), "avg") == -10.0
    assert _get(engine, station_id, "month", date(2020, 1, 1), date(2020, 1, 31), "avg") == 10.0
    assert _get(engine, station_id, "year", date(2019, 1, 1), date(2019, 12, 31), "avg") == -10.0
    assert _get(engine, station_id, "year", date(2020, 1, 1), date(2020, 12, 31), "avg") == 10.0


def test_recompute_range_is_idempotent_upsert(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    station_id = _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(station_id=station_id, obs_date=date(2020, 1, 1), t_avg=5.0, t_min=0.0, t_max=10.0)
        )

    recompute_range(engine, station_id, date(2020, 1, 1), date(2020, 1, 1))
    recompute_range(engine, station_id, date(2020, 1, 1), date(2020, 1, 1))

    with engine.connect() as conn:
        n_rows = len(
            conn.execute(
                select(computed_indicators.c.id).where(
                    computed_indicators.c.station_id == station_id,
                    computed_indicators.c.period_type == "day",
                )
            ).all()
        )
    assert n_rows == 3  # avg/min/max — не задублировались при повторном пересчёте

    # правка сырых данных -> новый пересчёт должен обновить значение, не добавить строку
    with engine.begin() as conn:
        conn.execute(
            gg_readings.update()
            .where(gg_readings.c.station_id == station_id, gg_readings.c.obs_date == date(2020, 1, 1))
            .values(t_avg=50.0)
        )
    recompute_range(engine, station_id, date(2020, 1, 1), date(2020, 1, 1))

    assert _get(engine, station_id, "day", date(2020, 1, 1), date(2020, 1, 1), "avg") == 50.0
