from datetime import date, time

from sqlalchemy import select

from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine

WMO_INDEX = 27704


def _make_station(engine, wmo_index=WMO_INDEX, name="Мосальск"):
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name=name, wmo_index=wmo_index))


def _get(engine, wmo_index, period_type, period_start, period_end, stat_type):
    with engine.connect() as conn:
        return conn.execute(
            select(computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == wmo_index,
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
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "t_avg": 0.0, "t_min": -5.0, "t_max": 5.0},
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 2), "t_avg": 2.0, "t_min": -3.0, "t_max": 7.0},
            ],
        )

    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 2))

    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 1), date(2020, 1, 1), "avg") == 0.0
    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 2), date(2020, 1, 2), "avg") == 2.0
    # декада целиком (1-10), даже если запрошенный диапазон был только 1-2 января
    assert _get(engine, WMO_INDEX, "decade", date(2020, 1, 1), date(2020, 1, 10), "avg") == 1.0
    assert _get(engine, WMO_INDEX, "month", date(2020, 1, 1), date(2020, 1, 31), "avg") == 1.0
    assert _get(engine, WMO_INDEX, "year", date(2020, 1, 1), date(2020, 12, 31), "avg") == 1.0


def test_recompute_range_spans_decade_month_year_boundaries(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"wmo_index": WMO_INDEX, "obs_date": date(2019, 12, 30), "t_avg": -10.0, "t_min": -10.0, "t_max": -10.0},
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 2), "t_avg": 10.0, "t_min": 10.0, "t_max": 10.0},
            ],
        )

    recompute_range(engine, WMO_INDEX, date(2019, 12, 30), date(2020, 1, 2))

    # затронуты 2 разные декады (21-31 декабря 2019 и 1-10 января 2020),
    # 2 месяца, 2 года — каждый пересчитан по своим полным границам
    assert _get(engine, WMO_INDEX, "decade", date(2019, 12, 21), date(2019, 12, 31), "avg") == -10.0
    assert _get(engine, WMO_INDEX, "decade", date(2020, 1, 1), date(2020, 1, 10), "avg") == 10.0
    assert _get(engine, WMO_INDEX, "month", date(2019, 12, 1), date(2019, 12, 31), "avg") == -10.0
    assert _get(engine, WMO_INDEX, "month", date(2020, 1, 1), date(2020, 1, 31), "avg") == 10.0
    assert _get(engine, WMO_INDEX, "year", date(2019, 1, 1), date(2019, 12, 31), "avg") == -10.0
    assert _get(engine, WMO_INDEX, "year", date(2020, 1, 1), date(2020, 12, 31), "avg") == 10.0


def test_recompute_range_is_idempotent_upsert(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(wmo_index=WMO_INDEX, obs_date=date(2020, 1, 1), t_avg=5.0, t_min=0.0, t_max=10.0)
        )

    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))
    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))

    with engine.connect() as conn:
        n_rows = len(
            conn.execute(
                select(computed_indicators.c.id).where(
                    computed_indicators.c.wmo_index == WMO_INDEX,
                    computed_indicators.c.period_type == "day",
                )
            ).all()
        )
    assert n_rows == 3  # avg/min/max — не задублировались при повторном пересчёте

    # правка сырых данных -> новый пересчёт должен обновить значение, не добавить строку
    with engine.begin() as conn:
        conn.execute(
            gg_readings.update()
            .where(gg_readings.c.wmo_index == WMO_INDEX, gg_readings.c.obs_date == date(2020, 1, 1))
            .values(t_avg=50.0)
        )
    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))

    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 1), date(2020, 1, 1), "avg") == 50.0


def test_recompute_range_calls_progress_callback_for_each_day(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    seen_dates = []
    recompute_range(
        engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 3),
        progress_callback=seen_dates.append,
    )

    assert seen_dates == [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)]


def test_recompute_range_detects_conflict_between_rp5_and_gg(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            rp5_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2015, 1, 21), obs_time=time(0, 0),
                T_temperature=-2.5, source_file="Сухиничи_2015-2019_rp5.xls",
            )
        )
    recompute_range(engine, WMO_INDEX, date(2015, 1, 21), date(2015, 1, 21))
    assert _get(engine, WMO_INDEX, "day", date(2015, 1, 21), date(2015, 1, 21), "avg") == -2.5

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2015, 1, 21),
                t_avg=-2.3, t_min=-2.3, t_max=-2.3,
                source_file="Сухиничи 1959-2025_формат_ГГ.xlsx",
            )
        )

    seen_conflicts = []

    def resolver(conflict):
        seen_conflicts.append(conflict)
        return "old"  # оставляем значение из rp5

    recompute_range(engine, WMO_INDEX, date(2015, 1, 21), date(2015, 1, 21), conflict_resolver=resolver)

    assert len(seen_conflicts) == 3  # avg/min/max — у одиночного rp5-срока все три равны -2.5
    conflict = seen_conflicts[0]
    assert conflict.old_value == -2.5
    assert conflict.old_source_file == "Сухиничи_2015-2019_rp5.xls"
    assert conflict.new_value == -2.3
    assert conflict.new_source_file == "Сухиничи 1959-2025_формат_ГГ.xlsx"

    # выбор "old" — сохранённое значение не изменилось
    assert _get(engine, WMO_INDEX, "day", date(2015, 1, 21), date(2015, 1, 21), "avg") == -2.5


