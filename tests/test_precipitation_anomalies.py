"""Нормы, полнота и отклонения осадков; сезонные суммы."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from meteoregik.calculations import precipitation
from meteoregik.calculations.anomalies import (
    PRECIPITATION,
    STAT_ANOMALY,
    TEMPERATURE,
    calculation_status,
    compute_norms,
    fetch_station_values,
    load_station_norms,
    recalculate_station,
)
from meteoregik.calculations.precipitation import season_bounds, season_months, season_year, seasons_in_range
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, norm_bases, period_completeness, station_norms
from meteoregik.db.session import get_engine
from meteoregik.reports.aggregate_query import station_period_bounds

STATION = 27707


# --- сезоны ---


@pytest.mark.parametrize(
    ("day", "start", "end"),
    [
        (date(2020, 1, 15), date(2019, 12, 1), date(2020, 2, 29)),  # зима — с декабря прошлого года
        (date(2019, 12, 31), date(2019, 12, 1), date(2020, 2, 29)),
        (date(2021, 2, 28), date(2020, 12, 1), date(2021, 2, 28)),
        (date(2020, 3, 1), date(2020, 3, 1), date(2020, 5, 31)),
        (date(2020, 8, 31), date(2020, 6, 1), date(2020, 8, 31)),
        (date(2020, 11, 30), date(2020, 9, 1), date(2020, 11, 30)),
    ],
)
def test_season_bounds(day, start, end):
    assert season_bounds(day) == (start, end)


def test_season_months_and_year_of_winter():
    winter = date(2019, 12, 1)

    assert season_months(winter) == [(2019, 12), (2020, 1), (2020, 2)]
    assert season_year(winter) == 2020  # год, в котором зима заканчивается
    assert season_year(date(2020, 6, 1)) == 2020


def test_seasons_in_range_lists_every_touched_season():
    assert [start for start, _ in seasons_in_range(date(2020, 2, 20), date(2020, 6, 1))] == [
        date(2019, 12, 1), date(2020, 3, 1), date(2020, 6, 1),
    ]


# --- расчёт по базе ---


def _gg_days(first: date, last: date, amount: float, skip: frozenset[date] = frozenset()) -> list[dict]:
    rows = []
    day = first
    while day <= last:
        if day not in skip:
            rows.append({"wmo_index": STATION, "obs_date": day, "t_avg": 5.0, "t_min": 0.0, "t_max": 10.0,
                         "rrr_precipitation": amount, "source_file": "gg.xlsx"})
        day += timedelta(days=1)
    return rows


@pytest.fixture(scope="module")
def template_database(tmp_path_factory):
    """Три года ГГ по 1 мм в день, в 2021 — по 2 мм; за 10 июля 2020 строки
    нет вовсе: июль, лето и 2020 год для осадков неполные. Пересчёт трёх лет
    по дням занимает секунды, поэтому база собирается один раз на модуль, а
    каждый тест получает её копию."""
    db_path = tmp_path_factory.mktemp("precipitation") / "template.sqlite3"
    engine = get_engine(db_path)
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(gg_readings.insert(), [
            *_gg_days(date(2019, 1, 1), date(2019, 12, 31), 1.0),
            *_gg_days(date(2020, 1, 1), date(2020, 12, 31), 1.0, skip=frozenset({date(2020, 7, 10)})),
            *_gg_days(date(2021, 1, 1), date(2021, 12, 31), 2.0),
        ])
    recompute_range(engine, STATION, date(2019, 1, 1), date(2021, 12, 31))
    engine.dispose()
    return db_path


@pytest.fixture
def engine(template_database, tmp_path):
    source = sqlite3.connect(template_database)
    target = sqlite3.connect(tmp_path / "test.sqlite3")
    try:
        source.backup(target)  # не копия файла: часть данных может быть в журнале -wal
    finally:
        source.close()
        target.close()
    return get_engine(tmp_path / "test.sqlite3")


def _value(engine, period_type: str, start: date, stat_type: str = "sum") -> float | None:
    with engine.connect() as conn:
        return conn.execute(
            select(computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == STATION,
                computed_indicators.c.parameter == "R",
                computed_indicators.c.period_type == period_type,
                computed_indicators.c.period_start == start,
                computed_indicators.c.stat_type == stat_type,
            )
        ).scalar_one_or_none()


def test_recompute_stores_season_sums_with_winter_across_two_years(engine):
    # зима 2020: декабрь 2019 (31) + январь (31) + февраль 2020 (29)
    assert _value(engine, "season", date(2019, 12, 1)) == 91.0
    assert _value(engine, "season", date(2020, 6, 1)) == 91.0  # лето без 10 июля: 92 − 1
    # зима 2019 — только январь и февраль: декабря 2018 в базе нет
    assert _value(engine, "season", date(2018, 12, 1)) == 59.0


def test_season_rows_exist_only_for_precipitation(engine):
    with engine.connect() as conn:
        parameters = conn.execute(
            select(computed_indicators.c.parameter).where(computed_indicators.c.period_type == "season").distinct()
        ).scalars().all()

    assert parameters == ["R"]


def test_rebuild_station_matches_recompute_including_seasons(engine):
    def stored():
        with engine.connect() as conn:
            return sorted(
                tuple(row) for row in conn.execute(
                    select(
                        computed_indicators.c.period_type, computed_indicators.c.period_start,
                        computed_indicators.c.period_end, computed_indicators.c.value,
                    ).where(computed_indicators.c.parameter == "R")
                )
            )

    by_recompute = stored()
    with engine.begin() as conn:
        precipitation.rebuild_station(conn, STATION)

    assert stored() == by_recompute


def test_archive_bounds_do_not_start_with_previous_december(engine):
    """Зима 2019 начинается 1 декабря 2018 — "весь архив" от этого не должен
    начинаться с 2018 года."""
    assert station_period_bounds(engine, STATION) == (date(2019, 1, 1), date(2021, 12, 31))


def test_month_with_a_single_missing_day_is_incomplete_for_precipitation_but_complete_for_temperature(engine):
    recalculate_station(engine, STATION)

    rain = load_station_norms(engine, STATION, "R")
    temperature = load_station_norms(engine, STATION, "T")

    assert (2020, 7) not in rain.complete_months and 2020 not in rain.complete_years
    assert (2020, 7) in temperature.complete_months and 2020 in temperature.complete_years  # правило "3/5"
    assert rain.complete_years == frozenset({2019, 2021})


def test_precipitation_norms_are_means_of_complete_period_sums(engine):
    recalculate_station(engine, STATION)
    norms = load_station_norms(engine, STATION, "R")

    assert norms.standard is False  # полных лет меньше 24 — по всему архиву
    assert norms.annual.value == pytest.approx((365 + 730) / 2)  # 2020 неполный, в норму не входит
    assert (norms.annual.first_year, norms.annual.last_year, norms.annual.year_count) == (2019, 2021, 2)
    assert norms.months[1] == pytest.approx((31 + 31 + 62) / 3)  # январь полный во все три года
    assert norms.months[7] == pytest.approx((31 + 62) / 2)  # июль 2020 неполный
    assert norms.decades[(7, 1)] == pytest.approx((10 + 20) / 2)  # первая декада июля 2020 неполная


def test_season_norms_and_completeness(engine):
    recalculate_station(engine, STATION)
    norms = load_station_norms(engine, STATION, "R")

    # зима 2019 без декабря 2018 и лето 2020 без 10 июля — неполные
    assert date(2018, 12, 1) not in norms.complete_seasons
    assert date(2020, 6, 1) not in norms.complete_seasons
    assert date(2019, 12, 1) in norms.complete_seasons
    assert norms.seasons[6] == pytest.approx((92 + 184) / 2)  # лето: 2019 и 2021
    # зима: 2020 (91 мм) и 2021 (31 + 62 + 56 = 149 мм)
    assert norms.seasons[12] == pytest.approx((91 + 149) / 2)


def test_precipitation_anomalies_are_stored_in_millimetres_for_all_period_types(engine):
    recalculate_station(engine, STATION)
    norms = load_station_norms(engine, STATION, "R")

    assert _value(engine, "year", date(2021, 1, 1), STAT_ANOMALY) == pytest.approx(730 - norms.annual.value)
    assert _value(engine, "month", date(2021, 7, 1), STAT_ANOMALY) == pytest.approx(62 - norms.months[7])
    assert _value(engine, "decade", date(2021, 7, 1), STAT_ANOMALY) == pytest.approx(20 - norms.decades[(7, 1)])
    assert _value(engine, "season", date(2021, 6, 1), STAT_ANOMALY) == pytest.approx(184 - norms.seasons[6])
    # неполный период тоже получает отклонение — полнота хранится рядом
    assert _value(engine, "month", date(2020, 7, 1), STAT_ANOMALY) == pytest.approx(30 - norms.months[7])


def test_saved_norms_equal_computed_on_the_fly(engine):
    recalculate_station(engine, STATION)

    for rules in (TEMPERATURE, PRECIPITATION):
        values = fetch_station_values(engine, STATION, rules)
        expected = compute_norms(rules, values.days, values.months, values.years, values.seasons)
        assert load_station_norms(engine, STATION, rules.parameter) == expected


def test_completeness_and_norm_base_are_stored_per_parameter(engine):
    recalculate_station(engine, STATION)

    with engine.connect() as conn:
        bases = {row.parameter: (row.standard, row.years) for row in conn.execute(select(norm_bases))}
        july = dict(conn.execute(
            select(period_completeness.c.parameter, period_completeness.c.complete).where(
                period_completeness.c.period_type == "month",
                period_completeness.c.period_start == date(2020, 7, 1),
            )
        ).all())
        season_norm_parameters = conn.execute(
            select(station_norms.c.parameter).where(station_norms.c.period_type == "season").distinct()
        ).scalars().all()
    assert bases == {"T": (False, 3), "R": (False, 2)}
    assert july == {"T": True, "R": False}
    assert season_norm_parameters == ["R"]


def test_calculation_status_reports_both_norm_bases(engine):
    (stale,) = calculation_status(engine)
    assert stale.stale and stale.precipitation is None

    recalculate_station(engine, STATION)
    (status,) = calculation_status(engine)

    assert (status.norm_standard, status.norm_first_year, status.norm_last_year, status.norm_years) == (
        False, 2019, 2021, 3,
    )
    assert (status.precipitation.first_year, status.precipitation.last_year, status.precipitation.years) == (
        2019, 2021, 2,
    )


def test_recalculation_is_repeatable_and_keeps_values(engine):
    """Повторный расчёт не плодит строк отклонений и не трогает сами суммы."""
    recalculate_station(engine, STATION)
    with engine.connect() as conn:
        before = conn.execute(select(computed_indicators.c.id)).all()
    recalculate_station(engine, STATION)
    with engine.connect() as conn:
        after = conn.execute(select(computed_indicators.c.id)).all()

    assert len(after) == len(before)
    assert _value(engine, "year", date(2021, 1, 1)) == 730.0
