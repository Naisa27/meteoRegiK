from openpyxl import Workbook
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import rp5_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.importers.rp5_importer import Rp5Importer

DEFAULT_COLUMNS = [
    "Местное время в Мосальске",
    "T",
    "RRR",
    "U",
    "ff10",
    "ff3",
    "VV",
    "sss",
]


def _write_rp5_xlsx(path, wmo=27704, name="Мосальск", columns=None, rows=None):
    columns = columns if columns is not None else DEFAULT_COLUMNS
    rows = rows or []
    wb = Workbook()
    ws = wb.active
    ws.append([f"# Метеостанция {name}, Россия, WMO_ID={wmo}, выборка с 01.01.2020 по 31.12.2020, все дни"])
    ws.append(["# Кодировка: UTF-8"])
    ws.append(["#"])
    ws.append(columns)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return str(path)


def test_import_file_parses_header_and_rows(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    file_path = _write_rp5_xlsx(
        tmp_path / "rp5.xlsx",
        rows=[
            ["02.01.2020 09:00", 3.5, "Следы осадков", 94, None, 10, 4, None],
            ["01.01.2020 09:00", -1.2, "Осадков нет", 90, 13, None, 10, 2],
        ],
    )

    importer = Rp5Importer(engine)
    report = importer.import_file(file_path)

    assert report.station_name == "Мосальск"
    assert report.total_rows == 2
    assert report.imported_rows == 2
    assert report.skipped_rows == 0
    assert report.period_start.isoformat() == "2020-01-01"
    assert report.period_end.isoformat() == "2020-01-02"

    with engine.connect() as conn:
        station = conn.execute(select(stations).where(stations.c.wmo_index == 27704)).mappings().one()
        rows = conn.execute(select(rp5_readings).order_by(rp5_readings.c.obs_date)).mappings().all()

    assert station["name"] == "Мосальск"

    first, second = rows
    assert first["obs_date"].isoformat() == "2020-01-01"
    assert first["obs_time"].isoformat() == "09:00:00"
    assert first["T_temperature"] == -1.2
    assert first["RRR_precipitation"] == "Осадков нет"
    assert first["ff10_wind_gust"] == 13
    assert first["ff3_wind_gust"] is None

    assert second["RRR_precipitation"] == "Следы осадков"
    assert second["ff3_wind_gust"] == 10
    assert second["ff10_wind_gust"] is None


def test_import_file_is_idempotent_on_reimport(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    file_path = _write_rp5_xlsx(
        tmp_path / "rp5.xlsx",
        rows=[["01.01.2020 09:00", 1.0, None, 90, None, None, 10, None]],
    )
    importer = Rp5Importer(engine)

    first = importer.import_file(file_path)
    second = importer.import_file(file_path)

    assert first.imported_rows == 1
    assert second.imported_rows == 0
    assert second.skipped_rows == 1

    with engine.connect() as conn:
        n_rows = len(conn.execute(select(rp5_readings.c.id)).all())
    assert n_rows == 1


def test_import_file_dedups_by_date_and_time_allows_multiple_times_per_day(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    file_path = _write_rp5_xlsx(
        tmp_path / "rp5.xlsx",
        rows=[
            ["01.01.2020 09:00", 1.0, None, 90, None, None, 10, None],
            ["01.01.2020 12:00", 2.0, None, 88, None, None, 10, None],
        ],
    )
    importer = Rp5Importer(engine)
    report = importer.import_file(file_path)

    assert report.imported_rows == 2
    with engine.connect() as conn:
        n_rows = len(conn.execute(select(rp5_readings.c.id)).all())
    assert n_rows == 2


def test_import_file_reuses_existing_station_by_wmo_index(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    with engine.begin() as conn:
        existing_id = conn.execute(
            stations.insert().values(name="Мосальск (справочник)", wmo_index=27704)
        ).inserted_primary_key[0]

    file_path = _write_rp5_xlsx(
        tmp_path / "rp5.xlsx",
        rows=[["01.01.2020 09:00", 1.0, None, 90, None, None, 10, None]],
    )
    Rp5Importer(engine).import_file(file_path)

    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        row = conn.execute(select(stations).where(stations.c.id == existing_id)).mappings().one()

    assert n_stations == 1  # не создалась вторая станция с тем же wmo_index
    assert row["name"] == "Мосальск (справочник)"  # найденная станция не переименовывается


def test_import_file_handles_missing_optional_columns(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    # Файл без ff3/sss в выгрузке — состав столбцов зависит от того, что
    # пользователь выбрал на rp5.ru.
    file_path = _write_rp5_xlsx(
        tmp_path / "rp5.xlsx",
        columns=["Местное время в Мосальске", "T", "U"],
        rows=[["01.01.2020 09:00", 1.0, 90]],
    )
    report = Rp5Importer(engine).import_file(file_path)

    assert report.imported_rows == 1
    with engine.connect() as conn:
        row = conn.execute(select(rp5_readings)).mappings().one()
    assert row["T_temperature"] == 1.0
    assert row["U_humidity"] == 90
    assert row["ff3_wind_gust"] is None
    assert row["RRR_precipitation"] is None
