"""Суммы осадков: суточная сумма из сырых данных, суммы периодов, пакетный
пересчёт станции и миграция базы с уже залитыми данными."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import select

from meteoregik.calculations import precipitation
from meteoregik.calculations.precipitation import Rp5Precipitation, pick_main_series, rp5_daily_sum, to_mm
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import CURRENT_SCHEMA_VERSION, run_migrations
from meteoregik.db.schema import anomaly_state, computed_indicators, gg_readings, rp5_readings, schema_version
from meteoregik.db.session import get_engine

WMO_INDEX = 27707


def _record(hour: int, amount: str | float | None, period: float | None = 12.0) -> Rp5Precipitation:
    return Rp5Precipitation(hour, amount, period)


# --- суточная сумма по записям rp5 (без базы) ---


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("0.5", 0.5), ("6", 6.0), (3.2, 3.2), ("1,5", 1.5), ("Осадков нет", 0.0), ("Следы осадков", 0.0), (None, 0.0)],
)
def test_to_mm_treats_text_and_empty_as_zero(amount, expected):
    assert to_mm(amount) == expected


def test_daily_sum_takes_only_the_main_series_of_overlapping_twelve_hour_records():
    """Четыре записи в сутки по 12 часов — два перекрывающихся ряда: сумма
    всех дала бы вдвое больше выпавшего."""
    records = [_record(6, "5"), _record(9, "6"), _record(18, "1"), _record(21, "Осадков нет")]

    assert rp5_daily_sum(records, main=9) == 6.0
    assert rp5_daily_sum(records, main=6) == 6.0  # 5 + 1 — другой ряд, если основной он


def test_daily_sum_falls_back_to_the_other_series_when_main_has_no_records():
    records = [_record(6, "2.5"), _record(18, "0.3")]

    assert rp5_daily_sum(records, main=9) == 2.8


def test_daily_sum_without_twelve_hour_records_adds_everything():
    """Редкие сутки с одной записью за 24 часа или записями за 6 часов."""
    assert rp5_daily_sum([_record(9, "7.4", 24.0)], main=9) == 7.4
    assert rp5_daily_sum([_record(3, "1", 6.0), _record(9, "2", 6.0), _record(15, "0.5", None)], main=9) == 3.5


def test_daily_sum_is_missing_when_day_has_no_precipitation_records_at_all():
    """Строки за день есть, но осадки не записаны ни в один срок — пропуск."""
    assert rp5_daily_sum([_record(9, None), _record(21, None)], main=9) is None
    assert rp5_daily_sum([], main=9) is None


def test_empty_cells_of_other_observation_terms_do_not_make_the_day_missing():
    """В сроки, когда осадки не измеряют, ячейка пуста всегда — это не пропуск."""
    records = [_record(0, None, None), _record(3, None, None), _record(9, "Осадков нет"), _record(21, "1.2")]

    assert rp5_daily_sum(records, main=9) == 1.2


def test_daily_sum_ignores_extra_records_of_other_periods_next_to_main_series():
    records = [_record(9, "4"), _record(21, "1"), _record(9, "3", 6.0)]

    assert rp5_daily_sum(records, main=9) == 5.0


def test_pick_main_series_prefers_the_more_filled_one_and_is_stable_on_ties():
    assert pick_main_series({6: 100, 9: 140}) == 9
    assert pick_main_series({9: 5, 6: 5}) == 6
    assert pick_main_series({}) is None


# --- расчёт по базе ---


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    return engine


def _rp5(day: date, hour: int, amount: str | None, period: float | None = 12.0, temperature: float | None = 1.0):
    return {
        "wmo_index": WMO_INDEX, "obs_date": day, "obs_time": time(hour, 0), "T_temperature": temperature,
        "RRR_precipitation": amount, "tR_precipitation_period": period, "source_file": "rp5.xls",
    }


def _insert(engine, table, rows):
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)


def _sums(engine, period_type: str) -> dict[date, float]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(computed_indicators.c.period_start, computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == WMO_INDEX,
                computed_indicators.c.parameter == "R",
                computed_indicators.c.stat_type == "sum",
                computed_indicators.c.period_type == period_type,
            )
        ).all()
    return dict(rows)


def _two_days_of_rp5() -> list[dict]:
    first, second = date(2020, 1, 10), date(2020, 1, 11)
    return [
        _rp5(first, 6, "5"), _rp5(first, 9, "6"), _rp5(first, 18, "1"), _rp5(first, 21, "0.4"),
        _rp5(second, 6, "Осадков нет"), _rp5(second, 9, "Следы осадков"), _rp5(second, 21, "2"),
        # ряд 09/21 заполнен чаще — он основной
        _rp5(date(2020, 1, 12), 9, "Осадков нет"), _rp5(date(2020, 1, 12), 21, None, None),
        # температура есть, осадки не записаны ни в один срок — пропуск осадков
        _rp5(date(2020, 1, 13), 9, None, None), _rp5(date(2020, 1, 13), 21, None, None),
    ]


def test_main_series_is_found_from_station_data(engine):
    _insert(engine, rp5_readings, _two_days_of_rp5())

    with engine.connect() as conn:
        assert precipitation.main_series(conn, WMO_INDEX) == 9
        assert precipitation.main_series(conn, 99999) is None


def test_recompute_stores_daily_decade_month_and_year_sums(engine):
    _insert(engine, rp5_readings, _two_days_of_rp5())

    recompute_range(engine, WMO_INDEX, date(2020, 1, 9), date(2020, 1, 12))

    assert _sums(engine, "day") == {date(2020, 1, 10): 6.4, date(2020, 1, 11): 2.0, date(2020, 1, 12): 0.0}
    # 10-е — в первой декаде, 11-е и 12-е — во второй
    assert _sums(engine, "decade") == {date(2020, 1, 1): 6.4, date(2020, 1, 11): 2.0}
    assert _sums(engine, "month") == {date(2020, 1, 1): 8.4}
    assert _sums(engine, "year") == {date(2020, 1, 1): 8.4}


def test_day_without_precipitation_data_has_no_sum(engine):
    """9 января строк нет вовсе, 13-го осадки не записаны — оба дня
    пропущены, а не сухие."""
    _insert(engine, rp5_readings, _two_days_of_rp5())

    recompute_range(engine, WMO_INDEX, date(2020, 1, 9), date(2020, 1, 13))

    assert date(2020, 1, 9) not in _sums(engine, "day")
    assert date(2020, 1, 13) not in _sums(engine, "day")
    assert _sums(engine, "month") == {date(2020, 1, 1): 8.4}


def test_gg_value_wins_over_rp5_and_empty_gg_value_falls_back_to_rp5(engine):
    _insert(engine, rp5_readings, _two_days_of_rp5())
    _insert(engine, gg_readings, [
        {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 10), "t_avg": 1.0, "rrr_precipitation": 6.5,
         "source_file": "gg.xlsx"},
        {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 11), "t_avg": 1.0, "rrr_precipitation": None,
         "source_file": "gg.xlsx"},
    ])

    recompute_range(engine, WMO_INDEX, date(2020, 1, 10), date(2020, 1, 12))

    # 10-е — из ГГ; 11-го в ГГ осадки пусты — сумма rp5; 12-е — только rp5
    assert _sums(engine, "day") == {date(2020, 1, 10): 6.5, date(2020, 1, 11): 2.0, date(2020, 1, 12): 0.0}
    with engine.connect() as conn:
        sources = dict(conn.execute(
            select(computed_indicators.c.period_start, computed_indicators.c.source_file).where(
                computed_indicators.c.parameter == "R", computed_indicators.c.period_type == "day"
            )
        ).all())
    assert sources[date(2020, 1, 10)] == "gg.xlsx" and sources[date(2020, 1, 11)] == "rp5.xls"


def test_empty_gg_precipitation_without_rp5_is_a_missing_day_not_a_dry_one(engine):
    """Решение пользователя 2026-10-11: в ГГ есть температура, осадки пусты —
    день пропущен, сумма месяца его не включает и нулём не занижается."""
    _insert(engine, gg_readings, [
        {"wmo_index": WMO_INDEX, "obs_date": date(1975, 6, 1), "t_avg": 15.0, "rrr_precipitation": 4.0,
         "source_file": "gg.xlsx"},
        {"wmo_index": WMO_INDEX, "obs_date": date(1975, 6, 2), "t_avg": 16.0, "rrr_precipitation": None,
         "source_file": "gg.xlsx"},
        {"wmo_index": WMO_INDEX, "obs_date": date(1975, 6, 3), "t_avg": 16.0, "rrr_precipitation": 0.0,
         "source_file": "gg.xlsx"},
    ])

    recompute_range(engine, WMO_INDEX, date(1975, 6, 1), date(1975, 6, 3))

    assert _sums(engine, "day") == {date(1975, 6, 1): 4.0, date(1975, 6, 3): 0.0}


def test_precipitation_rows_do_not_touch_temperature_rows(engine):
    """Температура и осадки лежат в одной таблице — параметры не смешиваются."""
    _insert(engine, rp5_readings, _two_days_of_rp5())

    recompute_range(engine, WMO_INDEX, date(2020, 1, 10), date(2020, 1, 12))

    with engine.connect() as conn:
        rows = conn.execute(
            select(computed_indicators.c.parameter, computed_indicators.c.stat_type)
            .where(computed_indicators.c.period_type == "month")
        ).all()
    assert sorted(rows) == [("R", "sum"), ("T", "avg"), ("T", "max"), ("T", "min")]


def test_deleting_all_readings_of_a_day_removes_its_sum(engine):
    _insert(engine, rp5_readings, _two_days_of_rp5())
    recompute_range(engine, WMO_INDEX, date(2020, 1, 10), date(2020, 1, 12))

    with engine.begin() as conn:
        conn.execute(rp5_readings.delete().where(rp5_readings.c.obs_date == date(2020, 1, 11)))
    recompute_range(engine, WMO_INDEX, date(2020, 1, 11), date(2020, 1, 11))

    assert date(2020, 1, 11) not in _sums(engine, "day")
    assert _sums(engine, "month") == {date(2020, 1, 1): 6.4}


def test_conflict_between_sources_reports_the_parameter(engine):
    """ГГ поверх rp5 с другой суммой за день — расхождение, и в нём сказано,
    что это осадки, а не температура."""
    _insert(engine, rp5_readings, [_rp5(date(2020, 1, 10), 9, "6", temperature=1.0)])
    recompute_range(engine, WMO_INDEX, date(2020, 1, 10), date(2020, 1, 10))
    _insert(engine, gg_readings, [
        {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 10), "t_avg": 1.0, "t_min": 1.0, "t_max": 1.0,
         "rrr_precipitation": 6.5, "source_file": "gg.xlsx"},
    ])
    conflicts = []

    def keep_old(conflict):
        conflicts.append(conflict)
        return "old"

    recompute_range(engine, WMO_INDEX, date(2020, 1, 10), date(2020, 1, 10), conflict_resolver=keep_old)

    assert [(c.parameter, c.stat_type, c.period_type, c.old_value, c.new_value) for c in conflicts] == [
        ("R", "sum", "day", 6.0, 6.5)
    ]
    assert _sums(engine, "day") == {date(2020, 1, 10): 6.0}  # пользователь оставил сохранённое


def test_rebuild_station_gives_the_same_rows_as_recompute(engine):
    """Пакетный пересчёт (миграция) и пересчёт диапазона (импорт, правка) —
    два пути к одним и тем же числам."""
    _insert(engine, rp5_readings, _two_days_of_rp5())
    _insert(engine, gg_readings, [
        {"wmo_index": WMO_INDEX, "obs_date": date(2020, 2, 28), "t_avg": 1.0, "rrr_precipitation": 3.3,
         "source_file": "gg.xlsx"},
    ])

    def stored():
        with engine.connect() as conn:
            return sorted(
                tuple(row) for row in conn.execute(
                    select(
                        computed_indicators.c.period_type, computed_indicators.c.period_start,
                        computed_indicators.c.period_end, computed_indicators.c.value,
                        computed_indicators.c.source_file,
                    ).where(computed_indicators.c.parameter == "R")
                )
            )

    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 2, 29))
    by_recompute = stored()
    with engine.begin() as conn:
        conn.execute(computed_indicators.delete().where(computed_indicators.c.parameter == "R"))
        days = precipitation.rebuild_station(conn, WMO_INDEX)

    assert days == 4  # 13 января в счёт не идёт — осадков за него нет
    assert stored() == by_recompute


def test_migration_adds_precipitation_to_already_loaded_stations(engine):
    """База прошлой версии: сырые данные и температура есть, осадков нет."""
    _insert(engine, rp5_readings, _two_days_of_rp5())
    recompute_range(engine, WMO_INDEX, date(2020, 1, 10), date(2020, 1, 12))
    with engine.begin() as conn:
        conn.execute(computed_indicators.delete().where(computed_indicators.c.parameter == "R"))
        conn.execute(anomaly_state.insert().values(
            wmo_index=WMO_INDEX, calculated_at=datetime(2020, 1, 1), rule_version=1,
        ))
        conn.execute(schema_version.update().values(version=6))

    run_migrations(engine)

    assert _sums(engine, "month") == {date(2020, 1, 1): 8.4}
    with engine.connect() as conn:
        assert conn.execute(select(schema_version.c.version)).scalar_one() == CURRENT_SCHEMA_VERSION
        # отклонения станции устарели — суточные значения изменились
        assert conn.execute(select(anomaly_state.c.wmo_index)).all() == []
        temperature_rows = conn.execute(
            select(computed_indicators.c.id).where(computed_indicators.c.parameter == "T")
        ).all()
    assert temperature_rows  # температуру миграция не трогает
