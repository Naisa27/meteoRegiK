"""Регрессии зависимостей ручных исправлений наблюдений."""
from __future__ import annotations

from datetime import date, time

from sqlalchemy import select

from meteoregik.calculations.recompute import recompute_range
from meteoregik.calculations.freshness import ensure_computed_fresh
from meteoregik.db.schema import computed_indicators as ci, rp5_readings, pending_recomputations
from meteoregik.ui.data_browser import ReadingsTableModel, RP5_COLUMNS, RP5_TEMP_COLUMNS
from test_data_browser import _make_engine, _qapp, WMO_INDEX


def value(engine, day: date, parameter: str, stat: str, kind: str = "day") -> float:
    with engine.connect() as conn:
        return conn.execute(select(ci.c.value).where(
            ci.c.wmo_index == WMO_INDEX, ci.c.period_start == day,
            ci.c.parameter == parameter, ci.c.stat_type == stat, ci.c.period_type == kind,
        )).scalar_one()


def test_snow_edit_updates_month_and_preserves_selected_temperature(tmp_path) -> None:
    _qapp()
    engine = _make_engine(tmp_path)
    day = date(2005, 7, 3)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(wmo_index=WMO_INDEX, obs_date=day,
            obs_time=time(9), T_temperature=-21, sss_snow_depth="1"))
    recompute_range(engine, WMO_INDEX, day, day)
    with engine.begin() as conn:
        conn.execute(ci.update().where(ci.c.parameter == "T", ci.c.stat_type == "avg",
                                      ci.c.period_type == "day").values(value=-19))
    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, RP5_TEMP_COLUMNS)
    model.reload()
    column = [item[0] for item in RP5_COLUMNS].index("sss_snow_depth")
    assert model.setData(model.index(0, column), "0")
    assert value(engine, date(2005, 7, 1), "S", "sum", "month") == 0
    assert value(engine, day, "SNOW_DEPTH", "max") == 0
    assert value(engine, day, "T", "avg") == -19


def test_period_edit_switches_rain_series_for_other_days(tmp_path) -> None:
    _qapp()
    engine = _make_engine(tmp_path)
    first, second = date(2020, 1, 1), date(2020, 1, 2)
    with engine.begin() as conn:
        for day, hour, rain in ((first, 6, "1"), (first, 9, "10"), (second, 9, "20")):
            conn.execute(rp5_readings.insert().values(wmo_index=WMO_INDEX, obs_date=day,
                obs_time=time(hour), RRR_precipitation=rain, tR_precipitation_period=12))
    recompute_range(engine, WMO_INDEX, first, second)
    assert value(engine, first, "R", "sum") == 10
    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, RP5_TEMP_COLUMNS)
    model.reload()
    column = [item[0] for item in RP5_COLUMNS].index("tR_precipitation_period")
    assert model.setData(model.index(2, column), "6")
    assert value(engine, first, "R", "sum") == 1


def test_pending_snow_repaired_before_report(tmp_path, monkeypatch) -> None:
    _qapp()
    engine = _make_engine(tmp_path)
    day = date(2020, 1, 1)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(wmo_index=WMO_INDEX, obs_date=day,
            obs_time=time(9), sss_snow_depth="1"))
    recompute_range(engine, WMO_INDEX, day, day)
    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, RP5_TEMP_COLUMNS)
    model.reload()
    monkeypatch.setattr(model, "_run_recomputation", lambda wmo: None)
    column = [item[0] for item in RP5_COLUMNS].index("sss_snow_depth")
    model.setData(model.index(0, column), "0")
    with engine.connect() as conn:
        assert conn.execute(select(pending_recomputations.c.parameters)).scalar_one() == 4
    ensure_computed_fresh(engine, [WMO_INDEX])
    assert value(engine, day, "S", "sum") == 0


def test_migration_repairs_stale_snow_without_overwriting_temperature(tmp_path) -> None:
    from meteoregik.db.schema import schema_version
    from meteoregik.db.migrations import pending_data_updates, run_migrations, backup_database
    engine = _make_engine(tmp_path)
    day = date(2020, 1, 1)
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert().values(wmo_index=WMO_INDEX, obs_date=day,
            obs_time=time(9), T_temperature=-21, sss_snow_depth="1"))
    recompute_range(engine, WMO_INDEX, day, day)
    with engine.begin() as conn:
        conn.execute(rp5_readings.update().values(sss_snow_depth="0"))
        conn.execute(ci.update().where(ci.c.parameter == "T", ci.c.stat_type == "avg",
                                      ci.c.period_type == "day").values(value=-19))
        conn.execute(schema_version.update().values(version=14))
    assert pending_data_updates(engine)
    backup = backup_database(tmp_path / "test.sqlite3")
    progress = []
    run_migrations(engine, lambda *args: progress.append(args))
    assert progress == [(1, 1, WMO_INDEX)]
    assert value(engine, day, "S", "sum") == 0
    assert value(engine, day, "T", "avg") == -19
    assert backup.exists()
