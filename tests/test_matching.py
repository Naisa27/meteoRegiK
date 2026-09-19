import pytest
from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import stations
from meteoregik.db.session import get_engine
from meteoregik.stations.matching import find_or_create_station


def test_find_or_create_station_finds_existing_by_wmo_index(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    with engine.begin() as conn:
        existing_id = conn.execute(
            stations.insert().values(name="Мосальск", wmo_index=27704)
        ).inserted_primary_key[0]

    with engine.begin() as conn:
        found_id = find_or_create_station(conn, 27704, name="Другое имя — не должно использоваться")

    assert found_id == existing_id
    with engine.connect() as conn:
        row = conn.execute(select(stations).where(stations.c.id == existing_id)).mappings().one()
    assert row["name"] == "Мосальск"  # найденная станция не переименовывается


def test_find_or_create_station_creates_new_when_missing(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    with engine.begin() as conn:
        new_id = find_or_create_station(conn, 99999, name="Новая станция")

    with engine.connect() as conn:
        row = conn.execute(select(stations).where(stations.c.id == new_id)).mappings().one()
    assert row["name"] == "Новая станция"
    assert row["wmo_index"] == 99999
    assert row["country"] is None


def test_find_or_create_station_without_name_raises_when_missing(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    with engine.begin() as conn:
        with pytest.raises(ValueError):
            find_or_create_station(conn, 12345, name=None)


def test_find_or_create_station_second_call_reuses_created_station(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    with engine.begin() as conn:
        first_id = find_or_create_station(conn, 55555, name="Сухиничи")
        second_id = find_or_create_station(conn, 55555)  # без имени — уже должна найтись

    assert first_id == second_id
