from __future__ import annotations

from pathlib import Path

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


def test_multistation_report_has_individual_file_and_new_periods(tmp_path: Path) -> None:
    """Пересчёт получает каждую станцию и только её новые дни."""
    engine = _engine_with_station(tmp_path)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Калуга", wmo_index=27705))
    importer = GgImporter(engine)
    importer.import_file(_write_gg_xlsx(
        tmp_path / "old.xlsx", rows=[[27707, 2020, 1, 1, 1, 2, 0, -1]],
    ))
    file_path = _write_gg_xlsx(
        tmp_path / "multi.xlsx", rows=[
            [27707, 2020, 1, 1, 99, 100, 20, 50],
            [27707, 2020, 1, 3, 3, 4, None, None],
            [27705, 2020, 2, 10, 5, 6, 0, 1],
            [27705, 2020, 2, 11, 7, 8, None, 2],
        ],
    )
    report = importer.import_file(file_path)
    assert report.wmo_index is None and report.station_name is None
    by_station = {station.wmo_index: station for station in report.stations}
    assert set(by_station) == {27705, 27707}
    sukh = by_station[27707]
    assert sukh.station_name == "Сухиничи"
    assert (str(sukh.period_start), str(sukh.period_end)) == ("2020-01-01", "2020-01-03")
    assert (str(sukh.new_period_start), str(sukh.new_period_end)) == ("2020-01-03", "2020-01-03")
    assert (sukh.imported_rows, sukh.skipped_rows) == (1, 1)
    kaluga = by_station[27705]
    assert (str(kaluga.period_start), str(kaluga.period_end)) == ("2020-02-10", "2020-02-11")
    assert kaluga.new_period_start == kaluga.period_start
    assert kaluga.new_period_end == kaluga.period_end
    assert (report.total_rows, report.imported_rows, report.skipped_rows) == (4, 3, 1)
    again = importer.import_file(file_path)
    assert (again.imported_rows, again.skipped_rows) == (0, 4)
    assert {station.wmo_index for station in again.stations} == {27705, 27707}
    assert all(station.new_period_start is None and station.new_period_end is None for station in again.stations)


def test_identical_rows_inside_gg_file_are_skipped_with_balanced_report(tmp_path: Path) -> None:
    engine = _engine_with_station(tmp_path)
    path = _write_gg_xlsx(tmp_path / "duplicate.xlsx", rows=[
        [27707, 2020, 1, 1, 1, None, 0, None],
        [27707, 2020, 1, 1, 1, None, 0, None],
        [27707, 2020, 1, 2, None, None, None, None],
    ])
    importer = GgImporter(engine)
    report = importer.import_file(path)
    assert (report.imported_rows, report.skipped_rows, report.duplicates_in_file) == (1, 1, 1)
    assert report.total_rows == report.imported_rows + report.skipped_rows + report.rows_without_data
    assert report.stations[0].duplicates_in_file == 1
    again = importer.import_file(path)
    assert (again.imported_rows, again.skipped_rows, again.duplicates_in_file) == (0, 2, 0)
    assert again.stations[0].new_period_start is None
    assert again.stations[0].new_period_end is None
    assert str(again.stations[0].period_start) == "2020-01-01"
    with engine.connect() as conn:
        assert len(conn.execute(select(gg_readings)).all()) == 1


@pytest.mark.parametrize("column", [4, 5, 6, 7])
def test_conflicting_new_gg_keys_reject_entire_file_with_excel_rows(tmp_path: Path, column: int) -> None:
    from meteoregik.importers.base import DuplicateKeyConflictError

    engine = _engine_with_station(tmp_path)
    duplicate = [27707, 2020, 1, 1, 1, 2, 0, None]
    changed = duplicate.copy()
    changed[column] = 8
    path = _write_gg_xlsx(tmp_path / "conflict.xlsx", rows=[
        duplicate,
        [27707, 2020, 1, 2, None, None, None, None],
        [27707, 2020, 1, 3, 3, 4, 1, 0],
        changed,
    ])
    with pytest.raises(DuplicateKeyConflictError) as raised:
        GgImporter(engine).import_file(path)
    message = str(raised.value)
    assert "27707" in message and "2020-01-01" in message
    assert (raised.value.first_row, raised.value.second_row) == (2, 5)
    assert "строк" in message.lower()
    with engine.connect() as conn:
        assert conn.execute(select(gg_readings)).first() is None


def test_existing_gg_key_ignores_conflicting_file_rows(tmp_path: Path) -> None:
    engine = _engine_with_station(tmp_path)
    importer = GgImporter(engine)
    importer.import_file(_write_gg_xlsx(tmp_path / "old.xlsx", rows=[
        [27707, 2020, 1, 1, 1, 2, 0, None],
    ]))
    report = importer.import_file(_write_gg_xlsx(tmp_path / "new.xlsx", rows=[
        [27707, 2020, 1, 1, 10, 20, 30, 40],
        [27707, 2020, 1, 1, 50, 60, 70, 80],
        [27707, 2020, 1, 2, 2, 3, None, None],
    ]))
    assert (report.imported_rows, report.skipped_rows, report.duplicates_in_file) == (1, 2, 0)
    with engine.connect() as conn:
        old = conn.execute(select(gg_readings).where(gg_readings.c.obs_date == report.period_start)).mappings().one()
    assert old["t_avg"] == 1
