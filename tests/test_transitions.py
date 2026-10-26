"""Даты устойчивого перехода температуры: метод сумм отклонений (прочтения
Гидрометцентра и "Климата России"), окно поиска и полнота, что хранится и
когда сезоны пересчитываются."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, inspect, select

from meteoregik.calculations import transitions as tr
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import (
    computed_indicators,
    rp5_readings,
    schema_version,
    temperature_transitions,
    transition_seasons,
    transition_state,
)
from meteoregik.db.session import get_engine

STATION = 27707


def _series(start: date, values: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=offset), value) for offset, value in enumerate(values)]


# --- метод сумм отклонений ---


def test_spring_example_from_task_md_mosalsk_2010():
    """Пример из task.md: весна 2010, Мосальск, порог 0 °C (суточные средние
    20.02–26.03, дальше — устойчивое тепло)."""
    values = [
        0.21, -7.62, -9.30, -4.14, 0.89, 0.78, 1.11, -1.30, -2.41, -1.80, 1.00,
        -0.19, -3.40, -5.65, -8.75, -6.99, -9.06, -7.60, -3.45, -0.32, -2.96, -3.35, -4.50, -6.49, -7.92,
        -8.72, -9.26, -3.16, 4.56, 4.40, 3.96, -0.35, 0.79, 1.17, 0.61, 5.0, 6.0,
    ]
    winter = _series(date(2010, 1, 1), [-10.0] * 50)  # 01.01–19.02 без единого дня выше 0
    days = winter + _series(date(2010, 2, 20), values)

    assert tr.find_transition(days, 0, tr.SPRING) == date(2010, 3, 20)


def test_accumulated_warmth_forgives_a_return_of_cold():
    """Весна 2014, порог 10 °C: +18.3, −7.7, +21.4, −23.6, тепло. По
    накопленной сумме 18.3 + 21.4 > 23.6 — переход с первого тёплого периода
    (по одному периоду было бы 08.05)."""
    start = date(2014, 4, 1)
    values = [5.0] * 18 + [13.66] * 5 + [7.43] * 3 + [13.57] * 6 + [5.28] * 5 + [17.0] * 40
    days = _series(start, values)

    assert tr.find_transition(days, 10, tr.SPRING) == date(2014, 4, 19)
    # "Климат России": 18.3 < 23.6 и 21.4 < 23.6 — переход после второго похолодания
    assert tr.find_transition(days, 10, tr.SPRING, tr.METHOD_KR) == date(2014, 5, 8)


def test_readings_agree_when_each_cold_spell_follows_one_warm_period():
    """Мосальск 2010 из task.md: перед каждым похолоданием — один тёплый
    период, оба прочтения дают одну дату."""
    days = _series(date(2020, 3, 1), [-5.0] * 10 + [2.0] * 3 + [-1.0] * 2 + [3.0] * 30)

    assert tr.find_transition(days, 0, tr.SPRING, tr.METHOD_KR) == tr.find_transition(days, 0, tr.SPRING)


def test_return_of_cold_larger_than_accumulated_warmth_moves_the_date():
    start = date(2020, 3, 1)
    days = _series(start, [-5.0] * 10 + [2.0] * 3 + [-4.0] * 5 + [3.0] * 30)

    # 6 накопленного против 20 похолодания — переход после него
    assert tr.find_transition(days, 0, tr.SPRING) == date(2020, 3, 19)


def test_day_exactly_on_threshold_is_not_above_it():
    start = date(2020, 3, 1)
    days = _series(start, [-1.0] * 5 + [5.0] * 3 + [6.0] * 20)

    assert tr.find_transition(days, 5, tr.SPRING) == date(2020, 3, 9)


def test_autumn_mirrors_spring():
    start = date(2020, 9, 1)
    # Холод −6 (3 дня по 3 °C при пороге 5), тепло +4, затем устойчивый холод
    days = _series(start, [12.0] * 10 + [3.0] * 3 + [7.0] * 2 + [1.0] * 40)

    assert tr.find_transition(days, 5, tr.AUTUMN) == date(2020, 9, 11)


@pytest.mark.parametrize("values", [
    [3.0] * 60,  # весь сезон выше порога — зимы ниже него не было
    [-3.0] * 60,  # ни дня выше
    [3.0] * 20 + [-1.0] * 2 + [3.0] * 38,  # с начала окна тепло, похолодание его не отменяет
])
def test_no_transition(values):
    assert tr.find_transition(_series(date(2020, 1, 1), values), 0, tr.SPRING) is None


def test_no_candidate_survives_a_long_cold_spell_at_the_end_of_the_window():
    days = _series(date(2020, 1, 1), [-5.0] * 10 + [1.0] * 5 + [-2.0] * 20)

    assert tr.find_transition(days, 0, tr.SPRING) is None


# --- окно поиска ---


def test_season_windows():
    assert tr.season_window(2020, tr.SPRING) == (date(2020, 1, 1), date(2020, 7, 31))
    assert tr.season_window(2020, tr.AUTUMN) == (date(2020, 7, 1), date(2021, 2, 28))
    assert tr.season_window(2023, tr.AUTUMN) == (date(2023, 7, 1), date(2024, 2, 29))


@pytest.mark.parametrize(("day", "seasons"), [
    (date(2020, 3, 15), [(2020, tr.SPRING)]),
    (date(2020, 7, 15), [(2020, tr.SPRING), (2020, tr.AUTUMN)]),
    (date(2020, 10, 1), [(2020, tr.AUTUMN)]),
    (date(2021, 1, 20), [(2020, tr.AUTUMN), (2021, tr.SPRING)]),
])
def test_seasons_touching_a_day(day, seasons):
    assert tr.seasons_touching(day, day) == seasons


def _annual_cycle(first: date, last: date, skip: frozenset[date] = frozenset()) -> dict[date, float]:
    """Гладкий годовой ход: минимум −10 °C около 20 января, максимум +20 °C
    около 21 июля — каждый порог пересекается один раз весной и один осенью."""
    days = {}
    day = first
    while day <= last:
        if day not in skip:
            phase = 2 * math.pi * (day.timetuple().tm_yday - 20) / 365.25
            days[day] = round(5 - 15 * math.cos(phase), 2)
        day += timedelta(days=1)
    return days


def _first_day(days: dict[date, float], start: date, end: date, condition) -> date:
    return next(day for day in sorted(days) if start <= day <= end and condition(days[day]))


def test_smooth_cycle_crosses_each_threshold_once():
    days = _annual_cycle(date(2020, 1, 1), date(2021, 2, 28))

    spring = tr.season_transitions(2020, tr.SPRING, days)
    autumn = tr.season_transitions(2020, tr.AUTUMN, days)

    for threshold in tr.THRESHOLDS:
        assert spring[threshold].day == _first_day(
            days, date(2020, 1, 1), date(2020, 7, 31), lambda t: t > threshold
        )
        assert autumn[threshold].day == _first_day(
            days, date(2020, 7, 1), date(2021, 2, 28), lambda t: t <= threshold
        )


def test_only_months_up_to_the_one_after_the_date_must_be_complete():
    """Июль неполный (6 пропусков больше 5). Весной он нужен только переходу
    через 15 °C (июнь — следующий за ним месяц июль), переходы в марте–мае
    остаются; осенью июль — начало окна, он нужен всем порогам."""
    gap = frozenset(date(2020, 7, day) for day in (1, 5, 9, 13, 17, 21))
    days = _annual_cycle(date(2020, 1, 1), date(2021, 2, 28), skip=gap)

    for method in tr.METHODS:
        spring = tr.season_transitions(2020, tr.SPRING, days, method)
        autumn = tr.season_transitions(2020, tr.AUTUMN, days, method)
        assert {t: spring[t].status for t in tr.THRESHOLDS} == {
            0: tr.STATUS_DATE, 5: tr.STATUS_DATE, 10: tr.STATUS_DATE, 15: tr.STATUS_NO_DATA,
        }
        assert {t.status for t in autumn.values()} == {tr.STATUS_NO_DATA}


def test_incomplete_month_before_the_date_means_no_data():
    gap = frozenset(date(2020, 1, day) for day in range(1, 11))
    days = _annual_cycle(date(2020, 1, 1), date(2020, 12, 31), skip=gap)

    assert {t.status for t in tr.season_transitions(2020, tr.SPRING, days).values()} == {tr.STATUS_NO_DATA}


def test_no_transition_needs_the_whole_window():
    """"Перехода не было" — утверждение про весь сезон: при неполном июле
    его не сказать."""
    days = {day: -5.0 for day in (date(2020, 1, 1) + timedelta(days=n) for n in range(213))}
    assert tr.season_transitions(2020, tr.SPRING, days)[0].status == tr.STATUS_NONE

    for day in (1, 5, 9, 13, 17, 21):
        del days[date(2020, 7, day)]
    assert tr.season_transitions(2020, tr.SPRING, days)[0].status == tr.STATUS_NO_DATA


@pytest.mark.parametrize(("day", "last"), [
    (date(2020, 4, 19), (2020, 5)),
    (date(2020, 7, 2), (2020, 7)),  # месяца после даты в окне нет
    (None, (2020, 7)),
])
def test_required_months(day, last):
    months = tr.required_months(2020, tr.SPRING, day)

    assert (months[0], months[-1]) == ((2020, 1), last)


def test_short_gap_inside_a_complete_month_is_skipped():
    """3 пропущенных дня подряд — месяц полный; периоды по обе стороны
    пропуска не рвутся."""
    full = _annual_cycle(date(2020, 1, 1), date(2021, 2, 28))
    crossing = tr.season_transitions(2020, tr.SPRING, full)[0].day
    gap = frozenset(crossing + timedelta(days=offset) for offset in (1, 2, 3))
    days = _annual_cycle(date(2020, 1, 1), date(2021, 2, 28), skip=gap)

    assert tr.season_transitions(2020, tr.SPRING, days)[0].day == crossing


# --- средняя многолетняя дата ---


def _data(dates: dict[tuple[int, str, int], date | str], method: str = tr.METHOD_HMC) -> tr.StationTransitions:
    transitions = {
        (*key, method): tr.Transition(tr.STATUS_DATE, value) if isinstance(value, date) else tr.Transition(value)
        for key, value in dates.items()
    }
    return tr.StationTransitions(sorted({year for year, _, _ in dates}), transitions)


def test_mean_date_ignores_leap_days_and_years_without_transition():
    data = _data({
        (2019, tr.SPRING, 5): date(2019, 4, 10),
        (2020, tr.SPRING, 5): date(2020, 4, 12),  # високосный: тот же порядковый день, что 12.04.2019
        (2021, tr.SPRING, 5): tr.STATUS_NONE,
        (2022, tr.SPRING, 5): tr.STATUS_NO_DATA,
    })

    mean = tr.mean_date(data, tr.SPRING, 5)

    assert (mean.day.day, mean.day.month) == (11, 4)
    assert (mean.standard, mean.years, mean.none_years) == (False, 2, 1)


def test_mean_autumn_date_counts_january_after_december():
    data = _data({(2019, tr.AUTUMN, 0): date(2019, 12, 27), (2020, tr.AUTUMN, 0): date(2021, 1, 4)})

    mean = tr.mean_date(data, tr.AUTUMN, 0)

    assert (mean.day.day, mean.day.month) == (31, 12)


def test_mean_date_uses_wmo_years_when_there_are_enough_of_them():
    dates = {(year, tr.SPRING, 0): date(year, 3, 10) for year in range(1991, 2015)}  # 24 года
    dates[(2023, tr.SPRING, 0)] = date(2023, 5, 1)

    mean = tr.mean_date(_data(dates), tr.SPRING, 0)

    assert (mean.standard, mean.years, mean.first_year, mean.last_year) == (True, 24, 1991, 2014)
    assert (mean.day.day, mean.day.month) == (10, 3)


def test_duration_and_mean_duration():
    data = _data({
        (2020, tr.SPRING, 5): date(2020, 4, 10), (2020, tr.AUTUMN, 5): date(2020, 10, 10),
        (2021, tr.SPRING, 5): date(2021, 4, 20), (2021, tr.AUTUMN, 5): tr.STATUS_NO_DATA,
    })

    assert data.duration(2020, 5) == 183
    assert data.duration(2021, 5) is None
    assert tr.mean_duration(data, 5).days == 183


# --- хранение и пересчёт ---


@pytest.fixture
def engine(tmp_path):
    """Станция с 2019–2021 годами гладкого годового хода."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    days = _annual_cycle(date(2019, 1, 1), date(2021, 12, 31))
    with engine.begin() as conn:
        conn.execute(computed_indicators.insert(), [
            {"wmo_index": STATION, "period_type": "day", "period_start": day, "period_end": day,
             "parameter": "T", "stat_type": "avg", "value": value}
            for day, value in days.items()
        ])
    return engine


