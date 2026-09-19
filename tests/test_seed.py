from sqlalchemy import select

from meteoregik.db.migrations import run_migrations
from meteoregik.db.schema import ru_regions, stations
from meteoregik.db.seed import ensure_seed_data
from meteoregik.db.session import get_engine


def test_ensure_seed_data_loads_bundled_csv(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    ensure_seed_data(engine)

    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        n_regions = len(conn.execute(select(ru_regions.c.code)).all())
        rows = conn.execute(select(stations).where(stations.c.wmo_index == 27612)).mappings().all()
        region = conn.execute(select(ru_regions).where(ru_regions.c.code == 1)).mappings().one()

    assert n_stations == 2867
    assert n_regions == 91
    assert region["region"] == "Республика Адыгея"
    if rows:
        assert rows[0]["country"] == "ru"


def test_ensure_seed_data_does_not_duplicate_or_overwrite_user_edits(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    run_migrations(engine)

    ensure_seed_data(engine)

    with engine.begin() as conn:
        conn.execute(
            stations.update()
            .where(stations.c.wmo_index == 27612)
            .values(name="Правка пользователя")
        )

    ensure_seed_data(engine)  # повторный запуск (например, следующий старт приложения)

    with engine.connect() as conn:
        n_stations = len(conn.execute(select(stations.c.id)).all())
        row = conn.execute(select(stations).where(stations.c.wmo_index == 27612)).mappings().one()

    assert n_stations == 2867  # не задублировалось
    assert row["name"] == "Правка пользователя"  # правка не затёрта повторной заливкой
