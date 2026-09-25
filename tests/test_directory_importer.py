import pandas as pd
import pytest
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import ru_regions, stations
from meteoregik.db.session import get_engine
from meteoregik.stations.directory_importer import (
    DirectoryFormatError,
    DirectoryImportReport,
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


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    return engine


def test_import_stations_directory_inserts_new_rows(engine, tmp_path):
    xlsx = _write_stations_xlsx(
        tmp_path / "stations.xlsx",
        [
            (1, 20107, "Баренцбург", "ru", 0, 78.0586, 14.2210),
            (2, 37021, "Майкоп", "ru", 1, 44.6167, 40.0831),
            (3, 37609, "Ашотск", "am", "none", 41.0292, 43.8764),
            (4, 26781, "Минск", "by", "by01", 53.9, 27.5667),
        ],
    )

    report = import_stations_directory(engine, str(xlsx))
    assert report == DirectoryImportReport(inserted=4, updated=0)

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


def test_import_stations_directory_is_idempotent_and_updates_existing(engine, tmp_path):
    xlsx = _write_stations_xlsx(
        tmp_path / "stations.xlsx",
        [(1, 37021, "Майкоп", "ru", 1, 44.6167, 40.0831)],
    )
    assert import_stations_directory(engine, str(xlsx)) == DirectoryImportReport(inserted=1, updated=0)

    # Реимпорт того же файла: не создаёт дублей по wmo_index и ничего не обновляет.
    assert import_stations_directory(engine, str(xlsx)) == DirectoryImportReport(inserted=0, updated=0)

    with engine.connect() as conn:
        rows = conn.execute(select(stations)).mappings().all()
    assert len(rows) == 1

    # Реимпорт с изменившимися координатами обновляет существующую строку.
    xlsx_updated = _write_stations_xlsx(
        tmp_path / "stations_v2.xlsx",
        [(1, 37021, "Майкоп", "ru", 1, 44.7000, 40.1000)],
    )
    assert import_stations_directory(engine, str(xlsx_updated)) == DirectoryImportReport(inserted=0, updated=1)

    with engine.connect() as conn:
        rows = conn.execute(select(stations)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["lat"] == 44.7000
    assert rows[0]["updated_at"] is not None


def test_import_stations_directory_keeps_notes_and_stations_missing_from_file(engine, tmp_path):
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Своя", wmo_index=99999, notes="добавлена вручную"))
        conn.execute(stations.insert().values(name="Майкоп", wmo_index=37021, notes="проверить координаты"))
    xlsx = _write_stations_xlsx(tmp_path / "stations.xlsx", [(1, 37021, "Майкоп", "ru", 1, 44.6, 40.1)])

    import_stations_directory(engine, str(xlsx))

    with engine.connect() as conn:
        notes = dict(conn.execute(select(stations.c.wmo_index, stations.c.notes)).all())
    assert notes == {99999: "добавлена вручную", 37021: "проверить координаты"}


def test_import_stations_directory_rejects_file_without_required_columns(engine, tmp_path):
    xlsx = _write_regions_xlsx(tmp_path / "regions.xlsx", [(1, "Республика Адыгея")])

    with pytest.raises(DirectoryFormatError, match="нет столбцов wmo, name"):
        import_stations_directory(engine, str(xlsx))


@pytest.mark.parametrize(
    ("bad_row", "message"),
    [
        ((2, None, "Без индекса", "ru", 1, 44.0, 40.0), "Строка 3: в столбце wmo"),
        ((2, 37022, None, "ru", 1, 44.0, 40.0), "Строка 3: не указано название станции"),
    ],
)
def test_import_stations_directory_bad_row_changes_nothing(engine, tmp_path, bad_row, message):
    xlsx = _write_stations_xlsx(tmp_path / "stations.xlsx", [(1, 37021, "Майкоп", "ru", 1, 44.6, 40.1), bad_row])

    with pytest.raises(DirectoryFormatError, match=message):
        import_stations_directory(engine, str(xlsx))
    with engine.connect() as conn:
        assert conn.execute(select(stations)).all() == []  # весь файл проверяется до записи


def test_import_ru_regions_inserts_and_updates(engine, tmp_path):
    xlsx = _write_regions_xlsx(
        tmp_path / "regions.xlsx",
        [(0, "Россия"), (1, "Республика Адыгея")],
    )
    assert import_ru_regions(engine, str(xlsx)) == DirectoryImportReport(inserted=2, updated=0)
    assert import_ru_regions(engine, str(xlsx)) == DirectoryImportReport(inserted=0, updated=0)

    with engine.connect() as conn:
        rows = conn.execute(select(ru_regions)).mappings().all()
    assert len(rows) == 2
    by_code = {r["code"]: r for r in rows}
    assert by_code[1]["region"] == "Республика Адыгея"
    assert by_code[1]["updated_at"] is None


def test_import_ru_regions_reports_updates(engine, tmp_path):
    import_ru_regions(engine, str(_write_regions_xlsx(tmp_path / "a.xlsx", [(1, "Адыгея")])))
    report = import_ru_regions(
        engine, str(_write_regions_xlsx(tmp_path / "b.xlsx", [(1, "Республика Адыгея"), (2, "Башкортостан")]))
    )
    assert report == DirectoryImportReport(inserted=1, updated=1)


def test_import_ru_regions_rejects_stations_file(engine, tmp_path):
    xlsx = _write_stations_xlsx(tmp_path / "stations.xlsx", [(1, 37021, "Майкоп", "ru", 1, 44.6, 40.1)])
    with pytest.raises(DirectoryFormatError, match="справочник регионов"):
        import_ru_regions(engine, str(xlsx))
