"""Устойчивые пометки и автоматическое обнаружение неполного расчёта."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from meteoregik.calculations.pending import mark_pending, plan_recomputation
from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, pending_recomputations
from meteoregik.db.session import get_engine

DAY = date(2020, 1, 2)
WMO = 27705


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    result = get_engine(tmp_path / "pending.sqlite3")
    run_migrations(result)
    yield result
    result.dispose()


def add_day(engine: Engine, day: date = DAY, wmo: int = WMO, **values: object) -> None:
    with engine.begin() as conn:
        conn.execute(gg_readings.insert().values(wmo_index=wmo, obs_date=day, t_avg=values.get("t_avg", 3.0),
                                               t_min=values.get("t_min", 1.0), t_max=values.get("t_max", 5.0),
                                               rrr_precipitation=values.get("rrr_precipitation", 2.0)))


def pending_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(pending_recomputations)).mappings()]


def test_pending_marker_rolls_back_with_observations(engine: Engine) -> None:
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            conn.execute(gg_readings.insert().values(wmo_index=WMO, obs_date=DAY, t_avg=1.0))
            mark_pending(conn, WMO, [DAY])
            raise RuntimeError("Откат сырья и пометки")
    assert pending_rows(engine) == []
    with engine.connect() as conn:
        assert conn.execute(select(gg_readings)).first() is None


def test_failed_recompute_keeps_queue_and_retry_clears_it(engine: Engine) -> None:
    add_day(engine)
    with engine.begin() as conn:
        mark_pending(conn, WMO, [DAY], auto_accept=True)

    def fail(day: date) -> None:
        raise RuntimeError("Сбой после суток, до агрегатов")

    with pytest.raises(RuntimeError):
        recompute_range(engine, WMO, DAY, DAY, progress_callback=fail)
    assert pending_rows(engine)[0]["auto_accept"] is True
    recompute_range(engine, WMO, DAY, DAY)
    assert pending_rows(engine) == []
    assert plan_recomputation(engine, [WMO]) == ()


def test_new_revision_during_recompute_is_not_acknowledged(engine: Engine) -> None:
    add_day(engine)
    with engine.begin() as conn:
        mark_pending(conn, WMO, [DAY])

    def edit_again(day: date) -> None:
        with engine.begin() as conn:
            mark_pending(conn, WMO, [day])

    recompute_range(engine, WMO, DAY, DAY, progress_callback=edit_again)
    assert len(pending_rows(engine)) == 1
    assert pending_rows(engine)[0]["revision"] == 2


def test_auto_accept_updates_day_and_ancestors_without_second_question(engine: Engine) -> None:
    add_day(engine)
    recompute_range(engine, WMO, DAY, DAY)
    with engine.begin() as conn:
        conn.execute(gg_readings.update().values(t_avg=9.0).where(gg_readings.c.wmo_index == WMO))
        mark_pending(conn, WMO, [DAY], auto_accept=True)

    def unexpected(conflict: object) -> str:
        pytest.fail("Принятое изменение не должно спрашивать повторно")

    recompute_range(engine, WMO, DAY, DAY, conflict_resolver=unexpected)
    with engine.connect() as conn:
        rows = conn.execute(select(computed_indicators.c.value).where(
            computed_indicators.c.wmo_index == WMO, computed_indicators.c.parameter == "T",
            computed_indicators.c.stat_type == "avg",
        )).scalars().all()
    assert rows == [9.0] * 4


def test_latest_raw_change_replaces_auto_accept_decision(engine: Engine) -> None:
    with engine.begin() as conn:
        mark_pending(conn, WMO, [DAY], auto_accept=True)
        mark_pending(conn, WMO, [DAY], auto_accept=False)
    assert pending_rows(engine)[0]["auto_accept"] is False
    assert pending_rows(engine)[0]["revision"] == 2


def test_audit_repairs_missing_parameter_not_only_missing_date(engine: Engine) -> None:
    add_day(engine)
    recompute_range(engine, WMO, DAY, DAY)
    with engine.begin() as conn:
        conn.execute(delete(computed_indicators).where(computed_indicators.c.period_type == "day",
            computed_indicators.c.parameter == "R", computed_indicators.c.stat_type == "sum"))
    [target] = plan_recomputation(engine, [WMO])
    assert (target.wmo_index, target.start, target.end, target.periods_only) == (WMO, DAY, DAY, False)
    recompute_range(engine, target.wmo_index, target.start, target.end, periods_only=target.periods_only)
    assert plan_recomputation(engine, [WMO]) == ()


def test_period_only_repair_preserves_saved_daily_choice(engine: Engine) -> None:
    add_day(engine)
    recompute_range(engine, WMO, DAY, DAY)
    with engine.begin() as conn:
        conn.execute(computed_indicators.update().values(value=99.0).where(
            computed_indicators.c.period_type == "day", computed_indicators.c.parameter == "T",
            computed_indicators.c.stat_type == "avg"))
        conn.execute(delete(computed_indicators).where(computed_indicators.c.period_type != "day"))
    targets = plan_recomputation(engine, [WMO])
    assert targets and all(target.periods_only for target in targets)
    for target in targets:
        recompute_range(engine, target.wmo_index, target.start, target.end, periods_only=target.periods_only)
    with engine.connect() as conn:
        values = conn.execute(select(computed_indicators.c.value).where(computed_indicators.c.parameter == "T",
            computed_indicators.c.stat_type == "avg")).scalars().all()
    assert values == [99.0] * 4
    assert plan_recomputation(engine, [WMO]) == ()


def test_period_only_marker_survives_failure(engine: Engine) -> None:
    add_day(engine)
    recompute_range(engine, WMO, DAY, DAY)
    with engine.begin() as conn:
        conn.execute(delete(computed_indicators).where(computed_indicators.c.period_type == "month"))
    [target] = plan_recomputation(engine, [WMO])
    assert target.periods_only

    def fail(day: date) -> None:
        raise RuntimeError("Сбой ремонта периода")

    with pytest.raises(RuntimeError):
        recompute_range(engine, WMO, target.start, target.end, periods_only=True, progress_callback=fail)
    assert all(row["periods_only"] for row in pending_rows(engine))
    assert all(item.periods_only for item in plan_recomputation(engine, [WMO]))


def test_audit_does_not_require_legitimate_missing_gg_values(engine: Engine) -> None:
    add_day(engine, t_min=None, t_max=None, rrr_precipitation=None)
    recompute_range(engine, WMO, DAY, DAY)
    assert plan_recomputation(engine, [WMO]) == ()


def test_audit_finds_multiple_stations_and_does_not_bridge_empty_years(engine: Engine) -> None:
    add_day(engine)
    add_day(engine, date(2025, 1, 2))
    add_day(engine, DAY, 27707)
    plan = plan_recomputation(engine, [WMO, 27707])
    assert {(item.wmo_index, item.start, item.end) for item in plan} == {
        (WMO, DAY, DAY), (WMO, date(2025, 1, 2), date(2025, 1, 2)), (27707, DAY, DAY),
    }


def test_migration_from_ten_preserves_data_and_creates_queue(engine: Engine) -> None:
    from sqlalchemy import inspect
    from meteoregik.db.schema import schema_version
    add_day(engine)
    with engine.begin() as conn:
        pending_recomputations.drop(conn)
        conn.execute(schema_version.update().values(version=10))
    run_migrations(engine)
    assert "pending_recomputations" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.execute(select(gg_readings)).first() is not None


def test_missing_stat_repair_preserves_other_saved_daily_values(engine: Engine) -> None:
    add_day(engine)
    recompute_range(engine, WMO, DAY, DAY)
    with engine.begin() as conn:
        conn.execute(computed_indicators.update().where(
            computed_indicators.c.period_type == "day", computed_indicators.c.parameter == "T",
            computed_indicators.c.stat_type == "avg",
        ).values(value=99.0))
        conn.execute(delete(computed_indicators).where(
            computed_indicators.c.period_type == "day", computed_indicators.c.parameter == "T",
            computed_indicators.c.stat_type == "min",
        ))
    [target] = plan_recomputation(engine, [WMO])
    assert pending_rows(engine)[0]["missing_only"]
    recompute_range(engine, WMO, target.start, target.end, periods_only=target.periods_only)
    with engine.connect() as conn:
        day_values = dict(conn.execute(select(computed_indicators.c.stat_type, computed_indicators.c.value).where(
            computed_indicators.c.period_type == "day", computed_indicators.c.parameter == "T",
        )).all())
    assert day_values == {"avg": 99.0, "min": 1.0, "max": 5.0}
    assert plan_recomputation(engine, [WMO]) == ()