def _count(engine, table, *where) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar_one()


def test_everything_is_stale_before_the_first_calculation(engine):
    [station] = tr.stale_stations(engine)

    assert station.wmo_index == STATION
    assert len(station.seasons) == 6  # весна и осень 2019–2021


def test_ensure_fresh_stores_all_seasons_once(engine):
    done = tr.ensure_fresh(engine)

    assert [s.wmo_index for s in done] == [STATION]
    assert _count(engine, temperature_transitions) == 6 * len(tr.THRESHOLDS) * len(tr.METHODS)
    assert tr.ensure_fresh(engine) == []
    data = tr.load_station_transitions(engine, STATION)
    assert data.years == [2019, 2020, 2021]
    for method in tr.METHODS:
        assert data.get(2020, tr.SPRING, 10, method).status == tr.STATUS_DATE
        # Осень 2021: января–февраля 2022 нет, но для перехода через 0 °C в
        # ноябре нужны только месяцы по декабрь
        assert data.get(2021, tr.AUTUMN, 0, method).status == tr.STATUS_DATE


def test_recompute_marks_only_seasons_whose_window_contains_the_day(engine):
    tr.ensure_fresh(engine)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(
            wmo_index=STATION, obs_date=date(2020, 3, 15), obs_time=datetime(2020, 3, 15, 12).time(),
            T_temperature=30.0,
        ))
    recompute_range(engine, STATION, date(2020, 3, 15), date(2020, 3, 15))

    [station] = tr.stale_stations(engine)
    assert station.seasons == [(2020, tr.SPRING)]

    tr.ensure_fresh(engine)
    with engine.connect() as conn:
        state = conn.execute(select(transition_state)).one()
    assert state.recalculated == "весна 2020"


