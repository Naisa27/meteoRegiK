"""Регрессии границ архива, температурных решений и восстановления снега."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from meteoregik.calculations import snow
from meteoregik.calculations.pending import plan_recomputation
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import pending_data_updates, run_migrations
from meteoregik.db.schema import computed_indicators as ci, gg_readings, rp5_readings, schema_version
from meteoregik.db.session import get_engine

WMO = 27705
D1, D2, D3 = date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    result = get_engine(tmp_path / "snow.sqlite3")
    run_migrations(result)
    yield result
    result.dispose()


def add_rp5(engine: Engine, day: date, height: str | None, hour: int = 9) -> None:
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(wmo_index=WMO, obs_date=day,
            obs_time=time(hour), sss_snow_depth=height, T_temperature=-20.0))


def values(engine: Engine, parameter: str, kind: str = "day") -> dict[date, float]:
    stat = "max" if parameter == "SNOW_DEPTH" else "sum"
    with engine.connect() as conn:
        return dict(conn.execute(select(ci.c.period_start, ci.c.value).where(
            ci.c.wmo_index == WMO, ci.c.parameter == parameter, ci.c.stat_type == stat,
            ci.c.period_type == kind)).all())


@pytest.mark.parametrize("raw,expected", [("3,5", 3.5), ("3.5", 3.5),
    ("12", 12), ("Следы снега", 0), (None, 0), ("nan", 0), ("inf", 0), ("-3", 0)])
def test_numeric_depth(raw: str | None, expected: float) -> None:
    assert snow.numeric_depth(raw) == expected


def test_inner_gaps_maximum_and_missing_temperature(engine: Engine) -> None:
    add_rp5(engine, D1, "3,5")
    add_rp5(engine, D1, "6", 12)
    add_rp5(engine, D3, "текст")
    recompute_range(engine, WMO, D1, D3)
    assert values(engine, "SNOW_DEPTH") == {D1: 6, D2: 0, D3: 0}
    assert values(engine, "S") == {D1: 1, D2: 0, D3: 0}
    assert values(engine, "FROST_LOW_SNOW") == {D1: 0, D3: 1}
    assert values(engine, "S", "month")[D1] == 1
    assert values(engine, "SNOW_DEPTH", "winter_season")[date(2019, 7, 1)] == 6


def test_saved_temperature_choice_and_inclusive_thresholds(engine: Engine) -> None:
    add_rp5(engine, D1, "5")
    recompute_range(engine, WMO, D1, D1)
    assert values(engine, "FROST_LOW_SNOW")[D1] == 1
    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(wmo_index=WMO, obs_date=D1,
            t_avg=-19, t_min=-19, t_max=-19))
    recompute_range(engine, WMO, D1, D1, conflict_resolver=lambda _: "old")
    assert values(engine, "FROST_LOW_SNOW")[D1] == 1
    recompute_range(engine, WMO, D1, D1, conflict_resolver=lambda _: "new")
    assert values(engine, "FROST_LOW_SNOW")[D1] == 0


def test_archive_expands_shrinks_and_disappears(engine: Engine) -> None:
    add_rp5(engine, D3, "1")
    recompute_range(engine, WMO, D3, D3)
    add_rp5(engine, D1, "2")
    recompute_range(engine, WMO, D1, D1)
    assert set(values(engine, "SNOW_DEPTH")) == {D1, D2, D3}
    with engine.begin() as conn:
        conn.execute(delete(rp5_readings).where(rp5_readings.c.obs_date == D1))
    recompute_range(engine, WMO, D1, D1)
    assert values(engine, "SNOW_DEPTH") == {D3: 1}
    with engine.begin() as conn:
        conn.execute(delete(rp5_readings))
        conn.execute(gg_readings.insert().values(wmo_index=WMO, obs_date=D1, t_avg=-25))
    recompute_range(engine, WMO, D1, D3)
    assert values(engine, "S") == values(engine, "SNOW_DEPTH") == values(engine, "FROST_LOW_SNOW") == {}


def test_audit_repairs_snow_without_changing_temperature(engine: Engine) -> None:
    add_rp5(engine, D1, "1")
    recompute_range(engine, WMO, D1, D1)
    with engine.begin() as conn:
        conn.execute(delete(ci).where(ci.c.parameter == "S"))
        conn.execute(ci.update().where(ci.c.parameter == "T", ci.c.stat_type == "avg",
            ci.c.period_type == "day").values(value=-19))
    targets = plan_recomputation(engine, [WMO])
    assert targets and all(target.periods_only for target in targets)
    for target in targets:
        recompute_range(engine, WMO, target.start, target.end, periods_only=target.periods_only)
    assert values(engine, "S")[D1] == 1
    assert values(engine, "FROST_LOW_SNOW")[D1] == 0
    assert not plan_recomputation(engine, [WMO])


def test_migration_twelve_preserves_temperature_and_reports_progress(engine: Engine) -> None:
    add_rp5(engine, D1, "4")
    recompute_range(engine, WMO, D1, D1)
    with engine.begin() as conn:
        conn.execute(delete(ci).where(ci.c.parameter.in_(("S", "SNOW_DEPTH", "FROST_LOW_SNOW"))))
        conn.execute(schema_version.update().values(version=11))
    assert pending_data_updates(engine)
    progress: list[tuple[int, int, int]] = []
    run_migrations(engine, lambda *args: progress.append(args))
    assert progress == [(1, 1, WMO)]
    assert values(engine, "FROST_LOW_SNOW")[D1] == 1
    assert not pending_data_updates(engine)


def test_winter_boundary_and_disjoint_dates() -> None:
    assert snow.winter_bounds(date(2020, 6, 30)) == (date(2019, 7, 1), date(2020, 6, 30))
    assert snow.winter_bounds(date(2020, 7, 1)) == (date(2020, 7, 1), date(2021, 6, 30))
    assert snow.format_dates([D3, D1]) == "01.01.2020, 03.01.2020"
    assert snow.format_dates([D1, D2, D3]) == "01.01.2020–03.01.2020"


def test_migration_renames_frost_without_changing_wind(engine: Engine) -> None:
    add_rp5(engine, D1, "4")
    recompute_range(engine, WMO, D1, D1)
    with engine.begin() as conn:
        conn.execute(ci.update().where(ci.c.parameter == "FROST_LOW_SNOW").values(parameter="F"))
        conn.execute(ci.update().where(ci.c.parameter == "SNOW_DEPTH").values(parameter="H"))
        conn.execute(rp5_readings.update().values(Ff_wind_speed=7.5, H_cloud_base_height="600"))
        conn.execute(schema_version.update().values(version=12))
    run_migrations(engine)
    assert values(engine, "FROST_LOW_SNOW")[D1] == 1
    assert values(engine, "F") == {}
    assert values(engine, "SNOW_DEPTH")[D1] == 4
    with engine.connect() as conn:
        assert conn.execute(select(rp5_readings.c.Ff_wind_speed)).scalar_one() == 7.5
        assert conn.execute(select(rp5_readings.c.H_cloud_base_height)).scalar_one() == "600"
        assert not conn.execute(select(ci.c.id).where(ci.c.parameter == "H")).first()


def test_audit_removes_snow_outside_shrunken_archive(engine: Engine) -> None:
    add_rp5(engine, D1, "1")
    add_rp5(engine, D3, "1")
    recompute_range(engine, WMO, D1, D3)
    with engine.begin() as conn:
        conn.execute(delete(rp5_readings).where(rp5_readings.c.obs_date == D1))
    for target in plan_recomputation(engine, [WMO]):
        recompute_range(engine, WMO, target.start, target.end, periods_only=target.periods_only)
    assert values(engine, "SNOW_DEPTH") == {D3: 1}


def test_migration_keeps_pending_temperature_repair(engine: Engine) -> None:
    from meteoregik.calculations.pending import mark_pending
    from meteoregik.db.schema import pending_recomputations

    add_rp5(engine, D1, "5")
    recompute_range(engine, WMO, D1, D1)
    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(wmo_index=WMO, obs_date=D1,
            t_avg=-19, t_min=-19, t_max=-19))
        mark_pending(conn, WMO, [D1], auto_accept=True)
        conn.execute(schema_version.update().values(version=11))
    run_migrations(engine)
    with engine.connect() as conn:
        assert conn.execute(select(pending_recomputations.c.obs_date)).scalar_one() == D1
    for target in plan_recomputation(engine, [WMO]):
        recompute_range(engine, WMO, target.start, target.end, periods_only=target.periods_only)
    assert values(engine, "FROST_LOW_SNOW")[D1] == 0
