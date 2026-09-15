from sqlalchemy import inspect

from meteoregik.db.migrations import CURRENT_SCHEMA_VERSION, run_migrations
from meteoregik.db.session import get_engine


def test_run_migrations_creates_all_tables(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")

    run_migrations(engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "schema_version",
        "stations",
        "ru_regions",
        "rp5_readings",
        "gg_readings",
        "computed_indicators",
    } <= tables


def test_run_migrations_is_idempotent(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")

    run_migrations(engine)
    run_migrations(engine)  # не должно падать при повторном запуске

    with engine.connect() as conn:
        from sqlalchemy import select

        from meteoregik.db.schema import schema_version

        version = conn.execute(select(schema_version.c.version)).scalar_one()
    assert version == CURRENT_SCHEMA_VERSION