def test_old_rule_version_makes_season_stale(engine):
    tr.ensure_fresh(engine)
    with engine.begin() as conn:
        conn.execute(transition_seasons.update().where(transition_seasons.c.year == 2019).values(rule_version=0))

    [station] = tr.stale_stations(engine)
    assert station.seasons == [(2019, tr.SPRING), (2019, tr.AUTUMN)]


def test_seasons_without_data_are_removed(engine):
    tr.ensure_fresh(engine)
    with engine.begin() as conn:
        conn.execute(computed_indicators.delete().where(computed_indicators.c.period_start >= date(2021, 1, 1)))

    tr.ensure_fresh(engine)

    assert tr.load_station_transitions(engine, STATION).years == [2019, 2020]
    assert _count(engine, transition_seasons, transition_seasons.c.year == 2021) == 0


def test_station_states(engine):
    tr.ensure_fresh(engine)

    [state] = tr.station_states(engine)

    assert state.period == (date(2019, 1, 1), date(2021, 12, 31))
    assert state.years_with_dates == 3
    assert state.no_data_seasons == {}
    assert state.recalculated == "весна и осень 2019, весна и осень 2020, весна и осень 2021"


def test_migration_from_version_8_creates_tables(tmp_path):
    engine = get_engine(tmp_path / "old.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        for name in ("temperature_transitions", "transition_seasons", "transition_state"):
            conn.exec_driver_sql(f"DROP TABLE {name}")
        conn.execute(schema_version.update().values(version=8))

    run_migrations(engine)

    tables = set(inspect(engine).get_table_names())
    assert {"temperature_transitions", "transition_seasons", "transition_state"} <= tables


def test_migration_10_adds_method_and_forgets_calculated_seasons(tmp_path):
    engine = get_engine(tmp_path / "v9.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE temperature_transitions")
        conn.exec_driver_sql(
            "CREATE TABLE temperature_transitions (id INTEGER PRIMARY KEY, wmo_index INTEGER NOT NULL, "
            "year INTEGER NOT NULL, season TEXT NOT NULL, threshold INTEGER NOT NULL, status TEXT NOT NULL, "
            "transition_date DATE, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.exec_driver_sql(
            "INSERT INTO temperature_transitions (wmo_index, year, season, threshold, status) "
            "VALUES (27707, 2020, 'spring', 0, 'none')"
        )
        conn.execute(transition_seasons.insert().values(
            wmo_index=STATION, year=2020, season=tr.SPRING, calculated_at=datetime(2026, 10, 26), rule_version=1
        ))
        conn.execute(schema_version.update().values(version=9))

    run_migrations(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("temperature_transitions")}
    assert "method" in columns
    assert _count(engine, temperature_transitions) == 0
    assert _count(engine, transition_seasons) == 0
