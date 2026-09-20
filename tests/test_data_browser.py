from datetime import date, time

from PySide6.QtWidgets import QApplication
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from meteoregik.calculations.recompute import recompute_range
from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import computed_indicators, gg_readings, rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.ui.data_browser import (
    GG_COLUMNS,
    RP5_COLUMNS,
    DataBrowserDialog,
    ReadingsTableModel,
)

WMO_INDEX = 27704


def _qapp():
    return QApplication.instance() or QApplication([])


def _make_engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Мосальск", wmo_index=WMO_INDEX))
    return engine


def _insert_rp5_rows(engine, rows):
    with engine.begin() as conn:
        conn.execute(rp5_readings.insert(), rows)


def test_readings_table_model_pagination(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    _insert_rp5_rows(
        engine,
        [
            {
                "wmo_index": WMO_INDEX,
                "obs_date": date(2020, 1, 1 + i),
                "obs_time": time(9, 0),
                "T_temperature": float(i),
            }
            for i in range(5)
        ],
    )

    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.reload()

    assert model.total_count == 5
    assert model.rowCount() == 5
    assert model.columnCount() == len(RP5_COLUMNS)


def test_readings_table_model_filters_by_wmo_and_date(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Другая", wmo_index=99999))
    _insert_rp5_rows(
        engine,
        [
            {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0), "T_temperature": 1.0},
            {"wmo_index": WMO_INDEX, "obs_date": date(2020, 2, 1), "obs_time": time(9, 0), "T_temperature": 2.0},
            {"wmo_index": 99999, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0), "T_temperature": 3.0},
        ],
    )

    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.set_filters(WMO_INDEX, date(2020, 1, 1), date(2020, 1, 31))

    assert model.total_count == 1
    assert model.rows[0]["T_temperature"] == 1.0


def test_set_data_updates_db_and_triggers_recompute(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    _insert_rp5_rows(
        engine,
        [{"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0), "T_temperature": 1.0}],
    )
    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))

    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.reload()
    col = [c[0] for c in RP5_COLUMNS].index("T_temperature")
    index = model.index(0, col)

    assert model.setData(index, "9.5") is True

    with engine.connect() as conn:
        stored = conn.execute(select(rp5_readings.c.T_temperature)).scalar_one()
        day_max = conn.execute(
            select(computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == WMO_INDEX,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.stat_type == "max",
            )
        ).scalar_one()
    assert stored == 9.5
    assert day_max == 9.5


def test_set_data_rejects_invalid_value_without_writing(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    _insert_rp5_rows(
        engine,
        [{"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0), "T_temperature": 1.0}],
    )

    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.reload()
    col = [c[0] for c in RP5_COLUMNS].index("T_temperature")
    index = model.index(0, col)

    assert model.setData(index, "не число") is False
    with engine.connect() as conn:
        stored = conn.execute(select(rp5_readings.c.T_temperature)).scalar_one()
    assert stored == 1.0


def test_insert_row_and_duplicate_key_raises(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)

    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.set_filters(None, None, None)
    model.insert_row({"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0)})

    assert model.total_count == 1

    try:
        model.insert_row({"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0)})
        assert False, "ожидался IntegrityError"
    except IntegrityError:
        pass


def test_delete_row_removes_and_triggers_recompute(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    _insert_rp5_rows(
        engine,
        [{"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 1), "obs_time": time(9, 0), "T_temperature": 1.0}],
    )
    recompute_range(engine, WMO_INDEX, date(2020, 1, 1), date(2020, 1, 1))

    model = ReadingsTableModel(engine, rp5_readings, RP5_COLUMNS, {"T_temperature"})
    model.reload()
    model.delete_row(0)

    assert model.total_count == 0
    with engine.connect() as conn:
        day_avg = conn.execute(
            select(computed_indicators.c.value).where(
                computed_indicators.c.wmo_index == WMO_INDEX,
                computed_indicators.c.period_type == "day",
                computed_indicators.c.stat_type == "avg",
            )
        ).scalar_one_or_none()
    assert day_avg is None


def test_gg_columns_used_for_gg_table(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            gg_readings.insert().values(
                wmo_index=WMO_INDEX, obs_date=date(2020, 1, 1), t_avg=1.0, t_min=0.0, t_max=2.0
            )
        )

    model = ReadingsTableModel(engine, gg_readings, GG_COLUMNS, {"t_avg", "t_min", "t_max"})
    model.reload()

    assert model.total_count == 1
    assert model.rows[0]["t_avg"] == 1.0


def test_data_browser_dialog_builds_with_rp5_default(tmp_path):
    _qapp()
    engine = _make_engine(tmp_path)
    dialog = DataBrowserDialog(engine)

    assert dialog._model.table is rp5_readings
    assert "border-radius" in dialog.styleSheet()
