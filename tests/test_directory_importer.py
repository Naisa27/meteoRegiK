import pandas as pd
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.stations.directory_importer import (
    import_ru_regions,
    import_stations_directory,
)


def _write_stations_xlsx(path, rows):
    df = pd.DataFrame(rows, columns=["id", "wmo", "name", "country", "state", "lat", "lon"])
    df.to_excel(path, sheet_name="meteostations", index=False)
    return path


def _write_regions_xlsx(path, rows):
    df = pd.DataFrame(rows, columns=["code", "region"])
    df.to_excel(path, sheet_name="regions", index=False)
    return path


def test_import_stations_directory_inserts_new_rows(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    xlsx = _write_stations_xlsx(
        tmp_path / "stations.xlsx",
        [
            (1, 20107, "Баренцбург", "ru", 0, 78.0586, 14.2210),
            (2, 37021, "Майкоп", "ru", 1, 44.6167, 40.0831),
            (3, 37609, "Ашотск", "am", "none", 41.0292, 43.8764),
            (4, 26781, "Минск", "by", "by01", 53.9, 27.5667),
        ],
    )

    inserted = import_stations_directory(engine, str(xlsx))
    assert inserted == 4

    with engine.connect() as conn:
        rows = conn.execute(select(stations)).mappings().all()
    by_wmo = {r["wmo_index"]: r for r in rows}

    assert len(rows) == 4
    assert by_wmo[20107]["state"] == "0"
    assert by_wmo[37021]["state"] == "1"
    assert by_wmo[37609]["state"] is None  # 'none' -> NULL
    assert by_wmo[26781]["state"] == "by01"
    assert by_wmo[20107]["updated_at"] is None  # свежая вставка, не правка
    # id из файла игнорируется — PK автоинкрементный, не совпадает с файловым id
    assert {r["id"] for r in rows} == {1, 2, 3, 4}


def test_import_stations_directory_is_idempotent_and_updates_existing(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    xlsx = _write_stations_xlsx(
        tmp_path / "stations.xlsx",
        [(1, 37021, "Майкоп", "ru", 1, 44.6167, 40.0831)],
    )
    inserted_first = import_stations_directory(engine, str(xlsx))
    assert inserted_first == 1

    # Реимпорт того же файла: не создаёт дублей по wmo_index.
    inserted_second = import_stations_directory(engine, str(xlsx))
    assert inserted_second == 0

    with engine.connect() as conn:
        rows = conn.execute(select(stations)).mappings().all()
    assert len(rows) == 1

    # Реимпорт с изменившимися координатами обновляет существующую строку.
    xlsx_updated = _write_stations_xlsx(
        tmp_path / "stations_v2.xlsx",
        [(1, 37021, "Майкоп", "ru", 1, 44.7000, 40.1000)],
    )
    inserted_third = import_stations_directory(engine, str(xlsx_updated))
    assert inserted_third == 0

    with engine.connect() as conn:
        rows = conn.execute(select(stations)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["lat"] == 44.7000
    assert rows[0]["updated_at"] is not None


def test_import_ru_regions_inserts_and_updates(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    xlsx = _write_regions_xlsx(
        tmp_path / "regions.xlsx",
        [(0, "Россия"), (1, "Республика Адыгея")],
    )
    inserted = import_ru_regions(engine, str(xlsx))
    assert inserted == 2

    inserted_again = import_ru_regions(engine, str(xlsx))
    assert inserted_again == 0

    with engine.connect() as conn:
        rows = conn.execute(select(ru_regions)).mappings().all()
    assert len(rows) == 2
    by_code = {r["code"]: r for r in rows}
    assert by_code[1]["region"] == "Республика Адыгея"
    assert by_code[1]["updated_at"] is None
