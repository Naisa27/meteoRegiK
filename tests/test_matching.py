import pytest
from sqlalchemy import func, select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import stations
from meteoregik.db.session import get_engine
from meteoregik.stations.matching import StationNotFoundError, require_stations


@pytest.fixture
def engine(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert(), [
            {"name": "Мосальск", "wmo_index": 27704},
            {"name": "Сухиничи", "wmo_index": 27707},
        ])
    return engine


def test_require_stations_returns_directory_names_by_wmo_index(engine):
    with engine.connect() as conn:
        found = require_stations(conn, [27704, 27707, 27704], index_location="в файле")

    assert found == {27704: "Мосальск", 27707: "Сухиничи"}


def test_require_stations_does_not_create_missing_station(engine):
    with engine.begin() as conn:
        with pytest.raises(StationNotFoundError):
            require_stations(conn, [99999], index_location="в файле", name_from_file="Новая")

    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(stations)).scalar_one() == 2


def test_error_for_one_station_names_index_file_name_and_what_to_do(engine):
    with engine.connect() as conn:
        with pytest.raises(StationNotFoundError) as excinfo:
            require_stations(
                conn, [99999], index_location="в первой строке файла (WMO_ID=…)", name_from_file="Калуга"
            )

    message = str(excinfo.value)
    assert excinfo.value.wmo_indexes == [99999]
    assert "Станции с индексом ВМО 99999 (в файле она названа «Калуга») нет в справочнике" in message
    assert "Файл не загружен" in message
    assert "Проверьте индекс в первой строке файла (WMO_ID=…)" in message
    assert "«Справочники» → «Добавить станцию»" in message


def test_error_lists_all_missing_stations_at_once(engine):
    with engine.connect() as conn:
        with pytest.raises(StationNotFoundError) as excinfo:
            require_stations(conn, [55555, 27704, 11111], index_location="в столбце «Индекс ВМО»")

    assert excinfo.value.wmo_indexes == [11111, 55555]
    assert "Станций с индексами ВМО 11111, 55555 нет в справочнике" in str(excinfo.value)