def test_recompute_range_conflict_choosing_new_overwrites(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            rp5_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2015, 1, 21), obs_time=time(0, 0), T_temperature=-2.5
            )
        )
    recompute_range(engine, WMO_INDEX, date(2015, 1, 21), date(2015, 1, 21))

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2015, 1, 21), t_avg=-2.3, t_min=-2.3, t_max=-2.3
            )
        )

    recompute_range(
        engine, WMO_INDEX, date(2015, 1, 21), date(2015, 1, 21),
        conflict_resolver=lambda conflict: "new",
    )

    assert _get(engine, WMO_INDEX, "day", date(2015, 1, 21), date(2015, 1, 21), "avg") == -2.3


def test_recompute_range_without_resolver_keeps_previous_behaviour(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            rp5_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2015, 1, 21), obs_time=time(0, 0), T_temperature=-2.5
            )
        )
    recompute_range(engine, WMO_INDEX, date(2015, 1, 21), date(2015, 1, 21))

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2015, 1, 21), t_avg=-2.3, t_min=-2.3, t_max=-2.3
            )
        )

    recompute_range(engine, WMO_INDEX, date(2015, 1, 21), date(2015, 1, 21))  # без conflict_resolver

    assert _get(engine, WMO_INDEX, "day", date(2015, 1, 21), date(2015, 1, 21), "avg") == -2.3


def test_decade_aggregation_reads_stored_day_values_not_raw_recompute(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    # День 5 января "заморожен" в computed_indicators значением, не
    # соответствующим никаким сырым данным (их для этой даты вообще нет) —
    # имитирует ранее сделанный пользователем выбор при конфликте.
    with engine.begin() as conn:
        conn.execute(
            computed_indicators.insert(),
            [
                {"wmo_index": WMO_INDEX, "period_type": "day", "period_start": date(2020, 1, 5),
                 "period_end": date(2020, 1, 5), "parameter": "T", "stat_type": "avg", "value": 99.0},
                {"wmo_index": WMO_INDEX, "period_type": "day", "period_start": date(2020, 1, 5),
                 "period_end": date(2020, 1, 5), "parameter": "T", "stat_type": "min", "value": 99.0},
                {"wmo_index": WMO_INDEX, "period_type": "day", "period_start": date(2020, 1, 5),
                 "period_end": date(2020, 1, 5), "parameter": "T", "stat_type": "max", "value": 99.0},
            ],
        )
        conn.execute(
            gg_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2020, 1, 6), t_avg=1.0, t_min=1.0, t_max=1.0
            )
        )

    # Пересчитываем только 6 января — 5-е января day-циклом не затрагивается.
    recompute_range(engine, WMO_INDEX, date(2020, 1, 6), date(2020, 1, 6))

    # Но декада (1-10 января) пересчитывается целиком и должна учесть
    # "замороженное" значение 5 января как есть (99.0), а не проигнорировать
    # его из-за отсутствия сырых данных.
    assert _get(engine, WMO_INDEX, "decade", date(2020, 1, 1), date(2020, 1, 10), "avg") == 50.0


def test_decade_aggregation_rounds_average_to_two_decimals(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert(),
            [
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "t_avg": 1.0, "t_min": 1.0, "t_max": 1.0},
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 2), "t_avg": 2.0, "t_min": 2.0, "t_max": 2.0},
                {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 3), "t_avg": 2.0, "t_min": 2.0, "t_max": 2.0},
            ],
        )

    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 3))

    assert _get(engine, WMO_INDEX, "decade", date(2020, 1, 1), date(2020, 1, 10), "avg") == 1.67


def test_recompute_range_deletes_stale_value_when_no_raw_data_left(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    _make_station(engine)

    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2020, 1, 1), t_avg=5.0, t_min=0.0, t_max=10.0
            )
        )
    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))
    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 1), date(2020, 1, 1), "avg") == 5.0

    # Пользователь удалил единственную запись за день (например, через
    # "Просмотр данных") — сырых данных для этой даты больше нет вообще.
    with engine.begin() as conn:
        conn.execute(gg_readings.delete().where(gg_readings.c.wmo_index == WMO_INDEX))
    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))

    # computed_indicators — производный кэш: без сырых данных значение не
    # должно "зависать" со старым числом, а должно исчезнуть.
    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 1), date(2020, 1, 1), "avg") is None
    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 1), date(2020, 1, 1), "min") is None
    assert _get(engine, WMO_INDEX, "day", date(2020, 1, 1), date(2020, 1, 1), "max") is None
