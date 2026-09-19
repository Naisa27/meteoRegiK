from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import ru_regions, stations
from meteoregik.db.seed import ensure_seed_database
from meteoregik.db.session import get_engine


def test_ensure_seed_database_copies_template_when_missing(tmp_path):
    db_path = tmp_path / "test.sqlite3"

    ensure_seed_database(db_path)

    assert db_path.exists()
    engine = get_engine(db_path)
    run_migrations(engine)  # шаблон уже на CURRENT_SCHEMA_VERSION — должно быть no-op
    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        n_regions = len(conn.execute(select(ru_regions.c.code)).all())
        region = conn.execute(select(ru_regions).where(ru_regions.c.code == 1)).mappings().one()
        moscow = conn.execute(select(stations).where(stations.c.wmo_index == 27612)).mappings().one()

    assert n_stations == 2867
    assert n_regions == 91
    assert region["region"] == "Республика Адыгея"
    assert moscow["name"] == "Москва"


def test_ensure_seed_database_does_not_overwrite_existing_file(tmp_path):
    db_path = tmp_path / "test.sqlite3"

    ensure_seed_database(db_path)
    engine = get_engine(db_path)
    run_migrations(engine)

    with engine.begin() as conn:
        conn.execute(
            stations.update()
            .where(stations.c.wmo_index == 27612)
            .values(name="Правка пользователя")
        )

    ensure_seed_database(db_path)  # повторный запуск / обновление exe — файл уже есть

    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        row = conn.execute(select(stations).where(stations.c.wmo_index == 27612)).mappings().one()

    assert n_stations == 2867  # не задублировалось
    assert row["name"] == "Правка пользователя"  # правка не затёрта повторным копированием
