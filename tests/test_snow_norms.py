"""Счётная норма снега: отдельная база каждого периода и нулевые пропуски."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import delete, select

from meteoregik.calculations.anomalies import calculation_status, ensure_fresh, mark_stale
from meteoregik.calculations.snow import rebuild_station
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators as ci, rp5_readings, snow_norm_bases, station_norms
from meteoregik.db.session import get_engine
from meteoregik.reports.snow_data import build_snow

WMO = 27704


@pytest.fixture
def engine(tmp_path):
    result = get_engine(tmp_path / "snow_norms.sqlite3")
    run_migrations(result)
    yield result
    result.dispose()


def _archive(engine, first: date, last: date, snow_dates: set[date]) -> None:
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert(), [dict(wmo_index=WMO, obs_date=day,
            obs_time=time(9), sss_snow_depth="1" if day in snow_dates else None)
            for day in sorted({first, last} | snow_dates)])
        rebuild_station(conn, WMO)
        mark_stale(conn, WMO)


def _norm(engine, kind: str, month: int, decade: int = 0) -> float:
    with engine.connect() as conn:
        return conn.execute(select(station_norms.c.value).where(station_norms.c.wmo_index == WMO,
            station_norms.c.parameter == "S", station_norms.c.period_type == kind,
            station_norms.c.month == month, station_norms.c.decade == decade)).scalar_one()


def test_february_norm_uses_average_fraction_and_28_point_25(engine) -> None:
    _archive(engine, date(2019, 2, 1), date(2020, 2, 29),
             {date(2019, 2, 1), date(2020, 2, 1), date(2019, 2, 21), date(2020, 2, 21)})
    assert ensure_fresh(engine) == [WMO]
    assert _norm(engine, "month", 2) == pytest.approx((2 / 28 + 2 / 29) / 2 * 28.25)
    assert _norm(engine, "decade", 2, 1) == 1
    assert _norm(engine, "decade", 2, 3) == pytest.approx((1 / 8 + 1 / 9) / 2 * 8.25)
    data = build_snow(engine, WMO, date(2019, 2, 1), date(2019, 2, 28))
    assert len(data.heat_months) == 1
    assert data.heat_months[0].anomaly == pytest.approx(2 - _norm(engine, "month", 2))
    assert "пропуски" in data.heat_months[0].base_text
    assert data.heat_months[0].complete
    assert not ensure_fresh(engine)


def test_standard_base_selected_per_month_not_complete_year(engine) -> None:
    _archive(engine, date(1991, 2, 1), date(2014, 2, 28), set())
    ensure_fresh(engine)
    with engine.connect() as conn:
        bases = {(row.month, row.decade): row for row in conn.execute(select(snow_norm_bases).where(
            snow_norm_bases.c.wmo_index == WMO, snow_norm_bases.c.period_type == "month"))}
    assert bases[2, 0].standard and bases[2, 0].years == 24
    assert not bases[1, 0].standard and bases[1, 0].years == 23
    status = calculation_status(engine)[0]
    assert not status.stale and status.snow is not None
    assert not status.snow.standard


def test_partial_edges_are_not_used_in_normal_and_remain_marked(engine) -> None:
    _archive(engine, date(2020, 1, 15), date(2020, 2, 5), {date(2020, 1, 15)})
    data = build_snow(engine, WMO, date(2020, 1, 1), date(2020, 2, 29))
    assert len(data.heat_months) == 2
    assert all(cell.norm is None and not cell.complete for cell in data.heat_months)
    assert data.heat_decades[1].norm == 0  # третья декада января покрыта целиком
    with engine.connect() as conn:
        assert conn.execute(select(ci.c.value).where(ci.c.parameter == "S",
            ci.c.stat_type == "anomaly", ci.c.period_type == "month")).first() is None


def test_year_normal_is_sum_of_month_normals_and_invalidation_removes_orphans(engine) -> None:
    _archive(engine, date(2020, 1, 1), date(2020, 12, 31), {date(2020, 2, 1)})
    ensure_fresh(engine)
    assert _norm(engine, "year", 0) == pytest.approx(sum(_norm(engine, "month", m) for m in range(1, 13)))
    assert _norm(engine, "winter_season", 7) == _norm(engine, "year", 0)
    with engine.begin() as conn:
        conn.execute(delete(rp5_readings))
        rebuild_station(conn, WMO)
        mark_stale(conn, WMO)
    ensure_fresh(engine)
    with engine.connect() as conn:
        assert conn.execute(select(snow_norm_bases)).first() is None
        assert conn.execute(select(station_norms).where(station_norms.c.parameter == "S")).first() is None


def test_missing_snow_day_outside_filter_does_not_change_base_silently(engine) -> None:
    _archive(engine, date(2020, 1, 1), date(2020, 2, 29), set())
    ensure_fresh(engine)
    with engine.begin() as conn:
        conn.execute(delete(ci).where(ci.c.parameter == "S", ci.c.stat_type == "sum",
            ci.c.period_type == "day", ci.c.period_start == date(2020, 2, 15)))
        mark_stale(conn, WMO)
    with pytest.raises(RuntimeError, match="Повторно импортируйте"):
        build_snow(engine, WMO, date(2020, 1, 1), date(2020, 1, 31))
    assert calculation_status(engine)[0].stale
