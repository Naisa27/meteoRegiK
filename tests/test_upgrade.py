"""Обновление базы прошлой версии: что предстоит досчитать, копия базы,
окно хода расчёта и поведение при сбое."""

from __future__ import annotations

import sqlite3
from datetime import date, time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from sqlalchemy import select

from meteoregik.db import migrations
from meteoregik.db.migrations import (
    CURRENT_SCHEMA_VERSION,
    backup_database,
    backup_path,
    pending_data_updates,
    run_migrations,
)
from meteoregik.db.schema import computed_indicators, rp5_readings, schema_version, stations
from meteoregik.db.session import get_engine
from meteoregik.ui import upgrade_dialog
from meteoregik.ui.upgrade_dialog import UpgradeDialog, failure_text, run_upgrade, upgrade_text

WMO_INDEX = 27707


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def old_database(tmp_path):
    """База версии 6 с залитой станцией: осадков в расчётной таблице нет."""
    db_path = tmp_path / "meteoregik.sqlite3"
    engine = get_engine(db_path)
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(stations.insert().values(name="Сухиничи", wmo_index=WMO_INDEX))
        conn.execute(rp5_readings.insert(), [
            {"wmo_index": WMO_INDEX, "obs_date": date(2020, 1, 10), "obs_time": time(9, 0), "T_temperature": 1.0,
             "RRR_precipitation": "6", "tR_precipitation_period": 12.0},
        ])
        conn.execute(schema_version.update().values(version=6))
    return db_path, engine


def _precipitation_rows(engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(computed_indicators.c.id).where(computed_indicators.c.parameter == "R")).all())


def _version(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(schema_version.c.version)).scalar_one()


def test_old_database_with_data_has_pending_updates_named_for_the_user(old_database):
    _db_path, engine = old_database

    assert pending_data_updates(engine) == ["суммы осадков за сутки, декаду, месяц и год"]


def test_nothing_is_pending_for_current_new_or_empty_database(tmp_path, old_database):
    _db_path, old_engine = old_database
    run_migrations(old_engine)
    assert pending_data_updates(old_engine) == []  # уже обновлена

    assert pending_data_updates(get_engine(tmp_path / "new.sqlite3")) == []  # ещё не создана

    empty = get_engine(tmp_path / "empty.sqlite3")
    run_migrations(empty)
    with empty.begin() as conn:
        conn.execute(schema_version.update().values(version=6))
    assert pending_data_updates(empty) == []  # данных нет — считать нечего, окно не нужно


def test_backup_is_a_readable_copy_next_to_the_database(old_database):
    db_path, _engine = old_database

    backup = backup_database(db_path)

    assert backup == backup_path(db_path) == db_path.with_name("meteoregik_до_обновления.sqlite3")
    copy = sqlite3.connect(backup)
    try:
        assert copy.execute("select count(*) from rp5_readings").fetchone() == (1,)
        assert copy.execute("select version from schema_version").fetchone() == (6,)
    finally:
        copy.close()


def test_upgrade_text_names_the_new_indicators():
    text = upgrade_text(["суммы осадков за сутки, декаду, месяц и год"])

    assert "Идёт расчёт новых показателей" in text
    assert "суммы осадков за сутки, декаду, месяц и год" in text
    assert "не закрывайте программу" in text


def test_run_upgrade_calculates_makes_backup_and_reports_stations(old_database, monkeypatch):
    _qapp()
    db_path, engine = old_database
    monkeypatch.setattr(upgrade_dialog, "MIN_VISIBLE_SECONDS", 0.0)
    shown = []
    monkeypatch.setattr(UpgradeDialog, "show_station", lambda self, number, total, label: shown.append((number, total, label)))

    assert run_upgrade(engine, db_path, pending_data_updates(engine)) is True

    assert _version(engine) == CURRENT_SCHEMA_VERSION
    assert _precipitation_rows(engine) == 4  # день, декада, месяц, год
    assert backup_path(db_path).exists()
    assert shown == [(1, 1, "Сухиничи (27707)")]


def test_failed_upgrade_leaves_database_untouched_and_tells_the_user(old_database, monkeypatch):
    _qapp()
    db_path, engine = old_database
    monkeypatch.setattr(upgrade_dialog, "MIN_VISIBLE_SECONDS", 0.0)

    def broken(conn, wmo_index):
        raise RuntimeError("диск переполнен")

    monkeypatch.setattr(migrations.precipitation, "rebuild_station", broken)
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda parent, title, text: messages.append((title, text))))

    assert run_upgrade(engine, db_path, pending_data_updates(engine)) is False

    assert _version(engine) == 6  # попытка повторится при следующем запуске
    assert _precipitation_rows(engine) == 0
    assert len(messages) == 1
    title, text = messages[0]
    assert title == "Не удалось обновить базу данных"
    assert "диск переполнен" in text and str(backup_path(db_path)) in text
    assert "База данных не изменена" in text


def test_failure_text_without_backup_does_not_mention_a_copy():
    assert "Копия базы" not in failure_text(RuntimeError("сбой"), None)


def test_upgrade_dialog_cannot_be_closed_while_calculating():
    _qapp()
    dialog = UpgradeDialog(["суммы осадков"])
    dialog.show()

    dialog.close()
    dialog.reject()
    assert dialog.isVisible()

    dialog.finish()
    assert not dialog.isVisible()
