import pytest
from openpyxl import Workbook
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import gg_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.importers.base import ImportFormatError
from meteoregik.importers.gg_importer import GgImporter
from meteoregik.stations.matching import StationNotFoundError

COLUMNS = ["Индекс ВМО", "Год", "Месяц", "День", "Tavg", "Tmax", "RRR", "Tmin"]


def _write_gg_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return str(path)


def _engine_with_station(tmp_path, wmo=27707, name="Сухиничи"):
    """База со станцией в справочнике: без неё импорт не выполняется."""
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name=name, wmo_index=wmo))
    return engine


def test_import_file_loads_rows_with_station_name_from_directory(tmp_path):
    engine = _engine_with_station(tmp_path)

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[
            [27707, 2020, 1, 1, 24.2, 30.3, 0.0, 17.7],
            [27707, 2020, 1, 2, 21.5, 27.4, None, 17.8],
        ],
    )

    importer = GgImporter(engine)
    report = importer.import_file(file_path)

    assert report.station_name == "Сухиничи"
    assert report.total_rows == 2
    assert report.imported_rows == 2
    assert report.skipped_rows == 0
    assert report.period_start.isoformat() == "2020-01-01"
    assert report.period_end.isoformat() == "2020-01-02"

    with engine.connect() as conn:
        station = conn.execute(select(stations).where(stations.c.wmo_index == 27707)).mappings().one()
        rows = conn.execute(select(gg_readings).order_by(gg_readings.c.obs_date)).mappings().all()

    assert station["name"] == "Сухиничи"
    assert report.wmo_index == 27707
    assert rows[0]["t_avg"] == 24.2
    assert rows[0]["wmo_index"] == 27707
    assert rows[0]["rrr_precipitation"] == 0.0
    assert rows[0]["station_name"] == "Сухиничи"
    assert rows[1]["rrr_precipitation"] is None


def test_import_file_reuses_existing_station_by_wmo_index(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Сухиничи", wmo_index=27707))

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[[27707, 2020, 1, 1, 24.2, 30.3, 0.0, 17.7]],
    )
    importer = GgImporter(engine)
    report = importer.import_file(file_path)

    assert report.station_name == "Сухиничи"
    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        row = conn.execute(select(gg_readings)).mappings().one()

    assert n_stations == 1
    assert row["wmo_index"] == 27707


def test_import_file_refuses_stations_missing_in_directory(tmp_path):
    """Станций файла нет в справочнике — импорт останавливается целиком:
    не грузятся и строки известной станции, новые станции не создаются
    (раньше программа спрашивала название и создавала станцию)."""
    engine = _engine_with_station(tmp_path)
    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[
            [27707, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0],
            [99999, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0],
            [11111, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0],
        ],
    )

    with pytest.raises(StationNotFoundError) as excinfo:
        GgImporter(engine).import_file(file_path)

    assert excinfo.value.wmo_indexes == [11111, 99999]
    assert "Индекс ВМО" in str(excinfo.value)
    with engine.connect() as conn:
        assert conn.execute(select(gg_readings)).first() is None
        assert len(conn.execute(select(stations.c.id)).all()) == 1


def test_import_file_is_idempotent_on_reimport(tmp_path):
    engine = _engine_with_station(tmp_path)

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[[27707, 2020, 1, 1, 24.2, 30.3, 0.0, 17.7]],
    )
    importer = GgImporter(engine)

    first = importer.import_file(file_path)
    second = importer.import_file(file_path)

    assert first.imported_rows == 1
    assert second.imported_rows == 0
    assert second.skipped_rows == 1

    with engine.connect() as conn:
        n_rows = len(conn.execute(select(gg_readings.c.id)).all())
    assert n_rows == 1


def test_import_file_raises_clear_error_for_wrong_format(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    # Файл с чужими для ГГ столбцами (похож на случайную/неверную выгрузку,
    # не на rp5 специально — важно только, что нет "Индекс ВМО"/"Год"/...).
    wb = Workbook()
    ws = wb.active
    ws.append(["Местное время", "T", "RRR"])
    ws.append(["01.01.2020 09:00", 1.0, 0.0])
    file_path = str(tmp_path / "not_gg.xlsx")
    wb.save(file_path)

    with pytest.raises(ImportFormatError, match="неверный формат"):
        GgImporter(engine).import_file(file_path)


def test_rows_without_any_value_are_not_imported_and_counted(tmp_path):
    engine = _engine_with_station(tmp_path)

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[
            [27707, 2020, 1, 1, None, None, None, None],  # пустая — не грузим
            [27707, 2020, 1, 2, None, None, 0.0, None],  # одного RRR достаточно (и 0 — это данные)
            [27707, 2020, 1, 3, None, 5.0, None, None],  # одного Tmax достаточно
            [27707, 2020, 1, 4, None, None, None, None],  # пустая — не грузим
        ],
    )

    report = GgImporter(engine).import_file(file_path)

    assert report.total_rows == 4
    assert report.imported_rows == 2
    assert report.skipped_rows == 0
    assert report.rows_without_data == 2
    # период — только по строкам с данными
    assert report.period_start.isoformat() == "2020-01-02"
    assert report.period_end.isoformat() == "2020-01-03"
    with engine.connect() as conn:
        dates = [r[0].isoformat() for r in conn.execute(select(gg_readings.c.obs_date).order_by(gg_readings.c.obs_date))]
    assert dates == ["2020-01-02", "2020-01-03"]


def test_file_of_only_empty_rows_imports_nothing_and_does_not_check_station(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[[55555, 2020, 1, 1, None, None, None, None], [55555, 2020, 1, 2, None, None, None, None]],
    )

    # Станции 55555 в справочнике нет, но пустые строки импорт не останавливают
    report = GgImporter(engine).import_file(file_path)

    assert (report.total_rows, report.imported_rows, report.rows_without_data) == (2, 0, 2)
    assert report.wmo_index is None
    with engine.connect() as conn:
        assert conn.execute(select(gg_readings)).first() is None
        assert conn.execute(select(stations).where(stations.c.wmo_index == 55555)).first() is None
