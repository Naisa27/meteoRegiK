import pytest
from openpyxl import Workbook
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import gg_readings, stations
from meteoregik.db.session import get_engine
from meteoregik.importers.base import ImportFormatError
from meteoregik.importers.gg_importer import GgImporter

COLUMNS = ["Индекс ВМО", "Год", "Месяц", "День", "Tavg", "Tmax", "RRR", "Tmin"]


def _write_gg_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return str(path)


def test_import_file_creates_station_via_name_resolver(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[
            [27707, 2020, 1, 1, 24.2, 30.3, 0.0, 17.7],
            [27707, 2020, 1, 2, 21.5, 27.4, None, 17.8],
        ],
    )

    importer = GgImporter(engine, name_resolver=lambda wmo: f"Станция {wmo}")
    report = importer.import_file(file_path)

    assert report.station_name == "Станция 27707"
    assert report.total_rows == 2
    assert report.imported_rows == 2
    assert report.skipped_rows == 0
    assert report.period_start.isoformat() == "2020-01-01"
    assert report.period_end.isoformat() == "2020-01-02"

    with engine.connect() as conn:
        station = conn.execute(select(stations).where(stations.c.wmo_index == 27707)).mappings().one()
        rows = conn.execute(select(gg_readings).order_by(gg_readings.c.obs_date)).mappings().all()

    assert station["name"] == "Станция 27707"
    assert report.wmo_index == 27707
    assert rows[0]["t_avg"] == 24.2
    assert rows[0]["wmo_index"] == 27707
    assert rows[0]["rrr_precipitation"] == 0.0
    assert rows[0]["station_name"] == "Станция 27707"
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
    importer = GgImporter(engine)  # без name_resolver — станция уже есть, не понадобится
    report = importer.import_file(file_path)

    assert report.station_name == "Сухиничи"
    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        row = conn.execute(select(gg_readings)).mappings().one()

    assert n_stations == 1
    assert row["wmo_index"] == 27707


def test_import_file_without_name_resolver_raises_when_station_missing(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[[99999, 2020, 1, 1, 1.0, 2.0, 0.0, -1.0]],
    )
    importer = GgImporter(engine)

    with pytest.raises(ValueError):
        importer.import_file(file_path)

    with engine.connect() as conn:
        n_rows = len(conn.execute(select(gg_readings.c.id)).all())
    assert n_rows == 0  # ничего не вставлено — транзакция откатилась


def test_import_file_is_idempotent_on_reimport(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    file_path = _write_gg_xlsx(
        tmp_path / "gg.xlsx",
        rows=[[27707, 2020, 1, 1, 24.2, 30.3, 0.0, 17.7]],
    )
    importer = GgImporter(engine, name_resolver=lambda wmo: f"Станция {wmo}")

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
